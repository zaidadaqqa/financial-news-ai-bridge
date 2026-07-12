"""Deterministic series-identity parsing for Indicator Memory (Phase 4A).

Honesty over coverage: every function here either identifies a component
with effectively deterministic certainty or returns None. Nothing guesses,
nothing approximates, nothing merges similar indicators. A print that cannot
be fully keyed is stored UNKEYED with the reason — recoverable later, never
wrong now.

Series identity is canonical and wording-independent: country and event come
from the frozen News Intelligence engine's canonical vocabularies (e.g. both
"non-farm payrolls" and "NFP" already canonicalize to event "NFP" there);
this module adds only the deterministic variant and unit axes.
"""

import re
from dataclasses import dataclass

from app.services.intelligence.models import NewsIntelligenceResult

# Variant axes. FinancialJuice's real observed vocabulary; a headline
# matching more than one token on the same axis is AMBIGUOUS → unkeyed.
_BASIS_TOKENS = {
    "YOY": re.compile(r"\b(?:YoY|Y/Y)\b", re.I),
    "MOM": re.compile(r"\b(?:MoM|M/M)\b", re.I),
    "QOQ": re.compile(r"\b(?:QoQ|Q/Q)\b", re.I),
}
_STAGE_TOKENS = {
    "FINAL": re.compile(r"\bFinal\b", re.I),
    "PRELIM": re.compile(r"\bPrelim(?:inary)?\b", re.I),
    "FLASH": re.compile(r"\bFlash\b", re.I),
}
_ADJ_TOKENS = {
    "NSA": re.compile(r"\bNSA\b"),
    "SA": re.compile(r"\bSA\b"),
}

AMBIGUOUS = "AMBIGUOUS"


def _one_axis(headline: str, tokens: dict[str, re.Pattern[str]]) -> str:
    """Exactly-one semantics: no token → "NONE"; one token → its name;
    more than one distinct token on the axis → AMBIGUOUS."""
    hits = [name for name, pattern in tokens.items() if pattern.search(headline)]
    if not hits:
        return "NONE"
    if len(hits) == 1:
        return hits[0]
    return AMBIGUOUS


def parse_variant(headline: str) -> str | None:
    """Deterministic variant string BASIS-STAGE-ADJ (e.g. "MOM-FINAL-NONE",
    "YOY-NONE-NSA"). None when any axis is ambiguous — never guessed.
    An absent token is a stable identity too ("NONE"): FinancialJuice is
    consistent per series, so identical absence keys identically over time
    without ever merging two differently-labeled series."""
    parts = []
    for axis in (_BASIS_TOKENS, _STAGE_TOKENS, _ADJ_TOKENS):
        value = _one_axis(headline, axis)
        if value == AMBIGUOUS:
            return None
        parts.append(value)
    return "-".join(parts)


# Unit classes derived from the ACTUAL value's published form. Reuses the
# same suffix vocabulary the frozen Decimal parser accepts.
_PERCENT_RE = re.compile(r"%\s*$")
_SUFFIX_RE = re.compile(r"([KMB])\s*$", re.I)


def parse_unit_class(actual_raw: str) -> str | None:
    text = actual_raw.strip()
    if not text:
        return None
    if _PERCENT_RE.search(text):
        return "PERCENT"
    suffix = _SUFFIX_RE.search(text)
    if suffix:
        return f"COUNT_{suffix.group(1).upper()}"
    # Bare numeric level (e.g. "581", "48.5") — a stable class of its own.
    if re.fullmatch(r"-?\d[\d,]*\.?\d*", text):
        return "BARE"
    return None


@dataclass(frozen=True)
class SeriesIdentity:
    canonical_key: str
    country: str
    economic_event: str
    variant: str
    unit_class: str


def identify_series(
    headline: str, intelligence: NewsIntelligenceResult
) -> tuple[SeriesIdentity | None, str | None]:
    """Full deterministic identification. Returns (identity, None) on
    certainty, else (None, unkeyed_reason). Requires every component —
    partial identity is no identity."""
    if intelligence.actual is None:
        return None, "no_actual"
    if not intelligence.country:
        return None, "missing_country"
    if not intelligence.economic_event:
        return None, "missing_event"
    variant = parse_variant(headline)
    if variant is None:
        return None, "ambiguous_variant"
    unit_class = parse_unit_class(intelligence.actual)
    if unit_class is None:
        return None, "unparseable_unit"
    key = "|".join(
        (intelligence.country, intelligence.economic_event, variant, unit_class)
    )
    return (
        SeriesIdentity(
            canonical_key=key,
            country=intelligence.country,
            economic_event=intelligence.economic_event,
            variant=variant,
            unit_class=unit_class,
        ),
        None,
    )
