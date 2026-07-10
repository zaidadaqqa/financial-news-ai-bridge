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

SEPARATOR = "─────────────────────"


def _esc(text: str | None) -> str:
    return html.escape(str(text)) if text else ""


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in ("none", "null", "n/a", "", "0")


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

        # Headline
        headline_ar = ai_data.get("headline_ar") or ai_data.get("translation_ar", "")
        if headline_ar:
            icon = "🚨" if importance_int >= 4 else "📰"
            parts.append(f"{icon} <b>{_esc(headline_ar)}</b>")

        parts.append(SEPARATOR)

        # Explanation
        explanation = ai_data.get("explanation_ar", "")
        if explanation and not _is_empty(explanation):
            parts.append(_esc(explanation))
            parts.append("")

        # Why it matters / Market impact
        market_impact = ai_data.get("market_impact_ar", "")
        if market_impact and not _is_empty(market_impact):
            parts.append("<b>📊 التأثير المتوقع على الأسواق:</b>")
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
            parts.append("<b>📈 البيانات الاقتصادية:</b>")
            parts.extend(data_rows)
            parts.append("")

        # Affected assets
        assets = ai_data.get("affected_assets", [])
        if assets and isinstance(assets, list) and len(assets) > 0:
            parts.append(
                f"<b>🎯 الأصول المتأثرة:</b>  "
                f"{_esc('  •  '.join(str(a) for a in assets))}"
            )
            parts.append("")

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
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"المصدر: {_esc(source)}  ·  {_esc(now_str)}")

        return "\n".join(parts)
