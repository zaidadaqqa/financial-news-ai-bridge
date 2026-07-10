import html
from datetime import UTC, datetime
from typing import Any

from app.models.news import NewsEvent

IMPORTANCE_LABELS = {
    1: "منخفضة",
    2: "عادية",
    3: "مهمة",
    4: "عالية التأثير",
    5: "حرجة",
}

BIAS_LABELS = {
    "POSITIVE": "إيجابي ▲",
    "NEGATIVE": "سلبي ▼",
    "MIXED": "مختلط",
    "NEUTRAL": "محايد",
    "UNCLEAR": "غير محدد",
}

SEPARATOR = "─────────────────────"


def _esc(text: str | None) -> str:
    if not text:
        return ""
    return html.escape(str(text))


class TelegramFormatter:
    @staticmethod
    def format_raw_english(news: NewsEvent) -> str:
        headline = _esc(news.original_headline)
        return (
            f"<b>خبر مالي جديد</b>\n\n"
            f"{headline}\n\n"
            f"<i>جارٍ معالجة الترجمة والتحليل...</i>"
        )

    @staticmethod
    def format_premium_bilingual(news: NewsEvent, ai_data: dict[str, Any]) -> str:
        parts: list[str] = []

        importance_raw = ai_data.get("importance", 2)
        try:
            importance_int = int(importance_raw) if importance_raw is not None else 2
        except (ValueError, TypeError):
            importance_int = 2

        alert_prefix = "🚨" if importance_int >= 4 else "📰"

        headline_ar = ai_data.get("translation_ar", "")
        if headline_ar:
            parts.append(f"{alert_prefix} <b>{_esc(headline_ar)}</b>")
            parts.append("")

        if news.original_headline:
            parts.append(f"<i>{_esc(news.original_headline)}</i>")
            parts.append("")

        parts.append(SEPARATOR)

        summary = ai_data.get("summary_ar", "")
        if summary:
            parts.append(f"<b>الملخص:</b> {_esc(summary)}")

        importance_label = IMPORTANCE_LABELS.get(importance_int, "عادية")
        parts.append(f"<b>الأهمية:</b> {_esc(importance_label)}")

        bias = ai_data.get("market_bias", "")
        bias_label = BIAS_LABELS.get(str(bias).upper(), "")
        if bias_label:
            parts.append(f"<b>التوقع:</b> {bias_label}")

        impact = ai_data.get("impact", "")
        if impact and str(impact).strip():
            parts.append("")
            parts.append("<b>تأثير السوق:</b>")
            lines = [line.strip() for line in str(impact).split("\n") if line.strip()]
            parts.append(_esc("\n".join(lines[:2])))

        has_data = any(
            ai_data.get(k)
            and str(ai_data.get(k, "")).lower() not in ("none", "null", "")
            for k in ["actual", "forecast", "previous"]
        )
        if has_data:
            parts.append("")
            parts.append("<b>البيانات الاقتصادية:</b>")
            if ai_data.get("actual") and str(ai_data["actual"]).lower() not in (
                "none",
                "null",
            ):
                parts.append(f"  الفعلي: <b>{_esc(str(ai_data['actual']))}</b>")
            if ai_data.get("forecast") and str(ai_data["forecast"]).lower() not in (
                "none",
                "null",
            ):
                parts.append(f"  المتوقع: <b>{_esc(str(ai_data['forecast']))}</b>")
            if ai_data.get("previous") and str(ai_data["previous"]).lower() not in (
                "none",
                "null",
            ):
                parts.append(f"  السابق: <b>{_esc(str(ai_data['previous']))}</b>")

        assets = ai_data.get("affected_assets", [])
        if assets and isinstance(assets, list) and len(assets) > 0:
            parts.append("")
            parts.append(
                f"<b>الأصول المتأثرة:</b> {_esc(' • '.join(str(a) for a in assets))}"
            )

        parts.append("")
        parts.append(SEPARATOR)

        source = (
            "FinancialJuice"
            if "financialjuice" in (news.source_url or "").lower()
            else "News Bridge"
        )
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"<b>المصدر:</b> {_esc(source)}  |  {_esc(now_str)}")

        return "\n".join(parts)
