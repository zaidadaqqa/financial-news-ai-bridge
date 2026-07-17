"""Digest renderer regressions: separator-free DNA structure, HTML and
bidi safety, frozen emoji discipline, rank-preserving length trimming,
and verbatim number preservation. The renderer is deterministic — same
entries in, same bytes out."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.services.digest.formatter import (
    HARD_LIMIT,
    render_digest,
    render_quiet_digest,
)
from app.services.digest.models import DigestEntry, DigestWindow
from app.services.formatting.telegram_formatter import APPROVED_EMOJIS

WINDOW = DigestWindow.from_start(datetime(2026, 7, 16, 6, 0, tzinfo=UTC))
UPDATED_AT = datetime(2026, 7, 16, 12, 0, 30, tzinfo=UTC)

_LRI = "⁦"
_PDI = "⁩"

# Mirrors tests/test_editorial_dna.py: characters legitimately present
# besides emojis — ASCII, Arabic blocks, and structural punctuation.
_TEXT_RE = re.compile("[\n\t -~" "؀-ۿݐ-ݿࢠ-ࣿ" "ﭐ-﷿ﹰ-﻿" "·•«»…–—‏]")


def _entry(
    news_id: str = "n-1",
    *,
    headline: str = "الأسواق تترقب قرار الفائدة الأميركية",
    summary: str | None = None,
    category: str | None = "central_bank",
    importance: int = 4,
    breaking: bool = False,
) -> DigestEntry:
    return DigestEntry(
        news_id=news_id,
        story_id=None,
        category=category,
        importance=importance,
        headline_ar=headline,
        summary_ar=summary,
        has_data=False,
        is_breaking=breaking,
        created_at=WINDOW.start + timedelta(minutes=5),
    )


def test_structure_header_time_entries_footer_in_order() -> None:
    rendered = render_digest(
        [_entry("n-1"), _entry("n-2", category="geopolitical")], WINDOW, UPDATED_AT
    )
    blocks = rendered.split("\n\n")
    assert blocks[0].startswith("📌 <b>")
    assert "\n🕒 " in blocks[0]
    assert blocks[-1].startswith("آخر تحديث:")
    assert blocks[-1].endswith("المصدر: F.J.")
    assert "\n\n\n" not in rendered
    assert "06:00" in rendered and "12:00" in rendered  # window range
    assert "─" not in rendered and "· · ·" not in rendered  # separator-free


def test_dynamic_text_html_escaped() -> None:
    rendered = render_digest(
        [_entry(headline='خبر <b> عاجل & "هام"', summary=None)], WINDOW, UPDATED_AT
    )
    assert "&lt;b&gt;" in rendered
    assert "&amp;" in rendered
    # Only the formatter's own <b> tags remain (header + one entry), balanced.
    assert rendered.count("<b>") == rendered.count("</b>") == 2


def test_bidi_controls_stripped_and_isolates_balanced() -> None:
    hostile = "تقرير‮مقلوب⁦غير مغلق"
    rendered = render_digest([_entry(headline=hostile)], WINDOW, UPDATED_AT)
    assert "‮" not in rendered
    assert rendered.count(_LRI) == rendered.count(_PDI) == 2  # time + footer only


def test_newlines_in_stored_text_collapsed() -> None:
    rendered = render_digest(
        [_entry(headline="سطر أول\nسطر ثان\r\nثالث")], WINDOW, UPDATED_AT
    )
    assert "سطر أول سطر ثان ثالث" in rendered
    assert "\n\n\n" not in rendered


def test_one_leading_icon_per_entry_and_siren_for_breaking() -> None:
    rendered = render_digest(
        [
            _entry("n-1", breaking=True, category="geopolitical"),
            _entry("n-2", category="economic_data"),
            _entry("n-3", category=None),
        ],
        WINDOW,
        UPDATED_AT,
    )
    entry_blocks = rendered.split("\n\n")[1:-1]
    assert entry_blocks[0].startswith("🚨 <b>")
    assert entry_blocks[1].startswith("📊 <b>")
    assert entry_blocks[2].startswith("📰 <b>")
    for block in entry_blocks:
        assert "🚨🚨" not in block  # no chains


def test_every_rendered_char_is_text_or_registry_emoji() -> None:
    rendered = render_digest(
        [
            _entry("n-1", breaking=True, summary="تفاصيل إضافية موثقة للحدث الجاري"),
            _entry("n-2", category="commodities"),
            _entry("n-3", category="crypto"),
        ],
        WINDOW,
        UPDATED_AT,
    )
    approved: set[str] = {_LRI, _PDI}
    for emoji in APPROVED_EMOJIS:
        approved.update(emoji)
    foreign = {ch for ch in rendered if not _TEXT_RE.match(ch)}
    assert foreign <= approved, f"unapproved characters rendered: {foreign - approved}"


def test_summaries_dropped_before_entries_when_over_soft_limit() -> None:
    long_summary = "تحليل مطول جدا " * 30  # ~450 chars each
    entries = [
        _entry(f"n-{i}", summary=long_summary, category="geopolitical")
        for i in range(7)
    ]
    rendered = render_digest(entries, WINDOW, UPDATED_AT)
    assert len(rendered) <= HARD_LIMIT
    # All seven headlines survive; enough summaries are dropped instead.
    assert rendered.count("<b>") == 7 + 1  # 7 entries + header


def test_whole_entries_dropped_only_after_summaries_with_floor_three() -> None:
    huge_headline = "عنوان استثنائي بالغ الطول للاختبار " * 20  # ~700 chars
    entries = [_entry(f"n-{i}", headline=huge_headline) for i in range(10)]
    rendered = render_digest(entries, WINDOW, UPDATED_AT)
    assert len(rendered) <= HARD_LIMIT
    kept_entries = rendered.count("<b>") - 1
    assert kept_entries >= 3
    assert rendered.count("<b>") == rendered.count("</b>")  # never mid-tag
    assert rendered.rstrip().endswith("F.J.")


def test_numbers_preserved_verbatim() -> None:
    headline = "التضخم عند 2,090 نقطة والفائدة بين 5.25%-5.50% والعجز -10.4 مليار"
    rendered = render_digest([_entry(headline=headline)], WINDOW, UPDATED_AT)
    assert "2,090" in rendered
    assert "5.25%-5.50%" in rendered
    assert "-10.4" in rendered


def test_quiet_render_honest_and_minimal() -> None:
    rendered = render_quiet_digest(WINDOW, UPDATED_AT)
    assert "لم تُسجَّل" in rendered
    assert "📌" in rendered and "🕒" in rendered
    assert rendered.count("<b>") == 1  # header only — no invented entries
    assert "\n\n\n" not in rendered
    assert rendered.rstrip().endswith("F.J.")


def test_empty_entry_list_renders_quiet_digest() -> None:
    assert render_digest([], WINDOW, UPDATED_AT) == render_quiet_digest(
        WINDOW, UPDATED_AT
    )


def test_naive_updated_at_rejected() -> None:
    with pytest.raises(ValueError):
        render_digest([_entry()], WINDOW, datetime(2026, 7, 16, 12, 0))


def test_render_is_deterministic() -> None:
    entries = [_entry("n-1"), _entry("n-2", category="forex")]
    assert render_digest(entries, WINDOW, UPDATED_AT) == render_digest(
        entries, WINDOW, UPDATED_AT
    )
