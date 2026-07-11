from dataclasses import dataclass
from enum import StrEnum

from app.constants.enums import NewsCategory


class Urgency(StrEnum):
    BREAKING = "BREAKING"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class NumericSurprise(StrEnum):
    HIGHER = "HIGHER"
    LOWER = "LOWER"
    MATCH = "MATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NewsIntelligenceResult:
    category: NewsCategory
    urgency: Urgency
    country: str | None
    currency: str | None
    central_bank: str | None
    geopolitical: bool
    economic_event: str | None
    actual: str | None
    forecast: str | None
    previous: str | None
    surprise_direction: NumericSurprise
    affected_assets: tuple[str, ...]
    classification_reasons: tuple[str, ...]
    is_fallback: bool


SAFE_FALLBACK = NewsIntelligenceResult(
    category=NewsCategory.GENERAL,
    urgency=Urgency.NORMAL,
    country=None,
    currency=None,
    central_bank=None,
    geopolitical=False,
    economic_event=None,
    actual=None,
    forecast=None,
    previous=None,
    surprise_direction=NumericSurprise.UNKNOWN,
    affected_assets=(),
    classification_reasons=("fallback",),
    is_fallback=True,
)
