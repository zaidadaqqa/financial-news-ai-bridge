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
    # Percent range: both UNSIGNED endpoints are individually mandatory
    # (2026-07-14 family-B fix — the hyphen is a range dash, not a minus).
    ranged = extract_required_numbers("Fed holds rates at 5.25%-5.50%")
    assert "5.25%" in ranged and "5.50%" in ranged
    assert "-5.50%" not in ranged and "-5.50" not in ranged
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


# ---------------------------------------------------------------------------
# Validator defect family A (2026-07-14): comma-separated thousands.
# Real loss: "Previous 2,090" fragmented into "2" + "090" and the validator
# demanded a phantom "090" from the Arabic output.
# ---------------------------------------------------------------------------


def test_comma_thousands_are_one_number() -> None:
    required = extract_required_numbers(
        "New Zealand Visitor Arrivals Actual 1860 (Forecast -, Previous 2,090)"
    )
    assert "2090" in required
    assert "090" not in required and "090%" not in required
    assert "1860" in required


def test_comma_thousands_forms() -> None:
    for source, expected in (
        ("Deficit at 1,000 units", "1000"),
        ("Imports reached 10,500 tonnes", "10500"),
        ("Index closed at 1,234.5 points", "1234.5"),
        ("Balance swung to -2,090 million", "-2090"),
    ):
        required = extract_required_numbers(source)
        assert expected in required, (source, required)
    # A grammatical comma between two numbers is not a thousands separator.
    required = extract_required_numbers("In 2024, 45% of output was exported")
    assert "2024" in required and "45%" in required
    assert "2024," not in required


def test_validator_passes_arabic_writing_thousands_either_way() -> None:
    original = "New Zealand Visitor Arrivals Actual 1860 (Forecast -, Previous 2,090)"
    for previous_form in ("2,090", "2090"):
        output = {
            **ARABIC_NO_NUMBERS,
            "headline_ar": f"وافدو نيوزيلندا يبلغون 1860 مقابل {previous_form} سابقًا",
            "translation_ar": f"بلغ عدد الوافدين 1860 بعد {previous_form} في القراءة السابقة",  # noqa: E501
            "actual": "1860",
            "previous": previous_form,
        }
        OutputValidator.validate_ai_output(original, output)


def test_validator_fails_when_thousands_number_dropped() -> None:
    original = "Visitor Arrivals Actual 1860 (Forecast -, Previous 2,090)"
    output = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "عدد الوافدين يبلغ 1860",
        "translation_ar": "بلغ عدد الوافدين 1860",
        "actual": "1860",
    }
    with pytest.raises(ValidationError, match="2090"):
        OutputValidator.validate_ai_output(original, output)


# ---------------------------------------------------------------------------
# Validator defect family B (2026-07-14): percent ranges vs AI rephrasing.
# Real loss: "aim for 1.5%-2.5% inflation" — the AI's natural «بين 1.5%
# و2.5%» was rejected for missing the phantom signed token "-2.5%".
# ---------------------------------------------------------------------------


def test_percent_range_rephrased_naturally_passes() -> None:
    original = "Fed's Waller: Seems reasonable to aim for 1.5%-2.5% inflation"
    output = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "وولر: من المنطقي استهداف تضخم بين 1.5% و2.5%",
        "translation_ar": "قال وولر إن استهداف تضخم بين 1.5% و2.5% يبدو منطقيًا",
    }
    OutputValidator.validate_ai_output(original, output)


def test_percent_range_hyphenated_output_still_passes() -> None:
    original = "Fed holds rates at 5.25%-5.50%"
    output = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "الفيدرالي يثبت الفائدة عند 5.25%-5.50%",
        "translation_ar": "أبقى الفيدرالي الفائدة في نطاق 5.25%-5.50%",
    }
    OutputValidator.validate_ai_output(original, output)


def test_percent_range_changed_or_dropped_endpoint_fails() -> None:
    original = "Fed's Waller: Seems reasonable to aim for 1.5%-2.5% inflation"
    changed = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "وولر: من المنطقي استهداف تضخم بين 1.5% و3%",
        "translation_ar": "استهداف تضخم بين 1.5% و3% يبدو منطقيًا",
    }
    with pytest.raises(ValidationError, match="2.5"):
        OutputValidator.validate_ai_output(original, changed)
    midpoint = {
        **ARABIC_NO_NUMBERS,
        "headline_ar": "وولر يستهدف تضخمًا عند 2%",
        "translation_ar": "يستهدف وولر تضخمًا عند 2% تقريبًا",
    }
    with pytest.raises(ValidationError):
        OutputValidator.validate_ai_output(original, midpoint)


def test_percent_range_endpoints_extracted_unsigned() -> None:
    required = extract_required_numbers("target of 2.0%–3.0% next year")
    assert "2.0%" in required and "3.0%" in required
    assert "-3.0" not in required and "-3.0%" not in required
    # Non-percent hyphen pairs keep the pre-existing behavior untouched.
    years = extract_required_numbers("guidance covers 2024-2025")
    assert "2024" in required or "2024" in years  # years still enforced
    # Negative endpoints never loosen: conservative fallback to old handling.
    negative = extract_required_numbers("swing of -0.5%-0.2% expected")
    assert "-0.5%" in negative
