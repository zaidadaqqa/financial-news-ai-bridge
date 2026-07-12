"""Editorial Engine — deterministic editorial-mode selection (permanent
production architecture, documented in .claude_memory/NEWSROOM_DNA.md §12).

Part of the editorial PRESENTATION layer: pure functions over data that
already flows into the formatter (NewsIntelligenceResult, StoryDecision,
ai_data). No I/O, no business logic, no classification — the News
Intelligence and Story Intelligence engines remain the sole authorities on
category, urgency, and story relationships. This module only decides HOW an
already-classified item is presented.

One unified visual DNA, twelve deterministic editorial modes. Every mode
shares the same skeleton (hero headline, solid header rule, dotted footer
rule, quiet footer, gated optional sections, one semantic emoji per
section); modes differ only in badge, headline icon authority, and section
hierarchy — so a reader recognizes the message TYPE instantly while every
message still unmistakably belongs to this desk.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.intelligence.models import NewsIntelligenceResult, Urgency
from app.services.story.models import RelationshipType, StoryDecision


class EditorialMode(StrEnum):
    BREAKING = "BREAKING"
    BREAKING_UPDATE = "BREAKING_UPDATE"
    STORY_UPDATE = "STORY_UPDATE"
    ECONOMIC_DATA = "ECONOMIC_DATA"
    CENTRAL_BANK = "CENTRAL_BANK"
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"
    GEOPOLITICAL = "GEOPOLITICAL"
    COMPANY = "COMPANY"
    EARNINGS = "EARNINGS"
    COMMODITIES = "COMMODITIES"
    CRYPTO = "CRYPTO"
    GENERAL = "GENERAL"


# Section identifiers assembled by the formatter. The footer is not listed:
# it is invariant DNA, identical in every mode, always last.
EXPLANATION = "explanation"
DATA = "data"
IMPACT = "impact"
CONTEXT = "context"
WATCH = "watch"
ASSETS = "assets"

# Canonical hierarchy (the unified DNA baseline):
# event → key facts → interpretation → background → forward look → assets.
_CANONICAL = (EXPLANATION, DATA, IMPACT, CONTEXT, WATCH, ASSETS)

# ECONOMIC_DATA: the numbers ARE the event — the validated data block leads,
# prose contextualizes it. (The reference-channel review confirmed traders
# scan the print first; unlike those channels, we keep actual first.)
_DATA_FIRST = (DATA, EXPLANATION, IMPACT, CONTEXT, WATCH, ASSETS)

# STORY_UPDATE / BREAKING_UPDATE: the prior development is the anchor the
# reader needs immediately — context is promoted to directly after the event
# narrative, before numbers and interpretation.
_CONTEXT_PROMOTED = (EXPLANATION, CONTEXT, DATA, IMPACT, WATCH, ASSETS)


@dataclass(frozen=True)
class EditorialPlan:
    mode: EditorialMode
    badge: str | None  # «عاجل» / «تصحيح» / «تحديث» — never stacked
    force_siren: bool  # 🚨 replaces the category icon
    section_order: tuple[str, ...]


_MODE_PLANS: dict[EditorialMode, EditorialPlan] = {
    EditorialMode.BREAKING: EditorialPlan(
        EditorialMode.BREAKING, "عاجل", True, _CANONICAL
    ),
    EditorialMode.BREAKING_UPDATE: EditorialPlan(
        EditorialMode.BREAKING_UPDATE, "عاجل", True, _CONTEXT_PROMOTED
    ),
    EditorialMode.STORY_UPDATE: EditorialPlan(
        EditorialMode.STORY_UPDATE, "تحديث", False, _CONTEXT_PROMOTED
    ),
    EditorialMode.ECONOMIC_DATA: EditorialPlan(
        EditorialMode.ECONOMIC_DATA, None, False, _DATA_FIRST
    ),
    EditorialMode.CENTRAL_BANK: EditorialPlan(
        EditorialMode.CENTRAL_BANK, None, False, _CANONICAL
    ),
    EditorialMode.OFFICIAL_STATEMENT: EditorialPlan(
        EditorialMode.OFFICIAL_STATEMENT, None, False, _CANONICAL
    ),
    EditorialMode.GEOPOLITICAL: EditorialPlan(
        EditorialMode.GEOPOLITICAL, None, False, _CANONICAL
    ),
    EditorialMode.COMPANY: EditorialPlan(
        EditorialMode.COMPANY, None, False, _CANONICAL
    ),
    EditorialMode.EARNINGS: EditorialPlan(
        EditorialMode.EARNINGS, None, False, _CANONICAL
    ),
    EditorialMode.COMMODITIES: EditorialPlan(
        EditorialMode.COMMODITIES, None, False, _CANONICAL
    ),
    EditorialMode.CRYPTO: EditorialPlan(EditorialMode.CRYPTO, None, False, _CANONICAL),
    EditorialMode.GENERAL: EditorialPlan(
        EditorialMode.GENERAL, None, False, _CANONICAL
    ),
}

# Category → mode, applied identically to the engine's authoritative category
# (confident path) and the AI's self-reported category string (fallback path,
# preserving exact Phase 1/2 authority semantics). GOVERNMENT maps to
# OFFICIAL_STATEMENT: the real production corpus shows our government
# category is almost entirely minister/official quotes.
_CATEGORY_TO_MODE: dict[str, EditorialMode] = {
    "economic_data": EditorialMode.ECONOMIC_DATA,
    "central_bank": EditorialMode.CENTRAL_BANK,
    "government": EditorialMode.OFFICIAL_STATEMENT,
    "geopolitical": EditorialMode.GEOPOLITICAL,
    "company": EditorialMode.COMPANY,
    "earnings": EditorialMode.EARNINGS,
    "commodities": EditorialMode.COMMODITIES,
    "crypto": EditorialMode.CRYPTO,
    "breaking": EditorialMode.BREAKING,
    # forex / bonds / general and anything unknown → GENERAL
}


def _has_confident_update(story: StoryDecision | None) -> bool:
    return (
        story is not None
        and story.relationship in (RelationshipType.UPDATE, RelationshipType.CORRECTION)
        and bool(story.prior_headline_ar)
    )


def select_editorial_mode(
    intelligence: NewsIntelligenceResult | None,
    story: StoryDecision | None,
    ai_data: dict[str, Any],
) -> EditorialPlan:
    """Deterministic mode selection. Precedence (documented in
    NEWSROOM_DNA.md §12): breaking beats update beats category; the
    correction badge overrides the update badge on STORY_UPDATE."""
    engine_confident = intelligence is not None and not intelligence.is_fallback

    # Breaking-ness: the engine's urgency when confident, else the exact
    # Phase 2 legacy rule (AI importance ≥ 5 or self-reported "breaking").
    importance_raw = ai_data.get("importance", 2)
    try:
        importance_int = int(importance_raw) if importance_raw is not None else 2
    except (ValueError, TypeError):
        importance_int = 2
    is_breaking = (
        engine_confident
        and intelligence is not None
        and intelligence.urgency == Urgency.BREAKING
    ) or (importance_int >= 5 or str(ai_data.get("category") or "") == "breaking")

    is_update = _has_confident_update(story)

    if is_breaking and is_update:
        return _MODE_PLANS[EditorialMode.BREAKING_UPDATE]
    if is_breaking:
        return _MODE_PLANS[EditorialMode.BREAKING]
    if is_update:
        plan = _MODE_PLANS[EditorialMode.STORY_UPDATE]
        if story is not None and story.relationship == RelationshipType.CORRECTION:
            return EditorialPlan(plan.mode, "تصحيح", False, plan.section_order)
        return plan

    if engine_confident and intelligence is not None:
        category = intelligence.category.value
    else:
        category = str(ai_data.get("category") or "")
    mode = _CATEGORY_TO_MODE.get(category, EditorialMode.GENERAL)
    return _MODE_PLANS[mode]
