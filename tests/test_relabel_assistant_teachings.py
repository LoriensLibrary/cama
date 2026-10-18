"""Relabel of assistant-authored 'teaching' rows (review finding A-3, benchmark 3a). Temp stores only."""
import json
import sqlite3

import pytest

from cama.core import memory_guards
from cama.ops import relabel_assistant_teachings as relabel


def _seed(c):
    def add(text, source, who, mtype="experience", core=0):
        return c.execute(
            "INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,consent_level,is_core,"
            "trust_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1.0,?,?)",
            (text, mtype, source, "durable", who, "low", core, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        ).lastrowid
    ids = {
        "aelen1": add("I am steady today", "teaching", "aelen", "identity", 1),
        "aelen2": add("let us build the spec first", "teaching", "aelen", "building", 0),
        "user": add("ship it on Friday", "teaching", "user", "teaching", 0),
        "aelen_inf": add("she prefers mornings", "inference", "aelen", "experience", 0),
    }
    c.commit()
    return ids


def test_dry_run_reports_and_writes_nothing(fresh_db, tmp_path, capsys):
    c, m = fresh_db
    ids = _seed(c)
    before = c.execute("SELECT id, source_type, trust_score FROM memories ORDER BY id").fetchall()
    assert relabel.main([m.DB_PATH]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["rows"] == 2 and out["is_core"] == 1
    assert out["id_range"] == [ids["aelen1"], ids["aelen2"]]
    assert c.execute("SELECT id, source_type, trust_score FROM memories ORDER BY id").fetchall() == before
    assert not [p for p in tmp_path.rglob("*") if "bak-" in p.name or p.suffix == ".csv"]


def test_apply_relabels_only_the_assistant_teachings_with_backup_evidence_and_audit(fresh_db, tmp_path):
    c, m = fresh_db
    ids = _seed(c)
    memory_guards.install(c)
    out = relabel.apply(m.DB_PATH, "aelen", "exchange", tmp_path / "bak", "Angela, test run", expected=2)
    assert out["changed"] == 2 and out["remaining"] == 0 and out["quick_check"] == "ok"
    rows = {r["id"]: r for r in c.execute("SELECT id, source_type, proposed_by, memory_type, status, is_core, trust_score, trust_reason FROM memories")}
    for key in ("aelen1", "aelen2"):
        r = rows[ids[key]]
        assert (r["source_type"], r["trust_score"], r["trust_reason"]) == ("exchange", 0.8, "assistant_exchange")
        assert r["proposed_by"] == "aelen" and r["status"] == "durable"
    assert rows[ids["aelen1"]]["is_core"] == 1 and rows[ids["aelen1"]]["memory_type"] == "identity"
    assert rows[ids["user"]]["source_type"] == "teaching" and rows[ids["user"]]["trust_score"] == 1.0
    assert rows[ids["aelen_inf"]]["source_type"] == "inference"
    log = c.execute("SELECT memory_id, field, old_value, new_value, note FROM memory_log ORDER BY memory_id").fetchall()
    assert [(r["memory_id"], r["field"], r["old_value"], r["new_value"]) for r in log] == [
        (ids["aelen1"], "source_type", "teaching", "exchange"), (ids["aelen2"], "source_type", "teaching", "exchange")]
    assert "authorized by Angela, test run" in log[0]["note"]
    assert "proposed_by='user'" not in log[0]["note"]
    assert c.execute("SELECT COUNT(*) FROM memories WHERE proposed_by='user'").fetchone()[0] == 1  # no new user row
    bak = list((tmp_path / "bak").glob("*.bak-*"))
    ev = list((tmp_path / "bak").glob("relabel_aelen_*.csv"))
    assert len(bak) == 1 and len(ev) == 1
    restored = sqlite3.connect(bak[0])
    assert restored.execute("SELECT COUNT(*) FROM memories WHERE source_type='teaching' AND proposed_by='aelen'").fetchone()[0] == 2
    assert ev[0].read_text(encoding="utf-8").count("\n") == 3  # header + 2 rows


def test_apply_refuses_on_count_mismatch_and_changes_nothing(fresh_db, tmp_path):
    c, m = fresh_db
    _seed(c)
    with pytest.raises(RuntimeError):
        relabel.apply(m.DB_PATH, "aelen", "exchange", tmp_path / "bak", "test", expected=2790)
    assert c.execute("SELECT COUNT(*) FROM memories WHERE source_type='teaching' AND proposed_by='aelen'").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='memory_log'").fetchone()[0] == 0


def test_apply_requires_an_authorizer(fresh_db, tmp_path):
    c, m = fresh_db
    _seed(c)
    with pytest.raises(ValueError):
        relabel.apply(m.DB_PATH, "aelen", "exchange", tmp_path / "bak", "   ")
