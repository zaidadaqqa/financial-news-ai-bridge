import json
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import settings
from app.exceptions.custom_exceptions import AIResponseError, RetryableError
from app.log.logger import get_logger
from app.services.ai.base import BaseAIProvider

logger = get_logger(__name__)


class OpenAIProvider(BaseAIProvider):
    def __init__(self) -> None:
        self.api_key = settings.AI_API_KEY.get_secret_value()
        self.model = settings.AI_MODEL
        self.base_url = settings.AI_BASE_URL or "https://api.openai.com/v1"
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30.0,
        )
        self.system_prompt = self._load_prompt("prompts/translator.txt")
        self.glossary = self._load_prompt("prompts/glossary.txt")

    def _load_prompt(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning("Prompt file not found, falling back to empty", path=path)
            return ""

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(RetryableError),
    )
    async def generate_financial_translation(self, headline: str) -> dict[str, Any]:
        prompt_with_glossary = f"{self.system_prompt}\n\nGlossary:\n{self.glossary}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt_with_glossary},
                {"role": "user", "content": headline},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            response = await self.client.post("/chat/completions", json=payload)

            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(
                    f"OpenAI API returned status {response.status_code}"
                )

            response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            return dict(json.loads(content))

        except httpx.RequestError as e:
            logger.error("Network error during OpenAI API call", error=str(e))
            raise RetryableError("Network error calling OpenAI") from e
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse JSON from OpenAI", error=str(e), content=content
            )
            raise AIResponseError("Invalid JSON returned from AI") from e
        except Exception as e:
            if not isinstance(e, RetryableError):
                logger.error("Unexpected error in OpenAI API call", error=str(e))
                raise AIResponseError(f"Unexpected error: {str(e)}") from e
            raise e
