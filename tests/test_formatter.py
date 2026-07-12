"""Tests for TelegramFormatter.format_premium_bilingual (Objective B —
Premium User Experience). Covers: full category icon coverage, dynamic
commodities icon, importance-aware section gating, fact-before-interpretation
ordering, compact source, and asset dedup/cap. No network, no DB, no
Telegram calls."""

from datetime import UTC, datetime

from app.constants.enums import NewsCategory
from app.models.news import NewsEvent
from app.services.formatting.telegram_formatter import (
    CATEGORY_ICONS,
    TelegramFormatter,
)
from app.services.intelligence.engine import classify_news
from app.services.story.models import RelationshipType, StoryDecision

BASE_AI = {
    "headline_ar": "عنوان تجريبي",
    "explanation_ar": "شرح تجريبي لما حدث.",
    "market_impact_ar": "تحليل تجريبي للأثر على الأسواق.",
    "translation_ar": "ترجمة تجريبية",
    "summary_ar": "ملخص تجريبي.",
    "what_to_watch_ar": "حدث قادم تجريبي يستحق المتابعة.",
    "category": "general",
    "importance": 3,
    "confidence": 0.8,
    "market_bias": "NEUTRAL",
    "impact": "test",
    "affected_assets": ["USD"],
    "actual": None,
    "forecast": None,
    "previous": None,
    "currency": None,
    "company": None,
    "ticker": None,
}


def _news(source_url: str = "https://www.financialjuice.com/x") -> NewsEvent:
    return NewsEvent(
        source_message_id="x",
        source="rss",
        source_url=source_url,
        original_headline="test",
    )


def _ai(**overrides: object) -> dict:
    ai = dict(BASE_AI)
    ai.update(overrides)
    return ai


# ---------------------------------------------------------------------------
# Category icon coverage
# ---------------------------------------------------------------------------


def test_every_news_category_has_a_dedicated_icon() -> None:
    # Every value in the shared NewsCategory enum must have an explicit icon
    # (Objective B audit finding: 9/12 categories previously fell through to
    # the generic 📰 default).
    for category in NewsCategory:
        assert category.value in CATEGORY_ICONS, f"missing icon for {category.value}"


def test_government_gets_dedicated_icon() -> None:
    news = _news()
    intelligence = classify_news(
        "Japan finance minister Katayama: Tokyo to pursue steps urging GPIF"
    )
    rendered = TelegramFormatter.format_premium_bilingual(
        news, _ai(category="government"), intelligence
    )
    assert rendered.startswith("🏛")


def test_crypto_gets_dedicated_icon() -> None:
    news = _news()
    intelligence = classify_news(
        "Bitcoin rallies past $75,000 on institutional inflows"
    )
    rendered = TelegramFormatter.format_premium_bilingual(
        news, _ai(category="crypto"), intelligence
    )
    assert rendered.startswith("🪙")


def test_geopolitical_gets_dedicated_icon_not_generic_default() -> None:
    news = _news()
    intelligence = classify_news(
        "Strait of Hormuz crossings fall as operators remain cautious"
    )
    rendered = TelegramFormatter.format_premium_bilingual(
        news, _ai(category="geopolitical"), intelligence
    )
    assert rendered.startswith("🌍")


# ---------------------------------------------------------------------------
# Dynamic commodities icon (self-critique fix: gold != oil)
# ---------------------------------------------------------------------------


def test_gold_headline_gets_gold_icon_not_generic_oil_icon() -> None:
    news = _news()
    intelligence = classify_news("Gold rallies to record high above $2,500")
    ai_data = _ai(category="commodities", affected_assets=["XAU", "USD"])
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, intelligence)
    assert rendered.startswith("🥇")


def test_oil_headline_gets_oil_icon() -> None:
    news = _news()
    intelligence = classify_news("Oil tumbles as OPEC weighs surprise production hike")
    ai_data = _ai(category="commodities", affected_assets=["WTI", "BRENT"])
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, intelligence)
    assert rendered.startswith("🛢")


def test_commodities_with_no_assets_falls_back_to_generic_icon() -> None:
    news = _news()
    intelligence = classify_news("OPEC weighs surprise production hike")
    ai_data = _ai(category="commodities", affected_assets=[])
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, intelligence)
    assert rendered.startswith(CATEGORY_ICONS["commodities"])


# ---------------------------------------------------------------------------
# Fact-before-interpretation ordering
# ---------------------------------------------------------------------------


