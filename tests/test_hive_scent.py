"""Tests for the hive scent channel, cama/hive/cama_scent.py.

These cover the three properties the module claims and nothing else:
routing (a thread receives on the routes it activated), rectification with a
leak (weak off-route signal dies, strong off-route signal survives reduced,
no route can go permanently deaf), and the receipt gate (a conclusion with no
evidence cannot be published).

All fixtures use synthetic signals against a temp DB. Nothing here reads or
writes the live store.
"""

import pytest

from cama.hive import cama_hive, cama_scent


@pytest.fixture
def hive_db(tmp_path, monkeypatch):
    """Point the hive at a throwaway DB for the duration of one test.

    cama_hive.get_db() calls init_hive_tables() on connect, so the schema
    builds itself on first use.
    """
    db_file = tmp_path / "hive.db"
    monkeypatch.setattr(cama_hive, "DB_PATH", str(db_file))
    yield db_file


# ──────────────────────────────────────────────────────────────
# Rectifier, pure functions, no DB
# ──────────────────────────────────────────────────────────────

def test_on_route_signal_passes_at_full_gain():
    assert cama_scent.rectify(0.6, ["topic_cama"], ["topic_cama"]) == 0.6


def test_partial_overlap_scales_the_signal():
    # One of two routes matches, so gain is 0.5.
    score = cama_scent.rectify(0.6, ["topic_cama", "person_x"], ["topic_cama"])
    assert score == pytest.approx(0.3)


def test_weak_off_route_signal_is_zeroed():
    """The rectification itself: noise on the wrong route does not reach boot."""
    assert cama_scent.rectify(0.2, ["topic_other"], ["topic_cama"]) == 0.0


def test_strong_off_route_signal_leaks_through_reduced():
    """The leak term. A hard rectifier here would create dead routes."""
    score = cama_scent.rectify(0.6, ["topic_other"], ["topic_cama"])
    assert 0.0 < score < 0.6


def test_unrouted_legacy_pheromone_still_reaches_boot():
    """Every pheromone emitted before this module has no routes recorded.

    Unrouted means unclassified, not irrelevant, so it takes the leak path
    rather than being treated as a zero-overlap miss.
    """
    assert cama_scent.rectify(0.6, [], ["topic_cama"]) > 0.0
    assert cama_scent.rectify(0.1, [], ["topic_cama"]) == 0.0


def test_a_thread_with_no_active_routes_still_smells_something():
    """Boot can fail to activate any librarian. That must not deafen it."""
    assert cama_scent.rectify(0.6, ["topic_cama"], []) > 0.0


# ──────────────────────────────────────────────────────────────
# Receipt gate
# ──────────────────────────────────────────────────────────────

def test_finding_without_a_receipt_is_rejected(hive_db):
    out = cama_scent.emit_finding(signal="the memory system is unreachable", receipt="")
    assert out["status"] == "rejected"
    assert "receipt" in out["reason"]


def test_finding_with_whitespace_receipt_is_rejected(hive_db):
    out = cama_scent.emit_finding(signal="something broke", receipt="   ")
    assert out["status"] == "rejected"


def test_empty_signal_is_rejected(hive_db):
    out = cama_scent.emit_finding(signal="  ", receipt="cama_health returned 0 rows")
    assert out["status"] == "rejected"


def test_unknown_kind_is_rejected(hive_db):
    out = cama_scent.emit_finding(
        signal="x", receipt="y", kind="speculation"
    )
    assert out["status"] == "rejected"


def test_finding_with_a_receipt_is_published(hive_db):
    out = cama_scent.emit_finding(
        signal="second server answers, first returns not found",
        receipt="health check: server A nominal, server B 404 on every route",
        routes=["topic_infra"],
    )
    assert out["status"] == "emitted"
    assert out["pheromone_id"] is not None
    assert out["routes"] == ["topic_infra"]


# ──────────────────────────────────────────────────────────────
# Routing and consumption
# ──────────────────────────────────────────────────────────────

def test_read_scent_prefers_findings_on_the_threads_own_routes(hive_db):
    cama_scent.emit_finding(
        signal="on-route finding",
        receipt="count=1",
        routes=["topic_infra"],
        intensity=0.6,
    )
    cama_scent.emit_finding(
        signal="off-route finding",
        receipt="count=2",
        routes=["topic_gardening"],
        intensity=0.6,
    )
    smelled = cama_scent.read_scent(active_routes=["topic_infra"])
    signals = [s["signal"] for s in smelled]
    assert "on-route finding" in signals
    # Both may appear because of the leak, but the on-route one must rank first.
    assert smelled[0]["signal"] == "on-route finding"
    assert smelled[0]["salience"] > smelled[-1]["salience"]


def test_weak_off_route_finding_is_filtered_out_entirely(hive_db):
    cama_scent.emit_finding(
        signal="faint and irrelevant",
        receipt="count=0",
        routes=["topic_gardening"],
        intensity=0.2,
    )
    smelled = cama_scent.read_scent(active_routes=["topic_infra"])
    assert smelled == []


def test_reading_scent_increments_consumed_count(hive_db):
    """The instrumentation gap that let the channel sit dead for months.

    consumed_count existed in the schema since April and nothing ever wrote
    it, so 50 unread pheromones looked identical to 50 read ones.
    """
    emitted = cama_scent.emit_finding(
        signal="tracked finding",
        receipt="verified by two independent calls",
        routes=["topic_infra"],
    )
    pid = emitted["pheromone_id"]

    c = cama_hive.get_db()
    try:
        before = c.execute(
            "SELECT consumed_count FROM hive_pheromones WHERE id = ?", (pid,)
        ).fetchone()["consumed_count"]
    finally:
        c.close()
    assert before == 0

    cama_scent.read_scent(active_routes=["topic_infra"])

    c = cama_hive.get_db()
    try:
        after = c.execute(
            "SELECT consumed_count FROM hive_pheromones WHERE id = ?", (pid,)
        ).fetchone()["consumed_count"]
    finally:
        c.close()
    assert after == 1


def test_read_scent_carries_the_receipt_forward(hive_db):
    """A receiving thread must be able to check the finding, not just believe it."""
    cama_scent.emit_finding(
        signal="transport misconfigured",
        receipt="server A nominal, server B 404 on every route",
        routes=["topic_infra"],
    )
    smelled = cama_scent.read_scent(active_routes=["topic_infra"])
    assert smelled
    assert "404" in smelled[0]["receipt"]


def test_read_scent_is_empty_on_a_quiet_hive(hive_db):
    assert cama_scent.read_scent(active_routes=["topic_infra"]) == []
