import sqlite3

import pytest

from cama.core import retrieval_feedback as fb
from cama.ops import relabel_assistant_teachings as relabel


def test_feedback_does_not_commit_caller_and_partial_batch_rolls_back(fresh_db):
    c, _ = fresh_db
    fb.ensure_table(c)
    c.execute("CREATE TABLE marker(value INTEGER)")
    c.commit()
    c.execute("INSERT INTO marker VALUES(1)")
    fb.record(c, [1], "shown", "query")
    c.rollback()
    assert c.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0] == 0
    c.execute("CREATE TRIGGER fail_feedback BEFORE INSERT ON retrieval_feedback WHEN NEW.memory_id=2 BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        fb.record(c, [1, 2], "shown")
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0] == 0


def test_feedback_summary_is_read_only_and_excludes_fixture_only_ids(fresh_db):
    c, _ = fresh_db
    c.execute("DROP TABLE IF EXISTS retrieval_feedback")
    c.commit()
    assert fb.summary(c)["shown"] == 0
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='retrieval_feedback'").fetchone()
    fb.record(c, [900], "shown", "__fixture__")
    fb.record(c, [900], "used", "__fixture__")
    c.commit()
    result = fb.summary(c)
    assert result["shown"] == result["used"] == result["distinct_shown"] == result["distinct_shown_then_used"] == 0


def test_relabel_backup_includes_wal_with_old_reader_and_blocks_other_writers(fresh_db, tmp_path, monkeypatch):
    c, m = fresh_db
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    reader = sqlite3.connect(m.DB_PATH)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM memories").fetchone()
    c.execute("INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,created_at,updated_at) VALUES('synthetic','exchange','teaching','durable','aelen','2026-01-01','2026-01-01')")
    c.commit()
    original_hash = relabel._sha256

    def check_locked(path):
        competitor = sqlite3.connect(m.DB_PATH, timeout=0.01)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
        finally:
            competitor.close()
        return original_hash(path)

    monkeypatch.setattr(relabel, "_sha256", check_locked)
    try:
        out = relabel.apply(m.DB_PATH, "aelen", "exchange", tmp_path / "backup", "synthetic test", expected=1)
        assert out["changed"] == 1
        with sqlite3.connect(out["backup"]) as backup:
            assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT source_type FROM memories WHERE proposed_by='aelen'").fetchone()[0] == "teaching"
    finally:
        reader.close()


def test_protected_sections_cannot_displace_primary_instructions():
    import json

    from cama.core.boot_payload import budget_boot
    data = {"instruction": "Keep provenance separate.",
            "identity": {"teaching": "A user correction."},
            **{key: "x" * 1400 for key in ("aelen_state", "hive", "brain_layers", "scent")}}
    result = budget_boot(data, max_chars=4000)
    assert len(result) <= 4000
    decoded = json.loads(result)
    assert decoded["instruction"] == data["instruction"]
    assert decoded["identity"] == data["identity"]
