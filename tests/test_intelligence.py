from decimal import Decimal
from unittest.mock import patch

from app.constants.enums import NewsCategory
from app.services.intelligence.engine import classify_news
from app.services.intelligence.models import NumericSurprise, Urgency
from app.services.intelligence.rules import (
    compare_economic_values,
    detect_company_structural_evidence,
    extract_actual_forecast_previous,
    is_earnings,
    parse_economic_value,
    score_tier_b,
)

# ---------------------------------------------------------------------------
# Real headline fixtures pulled read-only from production (§16 of
# NEWS_INTELLIGENCE_ARCHITECTURE.md) — used so classification tests are
# grounded in what FinancialJuice actually publishes, not synthetic guesses.
# ---------------------------------------------------------------------------

REAL_CENTRAL_BANK = (
    "BoJ set to keep interest rates unchanged in July but maintain policy guidance"
)
REAL_ECONOMIC_DATA = (
    "Canadian Participation Rate Actual 65.0% (Forecast 65%, Previous 65.0%)"
)
REAL_NEGATIVE_ECONOMIC = (
    "German CPI Final MoM Actual -0.3% (Forecast -0.3%, Previous -0.3%)"
)
REAL_DASH_FORECAST = (
    "US Baker Hughes Total Rig Count Actual 581 (Forecast -, Previous 580)"
)
REAL_LOWERCASE_K = (
    "Canadian Employment Change Actual 18.2k (Forecast 10k, Previous 87.8k)"
)
REAL_GEOPOLITICAL_COMMODITY_MIX = (
    "Strait of Hormuz crossings fall as operators remain cautious -MarineTraffic"
)
REAL_GOVERNMENT_BONDS_MIX = (
    "Japan finmin Katayama: aims to accelerate talks on broadening JGB "
    "offerings for households"
)
REAL_ROUTINE_SERIES = "60-Day Correlation Matrix"
REAL_CFTC_ROUTINE = "CFTC Positions in the Week Ended July 7th"
REAL_AMBIGUOUS = "Some completely unrelated headline about nothing specific at all"


# ---------------------------------------------------------------------------
# Deterministic repeatability / no I/O / no DB / no network
# ---------------------------------------------------------------------------


def test_deterministic_repeatability() -> None:
    results = [classify_news(REAL_ECONOMIC_DATA) for _ in range(5)]
    assert all(r == results[0] for r in results)


def test_classify_news_is_pure_no_io() -> None:
    # No DB session, no network client is constructed anywhere in the call —
    # proven simply by the fact this runs with none available/mocked.
    result = classify_news(REAL_ECONOMIC_DATA)
    assert result.category == NewsCategory.ECONOMIC_DATA


# ---------------------------------------------------------------------------
# Fallback semantics
# ---------------------------------------------------------------------------


def test_true_zero_signal_fallback() -> None:
    result = classify_news(REAL_AMBIGUOUS)
    assert result.category == NewsCategory.GENERAL
    assert result.is_fallback is True


def test_general_but_not_fallback_routine_series() -> None:
    result = classify_news(REAL_CFTC_ROUTINE)
    assert result.category == NewsCategory.GENERAL
    assert result.is_fallback is False
    assert result.urgency == Urgency.LOW


def test_general_but_not_fallback_routine_correlation_matrix() -> None:
    result = classify_news(REAL_ROUTINE_SERIES)
    assert result.category == NewsCategory.GENERAL
    assert result.is_fallback is False


def test_internal_exception_falls_back_safely() -> None:
    with patch(
        "app.services.intelligence.engine.detect_central_bank",
        side_effect=RuntimeError("boom"),
    ):
        result = classify_news(REAL_CENTRAL_BANK)
    assert result.is_fallback is True
    assert result.category == NewsCategory.GENERAL
    assert result.classification_reasons == ("fallback",)


def test_classification_failure_result_is_the_shared_safe_fallback_constant() -> None:
    from app.services.intelligence.models import SAFE_FALLBACK

    with patch(
        "app.services.intelligence.engine.detect_central_bank",
        side_effect=RuntimeError("boom"),
    ):
        result = classify_news(REAL_CENTRAL_BANK)
    assert result is SAFE_FALLBACK


# ---------------------------------------------------------------------------
# Tier A hard overrides
# ---------------------------------------------------------------------------


