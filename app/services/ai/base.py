from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.intelligence.models import NewsIntelligenceResult
    from app.services.story.models import StoryDecision


class BaseAIProvider(ABC):
    @abstractmethod
    async def generate_financial_translation(
        self,
        headline: str,
        intelligence: "NewsIntelligenceResult | None" = None,
        story: "StoryDecision | None" = None,
    ) -> dict[str, Any]:
        """
        Process the raw English headline and return a structured JSON response
        containing the translation, classification, and market intelligence.

        `intelligence` and `story` are optional application context (see
        app.services.intelligence / app.services.story) — implementations are
        not required to use them, but the signature must accept them for
        interface consistency.
        """
        pass
