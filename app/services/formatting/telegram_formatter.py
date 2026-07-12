import html
from datetime import UTC, datetime
from typing import Any

from app.models.news import NewsEvent
from app.services.formatting.editorial_engine import (
    ASSETS,
    CONTEXT,
    DATA,
    EXPLANATION,
    IMPACT,
    WATCH,
    select_editorial_mode,
)
from app.services.intelligence.models import (
    NewsIntelligenceResult,
    NumericSurprise,
)
from app.services.story.models import RelationshipType, StoryDecision

# Deterministic verdict vocabulary for the economic-data block: a numeric
# fact about the print vs the forecast — never a good/bad judgment (that is
# the AI's analysis, in its own clearly-framed section). UNKNOWN renders
# nothing, ever.
_SURPRISE_VERDICTS_AR = {
    NumericSurprise.HIGHER: "أعلى من التوقعات",
    NumericSurprise.LOWER: "أدنى من التوقعات",
    NumericSurprise.MATCH: "مطابقة للتوقعات",
}

IMPORTANCE_LABELS_AR = {
    1: "منخفضة",
    2: "عادية",
    3: "مهمة",
    4: "عالية التأثير",
    5: "عاجلة وحرجة",
}

BIAS_LABELS_AR = {
    "POSITIVE": "إيجابي ▲",
    "NEGATIVE": "سلبي ▼",
    "MIXED": "مختلط",
    "NEUTRAL": "محايد",
    "UNCLEAR": "غير محدد",
}

# Category → header icon. Purpose-built so the icon signals what kind of
# story this is at a glance, consistently across every message. Every
# NewsCategory value has a dedicated icon (Objective B audit finding: 9 of
# 12 categories previously fell through to the generic 📰 default) — the
# reader learns this small, fixed vocabulary once and it pays off on every
# future message, rather than a text category tag repeated on every message
# (rejected — see .claude_memory/NEWSROOM_DNA.md §11).
CATEGORY_ICONS = {
    "breaking": "🚨",
    "central_bank": "🏦",
    "economic_data": "📊",
    "government": "🏛",
    "company": "🏢",
    "earnings": "💹",
    "commodities": "🛢",
    "crypto": "🪙",
    "geopolitical": "🌍",
    "forex": "💱",
    "bonds": "📜",
    "general": "📰",
}

# Substring match (checked against the uppercased asset string) → icon.
# Order matters: more specific tokens first.
ASSET_ICON_RULES: list[tuple[str, str]] = [
    ("XAU", "🥇"),
    ("GOLD", "🥇"),
    ("WTI", "🛢"),
    ("BRENT", "🛢"),
    ("OIL", "🛢"),
    ("BTC", "🪙"),
    ("ETH", "🪙"),
    ("CRYPTO", "🪙"),
    ("USD", "💵"),
    ("EUR", "💶"),
    ("GBP", "🇬🇧"),
    ("JPY", "🇯🇵"),
    ("CAD", "🇨🇦"),
]

SEPARATOR = "─────────────────────"
# Lighter, shorter rule for the footer only — the header/body boundary is a
# structural divider (keeps the full SEPARATOR); the footer boundary is a
# closing signature, not a new section, so it reads as visually lighter
# (final editorial polish, .claude_memory/NEWSROOM_DNA.md §11).
FOOTER_SEPARATOR = "· · · · · · · · · ·"


def _esc(text: str | None) -> str:
    return html.escape(str(text)) if text else ""


def _is_empty(value: Any) -> bool:
    # Dash variants are FinancialJuice's literal "no figure available"
    # placeholder (real headline: "Forecast -") — if the AI passes one
    # through, it must be omitted, never rendered as if it were a value.
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in ("none", "null", "n/a", "", "0", "-", "--", "—", "–")


# What-to-Watch defensive filler backstop. The PRIMARY quality control for
# this field is the AI prompt (prompts/translator.txt instructs: one concrete
# next thing to monitor, or null — never invented filler). This list exists
# only as a small, deterministic formatter-level backstop for the known
# boilerplate phrases that carry zero information; it matches the WHOLE
# normalized text exactly (never substrings), so a real sentence that merely
# contains one of these words is never hidden. Keep this list short — if it
# starts growing, the fix belongs in the prompt, not here.
_GENERIC_WATCH_FILLER = frozenset(
    phrase.strip()
    for phrase in (
        "راقب الأسواق",
        "تابع التطورات",
        "راقب رد فعل المستثمرين",
        "يترقب المستثمرون المستجدات",
        "ستتجه الأنظار إلى الأسواق",
        "تابع ما سيحدث لاحقًا",
        "تابع ما سيحدث لاحقا",
    )
)


def _is_generic_watch_filler(text: str) -> bool:
    normalized = str(text).strip().strip(".!؟?،:؛").strip()
    return normalized in _GENERIC_WATCH_FILLER


def _asset_icon(asset: str) -> str:
    upper = asset.upper()
    for token, icon in ASSET_ICON_RULES:
        if token in upper:
            return icon
    return "📈"  # default: treat as equity/index


