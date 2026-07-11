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
from app.services.intelligence.models import NewsIntelligenceResult

logger = get_logger(__name__)


def _build_intelligence_context(intelligence: NewsIntelligenceResult) -> str:
    """Builds a short, clearly-labeled internal context block appended after the
    headline. Hardening notes (Phase 2.2 review, see
    .claude_memory/NEWS_INTELLIGENCE_ARCHITECTURE.md §7):

    - Only ever built when `intelligence.is_fallback` is False (checked by the
      caller) — a low-confidence result is never handed to the model as if it
      were established fact.
    - Every value written here is a plain string/enum-`.value` primitive
      already produced elsewhere in this module (category, country, currency,
      central_bank, economic_event, numeric_surprise) — never a dataclass
      repr, an Enum repr (`Urgency.BREAKING`), or any other debugging
      representation that could leak internal Python structure into the
      prompt.
    - The block explicitly separates "authoritative" fields (category/
      country/currency/central_bank/economic_event/numeric_surprise — the
      application already decided these; the model must not contradict them)
      from the fact that Arabic prose, interpretation, and market_bias remain
      entirely the model's own job — matching the authority split in §7.
    - Explicitly forbids inventing facts beyond the headline and this block,
      to close the "instruct the AI to fabricate missing information" risk.
    """
    lines = [
        "== APPLICATION CONTEXT (internal — do not quote, translate, repeat, or "
        "mention these field names or this block in your output) ==",
        "The fields below are established by the application and are "
        "authoritative: treat them as confirmed facts and do not contradict "
        "them. They are not a substitute for the headline above — do not "
        "invent or infer any detail beyond what the headline states and what "
        "is listed here. All prose, interpretation, and market_bias remain "
        "entirely your own judgment.",
        f"category: {intelligence.category.value}",
    ]
    if intelligence.country:
        lines.append(f"country: {intelligence.country}")
    if intelligence.currency:
        lines.append(f"currency: {intelligence.currency}")
    if intelligence.central_bank:
        lines.append(f"central_bank: {intelligence.central_bank}")
    if intelligence.economic_event:
        lines.append(f"economic_event: {intelligence.economic_event}")
    if intelligence.surprise_direction.value != "UNKNOWN":
        lines.append(f"numeric_surprise: {intelligence.surprise_direction.value}")
    return "\n".join(lines)


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
    async def generate_financial_translation(
        self, headline: str, intelligence: NewsIntelligenceResult | None = None
    ) -> dict[str, Any]:
        prompt_with_glossary = f"{self.system_prompt}\n\nGlossary:\n{self.glossary}"

        # Clearly labeled separation between the source headline (translate/
        # analyze this) and internal application context (facts to stay
        # consistent with, never to quote) — reduces prompt-injection
        # ambiguity about which block is "the story" versus "instructions."
        user_content = headline
        if intelligence is not None and not intelligence.is_fallback:
            user_content = (
                f"HEADLINE:\n{headline}\n\n{_build_intelligence_context(intelligence)}"
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt_with_glossary},
                {"role": "user", "content": user_content},
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
