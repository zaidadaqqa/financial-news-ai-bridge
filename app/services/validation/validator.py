import re
from typing import Any

from app.exceptions.custom_exceptions import ValidationError

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
_ORDINAL_SUFFIX_RE = re.compile(r"(?:st|nd|rd|th)\b", re.I)
# Month-adjacent day ranges ("July 1-10", "July 6th-12th") are calendar
# context, not market data. Production evidence (2026-07-12/13): four real
# messages died on these — and because the validator only fails when BOTH
# the signed and bare forms are missing, those failures prove good Arabic
# rephrases the range entirely (no bare "10" either). Full exemption is
# therefore correct; requiring the endpoints would have lost the same
# messages. The month guard keeps this narrow: numeric ranges without a
# month ("5.25%-5.50%") are untouched.
_MONTH_DAY_RANGE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*[-–]\s*"
    r"\d{1,2}(?:st|nd|rd|th)?\b",
    re.I,
)


def extract_numbers(text: str) -> set[str]:
    """Extract all significant numbers from text including ranges and basis points."""
    if not text:
        return set()
    matches = _NUMBER_RE.findall(text)
    # Also extract both ends of ranges like 5.25%-5.50%
    result = set()
    for m in matches:
        result.add(m)
        # Strip % to get bare number for cross-checking
        result.add(m.rstrip("%"))
    return {m for m in result if m not in ("", "-")}


def extract_required_numbers(text: str) -> set[str]:
    """Numbers the Arabic output MUST preserve. Same extraction as
    extract_numbers minus three narrow exemptions, each anchored to a real
    production loss (six messages in 36h, 2026-07-12/13 — see CHANGELOG):

    - digits glued to a preceding letter are identifiers, not quantities
      ("G10" demanded '10'),
    - ordinal-suffixed digits are grammar, not market data ("21st sanctions
      package" demanded '21'; Arabic spells ordinals out),
    - month-adjacent day ranges are dates ("July 1-10" demanded '-10').

    Exemption means non-enforcement only — nothing is ever stripped from the
    AI output side. Genuine market figures (signed values, decimals,
    percentages, numeric ranges like 5.25%-5.50%) remain fully protected:
    none of the exemptions can match them."""
    if not text:
        return set()
    range_spans = [m.span() for m in _MONTH_DAY_RANGE_RE.finditer(text)]
    result = set()
    for m in _NUMBER_RE.finditer(text):
        start, end = m.span()
        if start > 0 and text[start - 1].isalpha():
            continue  # identifier like G10 / WAF6
        if _ORDINAL_SUFFIX_RE.match(text, end):
            continue  # ordinal like 21st
        if any(rs <= start and end <= r_end for rs, r_end in range_spans):
            continue  # day range like July 1-10
        result.add(m.group())
        result.add(m.group().rstrip("%"))
    return {m for m in result if m not in ("", "-")}


