"""Public entrypoint for the News Intelligence Engine.

classify_news() is a pure, local, deterministic function: no I/O, no network, no
database access, no OpenAI call. It never raises — any internal error is caught
and logged, and a safe fallback result is returned instead. See
.claude_memory/NEWS_INTELLIGENCE_ARCHITECTURE.md for the full design (hybrid
Tier A/B/C classification, authority rules, fallback semantics).
"""

from app.constants.enums import NewsCategory
from app.log.logger import get_logger
from app.services.intelligence.models import (
    SAFE_FALLBACK,
    NewsIntelligenceResult,
    Urgency,
)
from app.services.intelligence.rules import (
    compare_economic_values,
    detect_bare_currency,
    detect_central_bank,
    detect_country,
    detect_economic_indicator,
    extract_actual_forecast_previous,
    has_breaking_shock_language,
    has_generic_central_bank_phrase,
    is_earnings,
    is_routine_series,
    pick_tier_b_winner,
    score_tier_b,
)

logger = get_logger(__name__)


def classify_news(
    headline: str, source_url: str | None = None
) -> NewsIntelligenceResult:
    """Classify a headline. Never raises — returns SAFE_FALLBACK on internal error."""
    try:
        return _classify(headline)
    except Exception as e:
        logger.warning(
            "News classification failed, using safe fallback",
            error_type=type(e).__name__,
        )
        return SAFE_FALLBACK


def _classify(headline: str) -> NewsIntelligenceResult:
    reasons: list[str] = []

    category: NewsCategory | None = None
    central_bank: str | None = None
    country: str | None = None
    currency: str | None = None

    # --- Tier A: hard overrides -------------------------------------------------
    cb_match = detect_central_bank(headline)
    if cb_match:
        code, cb_country, cb_currency = cb_match
        category = NewsCategory.CENTRAL_BANK
        central_bank = code
        country = cb_country
        currency = cb_currency
        reasons.append(f"central_bank_hard_override:{code}")
    elif has_generic_central_bank_phrase(headline):
        # No specific institution named (e.g. "China central bank injects...")
        # — still a confident CENTRAL_BANK signal; central_bank code stays
        # unknown rather than guessed. Real gap found in §16 review.
        category = NewsCategory.CENTRAL_BANK
        reasons.append("central_bank_hard_override:generic_phrase")

    actual, forecast, previous = extract_actual_forecast_previous(headline)
    economic_event = detect_economic_indicator(headline)

    if category is None and economic_event is not None:
        category = NewsCategory.ECONOMIC_DATA
        reasons.append(f"economic_data_hard_override:{economic_event}")

    if category is None and is_earnings(headline):
        category = NewsCategory.EARNINGS
        reasons.append("earnings_hard_override")

    # --- Tier B: evidence scoring (only if no Tier A rule fired) ---------------
    if category is None:
        scores = score_tier_b(headline)
        winner = pick_tier_b_winner(scores)
        if winner is not None:
            category = winner
            reasons.append(f"tier_b_winner:{winner.value}:score={scores[winner]}")
        else:
            category = NewsCategory.GENERAL
            reasons.append("no_tier_b_evidence")

    geopolitical = category == NewsCategory.GEOPOLITICAL

    # --- Country / currency (fill in whatever Tier A didn't already set) -------
    if country is None or currency is None:
        country_match = detect_country(headline)
        if country_match:
            country = country or country_match[0]
            currency = currency or country_match[1]
    if currency is None:
        currency = detect_bare_currency(headline)

    # --- Numeric surprise --------------------------------------------------------
    if forecast is not None:
        surprise = compare_economic_values(actual, forecast)
    elif previous is not None:
        surprise = compare_economic_values(actual, previous)
    else:
        surprise = compare_economic_values(actual, None)

    # --- Urgency (independent of category) --------------------------------------
    urgency = _derive_urgency(headline, category, central_bank, geopolitical)

    # --- Affected assets (hint only — never rendered directly, see architecture §7) ---
    affected_assets = tuple(a for a in (currency,) if a)

    # --- Fallback semantics (§12: GENERAL alone is not automatically fallback) ---
    has_any_signal = bool(
        central_bank
        or economic_event
        or country
        or currency
        or category != NewsCategory.GENERAL
        or is_routine_series(headline)
    )
    is_fallback = not has_any_signal

    return NewsIntelligenceResult(
        category=category,
        urgency=urgency,
        country=country,
        currency=currency,
        central_bank=central_bank,
        geopolitical=geopolitical,
        economic_event=economic_event,
        actual=actual,
        forecast=forecast,
        previous=previous,
        surprise_direction=surprise,
        affected_assets=affected_assets,
        classification_reasons=tuple(reasons),
        is_fallback=is_fallback,
    )


def _derive_urgency(
    headline: str,
    category: NewsCategory,
    central_bank: str | None,
    geopolitical: bool,
) -> Urgency:
    # Routine recurring data-series snapshots (checked first) are never urgent,
    # even when they happen to match central-bank/geopolitical evidence — e.g.
    # "Fed Interest Rate Probabilities" is a routine snapshot, not breaking news.
    if is_routine_series(headline):
        return Urgency.LOW

    shock = has_breaking_shock_language(headline)
    if shock and (central_bank or geopolitical):
        return Urgency.BREAKING

    if central_bank or geopolitical:
        return Urgency.HIGH

    return Urgency.NORMAL
