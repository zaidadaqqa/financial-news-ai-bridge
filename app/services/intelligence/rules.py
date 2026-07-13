"""Keyword tables, hybrid classifier, and Decimal-based numeric comparison.

See .claude_memory/NEWS_INTELLIGENCE_ARCHITECTURE.md for the full design
rationale, including the real FinancialJuice headline sample (§16) that
grounded these keyword choices and the Decimal parsing edge cases below.
This module has no I/O and no side effects.

To extend: add a new keyword/phrase to the relevant table below. Central-bank
and country/currency patterns are case-sensitive (matched against real
observed capitalization, e.g. "Fed", "BoJ") to avoid colliding with common
English words ("fed" as a verb); everything else is case-insensitive.
"""

import re
from decimal import Decimal, InvalidOperation

from app.constants.enums import NewsCategory
from app.services.intelligence.models import NumericSurprise

# ---------------------------------------------------------------------------
# Central banks — case-sensitive, matched against real observed capitalization
# (§16 of the architecture doc: "Fed", "ECB", "RBNZ", "SNB", "BoJ" all appear
# in real production headlines exactly this way).
# ---------------------------------------------------------------------------

CENTRAL_BANK_PATTERNS: list[tuple[str, str, str, re.Pattern[str]]] = [
    # (code, country, currency, pattern)
    ("FED", "United States", "USD", re.compile(r"\b(Fed|FOMC|Federal Reserve)\b")),
    ("ECB", "Eurozone", "EUR", re.compile(r"\b(ECB|European Central Bank)\b")),
    ("BOE", "United Kingdom", "GBP", re.compile(r"\b(BoE|BOE|Bank of England)\b")),
    ("BOJ", "Japan", "JPY", re.compile(r"\b(BoJ|BOJ|Bank of Japan)\b")),
    ("BOC", "Canada", "CAD", re.compile(r"\b(BoC|BOC|Bank of Canada)\b")),
    ("RBA", "Australia", "AUD", re.compile(r"\bRBA\b")),
    ("RBNZ", "New Zealand", "NZD", re.compile(r"\bRBNZ\b")),
    ("SNB", "Switzerland", "CHF", re.compile(r"\bSNB\b")),
    ("PBOC", "China", "CNY", re.compile(r"\b(PBOC|PBoC|People's Bank of China)\b")),
]

# ---------------------------------------------------------------------------
# Economic indicators — recognized names, used for both Tier-A economic-data
# detection and the `economic_event` field. Case-insensitive, word-boundary.
# ---------------------------------------------------------------------------

ECONOMIC_INDICATORS: dict[str, str] = {
    r"\bHICP\b": "HICP",
    r"\bCPI\b": "CPI",
    r"\bPPI\b": "PPI",
    r"\bGDP\b": "GDP",
    r"\bPMI\b": "PMI",
    r"\bNFP\b": "NFP",
    r"\bnon-?farm payrolls\b": "NFP",
    r"\bunemployment rate\b": "Unemployment Rate",
    r"\bemployment change\b": "Employment Change",
    r"\bretail sales\b": "Retail Sales",
    r"\bindustrial production\b": "Industrial Production",
    r"\btrade balance\b": "Trade Balance",
    r"\bhousing starts\b": "Housing Starts",
    r"\bbuilding permits\b": "Building Permits",
    r"\bconsumer confidence\b": "Consumer Confidence",
    r"\bdurable goods\b": "Durable Goods",
    r"\bISM\b": "ISM",
    r"\bparticipation rate\b": "Participation Rate",
    r"\baverage hourly earnings\b": "Average Hourly Earnings",
    r"\bjobless claims\b": "Jobless Claims",
    r"\brig count\b": "Rig Count",
}
_ECONOMIC_INDICATOR_PATTERNS = [
    (re.compile(p, re.I), name) for p, name in ECONOMIC_INDICATORS.items()
]

