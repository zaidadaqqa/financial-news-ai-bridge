"""Deterministic story-matching rules (Phase 3): salient-token extraction,
weighted evidence scoring, hard exclusions, and relationship refinement.

Pure functions, no I/O. Grounded in the full 274-record production-history
audit (2026-07-10 → 2026-07-12) documented in
.claude_memory/STORY_INTELLIGENCE_ARCHITECTURE.md §4/§8 — including the three
real false-positive patterns that shaped the exclusions here.
"""

import re

from app.models.story import Story
from app.services.intelligence.models import NewsIntelligenceResult

# Generic tokens carry no story identity: function words, newswire verbs,
# calendar terms, and market generics that co-occur across unrelated
# headlines. Everything else is "salient". Kept deliberately compact — if a
# false link ever traces to one generic word, add that word here with a
# comment, never a headline-specific patch.
GENERIC_TOKENS = frozenset("""
the a an and or of to in on for with as at by from into over after before
amid is are was were be been has have had will would could should may might
says said say reports report reported new more most latest update news
rises rise fell falls falling rising jumps drops surges tumbles gains
higher lower up down near above below since due set expects expected
this that these those its his her their our your out not no off than then
if but so because while during between against about around under
week month year today yesterday tomorrow monday tuesday wednesday thursday
friday saturday sunday january february march april may june july august
september october november december
actual forecast previous yoy mom qoq
market markets stocks stock trading traders investors
million billion trillion percent bln mln
according sources source statement announcement announces announced
official officials
""".split())

_TOKEN_RE = re.compile(r"[a-z][a-z0-9'&.-]{2,}")

# Explicit correction markers only (§7). "Revised" is deliberately absent —
# it legitimately appears inside economic-data value syntax
# ("Previous -7.6%, Revised -6.6%") and would misfire there.
_CORRECTION_RE = re.compile(r"\bcorrection:|\b(?:corrects|clarifies|retracts?)\b", re.I)


def salient_tokens(headline: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall(headline.lower()))
    return {t.strip(".-'&") for t in tokens if t not in GENERIC_TOKENS and len(t) >= 3}


def has_correction_marker(headline: str) -> bool:
    return bool(_CORRECTION_RE.search(headline))


def score_candidate(
    story: Story,
    intelligence: NewsIntelligenceResult,
    item_tokens: set[str],
) -> tuple[int, list[str]]:
    """Weighted evidence score of one candidate story against a new item.

    Weights (§8): central bank +3 (strong); economic event +2 with a +1
    same-country bonus; shared salient tokens +1 each capped at 4 (matched
    against the story's bounded anchor∪latest set); category +1 (weak);
    country-or-currency +1 total (weak). Weak signals alone cannot reach
    MATCH_THRESHOLD by construction.
    """
    # Hard exclusion (real audit finding): the same economic indicator from
    # two different countries is two different stories, always — German CPI
    # must never absorb the French or Norwegian release.
    if (
        story.primary_category == "economic_data"
        and intelligence.category.value == "economic_data"
        and story.country
        and intelligence.country
        and story.country != intelligence.country
    ):
        return 0, ["excluded:conflicting_countries"]

    reasons: list[str] = []
    score = 0

    if story.central_bank and story.central_bank == intelligence.central_bank:
        score += 3
        reasons.append(f"central_bank:{story.central_bank}:+3")

    if story.economic_event and story.economic_event == intelligence.economic_event:
        score += 2
        reasons.append(f"economic_event:{story.economic_event}:+2")
        if story.country and story.country == intelligence.country:
            score += 1
            reasons.append("event_same_country:+1")

    # Event-name token must not double-count with the event match itself
    # (real audit finding: "cpi" as a token re-scored the CPI event match).
    effective_tokens = item_tokens
    if intelligence.economic_event:
        effective_tokens = item_tokens - {intelligence.economic_event.lower()}

    story_tokens = set(story.anchor_tokens) | set(story.latest_tokens)
    shared = story_tokens & effective_tokens
    if shared:
        pts = min(len(shared), 4)
        score += pts
        reasons.append(f"tokens:{sorted(shared)[:4]}:+{pts}")

    if story.primary_category == intelligence.category.value:
        score += 1
        reasons.append("category:+1")

    if (story.country and story.country == intelligence.country) or (
        story.currency and story.currency == intelligence.currency
    ):
        score += 1
        reasons.append("geo:+1")

    return score, reasons


def repetition_overlap(item_tokens: set[str], latest_tokens: set[str]) -> float:
    """Share of the smaller token set that overlaps the story's latest item —
    at/above REPETITION_OVERLAP_RATIO the item is the same information
    reworded."""
    if not item_tokens or not latest_tokens:
        return 0.0
    return len(item_tokens & latest_tokens) / min(len(item_tokens), len(latest_tokens))
