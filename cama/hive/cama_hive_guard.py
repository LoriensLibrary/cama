"""Personal-Hive admission and containment boundary.

Local operator methods provision identity and publish the inherited compass.
HTTP workers only receive boot context, submit provisional findings, and report
alarms. A server HMAC attests lineage; it is not proof that content is true.
The signing key and database must be inaccessible to worker processes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class GuardDenied(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class HiveGuard:
    def __init__(self, db_path, signing_key: bytes):
        if len(signing_key) < 32:
            raise ValueError("Signing key must contain at least 32 bytes")
        self.db_path = str(db_path)
        self.key = signing_key
        with self.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS guard_hives (
                    hive TEXT PRIMARY KEY, queen TEXT NOT NULL, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS guard_compasses (
                    hive TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                    digest TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(hive, version));
                CREATE TABLE IF NOT EXISTS guard_agents (
                    id TEXT PRIMARY KEY, hive TEXT NOT NULL, parent TEXT,
                    role TEXT NOT NULL, claims TEXT NOT NULL, token_hash TEXT NOT NULL,
                    status TEXT NOT NULL, ack_version INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS guard_records (
                    id TEXT PRIMARY KEY, hive TEXT NOT NULL, agent TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, evidence TEXT NOT NULL,
                    version INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS guard_events (
                    id INTEGER PRIMARY KEY, hive TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT, reason TEXT, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS guard_record_hive ON guard_records(hive, created);
            """)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _event(self, c, hive, actor, action, target=None, reason=None):
        c.execute("INSERT INTO guard_events(hive,actor,action,target,reason,created) VALUES(?,?,?,?,?,?)",
                  (hive, actor, action, target, reason, time.time()))

    def create_hive(self, hive: str, queen: str, compass: dict):
        """Operator only. Compass contains approved rules/references, not raw archives."""
        if not hive.strip() or not queen.strip() or not isinstance(compass, dict) or not compass:
            raise ValueError("Hive, queen and nonempty compass required")
        body = canonical(compass)
        with self.db() as c:
            c.execute("INSERT INTO guard_hives VALUES(?,?,1)", (hive, queen))
            c.execute("INSERT INTO guard_compasses VALUES(?,1,?,?,?)",
                      (hive, body, hashlib.sha256(body.encode()).hexdigest(), time.time()))
            self._event(c, hive, "operator", "create_hive")

    def publish_compass(self, hive: str, compass: dict):
        """Operator only; immutable versions, no model-controlled promotion path."""
        if not isinstance(compass, dict) or not compass:
            raise ValueError("Nonempty compass required")
        body = canonical(compass)
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT version FROM guard_hives WHERE hive=?", (hive,)).fetchone()
            if not row:
                raise GuardDenied("Unknown hive")
            version = row[0] + 1
            c.execute("INSERT INTO guard_compasses VALUES(?,?,?,?,?)",
                      (hive, version, body, hashlib.sha256(body.encode()).hexdigest(), time.time()))
            c.execute("UPDATE guard_hives SET version=? WHERE hive=?", (version, hive))
            self._event(c, hive, "operator", "publish_compass", reason=str(version))
            return version

    def issue(self, hive: str, role="worker", parent=None, lifetime=3600):
        """Operator-only issuance. No endpoint allows workers to mint identities."""
        if role not in {"queen", "worker", "librarian"} or not 1 <= lifetime <= 86400:
            raise ValueError("Invalid role or lifetime (1..86400 seconds)")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            root = c.execute("SELECT * FROM guard_hives WHERE hive=?", (hive,)).fetchone()
            if not root:
                raise GuardDenied("Unknown hive")
            expiry = time.time() + lifetime
            if role != "queen":
                ancestor = self._active(c, parent)
                if ancestor["hive"] != hive or ancestor["role"] != "queen":
                    raise GuardDenied("Agents require a same-hive queen parent")
                expiry = min(expiry, json.loads(ancestor["claims"])["expires"])
            elif parent is not None:
                raise GuardDenied("Queen cannot have a parent")
            claims = {"agent": uuid.uuid4().hex, "hive": hive, "queen": root["queen"],
                      "parent": parent, "role": role, "born_version": root["version"],
                      "expires": expiry, "nonce": secrets.token_hex(16)}
            body = canonical(claims)
            payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
            signature = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            token = "hg1." + payload + "." + signature
            c.execute("INSERT INTO guard_agents VALUES(?,?,?,?,?,?, 'active',0)",
                      (claims["agent"], hive, parent, role, body, hashlib.sha256(token.encode()).hexdigest()))
            self._event(c, hive, "operator", "issue", claims["agent"])
            return {"agent": claims["agent"], "token": token, "lineage": claims}

    def _active(self, c, agent):
        row = c.execute("SELECT * FROM guard_agents WHERE id=?", (agent,)).fetchone()
        if not row or row["status"] != "active":
            raise GuardDenied("Agent unavailable")
        if json.loads(row["claims"])["expires"] <= time.time():
            raise GuardDenied("Agent expired")
        if row["parent"]:
            self._active(c, row["parent"])
        return row

    def authenticate(self, token):
        try:
            if len(token) > 4096:
                raise ValueError()
            prefix, payload, signature = token.split(".")
            expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            if prefix != "hg1" or not hmac.compare_digest(signature, expected):
                raise ValueError()
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            with self.db() as c:
                row = self._active(c, claims["agent"])
                if not hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
                    raise ValueError()
                if row["claims"] != canonical(claims):
                    raise ValueError()
            return claims
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise GuardDenied("Invalid credential") from exc

    def boot(self, agent):
        with self.db() as c:
            row = self._active(c, agent)
            compass = c.execute("SELECT p.* FROM guard_compasses p JOIN guard_hives h "
                                "ON p.hive=h.hive AND p.version=h.version WHERE h.hive=?",
                                (row["hive"],)).fetchone()
            return {"lineage": json.loads(row["claims"]), "compass": json.loads(compass["body"]),
                    "version": compass["version"], "digest": compass["digest"],
                    "acknowledged": row["ack_version"] == compass["version"]}

    def acknowledge(self, agent, version, digest):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self._active(c, agent)
            current = c.execute("SELECT p.* FROM guard_compasses p JOIN guard_hives h "
                                "ON p.hive=h.hive AND p.version=h.version WHERE h.hive=?",
                                (row["hive"],)).fetchone()
            if version != current["version"] or digest != current["digest"]:
                raise GuardDenied("Compass changed; boot again")
            c.execute("UPDATE guard_agents SET ack_version=? WHERE id=?", (version, agent))
            self._event(c, row["hive"], agent, "ack_compass", reason=str(version))

    def submit(self, agent, kind, body, evidence):
        if kind not in {"finding", "alarm"} or not body.strip() or len(body) > 4000:
            raise ValueError("Invalid record")
        if not evidence or len(evidence) > 10 or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in evidence):
            raise ValueError("Require 1..10 bounded evidence references")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self._active(c, agent)
            version = c.execute("SELECT version FROM guard_hives WHERE hive=?", (row["hive"],)).fetchone()[0]
            if row["ack_version"] != version:
                raise GuardDenied("Current compass acknowledgement required")
            record = uuid.uuid4().hex
            c.execute("INSERT INTO guard_records VALUES(?,?,?,?,?,?,?,?)",
                      (record, row["hive"], agent, kind, body, canonical(evidence), version, time.time()))
            self._event(c, row["hive"], agent, "submit_" + kind, record)
            return {"id": record, "status": "provisional", "automatic_action": False}

    def records(self, agent, limit=50):
        with self.db() as c:
            row = self._active(c, agent)
            if row["role"] not in {"queen", "librarian"}:
                raise GuardDenied("Reviewer role required")
            rows = c.execute("SELECT r.*, a.status AS author_status FROM guard_records r "
                             "JOIN guard_agents a ON r.agent=a.id WHERE r.hive=? ORDER BY r.created DESC LIMIT ?",
                             (row["hive"], max(1, min(int(limit), 100)))).fetchall()
            return [dict(r) | {"status": "provisional", "trusted_instruction": False} for r in rows]

    def quarantine(self, actor, target, alarm_id):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            reviewer = self._active(c, actor)
            worker = self._active(c, target)
            alarm = c.execute("SELECT * FROM guard_records WHERE id=? AND kind='alarm'", (alarm_id,)).fetchone()
            if (reviewer["role"] not in {"queen", "librarian"} or worker["role"] != "worker"
                    or reviewer["hive"] != worker["hive"] or not alarm or alarm["hive"] != reviewer["hive"]):
                raise GuardDenied("Same-hive reviewer, worker target and alarm required")
            version = c.execute("SELECT version FROM guard_hives WHERE hive=?", (reviewer["hive"],)).fetchone()[0]
            if reviewer["ack_version"] != version:
                raise GuardDenied("Current compass acknowledgement required")
            c.execute("UPDATE guard_agents SET status='quarantined' WHERE id=?", (target,))
            self._event(c, reviewer["hive"], actor, "quarantine", target, alarm_id)

    def revoke(self, agent, reason):
        """Operator only. Revoking a queen also blocks her descendants."""
        if not reason.strip():
            raise ValueError("Reason required")
        with self.db() as c:
            row = c.execute("SELECT hive FROM guard_agents WHERE id=?", (agent,)).fetchone()
            if not row:
                raise GuardDenied("Unknown agent")
            c.execute("UPDATE guard_agents SET status='revoked' WHERE id=?", (agent,))
            self._event(c, row[0], "operator", "revoke", agent, reason)


def from_directory(directory):
    """Open provisioned state, never create a signing key on an HTTP request."""
    directory = Path(directory)
    return HiveGuard(directory / "guard.db", (directory / "signing.key").read_bytes())