# ---------------------------------------------------------------------------
# Earnings — Tier-A hard override.
#
# Validation status (honest, per NEWS_INTELLIGENCE_ARCHITECTURE.md §16/§19):
# the real production sample (251 records, 2026-07-10 09:20 UTC through
# 2026-07-11 09:27 UTC — a single ~24h window, not multiple weeks; this is
# the entire history available on a service deployed 2026-07-10) contains
# ZERO genuine corporate-earnings headlines. The rule below is therefore
# still NOT validated against a real positive example — that remains an
# honest, documented gap, not a claim of proof.
#
# What the real sample DID surface: two false-positive risks against the
# *original* looser regex (`\b(earnings|...|guidance|...)\b`), found by
# grepping the full 251-record sample read-only:
#   1. "BoJ set to keep interest rates unchanged in July but maintain policy
#      guidance..." — bare "guidance" is standard central-bank vocabulary
#      ("forward guidance"), not a corporate-earnings signal.
#   2. "Canadian Average Hourly Earnings YoY Actual 3.70% (...)" — bare
#      "earnings" is also the name of a recognized economic indicator
#      (ECONOMIC_INDICATORS above), not corporate earnings.
# Both were already shielded from actually misclassifying in practice
# because Tier A checks central-bank and economic-data before earnings (see
# engine.py) — but relying on check *order* alone for correctness is
# fragile. The regex itself is now tightened to require earnings-specific
# multi-word phrasing or an unambiguous acronym, so it is safe even if
# evaluated on its own, not just protected by ordering.
# ---------------------------------------------------------------------------

