import html
from datetime import UTC, datetime
from typing import Any

from app.models.news import NewsEvent

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
# story this is at a glance, consistently across every message.
CATEGORY_ICONS = {
    "breaking": "🚨",
    "central_bank": "🏦",
    "economic_data": "📊",
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


def _esc(text: str | None) -> str:
    return html.escape(str(text)) if text else ""


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in ("none", "null", "n/a", "", "0")


def _asset_icon(asset: str) -> str:
    upper = asset.upper()
    for token, icon in ASSET_ICON_RULES:
        if token in upper:
            return icon
    return "📈"  # default: treat as equity/index


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
    def format_premium_bilingual(news: NewsEvent, ai_data: dict[str, Any]) -> str:
        parts: list[str] = []

        importance_raw = ai_data.get("importance", 2)
        try:
            importance_int = int(importance_raw) if importance_raw is not None else 2
        except (ValueError, TypeError):
            importance_int = 2

        category = str(ai_data.get("category") or "")

        # Headline — icon signals story type: breaking/critical always wins,
        # otherwise the category carries the icon, else a plain default.
        headline_ar = ai_data.get("headline_ar") or ai_data.get("translation_ar", "")
        if headline_ar:
            if importance_int >= 5 or category == "breaking":
                icon = "🚨"
            else:
                icon = CATEGORY_ICONS.get(category, "📰")
            parts.append(f"{icon} <b>{_esc(headline_ar)}</b>")

        parts.append(SEPARATOR)

        # Explanation — what happened and why it matters
        explanation = ai_data.get("explanation_ar", "")
        if explanation and not _is_empty(explanation):
            parts.append(_esc(explanation))
            parts.append("")

        # Market impact analysis
        market_impact = ai_data.get("market_impact_ar", "")
        if market_impact and not _is_empty(market_impact):
            parts.append("⚡ <b>التأثير على الأسواق:</b>")
            parts.append(_esc(market_impact))
            parts.append("")

        # Economic data section (only if actual/forecast/previous are real values)
        actual = ai_data.get("actual")
        forecast = ai_data.get("forecast")
        previous = ai_data.get("previous")

        data_rows = []
        if not _is_empty(actual):
            data_rows.append(f"  الفعلي: <b>{_esc(str(actual))}</b>")
        if not _is_empty(forecast):
            data_rows.append(f"  المتوقع: <b>{_esc(str(forecast))}</b>")
        if not _is_empty(previous):
            data_rows.append(f"  السابق: <b>{_esc(str(previous))}</b>")

        if data_rows:
            parts.append("📊 <b>البيانات الاقتصادية:</b>")
            parts.extend(data_rows)
            parts.append("")

        # Affected assets — each tagged with its own currency/commodity/equity icon.
        # No leading section icon: the per-asset icons already carry the meaning,
        # so a wrapper icon here would be decoration, not information.
        assets = ai_data.get("affected_assets", [])
        if assets and isinstance(assets, list) and len(assets) > 0:
            tagged = "  •  ".join(
                f"{_asset_icon(str(a))} {_esc(str(a))}" for a in assets
            )
            parts.append(f"<b>الأصول المتأثرة:</b> {tagged}")
            parts.append("")

        # What to watch next
        what_to_watch = ai_data.get("what_to_watch_ar", "")
        if what_to_watch and not _is_empty(what_to_watch):
            parts.append("👀 <b>ما يجب مراقبته:</b>")
            parts.append(_esc(what_to_watch))
            parts.append("")

        # No separate "key takeaway" section: summary_ar and headline_ar both ask
        # for a one-sentence takeaway, so rendering both reads as the same point
        # said twice. The headline already carries that role in the message.

        parts.append(SEPARATOR)

        # Metadata row
        bias = ai_data.get("market_bias", "")
        bias_label = BIAS_LABELS_AR.get(str(bias).upper(), "")
        importance_label = IMPORTANCE_LABELS_AR.get(importance_int, "عادية")

        meta_parts = []
        if bias_label:
            meta_parts.append(f"الاتجاه: {bias_label}")
        meta_parts.append(f"الأهمية: {importance_label}")
        parts.append("  |  ".join(meta_parts))

        # Source and timestamp
        source = (
            "FinancialJuice"
            if "financialjuice" in (news.source_url or "").lower()
            else "News Bridge"
        )
        # Uses created_at (when the item was received) rather than "now" (when this
        # edit runs) — the latter drifts from the real event time by however long
        # AI processing took, which is exactly the kind of time confusion to avoid.
        event_time = news.created_at or datetime.now(UTC)
        time_str = event_time.strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"🕒 المصدر: {_esc(source)}  ·  {_esc(time_str)}")

        return "\n".join(parts)
