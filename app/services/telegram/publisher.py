import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import settings
from app.exceptions.custom_exceptions import RetryableError, TelegramPublishError
from app.log.logger import get_logger

logger = get_logger(__name__)


class TelegramPublisher:
    def __init__(self) -> None:
        self.bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.thread_id = settings.TELEGRAM_THREAD_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.client = httpx.AsyncClient(timeout=30.0)

    def _base_payload(self) -> dict:
        payload: dict = {
            "chat_id": self.chat_id,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self.thread_id:
            payload["message_thread_id"] = self.thread_id
        return payload

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def publish_message(self, text: str) -> str | None:
        url = f"{self.base_url}/sendMessage"
        payload = {**self._base_payload(), "text": text}

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                msg_id = str(data["result"]["message_id"])
                logger.info("Telegram message sent", message_id=msg_id)
                return msg_id
            raise TelegramPublishError(
                f"Telegram API error: {data.get('description', 'unknown')}"
            )

        except httpx.RequestError as e:
            logger.error("Network error sending to Telegram", error=type(e).__name__)
            raise RetryableError("Network error calling Telegram") from e

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def edit_message(self, message_id: str, text: str) -> bool:
        url = f"{self.base_url}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            # Idempotent retry: if a network error struck AFTER Telegram
            # applied a previous attempt, the retry sends identical text and
            # Telegram answers 400 "message is not modified" — the edit IS
            # delivered. Treating that as failure wrongly FAILED a delivered
            # item in production (2026-07-13) and hid it from the
            # published-priors story gate.
            if (
                response.status_code == 400
                and "message is not modified" in response.text.lower()
            ):
                logger.info(
                    "Telegram edit already applied (retry idempotence)",
                    message_id=message_id,
                )
                return True

            response.raise_for_status()
            ok = bool(response.json().get("ok", False))
            if ok:
                logger.info("Telegram message edited", message_id=message_id)
            return ok

        except httpx.RequestError as e:
            logger.error(
                "Network error editing Telegram message", error=type(e).__name__
            )
            raise RetryableError("Network error editing Telegram message") from e

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def pin_chat_message(
        self, message_id: str, disable_notification: bool = True
    ) -> bool:
        url = f"{self.base_url}/pinChatMessage"
        payload = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "disable_notification": disable_notification,
        }

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            if response.status_code == 200 and response.json().get("ok"):
                logger.info("Telegram message pinned", message_id=message_id)
                return True

            # Pin failure is non-fatal by design: an unpinned digest is a
            # cosmetic defect the next cycle repairs, so the caller gets
            # False instead of an exception.
            logger.warning(
                "Telegram pin failed",
                message_id=message_id,
                status_code=response.status_code,
                description=self._api_description(response),
            )
            return False

        except httpx.RequestError as e:
            logger.error(
                "Network error pinning Telegram message", error=type(e).__name__
            )
            raise RetryableError("Network error pinning Telegram message") from e

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def get_chat(self) -> dict | None:
        url = f"{self.base_url}/getChat"
        payload = {"chat_id": self.chat_id}

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            if response.status_code == 200:
                data = response.json()
                result = data.get("result")
                if data.get("ok") and isinstance(result, dict):
                    return result

            logger.warning(
                "Telegram getChat failed",
                status_code=response.status_code,
                description=self._api_description(response),
            )
            return None

        except httpx.RequestError as e:
            logger.error(
                "Network error calling Telegram getChat", error=type(e).__name__
            )
            raise RetryableError("Network error calling Telegram getChat") from e

    @staticmethod
    def _api_description(response: httpx.Response) -> str:
        try:
            return str(response.json().get("description", ""))[:200]
        except ValueError:
            return ""

    async def close(self) -> None:
        await self.client.aclose()