def test_economic_data_table_appears_before_market_impact() -> None:
    news = _news()
    intelligence = classify_news(
        "Canadian Participation Rate Actual 65.0% (Forecast 65%, Previous 65.0%)"
    )
    ai_data = _ai(
        category="economic_data", actual="65.0%", forecast="65%", previous="65.0%"
    )
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, intelligence)
    data_pos = rendered.index("الفعلي")  # data-first mode: rows, no header
    impact_pos = rendered.index("التأثير على الأسواق")
    assert data_pos < impact_pos


# ---------------------------------------------------------------------------
# Importance-aware section gating
# ---------------------------------------------------------------------------


def test_importance_1_shows_only_headline_context_and_source() -> None:
    news = _news()
    ai_data = _ai(importance=1)
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert "التأثير على الأسواق" not in rendered
    assert "ما يجب مراقبته" not in rendered
    assert "الأصول المتأثرة" not in rendered
    assert "الأهمية:" not in rendered
    assert "شرح تجريبي لما حدث." in rendered
    assert "F.J." in rendered


def test_importance_2_shows_interpretation_but_not_optional_sections() -> None:
    news = _news()
    ai_data = _ai(importance=2)
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert "التأثير على الأسواق" in rendered
    assert "الأهمية:" in rendered
    assert "ما يجب مراقبته" not in rendered
    assert "الأصول المتأثرة" not in rendered


def test_importance_3_shows_full_section_set_when_present() -> None:
    news = _news()
    ai_data = _ai(importance=3)
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert "التأثير على الأسواق" in rendered
    assert "ما يجب مراقبته" in rendered
    assert "الأصول المتأثرة" in rendered


def test_data_table_not_gated_by_importance() -> None:
    # §17: actual/forecast/previous must render whenever present, regardless
    # of importance — not subject to the same tiering as interpretation.
    news = _news()
    ai_data = _ai(importance=1, actual="65.0%", forecast="65%", previous="65.0%")
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert "البيانات الاقتصادية" in rendered
    assert "التأثير على الأسواق" not in rendered  # still importance-gated


# ---------------------------------------------------------------------------
# Compact source (§20)
# ---------------------------------------------------------------------------


def test_source_is_compact_fj_not_full_name() -> None:
    news = _news("https://www.financialjuice.com/feed/item/1")
    rendered = TelegramFormatter.format_premium_bilingual(news, _ai(), None)
    assert "F.J." in rendered
    assert "FinancialJuice" not in rendered


# ---------------------------------------------------------------------------
# Asset dedup and cap (§19)
# ---------------------------------------------------------------------------


def test_assets_are_deduplicated_case_insensitively() -> None:
    news = _news()
    ai_data = _ai(importance=3, affected_assets=["WTI", "BRENT", "WTI", "brent"])
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert rendered.count("WTI") == 1
    assert rendered.lower().count("brent") == 1


def test_assets_are_capped_at_four() -> None:
    news = _news()
    ai_data = _ai(
        importance=3, affected_assets=["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
    )
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    lines = rendered.split("\n")
    header_idx = [i for i, line in enumerate(lines) if "الأصول المتأثرة" in line][0]
    # Assets now render on their own line below the section header (final
    # editorial polish — matches every other section's icon+label-then-
    # content pattern instead of one crowded inline line).
    assets_line = lines[header_idx + 1]
    assert assets_line.count("•") == 3  # 4 items => 3 separators


# ---------------------------------------------------------------------------
# Never-display audit (§21) — regression guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Final Phase 2 editorial polish — footer
# ---------------------------------------------------------------------------


def test_footer_meta_is_compact_bullet_form() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), None)
    # Direction value stays visible but its field label is gone; importance
    # keeps its label; light bullet joins them instead of a heavy pipe.
    assert "محايد • الأهمية: مهمة" in rendered
    assert "الاتجاه:" not in rendered
    assert "  |  " not in rendered


def test_footer_source_line_is_light_but_explicit() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), None)
    assert "المصدر: F.J." in rendered
    assert "🕒" not in rendered
    assert "UTC" in rendered  # timestamp still present


def test_footer_uses_lighter_rule_than_header() -> None:
    from app.services.formatting.telegram_formatter import (
        FOOTER_SEPARATOR,
        SEPARATOR,
    )

    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), None)
    assert rendered.count(SEPARATOR) == 1  # header/body boundary only
    assert rendered.count(FOOTER_SEPARATOR) == 1  # footer boundary only


