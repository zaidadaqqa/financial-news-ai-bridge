"""Editorial Engine tests (NEWSROOM_DNA.md §12): deterministic mode
selection, badges, verdict line, and per-mode hierarchy — all within the one
unified visual DNA. No network, no DB."""

from datetime import UTC, datetime

from app.models.news import NewsEvent
from app.services.formatting.editorial_engine import (
    EditorialMode,
    select_editorial_mode,
)
from app.services.formatting.telegram_formatter import TelegramFormatter
from app.services.intelligence.engine import classify_news
from app.services.intelligence.models import SAFE_FALLBACK
from app.services.story.models import RelationshipType, StoryDecision

BASE_AI: dict[str, object] = {
    "headline_ar": "عنوان تجريبي",
    "explanation_ar": "شرح تجريبي لما حدث.",
    "market_impact_ar": "تحليل تجريبي للأثر.",
    "translation_ar": "ترجمة",
    "summary_ar": "ملخص.",
    "what_to_watch_ar": None,
    "category": "general",
    "importance": 3,
    "confidence": 0.8,
    "market_bias": "NEUTRAL",
    "impact": "t",
    "affected_assets": [],
    "actual": None,
    "forecast": None,
    "previous": None,
    "currency": None,
    "company": None,
    "ticker": None,
}


def _ai(**overrides: object) -> dict[str, object]:
    d: dict[str, object] = dict(BASE_AI)
    d.update(overrides)
    return d


def _news() -> NewsEvent:
    return NewsEvent(
        source_message_id="x",
        source="rss",
        source_url="https://www.financialjuice.com/x",
        original_headline="t",
    )


def _update_story(
    relationship: RelationshipType = RelationshipType.UPDATE,
) -> StoryDecision:
    return StoryDecision(
        story_id="s-1",
        relationship=relationship,
        is_new_story=False,
        evidence_score=5,
        matching_reasons=("tokens:+4",),
        prior_original_headline="Prior development",
        prior_headline_ar="تطور سابق منشور",
        prior_at=datetime(2026, 7, 12, 1, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Mode selection — deterministic, full coverage
# ---------------------------------------------------------------------------


def test_mode_selection_is_deterministic() -> None:
    intel = classify_news("Fed announces emergency surprise rate cut")
    plans = [select_editorial_mode(intel, None, _ai()) for _ in range(5)]
    assert all(p == plans[0] for p in plans)


def test_breaking_mode() -> None:
    intel = classify_news("Fed announces emergency surprise rate cut")
    plan = select_editorial_mode(intel, None, _ai())
    assert plan.mode == EditorialMode.BREAKING
    assert plan.badge == "عاجل"
    assert plan.force_siren


def test_breaking_update_mode_beats_both() -> None:
    intel = classify_news("Fed announces emergency surprise rate cut")
    plan = select_editorial_mode(intel, _update_story(), _ai())
    assert plan.mode == EditorialMode.BREAKING_UPDATE
    assert plan.badge == "عاجل"  # never stacked with تحديث


def test_story_update_mode_and_correction_badge() -> None:
    intel = classify_news("BoJ governor explains the decision")
    plan = select_editorial_mode(intel, _update_story(), _ai())
    assert plan.mode == EditorialMode.STORY_UPDATE
    assert plan.badge == "تحديث"
    corr = select_editorial_mode(
        intel, _update_story(RelationshipType.CORRECTION), _ai()
    )
    assert corr.mode == EditorialMode.STORY_UPDATE
    assert corr.badge == "تصحيح"


def test_category_modes_from_confident_engine() -> None:
    cpi = "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)"
    minister = "Japan finance minister Katayama: tracking market conditions"
    cases = {
        cpi: EditorialMode.ECONOMIC_DATA,
        "BoJ set to keep interest rates unchanged in July": EditorialMode.CENTRAL_BANK,
        minister: EditorialMode.OFFICIAL_STATEMENT,
        "Ceasefire talks continue amid military tensions": EditorialMode.GEOPOLITICAL,
        "Apple sues OpenAI for trade secret theft. $AAPL": EditorialMode.COMPANY,
        "Tesla Inc quarterly results beat estimates": EditorialMode.EARNINGS,
        "Oil tumbles as OPEC weighs production hike": EditorialMode.COMMODITIES,
        "Bitcoin rallies past $75,000 on institutional inflows": EditorialMode.CRYPTO,
    }
    for headline, expected in cases.items():
        intel = classify_news(headline)
        plan = select_editorial_mode(intel, None, _ai())
        assert plan.mode == expected, (headline, plan.mode)


def test_general_mode_for_fallback_and_unknowns() -> None:
    plan = select_editorial_mode(SAFE_FALLBACK, None, _ai(category="forex"))
    assert plan.mode == EditorialMode.GENERAL
    assert plan.badge is None


def test_fallback_path_uses_ai_category() -> None:
    plan = select_editorial_mode(SAFE_FALLBACK, None, _ai(category="central_bank"))
    assert plan.mode == EditorialMode.CENTRAL_BANK
    legacy_breaking = select_editorial_mode(None, None, _ai(importance=5))
    assert legacy_breaking.mode == EditorialMode.BREAKING


def test_repetition_never_gets_update_mode() -> None:
    intel = classify_news("BoJ governor explains the decision")
    rep = _update_story(RelationshipType.REPETITION)
    plan = select_editorial_mode(intel, rep, _ai())
    assert plan.mode == EditorialMode.CENTRAL_BANK
    assert plan.badge is None


# ---------------------------------------------------------------------------
# Rendered badges — instant type recognition, exactly one primary icon
# ---------------------------------------------------------------------------


def test_breaking_renders_ajel_badge_with_single_siren() -> None:
    intel = classify_news("Fed announces emergency surprise rate cut")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    first = rendered.split("\n")[0]
    assert first.startswith("🚨 <b>عاجل | ")
    assert first.count("🚨") == 1


def test_update_renders_tahdith_badge_with_category_icon() -> None:
    intel = classify_news("BoJ governor explains the decision")
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(), intel, _update_story()
    )
    assert rendered.startswith("🏦 <b>تحديث | ")


def test_correction_renders_tasheeh_badge() -> None:
    intel = classify_news("Statistics office corrects earlier figure")
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(), intel, _update_story(RelationshipType.CORRECTION)
    )
    assert "تصحيح | " in rendered.split("\n")[0]


