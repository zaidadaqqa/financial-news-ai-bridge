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
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.client = httpx.AsyncClient(timeout=30.0)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def publish_message(self, text: str) -> str | None:
        """Publishes a new message and returns its message ID."""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                return str(data["result"]["message_id"])
            raise TelegramPublishError(f"Telegram returned error: {data}")

        except httpx.RequestError as e:
            logger.error("Failed to connect to Telegram", error=str(e))
            raise RetryableError("Network error calling Telegram") from e

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def edit_message(self, message_id: str, text: str) -> bool:
        """Edits an existing message by its ID."""
        url = f"{self.base_url}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            response = await self.client.post(url, json=payload)
            if response.status_code == 429:
                raise RetryableError("Telegram API Rate Limit (429)")

            response.raise_for_status()
            return bool(response.json().get("ok", False))

        except httpx.RequestError as e:
            logger.error("Failed to edit Telegram message", error=str(e))
            raise RetryableError("Network error editing Telegram message") from e