def _category_icon(category: str, assets: Any) -> str:
    """Self-critique fix (Objective B): a static per-category icon table would
    make every COMMODITIES headline show the same icon (🛢) even when the
    story is actually about gold — visually misleading. For COMMODITIES
    specifically, derive the icon from the actual affected asset (reusing
    the same per-asset icon table the assets line already uses, so the
    headline icon and the assets line can never contradict each other) and
    only fall back to the generic commodities icon when no asset is known."""
    if category == "commodities" and isinstance(assets, list) and assets:
        first = str(assets[0]).strip()
        if first:
            return _asset_icon(first)
    return CATEGORY_ICONS.get(category, "📰")


class TelegramFormatter:
    @staticmethod
    def format_raw_english(news: NewsEvent) -> str:
        headline = _esc(news.original_headline)
        return (
            f"<b>📡 خبر مالي جديد</b>\n\n"
            f"{headline}\n\n"
            f"<i>⏳ جارٍ تحليل الخبر وترجمته...</i>"
        )

    @staticmethod
    def format_premium_bilingual(
        news: NewsEvent,
        ai_data: dict[str, Any],
        intelligence: NewsIntelligenceResult | None = None,
        story: StoryDecision | None = None,
    ) -> str:
        parts: list[str] = []

        importance_raw = ai_data.get("importance", 2)
        try:
            importance_int = int(importance_raw) if importance_raw is not None else 2
        except (ValueError, TypeError):
            importance_int = 2

        # Importance-aware section gating (Objective B, §15): a routine
        # importance-1 item should not carry the same visual weight as a
        # breaking importance-5 item. This does NOT gate the factual data
        # table (actual/forecast/previous) — §17 requires those presented
        # "whenever they exist," unconditionally; only the interpretive/
        # optional sections scale with importance.
        show_interpretation = importance_int >= 2
        show_optional_sections = importance_int >= 3

        # Editorial Engine (NEWSROOM_DNA.md §12): deterministic mode selection
        # BEFORE rendering. One unified visual DNA; the mode decides badge,
        # icon authority, and section hierarchy. Pure presentation — category/
        # urgency/story authority stays with the intelligence engines.
        plan = select_editorial_mode(intelligence, story, ai_data)

        # Category authority: the engine's classification wins when confident
        # (NEWS_INTELLIGENCE_ARCHITECTURE.md §7) — the AI no longer controls the
        # header icon in that case. Falls back to Phase 1's exact behavior
        # (AI's self-reported category) when intelligence is unavailable/fallback.
        if intelligence is not None and not intelligence.is_fallback:
            category = intelligence.category.value
        else:
            category = str(ai_data.get("category") or "")

        # Headline — the hero element. Exactly one primary icon (🚨 when the
        # mode says breaking, else the category icon), plus the mode's badge
        # («عاجل» / «تصحيح» / «تحديث» — never stacked) so the reader knows the
        # message TYPE before reading a word of the text.
        headline_ar = ai_data.get("headline_ar") or ai_data.get("translation_ar", "")
        if headline_ar:
            if plan.force_siren:
                icon = "🚨"
            else:
                icon = _category_icon(category, ai_data.get("affected_assets"))
            badge = f"{plan.badge} | " if plan.badge else ""
            parts.append(f"{icon} <b>{badge}{_esc(headline_ar)}</b>")
            # Breathing room before the divider so the headline reads as the
            # message's hero element rather than crowding straight into the
            # rule below it (final editorial polish, NEWSROOM_DNA.md §11).
            parts.append("")

        parts.append(SEPARATOR)

        # ------------------------------------------------------------------
        # Build each section as an independent fragment; the editorial mode's
        # section_order assembles them. Every gate below is identical across
        # all modes — modes reorder, they never un-gate.
        # ------------------------------------------------------------------
        fragments: dict[str, list[str]] = {}

        # Explanation — confirmed fact: what happened and why it matters.
        explanation = ai_data.get("explanation_ar", "")
        if explanation and not _is_empty(explanation):
            fragments[EXPLANATION] = [_esc(explanation), ""]

        # Story context (Phase 3) — the story's previous PUBLISHED
        # development. Confident UPDATE/CORRECTION with a published Arabic
        # prior, importance ≥ 2 only. Stored, validated data — never
        # AI-generated — so it cannot fabricate history.
        if (
            story is not None
            and story.relationship
            in (RelationshipType.UPDATE, RelationshipType.CORRECTION)
            and story.prior_headline_ar
            and not _is_empty(story.prior_headline_ar)
            and show_interpretation
        ):
            fragments[CONTEXT] = [
                "🔗 <b>تطور سابق:</b>",
                _esc(story.prior_headline_ar),
                "",
            ]

        # Economic data — the validated numbers, never importance-gated,
        # labels bold / values plain, fixed actual→forecast→previous order,
        # missing or dash-placeholder rows omitted cleanly.
        actual = ai_data.get("actual")
        forecast = ai_data.get("forecast")
        previous = ai_data.get("previous")

        data_rows = []
        if not _is_empty(actual):
            data_rows.append(f"<b>الفعلي:</b> {_esc(str(actual))}")
        if not _is_empty(forecast):
            data_rows.append(f"<b>المتوقع:</b> {_esc(str(forecast))}")
        if not _is_empty(previous):
            data_rows.append(f"<b>السابق:</b> {_esc(str(previous))}")

        # Deterministic verdict (Editorial DNA upgrade): rendered from the
        # intelligence engine's Decimal-validated comparison, ONLY when a real
        # forecast was present on both the engine's and the AI's side and the
        # comparison succeeded. Numeric fact only ("above forecast") — never
        # a market-direction judgment; that stays in the AI's impact section,
        # clearly framed as analysis.
        if (
            data_rows
            and intelligence is not None
            and not intelligence.is_fallback
            and intelligence.forecast is not None
            and not _is_empty(forecast)
        ):
            verdict = _SURPRISE_VERDICTS_AR.get(intelligence.surprise_direction)
            if verdict:
                data_rows.append(f"<b>النتيجة: {verdict}</b>")

        if data_rows:
            data_leads = bool(plan.section_order) and plan.section_order[0] == DATA
            if data_leads:
                fragments[DATA] = [*data_rows, ""]
            else:
                fragments[DATA] = ["📊 <b>البيانات الاقتصادية:</b>", *data_rows, ""]

        # Market impact — interpretation, importance ≥ 2 only.
        market_impact = ai_data.get("market_impact_ar", "")
        if show_interpretation and market_impact and not _is_empty(market_impact):
            fragments[IMPACT] = [
                "⚡ <b>التأثير على الأسواق:</b>",
                _esc(market_impact),
                "",
            ]

        # Affected assets — deduplicated, capped at 4, importance ≥ 3 only.
        assets = ai_data.get("affected_assets", [])
        if show_optional_sections and assets and isinstance(assets, list):
            seen: set[str] = set()
            deduped = []
            for a in assets:
                a_str = str(a).strip()
                key = a_str.upper()
                if a_str and key not in seen:
                    seen.add(key)
                    deduped.append(a_str)
            deduped = deduped[:4]
            if deduped:
                tagged = "  •  ".join(f"{_asset_icon(a)} {_esc(a)}" for a in deduped)
                fragments[ASSETS] = ["💼 <b>الأصول المتأثرة:</b>", tagged, ""]

        # What to watch — concrete next development only, importance ≥ 3,
        # generic-filler backstop applies.
        what_to_watch = ai_data.get("what_to_watch_ar", "")
        if (
            show_optional_sections
            and what_to_watch
            and not _is_empty(what_to_watch)
            and not _is_generic_watch_filler(what_to_watch)
        ):
            fragments[WATCH] = [
                "👀 <b>ما يجب مراقبته:</b>",
                _esc(what_to_watch),
                "",
            ]

        # Assemble the body in the editorial mode's hierarchy. Modes reorder
        # existing gated sections; they never add, remove, or un-gate one.
        for section in plan.section_order:
            parts.extend(fragments.get(section, ()))

        # No separate "key takeaway" section: summary_ar and headline_ar both ask
        # for a one-sentence takeaway, so rendering both reads as the same point
        # said twice. The headline already carries that role in the message.

        # Footer (final Phase 2 polish) — a quiet editorial signature, not a
        # data section. The direction value (محايد / إيجابي ▲ / سلبي ▼) is
        # self-explanatory, so its "الاتجاه:" field label was dropped; the
        # importance value is an adjective that needs its label, so
        # "الأهمية:" stays. Joined with a light bullet instead of a heavy
        # pipe. Omitted entirely for importance-1 items per §15's
        # "very short" spec.
        parts.append(FOOTER_SEPARATOR)
        if show_interpretation:
            bias = ai_data.get("market_bias", "")
            bias_label = BIAS_LABELS_AR.get(str(bias).upper(), "")
            importance_label = IMPORTANCE_LABELS_AR.get(importance_int, "عادية")

            meta_parts = []
            if bias_label:
                meta_parts.append(bias_label)
            meta_parts.append(f"الأهمية: {importance_label}")
            parts.append(" • ".join(meta_parts))

        # Source and timestamp — compact form (§20: "F.J.", never the full
        # name; source stays visually secondary). No clock emoji: the
        # timestamp is self-evident, so the icon was pure decoration
        # (emoji philosophy — every emoji must carry meaning).
        source = (
            "F.J."
            if "financialjuice" in (news.source_url or "").lower()
            else "News Bridge"
        )
        # Uses created_at (when the item was received) rather than "now" (when this
        # edit runs) — the latter drifts from the real event time by however long
        # AI processing took, which is exactly the kind of time confusion to avoid.
        event_time = news.created_at or datetime.now(UTC)
        time_str = event_time.strftime("%H:%M UTC")
        parts.append(f"المصدر: {_esc(source)} · {_esc(time_str)}")

        return "\n".join(parts)
