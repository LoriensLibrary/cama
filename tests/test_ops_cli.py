"""Smoke tests for the cama-ops CLI.

The CLI is small; the tests verify each subcommand round-trips
through the keys DB without raising and produces parseable JSON.
"""

from __future__ import annotations

import json

import pytest

from cama.ops.cli import main as ops_main


@pytest.fixture
def ops_env(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAMA_API_KEY_DB", str(tmp_path / "keys.db"))
    return capsys


def _run(argv, capsys) -> dict:
    rc = ops_main(argv)
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    return {"rc": rc, "out": data}


class TestOpsKeys:
    def test_create_returns_fingerprint_and_one_shot_plaintext(self, ops_env):
        r = _run(["keys", "create", "--dyad", "alpha", "--kind", "live"], ops_env)
        assert r["rc"] == 0
        assert r["out"]["ok"] is True
        assert r["out"]["fingerprint"]
        assert r["out"]["key"].startswith("cama_sk_live_")
        assert "shown ONCE" in r["out"]["note"]

    def test_list_returns_created_keys(self, ops_env):
        _run(["keys", "create", "--dyad", "alpha", "--kind", "live"], ops_env)
        _run(["keys", "create", "--dyad", "beta", "--kind", "dev"], ops_env)
        r = _run(["keys", "list"], ops_env)
        assert r["rc"] == 0
        assert r["out"]["count"] == 2
        dyads = sorted(k["dyad_id"] for k in r["out"]["keys"])
        assert dyads == ["alpha", "beta"]

    def test_list_filters_by_dyad(self, ops_env):
        _run(["keys", "create", "--dyad", "alpha", "--kind", "live"], ops_env)
        _run(["keys", "create", "--dyad", "beta", "--kind", "live"], ops_env)
        r = _run(["keys", "list", "--dyad", "alpha"], ops_env)
        assert r["out"]["count"] == 1
        assert r["out"]["keys"][0]["dyad_id"] == "alpha"

    def test_revoke_marks_key_revoked(self, ops_env):
        created = _run(
            ["keys", "create", "--dyad", "alpha", "--kind", "live"], ops_env
        )
        fp = created["out"]["fingerprint"]
        revoked = _run(["keys", "revoke", fp], ops_env)
        assert revoked["rc"] == 0
        assert revoked["out"]["ok"] is True

        # Listing now shows revoked_at populated
        listed = _run(["keys", "list"], ops_env)
        assert listed["out"]["keys"][0]["revoked_at"] is not None

    def test_revoke_unknown_returns_nonzero(self, ops_env):
        r = _run(["keys", "revoke", "abc123abc123"], ops_env)
        assert r["rc"] == 1
        assert r["out"]["ok"] is False


class TestOpsDyads:
    def test_dyads_list_groups_by_dyad(self, ops_env):
        _run(["keys", "create", "--dyad", "alpha", "--kind", "live"], ops_env)
        _run(["keys", "create", "--dyad", "alpha", "--kind", "dev"], ops_env)
        _run(["keys", "create", "--dyad", "beta", "--kind", "live"], ops_env)
        r = _run(["dyads", "list"], ops_env)
        assert r["out"]["count"] == 2
        dyads = {d["dyad_id"]: d["key_count"] for d in r["out"]["dyads"]}
        assert dyads == {"alpha": 2, "beta": 1}


class TestOpsAuditLog:
    def test_audit_log_returns_empty_when_no_requests(self, ops_env):
        r = _run(["audit-log"], ops_env)
        assert r["rc"] == 0
        assert r["out"]["count"] == 0
        assert r["out"]["audit_log"] == []
