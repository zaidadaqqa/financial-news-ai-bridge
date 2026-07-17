"""Six-hour digest window semantics: four fixed UTC windows per day,
start inclusive / end exclusive, timezone-aware everywhere, no overlap
and no gap. These invariants are the foundation of exactly-once digest
processing — a boundary bug here double-publishes or skips windows."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.digest.models import WINDOW_HOURS, DigestWindow, next_boundary


def _utc(hour: int, minute: int = 0, second: int = 0, microsecond: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, second, microsecond, tzinfo=UTC)


def test_latest_completed_at_each_exact_boundary() -> None:
    for boundary_hour in (0, 6, 12, 18):
        window = DigestWindow.latest_completed(_utc(boundary_hour))
        assert window.end == _utc(boundary_hour)
        assert window.start == _utc(boundary_hour) - timedelta(hours=WINDOW_HOURS)


def test_latest_completed_at_midnight_reaches_previous_day() -> None:
    window = DigestWindow.latest_completed(_utc(0))
    assert window.start == datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 16, 0, 0, tzinfo=UTC)


def test_one_microsecond_before_boundary_still_previous_window() -> None:
    window = DigestWindow.latest_completed(_utc(5, 59, 59, 999999))
    assert window.start == datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 16, 0, 0, tzinfo=UTC)


def test_mid_window_moment_maps_to_last_completed() -> None:
    window = DigestWindow.latest_completed(_utc(9, 30))
    assert window.start == _utc(0)
    assert window.end == _utc(6)


def test_non_utc_timezone_input_is_normalized() -> None:
    from datetime import timezone

    plus_three = timezone(timedelta(hours=3))
    # 08:30+03:00 == 05:30 UTC → completed window is the previous 18–24.
    window = DigestWindow.latest_completed(
        datetime(2026, 7, 16, 8, 30, tzinfo=plus_three)
    )
    assert window.end == datetime(2026, 7, 16, 0, 0, tzinfo=UTC)


def test_from_start_builds_six_hour_window() -> None:
    window = DigestWindow.from_start(_utc(12))
    assert window.end == _utc(18)


def test_naive_datetimes_rejected() -> None:
    naive = datetime(2026, 7, 16, 6, 0)
    with pytest.raises(ValueError):
        DigestWindow.latest_completed(naive)
    with pytest.raises(ValueError):
        DigestWindow.from_start(naive)


def test_next_boundary_strictly_future_at_exact_boundary() -> None:
    for boundary_hour in (0, 6, 12, 18):
        now = _utc(boundary_hour)
        assert next_boundary(now) == now + timedelta(hours=WINDOW_HOURS)
        assert next_boundary(now) > now


def test_full_day_windows_have_no_overlap_and_no_gap() -> None:
    moments = [_utc(h, 1) for h in (0, 6, 12, 18)]
    windows = [DigestWindow.latest_completed(m) for m in moments]
    for earlier, later in zip(windows, windows[1:], strict=False):
        assert earlier.end == later.start  # no gap, no overlap
    total = sum((w.end - w.start).total_seconds() for w in windows)
    assert total == 24 * 3600