def test_footer_meta_without_bias_still_shows_importance() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(market_bias="not-a-real-bias"), None
    )
    assert "الأهمية: مهمة" in rendered
    assert " • " not in rendered.split("\n")[-2]  # no dangling bullet


# ---------------------------------------------------------------------------
# Final Phase 2 editorial polish — economic-data labels and placeholders
# ---------------------------------------------------------------------------


def test_economic_data_labels_bold_values_plain() -> None:
    ai_data = _ai(actual="65.0%", forecast="65%", previous="64.8%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "<b>الفعلي:</b> 65.0%" in rendered
    assert "<b>المتوقع:</b> 65%" in rendered
    assert "<b>السابق:</b> 64.8%" in rendered
    assert "<b>65.0%</b>" not in rendered  # values no longer bold


def test_dash_placeholder_forecast_is_omitted_not_rendered() -> None:
    # Real FinancialJuice usage: "Forecast -" means no figure exists.
    ai_data = _ai(actual="581", forecast="-", previous="580")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "المتوقع" not in rendered
    assert "<b>الفعلي:</b> 581" in rendered
    assert "<b>السابق:</b> 580" in rendered


def test_missing_value_row_omitted_cleanly() -> None:
    ai_data = _ai(actual="581", forecast=None, previous="580")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "المتوقع" not in rendered
    assert "البيانات الاقتصادية" in rendered


def test_negative_values_render_intact() -> None:
    ai_data = _ai(actual="-0.3%", forecast="-0.2%", previous="-0.1%")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "<b>الفعلي:</b> -0.3%" in rendered
    assert "<b>المتوقع:</b> -0.2%" in rendered


def test_no_empty_economic_data_block() -> None:
    ai_data = _ai(actual=None, forecast="-", previous=None)
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "البيانات الاقتصادية" not in rendered


# ---------------------------------------------------------------------------
# Final Phase 2 editorial polish — market-impact visibility
# ---------------------------------------------------------------------------


def test_empty_market_impact_omitted() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(market_impact_ar=""), None
    )
    assert "التأثير على الأسواق" not in rendered


def test_whitespace_only_market_impact_omitted() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(market_impact_ar="   \n  "), None
    )
    assert "التأثير على الأسواق" not in rendered


def test_short_meaningful_market_impact_stays_visible() -> None:
    # A one-line honest "limited relevance" reading is real analysis and
    # must never be hidden by any length-based heuristic.
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(market_impact_ar="تأثير محدود على الأسواق."), None
    )
    assert "التأثير على الأسواق" in rendered
    assert "تأثير محدود على الأسواق." in rendered


# ---------------------------------------------------------------------------
# Final Phase 2 editorial polish — What-to-Watch visibility
# ---------------------------------------------------------------------------


def test_empty_what_to_watch_omitted() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(what_to_watch_ar=None), None
    )
    assert "ما يجب مراقبته" not in rendered


def test_generic_watch_filler_omitted() -> None:
    for filler in ("راقب الأسواق", "تابع التطورات", "يترقب المستثمرون المستجدات"):
        rendered = TelegramFormatter.format_premium_bilingual(
            _news(), _ai(what_to_watch_ar=filler), None
        )
        assert "ما يجب مراقبته" not in rendered, f"filler not omitted: {filler}"


def test_generic_watch_filler_with_trailing_punctuation_omitted() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(what_to_watch_ar="تابع التطورات."), None
    )
    assert "ما يجب مراقبته" not in rendered


def test_meaningful_what_to_watch_stays_visible() -> None:
    concrete = "قرار الفيدرالي بشأن أسعار الفائدة يوم الأربعاء المقبل"
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(what_to_watch_ar=concrete), None
    )
    assert "ما يجب مراقبته" in rendered
    assert concrete in rendered


def test_sentence_containing_filler_words_is_not_hidden() -> None:
    # Exact-match backstop only: a longer concrete sentence that happens to
    # contain a filler phrase's words must never be suppressed.
    text = "تابع التطورات في اجتماع أوبك المقرر يوم الخميس"
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(what_to_watch_ar=text), None
    )
    assert "ما يجب مراقبته" in rendered


# ---------------------------------------------------------------------------
# Final Phase 2 editorial polish — breaking icon and escaping guards
# ---------------------------------------------------------------------------


