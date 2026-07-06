from datetime import UTC, datetime
from typing import Any

from app.models.news import NewsEvent


class TelegramFormatter:
    @staticmethod
    def format_raw_english(news: NewsEvent) -> str:
        """Formats the initial rapid English message."""
        return (
            "🚨 **New Event Detected**\n\n"
            f"{news.original_headline}\n\n"
            "_Processing translation & AI impact..._"
        )

    @staticmethod
    def format_premium_bilingual(news: NewsEvent, ai_data: dict[str, Any]) -> str:
        """Format the premium bilingual message after AI processing."""
        parts = []
        parts.append("━━━━━━━━━━━━━━━━━━")

        # Priority / Alert formatting
        alert_icon = "🚨" if int(ai_data.get("importance", 1)) >= 4 else "📰"
        parts.append(f"{alert_icon} **Breaking News**")

        category_name = (
            str(ai_data.get("category", "General")).replace("_", " ").title()
        )
        parts.append(f"**{category_name}**")

        importance = int(ai_data.get("importance", 2))
        parts.append(f"Importance: {'⭐' * importance}")

        parts.append("")
        parts.append(news.original_headline)
        parts.append("━━━━━━━━━━━━━━━━━━")

        # Arabic Translation
        if ai_data.get("translation_ar"):
            parts.append("🇸🇦 **Arabic Translation**")
            parts.append(ai_data["translation_ar"])
            parts.append("━━━━━━━━━━━━━━━━━━")

        # Economic Data (if present)
        has_data = any(ai_data.get(k) for k in ["actual", "forecast", "previous"])
        if has_data:
            parts.append("📊 **Market Data**")
            if ai_data.get("actual"):
                parts.append(f"Actual: **{ai_data['actual']}**")
            if ai_data.get("forecast"):
                parts.append(f"Forecast: **{ai_data['forecast']}**")
            if ai_data.get("previous"):
                parts.append(f"Previous: **{ai_data['previous']}**")
            parts.append("━━━━━━━━━━━━━━━━━━")

        # Flags / Assets
        assets = ai_data.get("affected_assets", [])
        if assets:
            parts.append("🎯 **Affected Assets**")
            parts.append(" • ".join(assets))
            parts.append("━━━━━━━━━━━━━━━━━━")

        # Market Impact
        impact = ai_data.get("impact", "")
        if impact:
            parts.append("📈 **Possible Market Impact**")
            lines = [line.strip() for line in impact.split("\n") if line.strip()]
            parts.append("\n".join(lines[:2]))  # Maximum 2 lines
            parts.append("━━━━━━━━━━━━━━━━━━")

        # Footer Source
        source = (
            "FinancialJuice"
            if "financialjuice" in (news.source_url or "").lower()
            else "News Bridge"
        )
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        parts.append("🕒 **Source**")
        parts.append(f"{source}\n{now_str}")
        parts.append("━━━━━━━━━━━━━━━━━━")

        return "\n".join(parts)
