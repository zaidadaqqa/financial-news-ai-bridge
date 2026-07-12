"""Story Intelligence value objects and constants (Phase 3).

Design: .claude_memory/STORY_INTELLIGENCE_ARCHITECTURE.md §7-§10.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RelationshipType(StrEnum):
    NEW_STORY = "NEW_STORY"
    UPDATE = "UPDATE"
    CORRECTION = "CORRECTION"
    REPETITION = "REPETITION"


# Time windows in hours, keyed by the STORY's primary category (§10). A story
# outside its window is simply no longer a match candidate; nothing mutates.
# geopolitical is deliberately 48h, not longer — the real-data audit showed a
# 96h window snowballing multi-day developments into one mega-story.
STORY_TIME_WINDOWS_H: dict[str, int] = {
    "economic_data": 12,
    "central_bank": 72,
    "geopolitical": 48,
    "earnings": 120,
    "company": 72,
    "government": 48,
    "commodities": 48,
    "forex": 24,
    "bonds": 24,
    "crypto": 48,
    "general": 24,
}
MAX_WINDOW_H = max(STORY_TIME_WINDOWS_H.values())

# Matching decision bands (§8). Weak signals alone (category +1, geo +1)
# can never reach MATCH_THRESHOLD — by construction, not by convention.
MATCH_THRESHOLD = 4
UNCERTAIN_SCORE = 3

# REPETITION: salient-token overlap with the story's LATEST item at or above
# this share of the smaller token set means "same information reworded".
REPETITION_OVERLAP_RATIO = 0.8

# Candidate query bound: stories active within MAX_WINDOW_H, newest first.
CANDIDATE_LIMIT = 60


@dataclass(frozen=True)
class StoryDecision:
    """Ephemeral, fully recomputable/reloadable outcome of the story step.

    prior_* fields describe the story's last PUBLISHED development BEFORE the
    current item — the only material ever rendered as reader context. They
    are None for a new story, for repetitions of an unpublished prior, or
    when the stored latest development is the current item itself (idempotent
    reprocessing guard, architecture §13).
    """

    story_id: str
    relationship: RelationshipType
    is_new_story: bool
    evidence_score: int
    matching_reasons: tuple[str, ...]
    prior_original_headline: str | None
    prior_headline_ar: str | None
    prior_at: datetime | None