_ARABIC_LETTER_RE = re.compile(r"[؀-ۿ]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
# Tickers, currencies, and acronyms (USD, CPI, BTC, G10, NFP) legitimately
# stay Latin inside professional Arabic text — excluded from the ratio.
_ALLCAPS_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")
MIN_ARABIC_LETTER_RATIO = 0.40


def _arabic_letter_ratio(text: str) -> float:
    """Share of alphabetic characters that are Arabic, ignoring digits,
    punctuation, and all-caps ticker/acronym tokens. Production audit
    (2026-07-13, 382 published rows): genuine translations score >= 0.90,
    English-left-as-'translation' rows score ~0.0, and NOTHING falls between
    0.40 and 0.60 — the 0.40 threshold sits at the permissive edge of that
    empty valley, minimizing false positives on ticker-heavy Arabic."""
    stripped = _ALLCAPS_TOKEN_RE.sub(" ", text)
    arabic = len(_ARABIC_LETTER_RE.findall(stripped))
    latin = len(_LATIN_LETTER_RE.findall(stripped))
    if arabic + latin == 0:
        return 1.0  # nothing alphabetic to judge (pure numbers/punctuation)
    return arabic / (arabic + latin)


def _is_placeholder(value: Any) -> bool:
    """Return True if the value is a null-like placeholder."""
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in ("none", "null", "n/a", "", "0", "na")


class OutputValidator:
    REQUIRED_FIELDS = [
        "headline_ar",
        "explanation_ar",
        "market_impact_ar",
        "translation_ar",
        "summary_ar",
        "what_to_watch_ar",
        "category",
        "importance",
        "confidence",
        "market_bias",
        "impact",
        "affected_assets",
        "actual",
        "forecast",
        "previous",
        "currency",
        "company",
        "ticker",
    ]

    ALLOWED_CATEGORIES = {
        "economic_data",
        "central_bank",
        "company",
        "earnings",
        "commodities",
        "forex",
        "bonds",
        "crypto",
        "geopolitical",
        "government",
        "breaking",
        "general",
    }

    ALLOWED_BIAS = {"POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL", "UNCLEAR"}

    # These carry the actual message content — an empty string would pass a bare
    # key-existence check but publish a visibly broken message. Fields not listed
    # here (what_to_watch_ar, actual, forecast, ...) are legitimately nullable.
    NON_EMPTY_TEXT_FIELDS = [
        "headline_ar",
        "explanation_ar",
        "market_impact_ar",
        "translation_ar",
        "summary_ar",
    ]

    @classmethod
    def validate_ai_output(
        cls, original_headline: str, ai_json: dict[str, Any]
    ) -> None:
        for field in cls.REQUIRED_FIELDS:
            if field not in ai_json:
                raise ValidationError(f"Missing required field: {field}")

        for field in cls.NON_EMPTY_TEXT_FIELDS:
            if not str(ai_json.get(field) or "").strip():
                raise ValidationError(f"Field '{field}' must not be empty")

        if (
            ai_json.get("category")
            and ai_json["category"] not in cls.ALLOWED_CATEGORIES
        ):
            raise ValidationError(f"Invalid category: {ai_json['category']}")

        if (
            ai_json.get("market_bias")
            and ai_json["market_bias"] not in cls.ALLOWED_BIAS
        ):
            raise ValidationError(f"Invalid market_bias: {ai_json['market_bias']}")

        importance = ai_json.get("importance")
        if importance is not None and not (1 <= int(importance) <= 5):
            raise ValidationError(f"importance must be 1–5, got {importance}")

        confidence = ai_json.get("confidence")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValidationError(f"confidence must be 0.0–1.0, got {confidence}")

        # The reader-facing Arabic fields must actually be Arabic. Real
        # defect: quote/chart items were published with English left as the
        # "translation" (23 of 382 published rows scored ~0.0).
        for field in ("headline_ar", "translation_ar"):
            value = str(ai_json.get(field) or "")
            ratio = _arabic_letter_ratio(value)
            if ratio < MIN_ARABIC_LETTER_RATIO:
                raise ValidationError(
                    f"Field '{field}' is not Arabic "
                    f"(arabic-letter ratio {ratio:.2f} < "
                    f"{MIN_ARABIC_LETTER_RATIO})"
                )

        # Number preservation: check numbers in original appear in Arabic output
        original_numbers = extract_required_numbers(original_headline)

        combined_arabic = " ".join(
            filter(
                None,
                [
                    ai_json.get("headline_ar", ""),
                    ai_json.get("translation_ar", ""),
                    ai_json.get("explanation_ar", ""),
                    ai_json.get("what_to_watch_ar", "") or "",
                    str(ai_json.get("actual", "") or ""),
                    str(ai_json.get("forecast", "") or ""),
                    str(ai_json.get("previous", "") or ""),
                ],
            )
        )
        output_numbers = extract_numbers(combined_arabic)

        for num in original_numbers:
            bare = num.rstrip("%")
            # Skip very short numbers (years, single digits in context) and pure zeros
            if len(bare) <= 1 or bare == "0":
                continue
            if num not in output_numbers and bare not in output_numbers:
                raise ValidationError(
                    f"Number '{num}' from original not found in Arabic output"
                )
