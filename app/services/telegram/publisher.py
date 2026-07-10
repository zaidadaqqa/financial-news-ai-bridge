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

    async def close(self) -> None:
        await self.client.aclose()
