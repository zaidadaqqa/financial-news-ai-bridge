import re
from typing import Any

from app.exceptions.custom_exceptions import ValidationError


def extract_numbers(text: str) -> list[str]:
    """Extracts all numbers from text, including decimals and percentages."""
    if not text:
        return []
    # Matches integers, decimals, and numbers followed by %
    pattern = r"\d+(?:\.\d+)?%?"
    return re.findall(pattern, text)


class OutputValidator:
    REQUIRED_FIELDS = [
        "translation_ar",
        "summary_ar",
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

    ALLOWED_CATEGORIES = [
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
    ]

    ALLOWED_BIAS = ["POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL", "UNCLEAR"]

    @classmethod
    def validate_ai_output(
        cls, original_headline: str, ai_json: dict[str, Any]
    ) -> None:
        # Check required fields
        for field in cls.REQUIRED_FIELDS:
            if field not in ai_json:
                raise ValidationError(f"Missing required field: {field}")

        # Validate category
        if (
            ai_json.get("category")
            and ai_json["category"] not in cls.ALLOWED_CATEGORIES
        ):
            raise ValidationError(f"Invalid category: {ai_json['category']}")

        # Validate market bias
        if (
            ai_json.get("market_bias")
            and ai_json["market_bias"] not in cls.ALLOWED_BIAS
        ):
            raise ValidationError(f"Invalid market bias: {ai_json['market_bias']}")

        # Validate importance and confidence
        importance = ai_json.get("importance")
        if importance is not None and not (1 <= int(importance) <= 5):
            raise ValidationError(
                f"Importance must be between 1 and 5, got {importance}"
            )

        confidence = ai_json.get("confidence")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValidationError(
                f"Confidence must be between 0.0 and 1.0, got {confidence}"
            )

        # Validate Numbers Preservation
        # All numbers in the original headline must exist in the output somehow
        # (specifically in translation, actual, forecast, or previous)
        original_numbers = extract_numbers(original_headline)

        combined_output_text = " ".join(
            filter(
                None,
                [
                    ai_json.get("translation_ar", ""),
                    str(ai_json.get("actual", "")),
                    str(ai_json.get("forecast", "")),
                    str(ai_json.get("previous", "")),
                ],
            )
        )

        output_numbers = extract_numbers(combined_output_text)

        for num in original_numbers:
            if num not in output_numbers:
                raise ValidationError(f"Number {num} missing from AI output")