def test_central_bank_hard_override() -> None:
    result = classify_news(REAL_CENTRAL_BANK)
    assert result.category == NewsCategory.CENTRAL_BANK
    assert result.central_bank == "BOJ"
    assert result.country == "Japan"
    assert result.currency == "JPY"
    assert result.is_fallback is False


def test_ecb_central_bank_detected() -> None:
    result = classify_news("ECB Interest Rate Probabilities")
    assert result.category == NewsCategory.CENTRAL_BANK
    assert result.central_bank == "ECB"


def test_structured_economic_data_hard_override() -> None:
    result = classify_news(REAL_ECONOMIC_DATA)
    assert result.category == NewsCategory.ECONOMIC_DATA
    assert result.economic_event == "Participation Rate"
    assert result.country == "Canada"
    assert result.currency == "CAD"


def test_earnings_hard_override() -> None:
    result = classify_news(
        "Apple reports quarterly earnings beat estimates, guidance raised"
    )
    assert result.category == NewsCategory.EARNINGS


def test_no_category_breaking_ever_emitted() -> None:
    samples = [
        REAL_CENTRAL_BANK,
        REAL_ECONOMIC_DATA,
        REAL_GEOPOLITICAL_COMMODITY_MIX,
        "Fed announces emergency surprise rate cut amid market turmoil",
        "North Korea military strikes escalate tensions",
        REAL_AMBIGUOUS,
    ]
    for headline in samples:
        result = classify_news(headline)
        assert result.category != NewsCategory.BREAKING


def test_urgency_breaking_with_real_content_category() -> None:
    result = classify_news(
        "Fed announces emergency surprise rate cut amid market turmoil"
    )
    assert result.urgency == Urgency.BREAKING
    assert result.category == NewsCategory.CENTRAL_BANK  # real category, not "breaking"


# ---------------------------------------------------------------------------
# Mixed / precedence cases
# ---------------------------------------------------------------------------


def test_mixed_geopolitical_commodity_headline() -> None:
    result = classify_news(REAL_GEOPOLITICAL_COMMODITY_MIX)
    assert result.category == NewsCategory.GEOPOLITICAL


def test_mixed_government_economic_headline() -> None:
    result = classify_news(REAL_GOVERNMENT_BONDS_MIX)
    assert result.category == NewsCategory.GOVERNMENT


# ---------------------------------------------------------------------------
# Decimal numeric handling
# ---------------------------------------------------------------------------


def test_decimal_normalization_equal_formatting_variants() -> None:
    assert parse_economic_value("65%") == parse_economic_value("65.0%")
    assert compare_economic_values("65%", "65.0%") == NumericSurprise.MATCH


def test_decimal_normalization_lowercase_k_suffix() -> None:
    assert parse_economic_value("18.2k") == Decimal("18200")


def test_decimal_normalization_uppercase_m_suffix() -> None:
    assert parse_economic_value("1790M") == Decimal("1790000000")


def test_negative_values_parsed_and_compared_correctly() -> None:
    assert parse_economic_value("-0.3%") == Decimal("-0.3")
    assert compare_economic_values("-0.3%", "-0.2%") == NumericSurprise.LOWER


def test_dash_placeholder_is_unknown_not_zero() -> None:
    assert parse_economic_value("-") is None
    assert compare_economic_values("581", "-") == NumericSurprise.UNKNOWN


def test_range_value_returns_unknown_never_guesses() -> None:
    assert parse_economic_value("5.25%-5.50%") is None
    assert compare_economic_values("5.25%-5.50%", "5.00%") == NumericSurprise.UNKNOWN


def test_unit_mismatch_returns_unknown() -> None:
    # one side has a percent sign, the other doesn't — never compared
    assert compare_economic_values("5%", "5") == NumericSurprise.UNKNOWN


def test_malformed_number_returns_none_safely() -> None:
    assert parse_economic_value("not a number") is None
    assert parse_economic_value("") is None
    assert parse_economic_value(None) is None


def test_missing_actual_forecast_previous_is_unknown() -> None:
    result = classify_news("A headline with no numeric data at all")
    assert result.surprise_direction == NumericSurprise.UNKNOWN


def test_real_headline_actual_forecast_previous_extraction() -> None:
    actual, forecast, previous = extract_actual_forecast_previous(REAL_ECONOMIC_DATA)
    assert actual == "65.0%"
    assert forecast == "65%"
    assert previous == "65.0%"


