import pytest

from app.exceptions.custom_exceptions import ValidationError
from app.services.validation.validator import OutputValidator, extract_numbers


def test_extract_numbers() -> None:
    text = "Headline CPI is at 3.5%, up from 3.2% in 2023. About 50k jobs lost."
    numbers = extract_numbers(text)
    assert "3.5%" in numbers
    assert "3.2%" in numbers
    assert "2023" in numbers
    assert "50" in numbers


def test_validator_success() -> None:
    original = "US Services PMI rises to 54.2"
    ai_output = {
        "translation_ar": "ارتفع مؤشر مديري المشتريات الخدمي الأمريكي إلى 54.2",
        "summary_ar": "تحسن القطاع الخدمي",
        "category": "economic_data",
        "importance": 3,
        "confidence": 0.9,
        "market_bias": "POSITIVE",
        "impact": "Positive for USD",
        "affected_assets": ["USD"],
        "actual": "54.2",
        "forecast": "53.8",
        "previous": "52.5",
        "currency": "USD",
        "company": "None",
        "ticker": "None",
    }
    # Should not raise
    OutputValidator.validate_ai_output(original, ai_output)


def test_validator_fails_missing_number() -> None:
    original = "US Services PMI rises to 54.2"
    ai_output = {
        "translation_ar": "ارتفع مؤشر مديري المشتريات الخدمي الأمريكي",
        "summary_ar": "تحسن القطاع الخدمي",
        "category": "economic_data",
        "importance": 3,
        "confidence": 0.9,
        "market_bias": "POSITIVE",
        "impact": "Positive for USD",
        "affected_assets": ["USD"],
        "actual": "None",
        "forecast": "None",
        "previous": "None",
        "currency": "USD",
        "company": "None",
        "ticker": "None",
    }
    with pytest.raises(ValidationError, match="54.2"):
        OutputValidator.validate_ai_output(original, ai_output)
