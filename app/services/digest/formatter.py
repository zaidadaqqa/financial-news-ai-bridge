"""Deterministic Arabic renderer for the six-hour pinned digest.

Pure functions, no I/O, no AI call: every Arabic string rendered here is a
validated per-item field (``translated_headline``/``summary_ar``) that
already passed number preservation and the Arabic-ratio gate at publish
time. Layout follows the separator-free Editorial DNA (NEWSROOM_DNA.md
§13) and Agent 2's digest design: hierarchy from blank lines and one
leading semantic icon per entry — no drawn rules, no emoji chains.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

from app.services.digest.models import DigestEntry, DigestWindow
from app.services.formatting.telegram_formatter import CATEGORY_ICONS

# Telegram's API maximum is 4096 chars; the digest keeps a deliberate
# safety margin and prefers one comfortable mobile screen over maximum
# density (a real busy-window render is ~1,000 chars).
SOFT_LIMIT = 2500
HARD_LIMIT = 3500

# Minimum entries preserved while trimming whole entries for length.
_MIN_ENTRIES_WHEN_TRIMMING = 3

_LRI = "\u2066"  # LEFT-TO-RIGHT ISOLATE — always paired with...
_PDI = "\u2069"  # ...POP DIRECTIONAL ISOLATE (balanced by construction)

# Explicit bidi embedding/override/isolate controls (U+202A–U+202E,
# U+2066–U+2069) are stripped from all database text: the digest adds its
# own balanced isolates only around times it formats itself, so stored
# text can never unbalance or spoof the message's direction handling.
_BIDI_CONTROLS = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
_WHITESPACE = re.compile(r"\s+")

_HEADER = "📌 <b>ملخص أهم التطورات — آخر 6 ساعات</b>"
_QUIET_TEXT = (
    "لم تُسجَّل خلال هذه الفترة تطورات مالية عالية الأهمية "
    "تستدعي إضافتها إلى الملخص."
)
_FALLBACK_ICON = "📰"

_ARABIC_MONTHS = {
    1: "يناير",
    2: "فبراير",
    3: "مارس",
    4: "أبريل",
    5: "مايو",
    6: "يونيو",
    7: "يوليو",
    8: "أغسطس",
    9: "سبتمبر",
    10: "أكتوبر",
    11: "نوفمبر",
    12: "ديسمبر",
}


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _clean(text: str) -> str:
    """Neutralize bidi controls and collapse text to one escaped line."""
    text = _BIDI_CONTROLS.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return html.escape(text)


def _isolated_time(text: str) -> str:
    return f"{_LRI}{text}{_PDI}"


def _time_line(window: DigestWindow) -> str:
    start = _aware_utc(window.start, "window.start")
    end = _aware_utc(window.end, "window.end")
    time_range = _isolated_time(f"{start:%H:%M} – {end:%H:%M} UTC")
    date_ar = f"{start.day} {_ARABIC_MONTHS[start.month]}"
    return f"🕒 {time_range} · {date_ar}"


def _footer(updated_at: datetime) -> str:
    updated = _aware_utc(updated_at, "updated_at")
    return f"آخر تحديث: {_isolated_time(f'{updated:%H:%M} UTC')} • المصدر: F.J."


def _entry_icon(entry: DigestEntry) -> str:
    if entry.is_breaking:
        return "🚨"
    return CATEGORY_ICONS.get(entry.category or "", _FALLBACK_ICON)


def _entry_block(entry: DigestEntry, with_summary: bool) -> str:
    lines = [f"{_entry_icon(entry)} <b>{_clean(entry.headline_ar)}</b>"]
    if with_summary and entry.summary_ar:
        summary = _clean(entry.summary_ar)
        if summary:
            lines.append(summary)
    return "\n".join(lines)


def _compose(
    entries: list[DigestEntry],
    summary_flags: list[bool],
    window: DigestWindow,
    updated_at: datetime,
) -> str:
    blocks = [f"{_HEADER}\n{_time_line(window)}"]
    blocks.extend(
        _entry_block(entry, flag)
        for entry, flag in zip(entries, summary_flags, strict=True)
    )
    blocks.append(_footer(updated_at))
    return "\n\n".join(blocks)


def render_digest(
    entries: list[DigestEntry], window: DigestWindow, updated_at: datetime
) -> str:
    """Render ranked entries (highest first) into final Telegram HTML.

    Length trimming is rank-preserving and never cuts inside an entry,
    a number, or an HTML tag — only whole summary lines, then whole
    lowest-ranked entries, are dropped:
    1. over SOFT_LIMIT → drop summary lines, lowest-ranked entry first;
    2. over HARD_LIMIT → drop whole entries from the bottom, never
       going below three;
    3. safety net: with three entries left, drop any remaining
       summaries (bounded input sizes make this unreachable in
       practice, but the loop provably terminates).
    """
    if not entries:
        return render_quiet_digest(window, updated_at)

    kept = list(entries)
    summary_flags = [entry.summary_ar is not None for entry in kept]

    text = _compose(kept, summary_flags, window, updated_at)

    for i in reversed(range(len(kept))):
        if len(text) <= SOFT_LIMIT:
            break
        if summary_flags[i]:
            summary_flags[i] = False
            text = _compose(kept, summary_flags, window, updated_at)

    while len(text) > HARD_LIMIT and len(kept) > _MIN_ENTRIES_WHEN_TRIMMING:
        kept.pop()
        summary_flags.pop()
        text = _compose(kept, summary_flags, window, updated_at)

    if len(text) > HARD_LIMIT and any(summary_flags):
        summary_flags = [False] * len(kept)
        text = _compose(kept, summary_flags, window, updated_at)

    return text


def render_quiet_digest(window: DigestWindow, updated_at: datetime) -> str:
    """Honest quiet-period digest — no invented stories, no filler."""
    blocks = [
        f"{_HEADER}\n{_time_line(window)}",
        _QUIET_TEXT,
        _footer(updated_at),
    ]
    return "\n\n".join(blocks)
