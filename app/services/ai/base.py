from abc import ABC, abstractmethod
from typing import Any


class BaseAIProvider(ABC):
    @abstractmethod
    async def generate_financial_translation(self, headline: str) -> dict[str, Any]:
        """
        Process the raw English headline and return a structured JSON response
        containing the translation, classification, and market intelligence.
        """
        pass
