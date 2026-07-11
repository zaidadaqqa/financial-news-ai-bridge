import pytest

from app.exceptions.custom_exceptions import ValidationError
from app.services.validation.validator import OutputValidator, extract_numbers

BASE_AI_OUTPUT = {
    "headline_ar": "ارتفع مؤشر مديري المشتريات الخدمي الأمريكي إلى 54.2",
    "explanation_ar": "أظهرت البيانات ارتفاعاً في مؤشر مديري المشتريات الخدمي الأمريكي.",
    "market_impact_ar": "قد يدعم الارتفاع الدولار الأمريكي على المدى القصير.",
    "translation_ar": "ارتفع مؤشر مديري المشتريات الخدمي الأمريكي إلى 54.2",
    "summary_ar": "تحسن القطاع الخدمي",
    "what_to_watch_ar": "ترقب صدور مؤشر مديري المشتريات الصناعي الأسبوع المقبل",
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
    "company": None,
    "ticker": None,
}


def test_extract_numbers() -> None:
    text = "Headline CPI is at 3.5%, up from 3.2% in 2023. About 50k jobs lost."
    numbers = extract_numbers(text)
    assert "3.5%" in numbers
    assert "3.2%" in numbers
    assert "2023" in numbers
    assert "50" in numbers


def test_validator_success() -> None:
    original = "US Services PMI rises to 54.2"
    OutputValidator.validate_ai_output(original, BASE_AI_OUTPUT)


def test_validator_fails_missing_number() -> None:
    original = "US Services PMI rises to 54.2"
    bad_output = {
        **BASE_AI_OUTPUT,
        "headline_ar": "ارتفع المؤشر الخدمي",
        "translation_ar": "ارتفع مؤشر مديري المشتريات الخدمي",
        "actual": None,
        "forecast": None,
        "previous": None,
    }
    with pytest.raises(ValidationError, match="54.2"):
        OutputValidator.validate_ai_output(original, bad_output)


def test_validator_fails_missing_required_field() -> None:
    bad_output = {k: v for k, v in BASE_AI_OUTPUT.items() if k != "headline_ar"}
    with pytest.raises(ValidationError, match="headline_ar"):
        OutputValidator.validate_ai_output("US PMI at 54.2", bad_output)


def test_validator_fails_invalid_category() -> None:
    bad_output = {**BASE_AI_OUTPUT, "category": "invalid_category"}
    with pytest.raises(ValidationError, match="Invalid category"):
        OutputValidator.validate_ai_output("US PMI at 54.2", bad_output)


def test_validator_fails_invalid_bias() -> None:
    bad_output = {**BASE_AI_OUTPUT, "market_bias": "BULLISH"}
    with pytest.raises(ValidationError, match="Invalid market_bias"):
        OutputValidator.validate_ai_output("US PMI at 54.2", bad_output)


def test_validator_fails_empty_required_text_field() -> None:
    bad_output = {**BASE_AI_OUTPUT, "explanation_ar": "   "}
    with pytest.raises(ValidationError, match="explanation_ar"):
        OutputValidator.validate_ai_output("US PMI at 54.2", bad_output)


def test_validator_number_in_range_preserved() -> None:
    original = "Fed holds rates at 5.25%-5.50%"
    output = {
        **BASE_AI_OUTPUT,
        "headline_ar": "الاحتياطي الفيدرالي يُبقي على أسعار الفائدة عند مستوى 5.25%-5.50%",  # noqa: E501
        "translation_ar": "أبقى الاحتياطي الفيدرالي على أسعار الفائدة عند 5.25%-5.50%",
        "actual": "5.25%-5.50%",
        "forecast": None,
        "previous": None,
    }
    OutputValidator.validate_ai_output(original, output)
