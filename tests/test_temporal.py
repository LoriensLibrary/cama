"""Contract tests for cama.temporal.cama_temporal.

This module ships zero tests in `tests/` before this file. The temporal
layer is non-trivial — it owns the *categorizer* (felt-time signals,
streamed baselines via Welford, probabilistic-OR signal stacking, the
N >= 5-sessions gate, weekend/sleep-window math) — and the load-bearing
properties (the gate especially) deserve regression tests so a future
refactor can't silently break "don't emit felt signals on empty data."

Tests are grouped:

  1. Pure helpers     — no DB, no time travel needed.
  2. Stateful API     — fresh tmp DB per test via the temporal_db fixture.
                        Monkey-patches cama.temporal.cama_temporal.DB_PATH
                        so the real ~/.cama/memory.db is never touched.

Anything stateful uses `set_timezone` / `session_start` / `mark_turn` /
`session_end` rather than poking the SQLite tables directly — these are
the contracts a future caller depends on, and they're what should keep
working.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone

import pytest

from cama.temporal import cama_temporal as t


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------
class TestParseIso:
    def test_returns_none_for_empty(self):
        assert t._parse_iso("") is None
        assert t._parse_iso(None) is None

    def test_handles_z_suffix(self):
        # Z is shorthand for UTC. Python <3.11 doesn't accept it directly;
        # the helper must translate it. This was the source of an earlier
        # bug fixed across cama_mcp.py and cama_sleep.py (per eval_system.py).
        dt = t._parse_iso("2026-05-21T12:34:56Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 21
        assert dt.hour == 12 and dt.minute == 34

    def test_handles_explicit_offset(self):
        dt = t._parse_iso("2026-05-21T12:34:56+00:00")
        assert dt is not None
        assert dt.utcoffset().total_seconds() == 0


class TestTodBand:
    @pytest.mark.parametrize(
        "hour,expected",
        [
            (0, "overnight"), (3, "overnight"), (4, "overnight"),
            (5, "early_morning"), (7, "early_morning"),
            (8, "morning"), (10, "morning"),
            (11, "midday"), (13, "midday"),
            (14, "afternoon"), (16, "afternoon"),
            (17, "evening"), (20, "evening"),
            (21, "late_night"), (23, "late_night"),
        ],
    )
    def test_classifies_band(self, hour, expected):
        assert t._tod_band(hour) == expected

    def test_returns_string(self):
        # All 24 hours produce a valid band.
        for h in range(24):
            assert isinstance(t._tod_band(h), str)


class TestSleepWindow:
    def _local(self, hour: int, minute: int = 0):
        # Build a tz-aware datetime at the given hour. The function only
        # reads hour + minute, so the date doesn't matter.
        return datetime(2026, 5, 21, hour, minute, tzinfo=timezone.utc)

    def test_inside_window_returns_zero(self):
        # SLEEP_WINDOW_LOCAL_HOURS = (23, 7). 23:30 and 02:00 are both in.
        assert t._hours_from_sleep_window(self._local(23, 30)) == 0.0
        assert t._hours_from_sleep_window(self._local(2)) == 0.0
        assert t._hours_from_sleep_window(self._local(6, 59)) == 0.0

    def test_outside_window_returns_distance_past_wake(self):
        # Wake edge is 7. 09:00 is 2 hours past wake.
        assert t._hours_from_sleep_window(self._local(9)) == 2.0
        # 15:00 is 8 hours past wake.
        assert t._hours_from_sleep_window(self._local(15)) == 8.0


class TestWelford:
    def test_single_point(self):
        m, m2, n = t._welford_update(0.0, 0.0, 0, 42.0)
        assert m == 42.0
        assert m2 == 0.0
        assert n == 1

    def test_matches_statistics_module(self):
        # The streaming math has to produce the same mean and variance
        # the stdlib computes from the full sample.
        sample = [3.0, 7.0, 11.0, 13.0, 23.0, 31.0, 35.0]
        m, m2, n = 0.0, 0.0, 0
        for x in sample:
            m, m2, n = t._welford_update(m, m2, n, x)
        assert n == len(sample)
        assert m == pytest.approx(statistics.mean(sample))
        assert t._welford_sd(m2, n) == pytest.approx(statistics.stdev(sample))

    def test_sd_zero_for_n_less_than_two(self):
        assert t._welford_sd(0.0, 0) == 0.0
        assert t._welford_sd(0.0, 1) == 0.0
        assert t._welford_sd(99.0, 1) == 0.0  # m2 ignored for n < 2


class TestLateHourLoad:
    def test_zero_when_no_history(self):
        assert t._late_hour_load([0] * 24, current_hour=3) == 0.0

    def test_zero_when_hour_at_or_above_uniform(self):
        # Hour 12 is "fair share" or more — no surprise.
        hist = [0] * 24
        hist[12] = 5  # only this hour, only 5 times — but it IS the only hour seen
        # prob = 5/5 = 1.0, way above 1/24 uniform => load 0.
        assert t._late_hour_load(hist, current_hour=12) == 0.0

    def test_high_when_rare_hour(self):
        # User who works 24 hours evenly with 100 total, except hour 3 = 0.
        hist = [100] * 24
        hist[3] = 0
        # prob = 0 -> load = 1.0
        assert t._late_hour_load(hist, current_hour=3) == 1.0


class TestStreakLoad:
    def test_gated_by_min_sessions(self):
        # Even at 30 consecutive days, if session_n < 5, signal is 0.
        assert t._streak_load(consecutive_days=30, session_n=4) == 0.0

    def test_zero_for_under_three_days(self):
        assert t._streak_load(consecutive_days=2, session_n=100) == 0.0

    def test_increases_with_streak(self):
        a = t._streak_load(5, session_n=100)
        b = t._streak_load(10, session_n=100)
        c = t._streak_load(20, session_n=100)
        assert 0.0 < a < b < c <= 1.0


class TestCompressionLoad:
    def test_zero_for_zero_or_one_session(self):
        assert t._compression_load(0) == 0.0
        assert t._compression_load(1) == 0.0

    def test_grows_monotonically(self):
        a = t._compression_load(2)
        b = t._compression_load(3)
        c = t._compression_load(5)
        assert 0.0 < a < b < c
        assert c < 1.0  # bounded


class TestDurationCreep:
    def test_gated_by_min_sessions(self):
        # Current is 5x mean — but n=4, so no signal.
        assert t._duration_creep_load(500.0, 100.0, 20.0, n=4) == 0.0

    def test_zero_below_1_5x_mean(self):
        assert t._duration_creep_load(120.0, 100.0, 20.0, n=10) == 0.0

    def test_saturates_at_3x_mean(self):
        assert t._duration_creep_load(300.0, 100.0, 20.0, n=10) == pytest.approx(1.0)
        assert t._duration_creep_load(500.0, 100.0, 20.0, n=10) == pytest.approx(1.0)


class TestWeekendCreep:
    def test_zero_when_not_weekend(self):
        assert t._weekend_creep_load(is_weekend=False, dow_hist=[0] * 7) == 0.0

    def test_zero_for_history_balanced_or_weekend_heavy(self):
        # User works 2/7 days on weekends = baseline. No load.
        hist = [10, 10, 10, 10, 10, 4, 6]  # weekend = 10/70 (14.3% - below baseline)
        # actually 10/70 < 2/7 = 28.6% baseline -> SOME load. fix the test:
        hist_balanced = [5, 5, 5, 5, 5, 5, 5]  # equal across days = weekend share 2/7
        assert t._weekend_creep_load(is_weekend=True, dow_hist=hist_balanced) == 0.0

    def test_high_when_weekends_rare(self):
        # Pure weekday worker: never touched a weekend.
        weekday_only = [10, 10, 10, 10, 10, 0, 0]
        load = t._weekend_creep_load(is_weekend=True, dow_hist=weekday_only)
        assert load == 1.0


class TestStack:
    def test_single_signal_returns_itself(self):
        assert t._stack(0.3) == pytest.approx(0.3)
        assert t._stack(0.0) == pytest.approx(0.0)
        assert t._stack(1.0) == pytest.approx(1.0)

    def test_two_independent_moderate_signals_compound(self):
        # Probabilistic OR: 0.5 OR 0.5 = 0.75 (not 1.0, not 0.5+0.5=1.0).
        # That's the categorizer's "moderate + moderate = strong" behavior.
        assert t._stack(0.5, 0.5) == pytest.approx(0.75)

    def test_output_bounded_to_unit_interval(self):
        # Even with crazy inputs, output stays in [0, 1] because _stack
        # clamps each signal first.
        assert 0.0 <= t._stack(2.0, 5.0, -1.0) <= 1.0


class TestSummaryLine:
    def test_silent_below_threshold(self):
        # stacked_pressure < STACKED_NOTE_THRESHOLD (0.6) returns None.
        assert (
            t._summary_line(
                tod_band="midday", is_weekend=False, consecutive_days=2,
                sessions_today=1, duration_ratio=None, stacked=0.2,
            )
            is None
        )

    def test_emits_above_threshold(self):
        msg = t._summary_line(
            tod_band="late_night", is_weekend=False, consecutive_days=8,
            sessions_today=3, duration_ratio=None, stacked=0.85,
        )
        assert isinstance(msg, str)
        # Should mention streak and session count.
        assert "8-day streak" in msg
        assert "3 sessions today" in msg
        assert "late night" in msg


# ---------------------------------------------------------------------------
# 2. Stateful API tests
# ---------------------------------------------------------------------------
@pytest.fixture
def temporal_db(tmp_path, monkeypatch):
    """Redirect the temporal module to a per-test SQLite file."""
    db_file = tmp_path / "temporal_test.db"
    monkeypatch.setattr(t, "DB_PATH", str(db_file))
    yield db_file


class TestInitSchema:
    def test_creates_singleton(self, temporal_db):
        t.init_schema()
        c = sqlite3.connect(str(temporal_db))
        row = c.execute(
            "SELECT timezone, total_sessions FROM temporal_state WHERE singleton=1"
        ).fetchone()
        c.close()
        assert row is not None
        assert row[0] == t.DEFAULT_TIMEZONE
        assert row[1] == 0

    def test_idempotent(self, temporal_db):
        t.init_schema()
        t.init_schema()  # second call must not raise or duplicate the singleton
        c = sqlite3.connect(str(temporal_db))
        count = c.execute("SELECT COUNT(*) FROM temporal_state").fetchone()[0]
        c.close()
        assert count == 1


class TestSetTimezone:
    def test_accepts_valid_iana(self, temporal_db):
        result = t.set_timezone("Europe/Paris")
        assert result["ok"] is True
        assert result["timezone"] == "Europe/Paris"
        # Verify persisted.
        assert t.get_state()["timezone"] == "Europe/Paris"

    def test_rejects_invalid(self, temporal_db):
        result = t.set_timezone("Not/A/Real/Zone")
        assert result["ok"] is False
        assert "invalid timezone" in result["error"]
        # Original default unchanged.
        assert t.get_state()["timezone"] == t.DEFAULT_TIMEZONE


class TestSessionLifecycle:
    def test_session_start_starts_first_session(self, temporal_db):
        result = t.session_start()
        assert result["started"] is True
        assert result["consecutive_days_active"] == 1
        # No prior session, no gap to report.
        assert result["gap_from_last_hours"] is None

    def test_mark_turn_increments(self, temporal_db):
        t.session_start()
        r1 = t.mark_turn()
        r2 = t.mark_turn()
        r3 = t.mark_turn()
        assert r1["turn_count"] == 1
        assert r2["turn_count"] == 2
        assert r3["turn_count"] == 3

    def test_mark_turn_noop_when_no_session(self, temporal_db):
        t.init_schema()
        result = t.mark_turn()
        assert result["ok"] is False
        assert "no active session" in result["reason"]

    def test_session_end_closes_and_increments_totals(self, temporal_db):
        t.session_start()
        t.mark_turn()
        result = t.session_end()
        assert result["closed"] is True
        assert result["total_sessions"] == 1
        # State should now have no active session.
        s = t.get_state()
        assert s["current_session_start_utc"] is None
        assert s["total_sessions"] == 1

    def test_session_end_noop_when_no_session(self, temporal_db):
        t.init_schema()
        result = t.session_end()
        assert result["ok"] is False
        assert "no active session" in result["reason"]

    def test_orphaned_session_is_closed_on_new_start(self, temporal_db):
        # Open a session and never explicitly close it.
        t.session_start()
        t.mark_turn()
        # Starting a new one should implicitly close the orphan.
        t.session_start()
        s = t.get_state()
        # Orphaned session counted in totals.
        assert s["total_sessions"] == 1
        # New session is active.
        assert s["current_session_start_utc"] is not None


class TestFeltSignalsAreSilentBeforeBaseline:
    """Load-bearing property: every felt signal must be 0 until n >= 5.
    This is the cardinal safety rule of the categorizer — don't emit
    felt-time pressure on noise."""

    def test_streak_load_silent(self):
        # Even at 30 consecutive days, n<5 keeps it silent.
        for n in range(5):
            assert t._streak_load(consecutive_days=30, session_n=n) == 0.0

    def test_duration_creep_silent(self):
        for n in range(5):
            assert t._duration_creep_load(
                current_minutes=500.0, mean_minutes=100.0, sd_minutes=20.0, n=n
            ) == 0.0