_EARNINGS_RE = re.compile(
    r"\b(quarterly earnings|quarterly results|earnings report|earnings call|"
    r"earnings beat|earnings miss|earnings warning|profit warning|EPS|"
    r"(?:raises|cuts|issues|provides|raised|lowered|withdraws|withdrew)\s+"
    r"(?:full[- ]year\s+)?guidance|"
    r"beats estimates|misses estimates|beat estimates|miss estimates|"
    r"revenue (?:beat|miss))\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Tier-B evidence scoring — weighted keyword classes, not raw counting.
#
# Why weighted, not raw count (§17 of the architecture doc originally rejected
# a "learned/weighted scoring model" — this is not that; there is nothing
# learned here, every weight is a small fixed constant assigned by hand, same
# as the plain-count version, just not all equal to 1):
#
# Raw equal-weight counting lets several weak, cross-category-generic words
# outscore a single strong, category-defining word. Concrete real risk found
# during review: "Bitcoin rallies as dollar weakens and investors eye currency
# diversification" — raw counting gives FOREX 2 ("dollar", "currency") vs.
# CRYPTO 1 ("bitcoin"), so FOREX would incorrectly win a headline that is
# fundamentally about Bitcoin. Weighted scoring fixes this because "bitcoin"
# is category-defining (STRONG) while "dollar"/"currency" are the kind of
# generic terms that show up across FOREX, COMMODITIES, and COMPANY headlines
# alike (WEAK) — see test_weighted_scoring_beats_raw_count_bitcoin_dollar.
#
# Weight classes (deliberately just three, plus one exclusion mechanism —
# not a per-keyword tuned table, which would be exactly the "large opaque
# weight table" this design is required to avoid):
#   STRONG (3) — the term is essentially category-defining in a financial-news
#                headline; realistic false-positive rate in this domain is
#                near zero (e.g. "opec", "bitcoin", "treasury", "forex").
#   MEDIUM (2) — strongly correlated with the category but occasionally shows
#                up in adjacent contexts (e.g. "crude" can appear in a
#                geopolitical oil-supply story; "minister" can appear in a
#                central-bank-adjacent quote).
#   WEAK   (1) — generic terms that routinely co-occur across multiple
#                categories and should only ever tip a genuine tie, never
#                outweigh a single stronger signal on their own (e.g. "oil",
#                "dollar", "yield" — each is common far outside its "home"
#                category).
# Grounded in §16/§21's real category samples plus the expanded 251-record
# read-only review (see NEWS_INTELLIGENCE_ARCHITECTURE.md §16/§22).
# ---------------------------------------------------------------------------

STRONG = 3
MEDIUM = 2
WEAK = 1

GEOPOLITICAL_KEYWORDS: dict[str, int] = {
    "war": STRONG,
    "invasion": STRONG,
    "missile": STRONG,
    "missiles": STRONG,
    "nuclear": STRONG,
    "ceasefire": STRONG,
    "sanctions": STRONG,
    "sanction": STRONG,
    "hostage": STRONG,
    "hostages": STRONG,
    "armistice": STRONG,
    "denuclearization": STRONG,
    "nuclear power plant": STRONG,
    "strait of hormuz": STRONG,
    "strike": MEDIUM,
    "strikes": MEDIUM,
    "attack": MEDIUM,
    "attacks": MEDIUM,
    "troops": MEDIUM,
    "conflict": MEDIUM,
    "embassy": MEDIUM,
    "ambassador": MEDIUM,
    "military": MEDIUM,
    "tension": WEAK,
    "tensions": WEAK,
    "diplomatic": WEAK,
    "summit": WEAK,
    "treaty": WEAK,
    "mediator": WEAK,
    "mediators": WEAK,
    "de-escalate": WEAK,
    "de-escalation": WEAK,
    "sovereignty": WEAK,
    "peace talks": WEAK,
}

COMMODITIES_KEYWORDS: dict[str, int] = {
    "opec": STRONG,
    "wti": STRONG,
    "brent": STRONG,
    "xau": STRONG,
    "xag": STRONG,
    "crude": MEDIUM,
    "barrel": MEDIUM,
    "barrels": MEDIUM,
    "natural gas": MEDIUM,
    "oil": WEAK,
    "gold": WEAK,
    "silver": WEAK,
    "gasoline": WEAK,
    "diesel": WEAK,
    "commodities": WEAK,
    "commodity": WEAK,
}

CRYPTO_KEYWORDS: dict[str, int] = {
    "bitcoin": STRONG,
    "ethereum": STRONG,
    "cryptocurrency": STRONG,
    "blockchain": STRONG,
    "stablecoin": STRONG,
    "crypto": MEDIUM,
    "btc": WEAK,
    "eth": WEAK,
}

BONDS_KEYWORDS: dict[str, int] = {
    "treasury": STRONG,
    "treasuries": STRONG,
    "jgb": STRONG,
    "gilt": STRONG,
    "gilts": STRONG,
    "bond auction": STRONG,
    "bond": MEDIUM,
    "bonds": MEDIUM,
    "coupon": MEDIUM,
    "yield": WEAK,
    "yields": WEAK,
}

# COMPANY's plain keyword table intentionally holds only corporate-action /
# reporting vocabulary. Corporate-suffix words (Inc/Corp/Ltd/PLC/...) and
# ticker/cashtag evidence are NOT here — they need capitalization-aware,
# structural matching (see detect_company_structural_evidence below), which
# a case-insensitive bare-keyword table cannot safely express (a
# case-insensitive `\binc\b` would match the ordinary English word "inc." in
# running prose; requiring it to follow a capitalized proper-noun-like token
# is what makes it safe).
COMPANY_KEYWORDS: dict[str, int] = {
    "acquisition": MEDIUM,
    "merger": MEDIUM,
    "acquire": MEDIUM,
    "acquires": MEDIUM,
    "ipo": MEDIUM,
    "buyback": MEDIUM,
    "shareholder": MEDIUM,
    "shareholders": MEDIUM,
    "ceo": MEDIUM,
    "adr": MEDIUM,
    "shares": WEAK,
    "revenue": WEAK,
    "profit": WEAK,
    "loss": WEAK,
    "filing": WEAK,
    "production": WEAK,
    "factory": WEAK,
    "product": WEAK,
    "board": WEAK,
}

GOVERNMENT_KEYWORDS: dict[str, int] = {
    "finance minister": STRONG,
    "economy minister": STRONG,
    "finmin": STRONG,
    "minister": MEDIUM,
    "ministry": MEDIUM,
    "parliament": MEDIUM,
    "congress": MEDIUM,
    "senate": MEDIUM,
    "cabinet": MEDIUM,
    "tariff": MEDIUM,
    "tariffs": MEDIUM,
    "fiscal": WEAK,
    "budget": WEAK,
}

FOREX_KEYWORDS: dict[str, int] = {
    "forex": STRONG,
    "fx": STRONG,
    "currencies": WEAK,
    "currency": WEAK,
    "dollar": WEAK,
    "euro": WEAK,
    "yen": WEAK,
    "pound": WEAK,
}

_CURRENCY_PAIR_RE = re.compile(
    r"\b(USD|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY)/?(USD|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY)\b"
)
_BARE_CURRENCY_RE = re.compile(r"\b(USD|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY)\b")

# Country adjectives seen in real economic-data headlines (§16: "German CPI...",
# "Canadian Unemployment Rate...", "Japanese PPI...", "French HICP...").
COUNTRY_ADJECTIVES: dict[str, tuple[str, str]] = {
    r"\bGerman\b": ("Germany", "EUR"),
    r"\bFrench\b": ("France", "EUR"),
    r"\bItalian\b": ("Italy", "EUR"),
    r"\bSpanish\b": ("Spain", "EUR"),
    r"\bDutch\b": ("Netherlands", "EUR"),
    r"\bCanadian\b": ("Canada", "CAD"),
    r"\bJapanese\b": ("Japan", "JPY"),
    r"\b(?:US|U\.S\.|American)\b": ("United States", "USD"),
    r"\b(?:UK|U\.K\.|British)\b": ("United Kingdom", "GBP"),
    r"\bChinese\b": ("China", "CNY"),
    r"\bAustralian\b": ("Australia", "AUD"),
    r"\bSwiss\b": ("Switzerland", "CHF"),
    r"\bNew Zealand\b": ("New Zealand", "NZD"),
    # Added 2026-07-13 after real accumulation: recurring Norwegian CPI
    # prints were the only honestly-unkeyed missing_country family in
    # production Indicator Memory. Targeted addition, not broad expansion.
    r"\bNorwegian\b": ("Norway", "NOK"),
}
_COUNTRY_PATTERNS = [(re.compile(p), val) for p, val in COUNTRY_ADJECTIVES.items()]

# ---------------------------------------------------------------------------
# "Routine data-series" titles — §16.9: recurring scheduled headlines that are
# genuinely GENERAL but should not be treated as low-signal/urgent.
# ---------------------------------------------------------------------------

_ROUTINE_SERIES_RE = re.compile(
    r"\b(Correlation Matrix|Implied Volatility|Fear (?:&|and) Greed Index|"
    r"CFTC Positions|FX Options Expiries|MOC Imbalance|Interest Rate Probabilities)\b",
    re.I,
)

_BREAKING_SHOCK_RE = re.compile(
    r"\b(emergency|surprise|unscheduled|ad hoc|strike|strikes|attack|attacks|invasion|"
    r"military action|nuclear)\b",
    re.I,
)

# Tier-C tie-breaker precedence (highest to lowest); only used on genuine ties.
# GOVERNMENT ranks above the instrument-specific categories (BONDS/COMPANY) on the
# same logic Tier A already applies to central bankers: who is speaking outranks
# what financial instrument they happen to be discussing (real example: a finance
# minister's remarks about JGB offerings is a GOVERNMENT story, not a BONDS story).
_TIER_C_PRECEDENCE = [
    NewsCategory.GEOPOLITICAL,
    NewsCategory.GOVERNMENT,
    NewsCategory.COMMODITIES,
    NewsCategory.BONDS,
    NewsCategory.CRYPTO,
    NewsCategory.COMPANY,
    NewsCategory.FOREX,
]

_TIER_B_KEYWORDS: dict[NewsCategory, dict[str, int]] = {
    NewsCategory.GEOPOLITICAL: GEOPOLITICAL_KEYWORDS,
    NewsCategory.COMMODITIES: COMMODITIES_KEYWORDS,
    NewsCategory.BONDS: BONDS_KEYWORDS,
    NewsCategory.CRYPTO: CRYPTO_KEYWORDS,
    NewsCategory.COMPANY: COMPANY_KEYWORDS,
    NewsCategory.GOVERNMENT: GOVERNMENT_KEYWORDS,
    NewsCategory.FOREX: FOREX_KEYWORDS,
}

# Word-boundary-safe compiled patterns — plain substring containment let short
# keywords false-trigger inside unrelated words (real bug found in §16 review:
# "yen" inside "von der Leyen" incorrectly matched FOREX). Multi-word phrases
# are escaped as literal phrases; single words get \b on both sides. Each
# pattern carries its weight class (STRONG/MEDIUM/WEAK) alongside it.
_TIER_B_PATTERNS: dict[NewsCategory, list[tuple[re.Pattern[str], int]]] = {
    category: [
        (re.compile(rf"\b{re.escape(kw)}\b"), weight) for kw, weight in kws.items()
    ]
    for category, kws in _TIER_B_KEYWORDS.items()
}

# ---------------------------------------------------------------------------
# COMPANY structural evidence — reusable patterns, not a company-name list.
#
# §21's known limitation ("COMPANY category detection is weak against real
# data") was validated against the real production sample (25 real
# AI-tagged `company` headlines, read-only review — see architecture doc
# §22): most are bare "{Name} {verb}..." headlines with no reusable
# structural signal and remain an accepted, documented miss (no company-name
# dictionary — that would be exactly the brittle complexity this design
# avoids). But the same sample also showed two *reusable, structural* signals
# that the original keyword-only design ignored entirely:
#   - cashtags: "$AMZN", "$LMT", "$AAPL", "$SKHYV" appear repeatedly, always
#     on genuine company stories.
#   - corporate suffixes attached to a proper-noun-like name: "Minimax
#     Group announces...".
# Both are matched structurally (regex over shape/capitalization), not by
# recognizing any specific company name — the same mechanism catches a
# company never seen before.
# ---------------------------------------------------------------------------

_CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF", "CNY"}

_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_EXCHANGE_TICKER_RE = re.compile(
    r"\((?:NASDAQ|NYSE|LSE|TSX|ASX|HKEX)\s*:\s*[A-Z]{1,6}\)"
)

# Suffix immediately follows one-to-four capitalized "name-shaped" tokens.
# "Group"/"Holdings" are included but are the most collision-prone (G7/G20/
# "working group" are not companies) so real multi-word non-company phrases
# using "Group" are excluded explicitly below rather than dropping the
# suffix entirely — it's a real, common company-name suffix in the sample
# ("Minimax Group").
_CORPORATE_SUFFIX_RE = re.compile(
    r"\b[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3}\s+"
    r"(?:Inc\.?|Corp\.?|Corporation|Ltd\.?|PLC|Co\.|LLC|AG|SA|NV|Holdings|Group)\b"
)
_NON_COMPANY_GROUP_RE = re.compile(
    r"\b(?:Working|Contact|Study|G7|G20|G8)\s+Group\b", re.I
)


def detect_company_structural_evidence(headline: str) -> str | None:
    """Case-sensitive structural COMPANY evidence — never a name dictionary.
    Returns a short reason tag, or None. Currency codes are explicitly
    excluded from the cashtag pattern so a bare "$USD"-shaped token is never
    read as a ticker; multi-word non-company "___ Group" phrases (G7, working
    groups) are excluded from the suffix pattern for the same reason."""
    cashtag = _CASHTAG_RE.search(headline)
    if cashtag and cashtag.group(1) not in _CURRENCY_CODES:
        return "cashtag"
    if _EXCHANGE_TICKER_RE.search(headline):
        return "exchange_ticker"
    suffix_match = _CORPORATE_SUFFIX_RE.search(headline)
    if suffix_match and not _NON_COMPANY_GROUP_RE.search(suffix_match.group(0)):
        return "corporate_suffix"
    return None


# ---------------------------------------------------------------------------
# Negative / exclusion signal — a documented, narrow case, not a general
# mechanism. "pound" is genuinely ambiguous between GBP (FOREX) and a unit of
# weight, which commodities headlines use routinely ("$4 per pound" for
# metals/agriculture). When COMMODITIES evidence is already present and the
# only FOREX evidence is a bare "pound" with no supporting GBP/sterling
# wording, the "pound" match is treated as noise, not currency evidence.
# ---------------------------------------------------------------------------

_POUND_RE = re.compile(r"\bpound\b", re.I)
_STERLING_RE = re.compile(r"\bsterling\b|\bgbp\b", re.I)


def _apply_exclusions(
    scores: dict[NewsCategory, int], headline: str
) -> dict[NewsCategory, int]:
    if NewsCategory.COMMODITIES in scores and NewsCategory.FOREX in scores:
        if _POUND_RE.search(headline) and not _STERLING_RE.search(headline):
            scores[NewsCategory.FOREX] -= WEAK
            if scores[NewsCategory.FOREX] <= 0:
                del scores[NewsCategory.FOREX]
    return scores


def score_tier_b(headline: str) -> dict[NewsCategory, int]:
    """Weighted evidence scoring (§A of the Phase 2.2 hardening review — see
    module docstring above the keyword tables for the full rationale)."""
    headline_lower = headline.lower()
    scores: dict[NewsCategory, int] = {}
    for category, weighted_patterns in _TIER_B_PATTERNS.items():
        score = sum(
            weight
            for pattern, weight in weighted_patterns
            if pattern.search(headline_lower)
        )
        if score:
            scores[category] = score

    structural = detect_company_structural_evidence(headline)
    if structural:
        scores[NewsCategory.COMPANY] = scores.get(NewsCategory.COMPANY, 0) + STRONG

    # Structural FOREX evidence — an explicit currency pair ("EUR/USD",
    # "EURUSD") is category-defining on its own, same logic as the COMPANY
    # cashtag bonus above. Found during audit: detect_currency_pair() was
    # fully implemented but never called anywhere, so pair-only headlines
    # (no "forex"/"fx"/currency-name word) previously scored zero FOREX
    # evidence and fell through to GENERAL/fallback.
    if detect_currency_pair(headline):
        scores[NewsCategory.FOREX] = scores.get(NewsCategory.FOREX, 0) + STRONG

    return _apply_exclusions(scores, headline_lower)


def pick_tier_b_winner(scores: dict[NewsCategory, int]) -> NewsCategory | None:
    if not scores:
        return None
    top_score = max(scores.values())
    tied = [cat for cat, s in scores.items() if s == top_score]
    if len(tied) == 1:
        return tied[0]
    for cat in _TIER_C_PRECEDENCE:
        if cat in tied:
            return cat
    return tied[0]  # unreachable — every Tier-B category is in the precedence list


def detect_central_bank(headline: str) -> tuple[str, str, str] | None:
    """Returns (code, country, currency) for the first central bank matched, or None."""
    for code, country, currency, pattern in CENTRAL_BANK_PATTERNS:
        if pattern.search(headline):
            return code, country, currency
    return None


_GENERIC_CENTRAL_BANK_RE = re.compile(r"\bcentral bank\b", re.I)


def has_generic_central_bank_phrase(headline: str) -> bool:
    """Catches headlines that say 'central bank' generically without naming a
    specific institution (§16 real gap: 'China central bank injects...' never
    names PBOC). Country, if detectable, still comes from detect_country()."""
    return bool(_GENERIC_CENTRAL_BANK_RE.search(headline))


def detect_economic_indicator(headline: str) -> str | None:
    for pattern, name in _ECONOMIC_INDICATOR_PATTERNS:
        if pattern.search(headline):
            return name
    return None


def is_earnings(headline: str) -> bool:
    return bool(_EARNINGS_RE.search(headline))


def is_routine_series(headline: str) -> bool:
    return bool(_ROUTINE_SERIES_RE.search(headline))


def has_breaking_shock_language(headline: str) -> bool:
    return bool(_BREAKING_SHOCK_RE.search(headline))


def detect_currency_pair(headline: str) -> bool:
    return bool(_CURRENCY_PAIR_RE.search(headline))


def detect_bare_currency(headline: str) -> str | None:
    match = _BARE_CURRENCY_RE.search(headline)
    return match.group(1) if match else None


def detect_country(headline: str) -> tuple[str, str] | None:
    """Returns (country, likely currency) from a country adjective, or None."""
    for pattern, value in _COUNTRY_PATTERNS:
        if pattern.search(headline):
            return value
    return None


# ---------------------------------------------------------------------------
# Actual / Forecast / Previous extraction — per-value token pattern (§10/§16.2:
# a loose blanket character class silently drops K/M/B suffixes; this doesn't).
# ---------------------------------------------------------------------------

_NUMBER_TOKEN = r"(?:-?\d[\d,]*\.?\d*\s*[%KMBkmb]?|-)"
_AFP_RE = re.compile(
    rf"Actual\s+({_NUMBER_TOKEN})\s*\(\s*Forecast\s+({_NUMBER_TOKEN})\s*,\s*Previous\s+({_NUMBER_TOKEN})\s*\)",
    re.IGNORECASE,
)


def extract_actual_forecast_previous(
    headline: str,
) -> tuple[str | None, str | None, str | None]:
    """Best-effort extraction of the FinancialJuice 'Actual X (Forecast Y, Previous
    Z)' shape. Returns (None, None, None) for any headline that doesn't match this
    exact shape — a safe miss, never a wrong guess. Cross-check hint only; see
    architecture §7/§10.
    """
    match = _AFP_RE.search(headline)
    if not match:
        return None, None, None
    actual, forecast, previous = (g.strip() for g in match.groups())
    return actual, forecast, previous


# ---------------------------------------------------------------------------
# Decimal-based numeric parsing and comparison (§10, §16.2-16.4)
# ---------------------------------------------------------------------------

_MULTIPLIERS = {
    "K": Decimal(1_000),
    "M": Decimal(1_000_000),
    "B": Decimal(1_000_000_000),
}
_RANGE_RE = re.compile(
    r"[%\d]\s*-\s*\d"
)  # digit/percent immediately before a dash => a range


def parse_economic_value(raw: str | None) -> Decimal | None:
    """Safely parse a single economic figure to Decimal. Never guesses — returns
    None for anything ambiguous (ranges, placeholders, unparseable text)."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text == "-":
        return None
    if _RANGE_RE.search(text):
        return None

    cleaned = text
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    cleaned = cleaned.replace(",", "").strip()
    for symbol in ("$", "€", "£", "¥"):
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.strip()

    multiplier = Decimal(1)
    if cleaned and cleaned[-1].upper() in _MULTIPLIERS:
        multiplier = _MULTIPLIERS[cleaned[-1].upper()]
        cleaned = cleaned[:-1].strip()

    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None


def _had_percent(raw: str) -> bool:
    return raw.strip().endswith("%")


def compare_economic_values(
    actual_raw: str | None, other_raw: str | None
) -> NumericSurprise:
    """Numeric-only comparison — no value judgment about whether HIGHER is good or bad.
    Unit mismatch (one side had '%' and the other didn't) safely returns UNKNOWN."""
    if actual_raw is None or other_raw is None:
        return NumericSurprise.UNKNOWN
    if _had_percent(actual_raw) != _had_percent(other_raw):
        return NumericSurprise.UNKNOWN

    actual = parse_economic_value(actual_raw)
    other = parse_economic_value(other_raw)
    if actual is None or other is None:
        return NumericSurprise.UNKNOWN
    if actual == other:
        return NumericSurprise.MATCH
    return NumericSurprise.HIGHER if actual > other else NumericSurprise.LOWER