def test_no_badge_on_plain_categories() -> None:
    intel = classify_news("Oil tumbles as OPEC weighs production hike")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    first = rendered.split("\n")[0]
    assert "عاجل" not in first and "تحديث" not in first and "تصحيح" not in first


# ---------------------------------------------------------------------------
# Verdict line — honest, deterministic, forecast-gated
# ---------------------------------------------------------------------------


def test_verdict_rendered_for_real_forecast_miss() -> None:
    headline = "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)"
    intel = classify_news(headline)
    ai = _ai(actual="-0.3%", forecast="-0.2%", previous="-0.1%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    assert "<b>النتيجة: أدنى من التوقعات</b>" in rendered


def test_verdict_match_wording() -> None:
    headline = "Canadian Participation Rate Actual 65.0% (Forecast 65%, Previous 65.0%)"
    intel = classify_news(headline)
    ai = _ai(actual="65.0%", forecast="65%", previous="65.0%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    assert "<b>النتيجة: مطابقة للتوقعات</b>" in rendered


def test_no_verdict_without_forecast() -> None:
    headline = "US Baker Hughes Total Rig Count Actual 581 (Forecast -, Previous 580)"
    intel = classify_news(headline)
    ai = _ai(actual="581", forecast=None, previous="580")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    assert "النتيجة" not in rendered  # engine compared vs previous — no forecast claim


def test_no_verdict_on_fallback_intelligence() -> None:
    ai = _ai(actual="2.5%", forecast="2.4%", previous="2.3%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, SAFE_FALLBACK)
    assert "النتيجة" not in rendered


# ---------------------------------------------------------------------------
# Per-mode hierarchy — one DNA, mode-driven order
# ---------------------------------------------------------------------------


def test_economic_data_mode_leads_with_numbers() -> None:
    headline = "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)"
    intel = classify_news(headline)
    ai = _ai(actual="-0.3%", forecast="-0.2%", previous="-0.1%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    # Data-first mode renders the rows WITHOUT the section header — the rows
    # label themselves and the headline icon already said 📊.
    assert "البيانات الاقتصادية" not in rendered
    assert rendered.index("الفعلي") < rendered.index("شرح تجريبي")


def test_story_update_mode_promotes_context_before_data() -> None:
    intel = classify_news("BoJ governor explains inflation revision decision")
    ai = _ai(actual="2.5%", forecast="2.4%", previous="2.3%")
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), ai, intel, _update_story()
    )
    explanation = rendered.index("شرح تجريبي")
    context = rendered.index("تطور سابق")
    data = rendered.index("البيانات الاقتصادية")
    impact = rendered.index("التأثير على الأسواق")
    assert explanation < context < data < impact


def test_canonical_mode_places_context_after_impact() -> None:
    # A non-update mode with a context fragment cannot occur in production
    # (context requires UPDATE/CORRECTION → STORY_UPDATE mode), so canonical
    # order is asserted via assets-last + impact-after-data instead.
    intel = classify_news("Oil tumbles as OPEC weighs production hike")
    ai = _ai(affected_assets=["WTI"], what_to_watch_ar="اجتماع أوبك المقبل يوم الخميس")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    impact = rendered.index("التأثير على الأسواق")
    watch = rendered.index("ما يجب مراقبته")
    assets = rendered.index("الأصول المتأثرة")
    assert impact < watch < assets  # assets are the trader's action footer


def test_all_modes_share_the_visual_dna_skeleton() -> None:
    from app.services.formatting.telegram_formatter import (
        FOOTER_SEPARATOR,
        SEPARATOR,
    )

    for headline in (
        "Fed announces emergency surprise rate cut",
        "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)",
        "Oil tumbles as OPEC weighs production hike",
        "Some completely unrelated headline about nothing at all",
    ):
        intel = classify_news(headline)
        rendered = TelegramFormatter.format_premium_bilingual(
            _news(), _ai(actual="-0.3%", forecast="-0.2%"), intel
        )
        assert rendered.count(SEPARATOR) == 1
        assert rendered.count(FOOTER_SEPARATOR) == 1
        assert "المصدر: F.J." in rendered
