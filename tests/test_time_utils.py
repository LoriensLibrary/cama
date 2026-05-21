"""Tests for cama.core.time_utils.

The helpers are tiny — but they're used by 28 modules now, so it's worth
a unit test that nails down the contract: timezone-aware, ISO-8601, UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cama.core.time_utils import now_iso, now_utc


def test_now_utc_returns_timezone_aware_datetime():
    t = now_utc()
    assert isinstance(t, datetime)
    assert t.tzinfo is not None
    # UTC offset is exactly zero — not just "any timezone".
    assert t.utcoffset() == timedelta(0)


def test_now_utc_is_recent():
    t = now_utc()
    delta = abs((datetime.now(timezone.utc) - t).total_seconds())
    # Generous bound; the call itself takes microseconds.
    assert delta < 1.0


def test_now_iso_is_string():
    s = now_iso()
    assert isinstance(s, str)


def test_now_iso_is_parseable_back_to_utc():
    s = now_iso()
    parsed = datetime.fromisoformat(s)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_now_iso_contains_offset_suffix():
    """ISO-8601 string from a UTC-aware datetime should carry +00:00."""
    s = now_iso()
    assert s.endswith("+00:00")


def test_now_iso_advances():
    """Two consecutive calls must produce monotonically non-decreasing strings."""
    a = now_iso()
    b = now_iso()
    assert b >= a