def test_breaking_renders_exactly_one_primary_icon() -> None:
    # A commodities story (which would normally get 🛢/🥇) that is BREAKING
    # must lead with 🚨 alone — never a combined "🛢🚨" pair.
    intelligence = classify_news(
        "Oil prices spike as military strikes hit key export terminal"
    )
    assert intelligence.urgency.value == "BREAKING"
    ai_data = _ai(category="commodities", affected_assets=["WTI"])
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), ai_data, intelligence
    )
    first_line = rendered.split("\n")[0]
    assert first_line.startswith("🚨 <b>")
    assert first_line.count("🚨") == 1
    assert "🛢" not in first_line


def test_dynamic_content_is_html_escaped() -> None:
    ai_data = _ai(
        headline_ar='<script>alert(1)</script> & "quoted"',
        explanation_ar="نص & <injected>",
    )
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai_data, None)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


def test_no_khulasa_takeaway_section() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), None)
    assert "الخلاصة" not in rendered
    assert "🎯" not in rendered


def test_no_internal_debug_fields_ever_rendered() -> None:
    news = _news()
    intelligence = classify_news("Fed announces emergency surprise rate cut")
    rendered = TelegramFormatter.format_premium_bilingual(
        news, _ai(category="central_bank"), intelligence
    )
    forbidden = [
        "confidence",
        "is_fallback",
        "classification_reasons",
        "NewsCategory.",
        "Urgency.",
        "latency",
        str(intelligence.classification_reasons),
    ]
    for token in forbidden:
        assert token not in rendered


# ---------------------------------------------------------------------------
# Story context section (Phase 3) — frozen layout + gated optional section
# ---------------------------------------------------------------------------


def _story_decision(
    relationship: RelationshipType = RelationshipType.UPDATE,
    prior_ar: str | None = "الفيدرالي يخفض الفائدة بمقدار 25 نقطة أساس",
) -> StoryDecision:
    return StoryDecision(
        story_id="story-abc-id",
        relationship=relationship,
        is_new_story=relationship == RelationshipType.NEW_STORY,
        evidence_score=6,
        matching_reasons=("central_bank:FED:+3",),
        prior_original_headline="Fed cuts rates by 25bp",
        prior_headline_ar=prior_ar,
        prior_at=datetime(2026, 7, 12, 1, 30, tzinfo=UTC),
    )


def test_story_context_shown_for_update() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(importance=3), None, _story_decision()
    )
    assert "🔗 <b>تطور سابق:</b>" in rendered
    assert "الفيدرالي يخفض الفائدة بمقدار 25 نقطة أساس" in rendered


def test_story_context_placed_after_explanation_before_data() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(),
        _ai(importance=3, actual="2.5%", forecast="2.4%", previous="2.3%"),
        None,
        _story_decision(),
    )
    explanation_pos = rendered.index("شرح تجريبي لما حدث.")
    context_pos = rendered.index("تطور سابق")
    data_pos = rendered.index("البيانات الاقتصادية")
    impact_pos = rendered.index("التأثير على الأسواق")
    assert explanation_pos < context_pos < data_pos < impact_pos


def test_story_context_hidden_for_new_story() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(),
        _ai(importance=3),
        None,
        _story_decision(relationship=RelationshipType.NEW_STORY, prior_ar=None),
    )
    assert "تطور سابق" not in rendered


def test_story_context_hidden_for_repetition() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(),
        _ai(importance=3),
        None,
        _story_decision(relationship=RelationshipType.REPETITION),
    )
    assert "تطور سابق" not in rendered


def test_story_context_hidden_without_published_prior() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(importance=3), None, _story_decision(prior_ar=None)
    )
    assert "تطور سابق" not in rendered


def test_story_context_hidden_at_importance_1() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(importance=1), None, _story_decision()
    )
    assert "تطور سابق" not in rendered


def test_story_context_never_leaks_internal_metadata() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(importance=3), None, _story_decision()
    )
    assert "story-abc-id" not in rendered
    assert "UPDATE" not in rendered
    assert "+3" not in rendered
    assert "evidence" not in rendered.lower()


def test_story_context_is_html_escaped() -> None:
    rendered = TelegramFormatter.format_premium_bilingual(
        _news(),
        _ai(importance=3),
        None,
        _story_decision(prior_ar='<script>alert(1)</script> & "quoted"'),
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_frozen_layout_unchanged_without_story() -> None:
    # Phase 2 output with story=None must be byte-identical to the frozen
    # layout — the parameter's absence is exact Phase 2 behavior.
    a = TelegramFormatter.format_premium_bilingual(_news(), _ai(importance=3), None)
    b = TelegramFormatter.format_premium_bilingual(
        _news(), _ai(importance=3), None, None
    )
    assert a == b
