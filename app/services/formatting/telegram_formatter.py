import html
from datetime import UTC, datetime
from typing import Any

from app.models.news import NewsEvent
from app.services.intelligence.models import NewsIntelligenceResult, Urgency

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

        # Category authority: the engine's classification wins when confident
        # (NEWS_INTELLIGENCE_ARCHITECTURE.md §7) — the AI no longer controls the
        # header icon in that case. Falls back to Phase 1's exact behavior
        # (AI's self-reported category) when intelligence is unavailable/fallback.
        if intelligence is not None and not intelligence.is_fallback:
            category = intelligence.category.value
        else:
            category = str(ai_data.get("category") or "")

        # Headline — icon signals story type: breaking/critical always wins,
        # otherwise the category carries the icon, else a plain default.
        # intelligence.urgency==BREAKING is a stronger, independently-computed
        # signal than the AI's own importance rating for this specific escalation.
        engine_says_breaking = (
            intelligence is not None
            and not intelligence.is_fallback
            and intelligence.urgency == Urgency.BREAKING
        )
        headline_ar = ai_data.get("headline_ar") or ai_data.get("translation_ar", "")
        if headline_ar:
            if importance_int >= 5 or category == "breaking" or engine_says_breaking:
                icon = "🚨"
            else:
                icon = _category_icon(category, ai_data.get("affected_assets"))
            parts.append(f"{icon} <b>{_esc(headline_ar)}</b>")
            # Breathing room before the divider so the headline reads as the
            # message's hero element rather than crowding straight into the
            # rule below it (final editorial polish, NEWSROOM_DNA.md §11).
            parts.append("")

        parts.append(SEPARATOR)

        # Explanation — confirmed fact, always shown: what happened and why
        # it matters. Comes before any interpretation or numbers (§16: Fact
        # before Interpretation).
        explanation = ai_data.get("explanation_ar", "")
        if explanation and not _is_empty(explanation):
            parts.append(_esc(explanation))
            parts.append("")

        # Economic data section — the raw numbers, presented immediately
        # after the fact and BEFORE any interpretation (§12/§16/§17). Never
        # importance-gated: if the figures exist, they are the story.
        actual = ai_data.get("actual")
        forecast = ai_data.get("forecast")
        previous = ai_data.get("previous")

        # Labels bold, values plain (final Phase 2 polish): the label is the
        # anchor the eye scans for; the value reads naturally beside it.
        # Order is fixed — actual, forecast, previous — and a missing or
        # dash-placeholder value is omitted cleanly (its row simply doesn't
        # exist), never rendered as an empty or fake figure.
        data_rows = []
        if not _is_empty(actual):
            data_rows.append(f"  <b>الفعلي:</b> {_esc(str(actual))}")
        if not _is_empty(forecast):
            data_rows.append(f"  <b>المتوقع:</b> {_esc(str(forecast))}")
        if not _is_empty(previous):
            data_rows.append(f"  <b>السابق:</b> {_esc(str(previous))}")

        if data_rows:
            parts.append("📊 <b>البيانات الاقتصادية:</b>")
            parts.extend(data_rows)
            parts.append("")

        # Market impact analysis — interpretation, comes after the facts and
        # numbers, and only once the story clears the importance-2 floor
        # (§15: importance-1 is headline + one context sentence + source only).
        market_impact = ai_data.get("market_impact_ar", "")
        if show_interpretation and market_impact and not _is_empty(market_impact):
            parts.append("⚡ <b>التأثير على الأسواق:</b>")
            parts.append(_esc(market_impact))
            parts.append("")

        # Affected assets — each tagged with its own currency/commodity/equity
        # icon. Given its own header line (💼), matching every other section's
        # icon+bold-label-then-content pattern, instead of the previous
        # inline "label: content" on one line — consistency of hierarchy and
        # better mobile wrapping when 3-4 assets are present (final editorial
        # polish, NEWSROOM_DNA.md §11). Deduplicated and capped at 4 (§19:
        # "never a long shopping list") as a defensive floor under the
        # prompt's own 3-4-asset discipline — the formatter must not trust
        # the AI output blindly on this. Only shown from importance 3 upward
        # (§15).
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
                parts.append("💼 <b>الأصول المتأثرة:</b>")
                parts.append(tagged)
                parts.append("")

        # What to watch next — optional, only from importance 3 upward (§15),
        # and only when it names a concrete next development. Known
        # zero-information boilerplate is omitted entirely rather than
        # rendered under a section heading (see _is_generic_watch_filler —
        # a deterministic backstop; the prompt owns the real quality rule).
        what_to_watch = ai_data.get("what_to_watch_ar", "")
        if (
            show_optional_sections
            and what_to_watch
            and not _is_empty(what_to_watch)
            and not _is_generic_watch_filler(what_to_watch)
        ):
            parts.append("👀 <b>ما يجب مراقبته:</b>")
            parts.append(_esc(what_to_watch))
            parts.append("")

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
        time_str = event_time.strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"المصدر: {_esc(source)} · {_esc(time_str)}")

        return "\n".join(parts)
