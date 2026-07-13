"""Separator-free Editorial DNA regressions (2026-07-13, evidence-based
redesign: owner directive + four-channel research, 0/476 messages with ruled
lines). Locks the new invariants: no drawn rules, single-blank-line rhythm,
deterministic country flag as a hero-line suffix, verdict arrows, and the
frozen emoji registry — nothing outside it may ever render."""

import re

from app.models.news import NewsEvent
from app.services.formatting.telegram_formatter import (
    APPROVED_EMOJIS,
    COUNTRY_FLAGS,
    TelegramFormatter,
)
from app.services.intelligence.engine import classify_news
from tests.test_editorial_engine import _ai, _news

# Characters legitimately present besides text: ASCII, Arabic blocks,
# and the small structural set (bullet, arrows live in the registry).
_TEXT_RE = re.compile(
    "[\n\t\u0020-\u007e"  # whitespace + printable ASCII
    "\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff"  # Arabic blocks
    "\ufb50-\ufdff\ufe70-\ufeff"  # Arabic presentation forms
    "\u00b7\u2022\u00ab\u00bb\u2026\u2013\u2014\u200f]"  # · • « » … – — RLM
)


def _foreign_chars(rendered: str) -> set[str]:
    return {ch for ch in rendered if not _TEXT_RE.match(ch)}


def _approved_chars() -> set[str]:
    chars: set[str] = set()
    for emoji in APPROVED_EMOJIS:
        chars.update(emoji)
    return chars


def test_country_flag_rendered_for_confident_country() -> None:
    intel = classify_news(
        "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)"
    )
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    first = rendered.split("\n")[0]
    assert first.endswith(" 🇩🇪")
    assert first.count("🇩🇪") == 1


def test_no_flag_without_detected_country() -> None:
    intel = classify_news("Oil tumbles as OPEC weighs production hike")
    assert intel.country is None
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    flags = [f for f in COUNTRY_FLAGS.values() if f in rendered]
    assert flags == []

    # No intelligence at all (Phase 1 path) → no flag either.
    rendered_none = TelegramFormatter.format_premium_bilingual(_news(), _ai(), None)
    assert [f for f in COUNTRY_FLAGS.values() if f in rendered_none] == []


def test_breaking_keeps_single_siren_with_flag_suffix() -> None:
    # The flag is a trailing qualifier — 🚨 stays the one and only primary.
    intel = classify_news("US Federal Reserve announces emergency surprise rate cut")
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    first = rendered.split("\n")[0]
    assert first.startswith("🚨 ")
    assert first.count("🚨") == 1
    if intel.country:  # engine detected US → suffix flag, exactly one
        assert first.endswith(" 🇺🇸")
        assert first.count("🇺🇸") == 1


def test_norwegian_flag_via_new_vocabulary() -> None:
    intel = classify_news(
        "Norwegian CPI YoY Actual 2.7% (Forecast 3.1%, Previous 3.1%)"
    )
    rendered = TelegramFormatter.format_premium_bilingual(_news(), _ai(), intel)
    assert rendered.split("\n")[0].endswith(" 🇳🇴")


def test_verdict_arrows_match_direction() -> None:
    beat = "US CPI YoY Actual 3.4% (Forecast 3.1%, Previous 3.0%)"
    miss = "US CPI YoY Actual 2.9% (Forecast 3.1%, Previous 3.0%)"
    match = "US CPI YoY Actual 3.1% (Forecast 3.1%, Previous 3.0%)"
    for headline, needle in (
        (beat, "أعلى من التوقعات ▲"),
        (miss, "أدنى من التوقعات ▼"),
        (match, "مطابقة للتوقعات"),
    ):
        intel = classify_news(headline)
        ai = _ai(actual=intel.actual, forecast=intel.forecast, previous=intel.previous)
        rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
        assert needle in rendered
    # MATCH carries no arrow at all.
    intel = classify_news(match)
    ai = _ai(actual=intel.actual, forecast=intel.forecast, previous=intel.previous)
    rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
    verdict_line = next(line for line in rendered.split("\n") if "النتيجة" in line)
    assert "▲" not in verdict_line and "▼" not in verdict_line


def test_every_rendered_emoji_is_in_the_approved_registry() -> None:
    """The registry lock: across representative renders of every message
    family, no character outside text + APPROVED_EMOJIS may appear."""
    approved = _approved_chars()
    headlines = (
        "US Federal Reserve announces emergency surprise rate cut",
        "German CPI Final MoM Actual -0.3% (Forecast -0.2%, Previous -0.1%)",
        "Oil tumbles as OPEC weighs production hike",
        "Gold climbs to fresh record high on safe-haven demand",
        "Bitcoin rallies past $75,000 on institutional inflows",
        "Japan finance minister Katayama: tracking market conditions",
        "Ceasefire talks continue amid military tensions",
        "Some completely unrelated headline about nothing at all",
    )
    for headline in headlines:
        intel = classify_news(headline)
        ai = _ai(
            actual=intel.actual,
            forecast=intel.forecast,
            previous=intel.previous,
            affected_assets=["GOLD", "USD", "US500"],
            what_to_watch_ar="ترقب بيانات التضخم الأمريكية غدًا",
        )
        rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
        stray = _foreign_chars(rendered) - approved
        assert not stray, (headline, stray)


def test_rhythm_never_produces_double_blank_lines() -> None:
    for importance in (1, 2, 3, 5):
        for actual in (None, "3.1%"):
            ai = _ai(importance=importance, actual=actual, forecast=actual)
            intel = classify_news("US CPI YoY Actual 3.1% (Forecast 3.1%)")
            rendered = TelegramFormatter.format_premium_bilingual(_news(), ai, intel)
            assert "\n\n\n" not in rendered
            assert not rendered.endswith("\n")


def test_raw_english_initial_message_untouched() -> None:
    news = NewsEvent(
        source_message_id="x",
        source="rss",
        original_headline="Fed cuts rates",
    )
    raw = TelegramFormatter.format_raw_english(news)
    assert "📡" in raw and "⏳" in raw  # fast path unchanged by the redesign