def test_real_negative_headline_extraction_and_surprise() -> None:
    actual, forecast, previous = extract_actual_forecast_previous(
        REAL_NEGATIVE_ECONOMIC
    )
    assert actual == "-0.3%"
    assert compare_economic_values(actual, forecast) == NumericSurprise.MATCH


def test_real_dash_forecast_headline() -> None:
    actual, forecast, previous = extract_actual_forecast_previous(REAL_DASH_FORECAST)
    assert actual == "581"
    assert forecast == "-"
    assert compare_economic_values(actual, forecast) == NumericSurprise.UNKNOWN


def test_real_lowercase_k_suffix_headline_surprise() -> None:
    actual, forecast, previous = extract_actual_forecast_previous(REAL_LOWERCASE_K)
    assert compare_economic_values(actual, forecast) == NumericSurprise.HIGHER


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_ambiguous_headline_lands_on_general() -> None:
    result = classify_news(REAL_AMBIGUOUS)
    assert result.category == NewsCategory.GENERAL


def test_multilingual_and_unusual_punctuation_does_not_crash() -> None:
    headline = (
        "North Korea's Kim Jong Un: military must be modernized — «KCNA» ’test’ عاجل"
    )
    result = classify_news(headline)
    assert result is not None
    assert result.category in NewsCategory


def test_empty_headline_does_not_crash() -> None:
    result = classify_news("")
    assert result.category == NewsCategory.GENERAL


def test_no_false_urgency_on_routine_central_bank_snapshot() -> None:
    result = classify_news("Fed Interest Rate Probabilities")
    assert result.urgency != Urgency.BREAKING
    assert result.urgency == Urgency.LOW


# ---------------------------------------------------------------------------
# (A) Weighted Tier B evidence — proving weighted scoring is safer than raw
# keyword counting, not just asserting a category outcome.
# ---------------------------------------------------------------------------


def test_weighted_scoring_beats_raw_count_bitcoin_vs_dollar() -> None:
    """Real risk found during review: a single category-defining STRONG word
    ("bitcoin") must outrank two generic WEAK words from a different category
    ("dollar", "currency"). Under the old equal-weight-count design this
    headline scored FOREX=2 ("dollar","currency") vs CRYPTO=1 ("bitcoin") and
    FOREX would have won outright — wrong, since this headline is
    fundamentally a Bitcoin story. Weighted scoring (STRONG=3 for "bitcoin"
    vs WEAK+WEAK=2 for "dollar"+"currency") fixes it."""
    headline = (
        "Bitcoin rallies as dollar weakens and investors eye currency "
        "diversification"
    )
    scores = score_tier_b(headline)
    assert scores[NewsCategory.CRYPTO] > scores[NewsCategory.FOREX]

    result = classify_news(headline)
    assert result.category == NewsCategory.CRYPTO


def test_weighted_scoring_documents_weight_classes() -> None:
    """Sanity check that the three documented weight classes actually differ,
    so a future edit can't silently collapse them back to equal-weight
    counting without a test noticing."""
    from app.services.intelligence.rules import MEDIUM, STRONG, WEAK

    assert STRONG > MEDIUM > WEAK > 0


def test_pound_commodity_exclusion_prevents_false_forex_signal() -> None:
    """Negative/exclusion signal (§A): "pound" is ambiguous between GBP and a
    unit of weight. When commodities evidence is present and there's no
    supporting sterling/GBP wording, "pound" must not tip a tie toward
    FOREX."""
    headline = "Silver prices jump to $30 per pound amid strong industrial demand"
    result = classify_news(headline)
    assert result.category == NewsCategory.COMMODITIES


def test_pound_still_counts_as_forex_with_sterling_context() -> None:
    headline = "Pound sterling gains against the dollar after UK GDP beats forecasts"
    scores = score_tier_b(headline)
    assert NewsCategory.FOREX in scores


# ---------------------------------------------------------------------------
# (B) COMPANY structural detection — positive and negative cases
# ---------------------------------------------------------------------------


def test_company_positive_cashtag() -> None:
    result = classify_news("Apple sues OpenAI for trade secret theft. $AAPL")
    assert result.category == NewsCategory.COMPANY


def test_company_positive_corporate_suffix() -> None:
    result = classify_news(
        "Minimax Group announces placement of 35.6 million new Class A shares"
    )
    assert result.category == NewsCategory.COMPANY


def test_company_positive_exchange_ticker() -> None:
    assert detect_company_structural_evidence("Shares of Foo Inc jump 5%") == (
        "corporate_suffix"
    )
    assert detect_company_structural_evidence("(NASDAQ: FOO) shares jump") == (
        "exchange_ticker"
    )


