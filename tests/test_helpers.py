"""Tests for pure-function helpers in cama_mcp.

These functions don't touch the DB but encode small invariants (timestamp
shape, recency decay, status weighting) that the README relies on. Easy
wins for regression coverage.
"""

import re
from datetime import datetime, timedelta, timezone


def test_now_returns_iso_utc(fresh_db):
    _, cama_mcp = fresh_db
    s = cama_mcp._now()
    # ISO-8601-ish, with a timezone suffix
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s)
    assert s.endswith("+00:00") or s.endswith("Z")


def test_parse_t_round_trips_now(fresh_db):
    _, cama_mcp = fresh_db
    s = cama_mcp._now()
    parsed = cama_mcp._parse_t(s)
    assert parsed is not None
    # Within a second of "now" (allowing for clock granularity)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5


def test_recency_decays_over_time(fresh_db):
    """_recency should return ~1.0 for now and trend toward 0 for old timestamps."""
    _, cama_mcp = fresh_db
    now = datetime.now(timezone.utc).isoformat()
    long_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    r_now = cama_mcp._recency(now)
    r_old = cama_mcp._recency(long_ago)

    assert 0.0 <= r_old < r_now <= 1.0
    assert r_now > 0.9  # very recent stays close to 1
    assert r_old < 0.1  # a year old has decayed substantially


def test_is_neg_flags_negative_valence(fresh_db):
    """The anti-spiral trigger fires on strongly negative affect."""
    _, cama_mcp = fresh_db
    negative = {"valence": -0.8, "arousal": 0.7}
    neutral = {"valence": 0.0, "arousal": 0.3}
    positive = {"valence": 0.6, "arousal": 0.4}

    assert cama_mcp._is_neg(negative) is True
    assert cama_mcp._is_neg(neutral) is False
    assert cama_mcp._is_neg(positive) is False


def test_status_weight_unknown_status_falls_back_safely(fresh_db):
    """Unknown statuses should not crash. They get a middling weight."""
    _, cama_mcp = fresh_db
    w = cama_mcp._status_weight("something-unexpected")
    assert 0.0 <= w <= 1.0
