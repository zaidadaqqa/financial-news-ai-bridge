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


# ---------------------------------------------------------------------------
# Validator precision (2026-07-13): three narrow exemptions, each anchored to
# a real production loss — six messages died in 36h demanding numbers that
# were nomenclature or dates, not market data. Genuine figures stay enforced.
# ---------------------------------------------------------------------------

from app.services.validation.validator import (  # noqa: E402
    _arabic_letter_ratio,
    extract_required_numbers,
)

ARABIC_NO_NUMBERS = {
    **BASE_AI_OUTPUT,
    "headline_ar": "صادرات كوريا الجنوبية ترتفع في أوائل يوليو",
    "translation_ar": "ارتفعت صادرات كوريا الجنوبية خلال الفترة المشمولة بالتقرير",
    "explanation_ar": "أظهرت بيانات الجمارك ارتفاعاً في الصادرات.",
    "actual": None,
    "forecast": None,
    "previous": None,
}


def test_required_numbers_exempt_the_three_real_families() -> None:
    # Real production culprits — each previously demanded a phantom number.
    dayrange = "S.Korea July 1-10 exports surge 53.9% y/y: customs agency"
    required = extract_required_numbers(dayrange)
    assert "53.9%" in required and "53.9" in required
    assert "-10" not in required and "1" not in required and "10" not in required

    assert "10" not in extract_required_numbers("G10 FX: dollar steadies")
    assert "21" not in extract_required_numbers(
        "EU approves 21st sanctions package against Russia"
    )
    assert extract_required_numbers("CPI for the June 6th-12th week") == set()


def test_required_numbers_keep_genuine_market_figures() -> None:
    required = extract_required_numbers(
        "German HICP Final MoM Actual -0.2% (Forecast -0.2%, Previous -0.2%)"
    )
    assert "-0.2%" in required and "-0.2" in required
    # Numeric range without a month: both ends stay protected (the tail via
    # its signed form — enforcement accepts signed-or-bare, and the existing
    # end-to-end range test proves the pass path).
    ranged = extract_required_numbers("Fed holds rates at 5.25%-5.50%")
    assert "5.25%" in ranged and "-5.50%" in ranged
    assert "3.1%" in extract_required_numbers("US CPI YoY at 3.1%")
    assert "2023" in extract_required_numbers("Deficit widened in 2023")


def test_validator_rescues_real_dayrange_headline() -> None:
    # The exact family that produced three production AI_FAILED items.
    original = "S. Korea July 1-10 exports surge 53.9% y/y: customs agency"
    output = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "صادرات كوريا الجنوبية تقفز 53.9% على أساس سنوي",
        "translation_ar": "قفزت صادرات كوريا الجنوبية بنسبة 53.9% في أوائل يوليو",
    }
    OutputValidator.validate_ai_output(original, output)


def test_validator_rescues_identifier_and_ordinal_headlines() -> None:
    OutputValidator.validate_ai_output(
        "G10 FX: dollar steadies ahead of CPI",
        {
            **ARABIC_NO_NUMBERS,
            "headline_ar": "استقرار الدولار أمام عملات مجموعة العشر قبل بيانات التضخم",
            "translation_ar": "استقر الدولار أمام عملات مجموعة العشر الرئيسية",
        },
    )
    OutputValidator.validate_ai_output(
        "EU approves 21st sanctions package against Russia",
        {
            **ARABIC_NO_NUMBERS,
            "headline_ar": "الاتحاد الأوروبي يقر الحزمة الحادية والعشرين من العقوبات على روسيا",  # noqa: E501
            "translation_ar": "أقر الاتحاد الأوروبي حزمة العقوبات الحادية والعشرين ضد روسيا",  # noqa: E501
        },
    )


def test_validator_still_fails_when_market_number_dropped() -> None:
    # Control: the same rescued family still fails if the REAL figure vanishes.
    original = "S. Korea July 1-10 exports surge 53.9% y/y: customs agency"
    with pytest.raises(ValidationError, match="53.9"):
        OutputValidator.validate_ai_output(original, ARABIC_NO_NUMBERS)


# ---------------------------------------------------------------------------
# Arabic-ness gate (2026-07-13): real defect — quote items were published
# with the English headline stored as the "translation".
# ---------------------------------------------------------------------------


def test_validator_rejects_english_left_as_translation() -> None:
    bad = {
        **BASE_AI_OUTPUT,
        "headline_ar": "Trump: Hit Iran very hard last night.",
        "translation_ar": "Trump: Hit Iran very hard last night.",
    }
    with pytest.raises(ValidationError, match="not Arabic"):
        OutputValidator.validate_ai_output("Trump: Hit Iran very hard", bad)


def test_validator_accepts_arabic_with_latin_tickers() -> None:
    output = {
        **BASE_AI_OUTPUT,
        "headline_ar": "الدولار USD يتراجع بعد بيانات CPI الأمريكية عند 54.2",
        "translation_ar": "تراجع مؤشر الدولار USD عقب صدور بيانات التضخم CPI عند 54.2",
    }
    OutputValidator.validate_ai_output("US Services PMI rises to 54.2", output)


def test_arabic_ratio_mechanics() -> None:
    assert _arabic_letter_ratio("الدولار يرتفع") == 1.0
    assert _arabic_letter_ratio("The dollar is rising today") == 0.0
    # All-caps acronyms and digits are excluded from the denominator.
    assert _arabic_letter_ratio("بيانات CPI عند 3.1% USD") == 1.0
    # Nothing alphabetic at all → passes (nothing to judge).
    assert _arabic_letter_ratio("3.1% — 54.2") == 1.0