def test_company_false_positive_currency_code_not_ticker() -> None:
    # A bare currency-code-shaped cashtag must never be read as a company
    # ticker.
    assert detect_company_structural_evidence("$USD gains broadly today") is None


def test_company_false_positive_g7_working_group() -> None:
    # "Group" alone must not flag international-body/diplomatic phrasing as
    # a company.
    assert (
        detect_company_structural_evidence(
            "G7 Group agrees on joint statement over sanctions"
        )
        is None
    )
    assert (
        detect_company_structural_evidence(
            "UN forms working Group to study sanctions enforcement"
        )
        is None
    )


def test_company_false_positive_capitalized_phrase_without_suffix() -> None:
    # Multiple capitalized words alone (no suffix, no ticker) must not be
    # treated as a company — this is the "don't treat every capitalized
    # phrase as a company" requirement.
    assert (
        detect_company_structural_evidence(
            "United Nations Security Council meets over Gaza ceasefire"
        )
        is None
    )


def test_government_not_misclassified_as_company() -> None:
    result = classify_news(
        "Japan's Finance Ministry board approves new fiscal budget measures"
    )
    assert result.category == NewsCategory.GOVERNMENT


def test_central_bank_not_misclassified_as_company() -> None:
    # Tier A central-bank hard override must win before COMPANY's Tier B
    # "board" keyword ever gets a chance to compete.
    result = classify_news(
        "European Central Bank raises capital requirements for board members"
    )
    assert result.category == NewsCategory.CENTRAL_BANK


# ---------------------------------------------------------------------------
# (C) Earnings override — hardened regex, positive and negative cases
# ---------------------------------------------------------------------------


def test_earnings_positive_quarterly_results() -> None:
    result = classify_news("Tesla Inc quarterly results beat estimates on deliveries")
    assert result.category == NewsCategory.EARNINGS


def test_earnings_positive_guidance_verb_phrase() -> None:
    assert is_earnings("Company XYZ Corp issues guidance for next quarter") is True


def test_earnings_negative_average_hourly_earnings_is_not_earnings() -> None:
    # Real production headline (§C validation) — an economic indicator name
    # containing the word "earnings", never a corporate-earnings signal.
    headline = (
        "Canadian Average Hourly Earnings YoY Actual 3.70% "
        "(Forecast 3.6%, Previous 3.20%)"
    )
    assert is_earnings(headline) is False
    result = classify_news(headline)
    assert result.category == NewsCategory.ECONOMIC_DATA


def test_earnings_negative_policy_guidance_is_not_earnings() -> None:
    # Real production headline (§C validation) — "policy guidance" is
    # standard central-bank vocabulary ("forward guidance"), not an earnings
    # signal.
    headline = (
        "BoJ set to keep interest rates unchanged in July but maintain "
        "policy guidance pledging to continue raising rates - Sources"
    )
    assert is_earnings(headline) is False
    result = classify_news(headline)
    assert result.category == NewsCategory.CENTRAL_BANK


def test_earnings_negative_generic_profit_commentary_is_not_earnings() -> None:
    # Ordinary profit-related commentary that isn't a formal earnings release
    # must not trigger the Tier-A override.
    assert is_earnings("Analysts expect steady profit growth across the sector") is (
        False
    )
    assert is_earnings("Government revenue rises on higher tax collection") is False


# ---------------------------------------------------------------------------
# (F) Fallback semantics — additional GENERAL-but-not-fallback real fixture
# ---------------------------------------------------------------------------


def test_general_not_fallback_bare_currency_evidence() -> None:
    # No Tier B category keyword matches at all here, but a real bare
    # currency code is detected independently — GENERAL and
    # is_fallback=False must coexist (§12 of the architecture doc).
    result = classify_news(
        "JPY holds near multi-decade lows amid intervention concerns"
    )
    assert result.category == NewsCategory.GENERAL
    assert result.is_fallback is False
    assert result.currency == "JPY"


def test_currency_pair_structural_evidence_drives_forex() -> None:
    # Audit finding: detect_currency_pair() existed but was never wired into
    # scoring — a pair-only headline with no "forex"/"fx"/currency-name word
    # previously fell through to GENERAL/fallback with zero signal.
    result = classify_news("EUR/USD holds steady in quiet pre-holiday trading")
    assert result.category == NewsCategory.FOREX
    assert result.is_fallback is False
