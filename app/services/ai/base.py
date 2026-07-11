from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.intelligence.models import NewsIntelligenceResult


class BaseAIProvider(ABC):
    @abstractmethod
    async def generate_financial_translation(
        self, headline: str, intelligence: "NewsIntelligenceResult | None" = None
    ) -> dict[str, Any]:
        """
        Process the raw English headline and return a structured JSON response
        containing the translation, classification, and market intelligence.

        `intelligence` is optional, advisory application context (see
        app.services.intelligence) — implementations are not required to use
        it, but the signature must accept it for interface consistency.
        """
        pass
