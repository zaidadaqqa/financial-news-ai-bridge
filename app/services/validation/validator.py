import re
from typing import Any

from app.exceptions.custom_exceptions import ValidationError


def extract_numbers(text: str) -> set[str]:
    """Extract all significant numbers from text including ranges and basis points."""
    if not text:
        return set()
    # Match: decimals, integers, percentages, ranges, signed values
    pattern = r"-?\d+(?:\.\d+)?%?"
    matches = re.findall(pattern, text)
    # Also extract both ends of ranges like 5.25%-5.50%
    result = set()
    for m in matches:
        result.add(m)
        # Strip % to get bare number for cross-checking
        result.add(m.rstrip("%"))
    return {m for m in result if m not in ("", "-")}


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

        # Number preservation: check numbers in original appear in Arabic output
        original_numbers = extract_numbers(original_headline)

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
