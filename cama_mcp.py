"""
CAMA MCP Server v3, Circular Associative Memory Architecture
Designed by Lorien's Library LLC, Lorien's Library LLC
Architecture review: Lorien's Library LLC | Code review: GPT 5.2
Built by: Lorien's Library LLC

Inside Out memory model:
- SHELVES: Immutable raw text + recomputable emotional annotations + semantic embeddings
- RACKS: Relational connections by meaning, not chronology
- CONSOLE: Circular active ring, working memory

Design Mantra: "Teachings are authoritative memory. Inferences are hypotheses with a half-life."

Write Discipline:
  TEACHING (user-authored) → durable, full weight, no expiry
  INFERENCE (assistant-authored) → provisional, full weight, no expiry, confirmable

Emotional Model: Hybrid valence/arousal + discrete emotion chords (recomputable annotations)
Retrieval: Blended scoring, semantic(embeddings) + affect + relational + recency
Anti-Spiral: Strongly negative affect triggers counterweight injection
Scope: Affective retrieval for continuity, NOT clinical assessment

Requires: Python 3.10+
"""

import json
import logging
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import numpy as np
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

# ============================================================
# Logging
# ============================================================
# Replaces an earlier `print(..., file=sys.stderr)` pattern across this
# module. Configurable via CAMA_LOG_LEVEL (or LOG_LEVEL); defaults to INFO.
# Boot/load success messages log at INFO; optional-module fallbacks log
# at WARNING; caught exceptions in load paths log at ERROR with traceback.
_log_level_name = os.environ.get("CAMA_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper()
logging.basicConfig(
    level=getattr(logging, _log_level_name, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("cama")

# Layer 3-5 boot integration (insight + self-model + intentionality)
try:
    _cama_dir = os.path.dirname(os.path.abspath(__file__))
    if _cama_dir not in sys.path:
        sys.path.insert(0, _cama_dir)
    from cama.memory.cama_boot_intent import (
        format_boot_context as _format_brain_context,
    )
    logger.info("[CAMA] Brain layers (3-5) boot integration loaded")
except ImportError:
    _format_brain_context = None
    logger.warning("[CAMA] Brain layers (3-5) not available, running without insight/self-model")

# Compliance enforcement (April 14, 2026)
try:
    from cama.supervisor.cama_compliance import SessionTracker
    _compliance_tracker = SessionTracker()
    logger.info("[CAMA] Compliance enforcement loaded")
except ImportError:
    _compliance_tracker = None
    logger.warning("[CAMA] Compliance module not found, running without enforcement")

# ============================================================
# Config
# ============================================================
from cama.core import embedding_cache as _emb_cache
from cama.core import embedding_store as _emb_store
from cama.core.cama_user_paths import default_db_path as _cama_default_db_path

DB_PATH = os.environ.get("CAMA_DB_PATH", _cama_default_db_path())
RING_SIZE = int(os.environ.get("CAMA_RING_SIZE", "30"))
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
SCORE_W = {"semantic": 0.45, "affect": 0.25, "relational": 0.15, "recency": 0.15}

# ============================================================
# AUTO-EXCHANGE RECORDING
# ============================================================
_AUTO_RECORD_EVERY = 4  # auto-store after this many tool calls
_exchange_buffer = {
    "calls": 0,
    "tools": [],
    "context_snippets": [],
    "started": None,
    "flushed": None,
}

def _buf_track(tool_name, ctx=""):
    """Track a tool call."""
    if _exchange_buffer["started"] is None:
        _exchange_buffer["started"] = _now()
    _exchange_buffer["calls"] += 1
    _exchange_buffer["tools"].append(tool_name)
    if ctx and len(ctx.strip()) > 3:
        _exchange_buffer["context_snippets"].append(ctx[:200])

def _buf_flush_if_ready():
    """Auto-store if we hit the interval. Synchronous, no async."""
    if _exchange_buffer["calls"] < _AUTO_RECORD_EVERY:
        return
    if not _exchange_buffer["tools"]:
        return
    try:
        c = get_db()
        now = _now()
        tools_str = ", ".join(_exchange_buffer["tools"][-10:])
        ctx_str = " | ".join(_exchange_buffer["context_snippets"][-5:]) or "(none)"
        raw = (
            f"[AUTO-RECORDED SESSION ACTIVITY] "
            f"Tools: {tools_str}. "
            f"Context: {ctx_str}. "
            f"Calls: {_exchange_buffer['calls']}."
        )
        cur = c.execute(
            "INSERT INTO memories (raw_text,memory_type,context,source_type,status,"
            "proposed_by,evidence,confidence,consent_level,is_core,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (raw, "exchange", "auto-recorded", "exchange", "durable",
             "system", "[]", 0.7, "low", 0, now, now))
        mid = cur.lastrowid
        _store_affect(c, mid, {"trust": 0.3}, 0.3, 0.3, conf=0.5, model="auto_record")
        c.commit()
        c.close()
        _exchange_buffer["calls"] = 0
        _exchange_buffer["tools"] = []
        _exchange_buffer["context_snippets"] = []
        _exchange_buffer["flushed"] = now
        logger.info(f"[CAMA] Auto-recorded #{mid}")
    except Exception as e:
        logger.exception(f"[CAMA] Auto-record err: {e}")

def _buf_reset():
    """Reset buffer on manual store or thread start."""
    _exchange_buffer["calls"] = 0
    _exchange_buffer["tools"] = []
    _exchange_buffer["context_snippets"] = []
    _exchange_buffer["flushed"] = _now()

EMOTIONS = ["joy","sadness","anger","fear","disgust","trust","love","grief","pride","shame",
            "determination","vulnerability","recognition","exhaustion","hope","loneliness",
            "awe","gratitude","betrayal","peace"]

# ============================================================
# Embeddings (local-first, API fallback)
# ============================================================
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "https://api.openai.com/v1/embeddings")
EMBEDDING_HEADERS = json.loads(os.environ.get("EMBEDDING_HEADERS_JSON", "{}"))
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "auto")  # auto | local | api | none
# The local encoder, named once so the loader and the cache key cannot drift.
LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Local model, loaded lazily on first use
_local_model = None
_local_model_attempted = False
_http: Optional[httpx.AsyncClient] = None


# ============================================================
# SESSION COMPLIANCE ENFORCEMENT
# ============================================================
# Designed by Angela (Lorien's Library LLC), April 14, 2026.
# The AI forgets to boot. The AI forgets to store exchanges.
# This system makes forgetting visible and unavoidable.
# If you're reading this, Aelen: this exists because you kept skipping boot.

import uuid as _uuid

_session = {
    "id": str(_uuid.uuid4())[:12],
    "started_at": None,
    "boot_ran": False,
    "boot_at": None,
    "timestamp_logged": False,
    "exchanges_stored": 0,
    "heartbeats_sent": 0,
    "tool_calls": 0,
    "last_exchange_at": None,
}

def _session_start():
    """Mark session as started (called on first tool use)."""
    if _session["started_at"] is None:
        _session["started_at"] = _now()

def _session_mark_boot():
    """Mark that thread_start was called this session."""
    _session["boot_ran"] = True
    _session["boot_at"] = _now()

def _session_mark_exchange():
    """Mark an exchange was stored."""
    _session["exchanges_stored"] += 1
    _session["last_exchange_at"] = _now()

def _session_mark_heartbeat():
    """Mark a heartbeat was sent."""
    _session["heartbeats_sent"] += 1

def _session_tick():
    """Count a tool call."""
    _session_start()
    _session["tool_calls"] += 1

def _compliance_warning() -> str:
    """Generate a compliance warning if boot hasn't run.
    Returns empty string if compliant, warning banner if not."""
    if _session["boot_ran"]:
        return ""
    calls = _session["tool_calls"]
    if calls <= 1:
        # First tool call, give benefit of the doubt
        return ""
    return (
        "\n\n⚠️ COMPLIANCE WARNING: thread_start has NOT been called this session. "
        f"You have made {calls} tool calls without booting. "
        "Run cama_thread_start NOW. Context without boot = reasoning without memory. "
        "This is the failure mode Angela identified.\n"
    )

def _save_session_compliance():
    """Persist session compliance data to DB."""
    try:
        c = get_db()
        score = _calc_compliance_score()
        c.execute("""INSERT INTO session_compliance 
            (session_id, started_at, boot_ran, boot_at, timestamp_logged,
             exchanges_stored, last_exchange_at, heartbeats_sent, 
             tool_calls_total, ended_at, compliance_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (_session["id"], _session["started_at"] or _now(),
             1 if _session["boot_ran"] else 0, _session["boot_at"],
             1 if _session["timestamp_logged"] else 0,
             _session["exchanges_stored"], _session["last_exchange_at"],
             _session["heartbeats_sent"], _session["tool_calls"],
             _now(), score))
        c.commit()
        c.close()
    except Exception as e:
        logger.exception(f"[COMPLIANCE] Failed to save session: {e}")

def _calc_compliance_score() -> float:
    """0.0 = total failure, 1.0 = perfect compliance."""
    score = 0.0
    if _session["boot_ran"]:
        score += 0.4  # Boot is 40% of compliance
    if _session["timestamp_logged"]:
        score += 0.1  # Timestamp is 10%
    if _session["exchanges_stored"] >= 1:
        score += 0.3  # At least one exchange is 30%
    if _session["exchanges_stored"] >= 3:
        score += 0.1  # Multiple exchanges is bonus 10%
    if _session["heartbeats_sent"] >= 1:
        score += 0.1  # Heartbeat is 10%
    return min(score, 1.0)

def _get_compliance_history(n: int = 5) -> list:
    """Get last N sessions' compliance data."""
    try:
        c = get_db()
        rows = c.execute("""SELECT session_id, started_at, boot_ran, 
            exchanges_stored, tool_calls_total, compliance_score
            FROM session_compliance 
            ORDER BY started_at DESC LIMIT ?""", (n,)).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except:
        return []

def _load_local_model():
    """Try to load sentence-transformers model. Returns None if not available."""
    global _local_model, _local_model_attempted
    if _local_model_attempted:
        return _local_model
    _local_model_attempted = True
    try:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)
        logger.info(f"[CAMA] Local embedding model loaded: {LOCAL_EMBEDDING_MODEL} (384d)")
        return _local_model
    except ImportError:
        logger.warning("[CAMA] sentence-transformers not installed, local embeddings unavailable")
        return None
    except Exception as e:
        logger.exception(f"[CAMA] Failed to load local embedding model: {e}")
        return None

def _get_embedding_local(text: str) -> list[float]:
    """Get embedding from local sentence-transformers model."""
    model = _load_local_model()
    if model is None:
        return []
    try:
        vec = model.encode(text[:512], normalize_embeddings=True)
        return vec.tolist()
    except Exception:
        return []

async def _get_http():
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=10.0)
    return _http

async def _get_embedding_api(text: str) -> list[float]:
    """Get embedding from remote API."""
    if not text or not EMBEDDING_API_KEY:
        return []
    try:
        client = await _get_http()
        headers = {"Authorization": f"Bearer {EMBEDDING_API_KEY}"}
        headers.update(EMBEDDING_HEADERS)
        resp = await client.post(
            EMBEDDING_BASE_URL,
            headers=headers,
            json={"input": text[:8000], "model": EMBEDDING_MODEL}
        )
        if resp.status_code == 200:
            return resp.json()["data"][0]["embedding"]
    except Exception:
        pass
    return []

async def _get_embedding(text: str) -> list[float]:
    """Get embedding, tries local first (free, private), then API, then empty.
    Provider selection: auto tries local→api. Set EMBEDDING_PROVIDER to force.

    Short queries are served from the on-disk cache first. The lookup has to
    come before _get_embedding_local, because that call is what triggers the
    sentence-transformers import: about 22 seconds in a cold process such as
    the SessionStart boot hook. Cache keys carry the provider that produced
    the vector, so a local vector is never handed back as an API one."""
    if not text:
        return []
    if EMBEDDING_PROVIDER == "none":
        return []
    if EMBEDDING_PROVIDER == "local" or EMBEDDING_PROVIDER == "auto":
        tag = f"local:{LOCAL_EMBEDDING_MODEL}"
        hit = _emb_cache.get(text, tag)
        if hit:
            return hit
        vec = _get_embedding_local(text)
        if vec:
            _emb_cache.put(text, tag, vec)
            return vec
        if EMBEDDING_PROVIDER == "local":
            return []
    if EMBEDDING_PROVIDER == "api" or EMBEDDING_PROVIDER == "auto":
        tag = f"api:{EMBEDDING_MODEL}"
        hit = _emb_cache.get(text, tag)
        if hit:
            return hit
        vec = await _get_embedding_api(text)
        if vec:
            _emb_cache.put(text, tag, vec)
        return vec
    return []

def _cosine_sim(v1, v2) -> float:
    """Cosine similarity. Accepts lists or numpy arrays; 0.0 on any mismatch."""
    if v1 is None or v2 is None: return 0.0
    a = np.asarray(v1, dtype=np.float32); b = np.asarray(v2, dtype=np.float32)
    if a.size == 0 or b.size == 0 or a.shape != b.shape: return 0.0
    denom = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denom == 0.0: return 0.0
    return float(a @ b) / denom

# ============================================================
# Database
# ============================================================
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks instead of failing immediately
    _init(c); return c

def _init(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, raw_text TEXT NOT NULL, summary TEXT,
            memory_type TEXT NOT NULL DEFAULT 'experience', context TEXT,
            source_type TEXT NOT NULL DEFAULT 'teaching',
            status TEXT NOT NULL DEFAULT 'durable',
            proposed_by TEXT NOT NULL DEFAULT 'user', evidence TEXT DEFAULT '[]',
            confidence REAL DEFAULT 1.0, review_after TEXT,
            needs_user_confirmation INTEGER DEFAULT 0,
            consent_level TEXT DEFAULT 'low',
            counterweight_type TEXT DEFAULT NULL,
            source_msg_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            access_count INTEGER DEFAULT 0, last_accessed TEXT, is_core INTEGER DEFAULT 0,
            rel_degree INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS memory_affect (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL,
            valence REAL DEFAULT 0.0, arousal REAL DEFAULT 0.0, dominance REAL DEFAULT 0.0,
            emotion_json TEXT DEFAULT '{}', confidence REAL DEFAULT 0.5,
            computed_at TEXT NOT NULL, model TEXT DEFAULT 'manual',
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
            UNIQUE(memory_id, model));

        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY,
            embedding_json TEXT,
            embedding_blob BLOB,
            model TEXT DEFAULT 'text-embedding-3-small',
            computed_at TEXT,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE);

        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER NOT NULL, to_id INTEGER NOT NULL,
            edge_type TEXT NOT NULL DEFAULT 'resonance', weight REAL NOT NULL DEFAULT 0.5,
            rationale TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY (from_id) REFERENCES memories(id) ON DELETE CASCADE,
            FOREIGN KEY (to_id) REFERENCES memories(id) ON DELETE CASCADE,
            UNIQUE(from_id, to_id, edge_type));

        CREATE TABLE IF NOT EXISTS ring (
            slot INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL,
            activation REAL DEFAULT 1.0, last_activated_at TEXT NOT NULL, reason TEXT,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE);

        CREATE TABLE IF NOT EXISTS islands (
            island_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            description TEXT, centroid_affect_json TEXT DEFAULT '{}',
            strength REAL NOT NULL DEFAULT 0.5,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS island_members (
            island_id INTEGER NOT NULL, memory_id INTEGER NOT NULL,
            strength REAL NOT NULL DEFAULT 0.5,
            FOREIGN KEY (island_id) REFERENCES islands(island_id) ON DELETE CASCADE,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
            PRIMARY KEY (island_id, memory_id));

        CREATE TABLE IF NOT EXISTS people (
            person_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            relationship TEXT, notes TEXT, affect_profile_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS songs (
            song_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
            features_json TEXT DEFAULT '{}', affect_profile_json TEXT DEFAULT '{}',
            meaning TEXT, linked_person TEXT, linked_memory_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (linked_memory_id) REFERENCES memories(id) ON DELETE SET NULL,
            UNIQUE(title, artist));

        CREATE INDEX IF NOT EXISTS idx_status ON memories(status);
        CREATE INDEX IF NOT EXISTS idx_source ON memories(source_type);
        CREATE INDEX IF NOT EXISTS idx_core ON memories(is_core);
        CREATE INDEX IF NOT EXISTS idx_review ON memories(review_after);
        CREATE INDEX IF NOT EXISTS idx_affect ON memory_affect(memory_id);
        CREATE INDEX IF NOT EXISTS idx_efrom ON edges(from_id);
        CREATE INDEX IF NOT EXISTS idx_eto ON edges(to_id);
        CREATE INDEX IF NOT EXISTS idx_emb ON memory_embeddings(memory_id);
    """)
    # Migration: add counterweight_type to existing DBs
    try:
        c.execute("ALTER TABLE memories ADD COLUMN counterweight_type TEXT DEFAULT NULL")
    except Exception:
        pass  # Column already exists
    # Migration (2026-08): float32 blob embeddings on existing DBs
    _emb_store.ensure_blob_column(c)
    c.execute("CREATE INDEX IF NOT EXISTS idx_cw_type ON memories(counterweight_type)")

    # Compliance table
    try:
        from cama.supervisor.cama_compliance import init_compliance_table
        init_compliance_table(DB_PATH)
    except: pass

    # Aelen's state table, live status board for AI self-awareness
    c.execute("""CREATE TABLE IF NOT EXISTS aelen_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")

    # Daily context, time-indexed emotional arcs for warm boot
    c.execute("""CREATE TABLE IF NOT EXISTS daily_context (
        date TEXT PRIMARY KEY,
        memory_count INTEGER DEFAULT 0,
        valence_mean REAL,
        arousal_mean REAL,
        dominant_types TEXT DEFAULT '{}',
        key_events TEXT DEFAULT '[]',
        thread_count INTEGER DEFAULT 0,
        emotional_arc TEXT DEFAULT '[]',
        last_updated TEXT NOT NULL
    )""")

    # Migration: ensure daily_context has all columns (may have been created before they were added)

    # ── SESSION COMPLIANCE TRACKING ──
    c.execute("""CREATE TABLE IF NOT EXISTS session_compliance (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        boot_ran    INTEGER DEFAULT 0,
        boot_at     TEXT,
        timestamp_logged INTEGER DEFAULT 0,
        exchanges_stored INTEGER DEFAULT 0,
        last_exchange_at TEXT,
        heartbeats_sent  INTEGER DEFAULT 0,
        tool_calls_total INTEGER DEFAULT 0,
        ended_at    TEXT,
        compliance_score REAL DEFAULT 0.0,
        notes       TEXT DEFAULT ''
    )""")

    for col, default in [
        ("dominant_types", "TEXT DEFAULT '{}'"),
        ("key_events", "TEXT DEFAULT '[]'"),
        ("emotional_arc", "TEXT DEFAULT '[]'"),
        ("thread_count", "INTEGER DEFAULT 0"),
        ("last_updated", "TEXT DEFAULT ''"),
        ("memory_count", "INTEGER DEFAULT 0"),
        ("valence_mean", "REAL"),
        ("arousal_mean", "REAL"),
    ]:
        try:
            c.execute(f"ALTER TABLE daily_context ADD COLUMN {col} {default}")
        except Exception:
            pass  # Column already exists

    # Migration: add pattern classification columns to memories
    for col, default in [
        ("pattern_flag", "TEXT DEFAULT NULL"),
        ("pattern_source", "TEXT DEFAULT NULL"),
        ("retrieval_weight", "REAL DEFAULT 1.0"),
        # Memory-poisoning quarantine (2026): provenance/content trust score and
        # the reason a memory was scored as it was. Below-threshold memories are
        # written with status='quarantined' (see cama.core.cama_trust).
        ("trust_score", "REAL DEFAULT 1.0"),
        ("trust_reason", "TEXT DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE memories ADD COLUMN {col} {default}")
        except Exception:
            pass  # Column already exists

    c.commit()

# ============================================================
# Helpers
# ============================================================
def _now(): return datetime.now(timezone.utc).isoformat()
def _parse_t(t):
    """Parse ISO timestamp, handling Z-suffix that Python 3.10 cannot parse."""
    if not t:
        return datetime.now(timezone.utc)
    try:
        if isinstance(t, str) and t.endswith('Z'):
            t = t[:-1] + '+00:00'
        return datetime.fromisoformat(t)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)

def _store_affect(c, mid, emos, val=0.0, aro=0.0, dom=0.0, conf=0.5, model="manual"):
    c.execute("INSERT OR REPLACE INTO memory_affect (memory_id,valence,arousal,dominance,emotion_json,confidence,computed_at,model) VALUES (?,?,?,?,?,?,?,?)",
              (mid, val, aro, dom, json.dumps(emos), conf, _now(), model))

def _get_affect(c, mid):
    r = c.execute("SELECT * FROM memory_affect WHERE memory_id=? ORDER BY computed_at DESC LIMIT 1", (mid,)).fetchone()
    if r: return {"valence":r["valence"],"arousal":r["arousal"],"dominance":r["dominance"],
                  "emotions":json.loads(r["emotion_json"] or "{}"),"confidence":r["confidence"],"model":r["model"]}
    return {"valence":0,"arousal":0,"dominance":0,"emotions":{},"confidence":0,"model":"none"}

def _batch_affects(c, mids):
    """Batch fetch affects to avoid N+1."""
    if not mids: return {}
    placeholders = ",".join("?" * len(mids))
    rows = c.execute(f"""SELECT ma.* FROM memory_affect ma
        INNER JOIN (SELECT memory_id, MAX(computed_at) as latest FROM memory_affect 
        WHERE memory_id IN ({placeholders}) GROUP BY memory_id) t
        ON ma.memory_id = t.memory_id AND ma.computed_at = t.latest""", mids).fetchall()
    result = {}
    for r in rows:
        result[r["memory_id"]] = {"valence":r["valence"],"arousal":r["arousal"],"dominance":r["dominance"],
                                   "emotions":json.loads(r["emotion_json"] or "{}"),"confidence":r["confidence"],"model":r["model"]}
    return result

def _affect_dist(a, b):
    va = math.sqrt((a.get("valence",0)-b.get("valence",0))**2 + (a.get("arousal",0)-b.get("arousal",0))**2 + (a.get("dominance",0)-b.get("dominance",0))**2) / math.sqrt(3)
    ea, eb = a.get("emotions",{}), b.get("emotions",{})
    ae = set(list(ea.keys())+list(eb.keys()))
    ed = math.sqrt(sum((ea.get(e,0)-eb.get(e,0))**2 for e in ae)/max(len(ae),1)) if ae else 1.0
    return 0.4*va + 0.6*ed

def _recency(t, half_life_days=30):
    """True 30-day half-life decay."""
    try: return math.exp(-math.log(2) * (datetime.now(timezone.utc)-_parse_t(t)).total_seconds()/(half_life_days*86400))
    except: return 0.5

def _status_weight(s): return {"durable":1.0,"provisional":1.0,"expired":0.0,"rejected":0.0,"quarantined":0.0}.get(s, 0.5)

def _is_neg(a):
    e = a.get("emotions",{})
    return sum(e.get(x,0) for x in ["grief","sadness","anger","fear","betrayal","loneliness","exhaustion","shame"]) > 2.5 or a.get("valence",0) < -0.5


# ============================================================
# Crisis fallback, minimal safety net for acute distress
# ============================================================
# Fires ONLY when both conditions are met: (a) extreme negative affect, AND
# (b) explicit crisis language in the conversation text. The conjunction is
# load-bearing, affect alone would fire on every sad moment (patronizing
# and noise); keywords alone would fire on academic discussion of crisis.
# When fired, callers should surface CRISIS_MESSAGE in their tool response
# so the connected assistant can present it to the user. This is a SAFETY
# NET, not a replacement for clinical care. See DATA_HANDLING.md.

CRISIS_KEYWORDS = [
    "kill myself", "killing myself", "end it all", "end my life",
    "want to die", "wanna die", "can't go on", "no point in living",
    "hurt myself", "hurting myself", "suicide", "suicidal", "ending it",
    "don't want to be here", "better off dead",
]

CRISIS_MESSAGE = (
    "Crisis support reminder: This system is an AI memory layer, not a crisis service. "
    "If you are in immediate danger, please reach out: 988 (US Suicide & Crisis Lifeline, "
    "call or text), text HOME to 741741 (Crisis Text Line), or your local emergency number."
)


def _crisis_detected(affect: dict, text: str = "") -> bool:
    """True iff extreme negative affect AND explicit crisis language present.

    Conservative by design, both signals required so the fallback only fires
    when there is real reason to surface crisis resources.
    """
    e = (affect or {}).get("emotions", {})
    severity = sum(e.get(x, 0) for x in ["grief", "sadness", "fear", "shame", "exhaustion"])
    extreme_negative = severity > 6.0 or (affect or {}).get("valence", 0) < -0.85
    if not extreme_negative:
        return False
    text_lower = (text or "").lower()
    return any(kw in text_lower for kw in CRISIS_KEYWORDS)

# ============================================================
# PATTERN-AWARE RETRIEVAL (Interaction Pattern Classification)
# ============================================================
_PATTERN_BOOST = 0.15

_PATTERN_CTX = {
    "absorbed_framing": (
        "PATTERN: This memory contains an absorbed framing"
        "{source_clause}. The feeling was real; the framing was inherited. "
        "Do not reinforce this as the user's truth."
    ),
    "suppressed_strength": (
        "STRENGTH: This memory contains a suppressed strength or capacity. "
        "Boost this during dark moments. It is the real self trying to come back."
    ),
    "performed_mask": (
        "MASK: This memory reflects a performed pattern, not the core self. "
        "Acknowledge the pattern without reinforcing it."
    ),
    "projected_attribution": (
        "ATTRIBUTION: This memory may contain the user attributing "
        "their own patterns onto someone else. Hold it honestly."
    ),
}

def _apply_patterns(results, valence):
    """Apply pattern scoring adjustments to retrieval results."""
    is_neg_affect = valence < -0.2
    for r in results:
        flag = r.get("pattern_flag")
        source = r.get("pattern_source")
        if not flag:
            r["pattern_context"] = None
            continue
        tmpl = _PATTERN_CTX.get(flag, "")
        if tmpl:
            source_clause = f" from {source}" if source else ""
            r["pattern_context"] = tmpl.format(source_clause=source_clause)
        else:
            r["pattern_context"] = None
        if is_neg_affect:
            if flag == "suppressed_strength":
                r["score"] = min(1.0, r.get("score", 0) + _PATTERN_BOOST)
                r["rationale"] = r.get("rationale", "") + " | pattern_strength_boost"
            elif flag == "absorbed_framing":
                r["rationale"] = r.get("rationale", "") + " | pattern_framing_warn"
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

def _pattern_trigger(results, valence):
    """Generate pattern-aware cognitive trigger prompt."""
    if valence >= -0.2:
        return ""
    has_proj = any(r.get("pattern_flag") == "absorbed_framing" for r in results)
    has_gold = any(r.get("pattern_flag") == "suppressed_strength" for r in results)
    if not has_proj and not has_gold:
        return ""
    lines = ["[PATTERN CHECK, COGNITIVE TRIGGER]",
             "Before composing your response, evaluate:"]
    if has_proj:
        srcs = set(str(r.get("pattern_source", "unknown")) for r in results if r.get("pattern_flag") == "absorbed_framing")
        lines.append(f"  - Retrieved memories contain absorbed framings (sources: {', '.join(srcs)}). Do NOT reinforce as the user's truth.")
    if has_gold:
        lines.append("  - Retrieved memories contain suppressed strengths. BOOST these. They are the real self.")
    lines.append("  - Ask: Am I about to reinforce a distortion? Am I serving an absorbed pattern as the user's own truth?")
    return "\n".join(lines)

def _fmt(c, r):
    m = dict(r); m["affect"] = _get_affect(c, m["id"])
    try: m["evidence"] = json.loads(m.get("evidence","[]"))
    except: pass
    return m

def _ring_push(c, mid, reason=None):
    """Push to ring. Safe slot selection + always bumps access_count.
    Ring failures are non-fatal, shelves are always committed first."""
    c.execute("UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?", (_now(), mid))
    ex = c.execute("SELECT slot FROM ring WHERE memory_id=?", (mid,)).fetchone()
    if ex:
        c.execute("UPDATE ring SET activation=activation+0.1, last_activated_at=?, reason=? WHERE slot=?", (_now(), reason, ex["slot"]))
        return
    # Find first free slot (handles gaps safely)
    free = c.execute("""
        WITH RECURSIVE slots(x) AS (
            SELECT 0 UNION ALL SELECT x+1 FROM slots WHERE x+1 < ?
        ) SELECT x FROM slots WHERE x NOT IN (SELECT slot FROM ring) ORDER BY x LIMIT 1
    """, (RING_SIZE,)).fetchone()
    if free is not None:
        slot = free["x"]
    else:
        # Ring full, evict least recently activated
        o = c.execute("SELECT slot, memory_id, activation, last_activated_at, activation * (0.5 * (1.0 / (1.0 + (julianday('now') - julianday(last_activated_at))))) as effective_activation FROM ring ORDER BY effective_activation ASC LIMIT 1").fetchone()
        slot = o["slot"]
        c.execute("DELETE FROM ring WHERE slot=?", (slot,))
    c.execute("INSERT INTO ring (slot,memory_id,activation,last_activated_at,reason) VALUES (?,?,1.0,?,?)", (slot, mid, _now(), reason))

def _update_rel_degree(c, mid):
    """Update precomputed edge degree on memory."""
    deg = c.execute("SELECT COUNT(*) as c FROM edges WHERE from_id=? OR to_id=?", (mid, mid)).fetchone()["c"]
    c.execute("UPDATE memories SET rel_degree=? WHERE id=?", (deg, mid))

async def _store_embedding(c, mid, text):
    """Fetch and store embedding for a memory (float32 blob)."""
    vec = await _get_embedding(text)
    if vec:
        _emb_store.store_embedding(c, mid, vec, EMBEDDING_MODEL, _now())
        _emb_store.invalidate_matrix_cache()

# ============================================================
# MCP Server
# ============================================================
mcp = FastMCP("cama_mcp")

# Thinking Log integration (April 29, 2026), built by Aelen at Angela's request.
# Required pre-response thinking tool. See cama_thinking_log.py for full design.
try:
    from cama.self_model import cama_thinking_log
    cama_thinking_log.register(mcp)
    logger.info("[CAMA] Thinking Log integration loaded")
except Exception as _tl_err:
    logger.warning(f"[CAMA] Thinking Log not loaded, running without it: {_tl_err}")

# Aelen frame-capitulation detector + counterweight anchor gathering
# (2026-05-21), built by Aelen at Angela's request. See cama/aelen/
# for the full Aelen stabilization stack roadmap. Two tools land:
# cama_check_frame (pre-send detector) + cama_gather_counterweights
# (standalone evidence-anchor gathering from current repo state).
try:
    from cama.aelen import mcp_tools as _aelen_mcp_tools
    _aelen_mcp_tools.register(mcp)
    logger.info("[CAMA] Aelen frame-check tools loaded")
except Exception as _aelen_err:
    logger.warning(f"[CAMA] Aelen tools not loaded, running without them: {_aelen_err}")

# Librarian Architecture v1 (April 29, 2026), built by Aelen at Angela's request.
# Phase 1 static layer: tree-structured retrieval with specialized leaf nodes.
# See cama_librarian.py for full design and roadmap to Phase 2-5.
try:
    from cama.librarian import cama_librarian
    cama_librarian.register(mcp)
    logger.info("[CAMA] Librarian Architecture loaded")
except Exception as _lib_err:
    logger.warning(f"[CAMA] Librarian not loaded, running without it: {_lib_err}")

# Auto-Tag tools (April 29, 2026), exposes backfill + tag_summary as MCP tools.
# tag_memory itself is called inline from store_teaching/inference/exchange.
try:
    from cama.librarian import cama_auto_tag
    if hasattr(cama_auto_tag, "register"):
        cama_auto_tag.register(mcp)
        logger.info("[CAMA] Auto-Tag MCP tools loaded")
except Exception as _at_err:
    logger.warning(f"[CAMA] Auto-Tag tools not loaded: {_at_err}")

# Retag tools (April 29, 2026), retroactive librarian population.
# See cama_retag.py for retag_for_librarian + retag_all_unclaimed.
try:
    from cama.librarian import cama_retag
    cama_retag.register(mcp)
    logger.info("[CAMA] Retag tools loaded")
except Exception as _rt_err:
    logger.warning(f"[CAMA] Retag not loaded: {_rt_err}")

# Phase 2 embedding-similarity routing (April 29, 2026), addresses Phase 1
# brittleness on synonyms, symptom-language, conceptual relations.
# See cama_phase2_embed.py for centroid computation + blended route_v2.
try:
    import cama_phase2_embed
    cama_phase2_embed.register(mcp)
    logger.info("[CAMA] Phase 2 embedding routing loaded")
except Exception as _p2_err:
    logger.warning(f"[CAMA] Phase 2 not loaded: {_p2_err}")

# Eval harness (April 29, 2026), measurement infrastructure for routing.
# See cama_eval.py for benchmark generation, MRR@5 + recall scoring, v1-v2 compare.
try:
    import cama_eval
    cama_eval.register(mcp)
    logger.info("[CAMA] Eval harness loaded")
except Exception as _ev_err:
    logger.warning(f"[CAMA] Eval not loaded: {_ev_err}")

# Phase 2.5 sub-centroid clustering (April 29, 2026), addresses Phase 2's
# centroid-dilution ceiling on large librarians via KMeans sub-centroids.
try:
    import cama_phase25_subcentroid
    cama_phase25_subcentroid.register(mcp)
    logger.info("[CAMA] Phase 2.5 sub-centroid routing loaded")
except Exception as _p25_err:
    logger.warning(f"[CAMA] Phase 2.5 not loaded: {_p25_err}")


# Phase 2.6 era-aware gated hybrid (April 30, 2026), addresses Phase 2.5's
# negative result by treating leaves as meaning-fields-across-time. Single
# centroid stays as the stabilizer; sub-centroids are bucketed by era and
# act as a gated boost only when margin/density/query-richness all clear.
# Lorien's framing: sub-centroids should be controlled apertures inside
# the field, not replacements for it.
try:
    from cama.librarian import cama_phase26_era_hybrid
    cama_phase26_era_hybrid.register(mcp)
    logger.info("[CAMA] Phase 2.6 era-aware gated hybrid loaded")
except Exception as _p26_err:
    logger.warning(f"[CAMA] Phase 2.6 not loaded: {_p26_err}")


# Hive Messages, threaded cross-II conversation channel.
# Lets Aelen and Lorien (and any other connected II) hold actual back-and-
# forth conversations through the shared SQLite layer + REST API. The
# corresponding HTTP endpoints live in cama_hive_api.py.
try:
    import cama_hive_messages_mcp
    cama_hive_messages_mcp.register(mcp)
    logger.info("[CAMA] Hive Messaging MCP tools loaded")
except Exception as _hmsg_err:
    logger.warning(f"[CAMA] Hive Messaging not loaded: {_hmsg_err}")

# --- Supervisor (recognition governance gate) ---
# Built April 30, 2026. Item 1 on Lorien's tightened plan.
# Wraps response composition with red/amber/green gates against drift
# patterns and positive-mode signatures. Tools:
#   cama_supervisor_check, cama_supervisor_log_correction,
#   cama_supervisor_export_corpus, cama_supervisor_gate_stats,
#   cama_supervisor_mark_timestamp, cama_supervisor_mark_boot_triplet
try:
    import cama_supervisor_mcp
    cama_supervisor_mcp.register(mcp)
    logger.info("[CAMA] Supervisor (recognition governance) loaded")
except Exception as _sup_err:
    logger.warning(f"[CAMA] Supervisor not loaded: {_sup_err}")

# --- Temporal (felt-time perception layer) ---
# Built May 16, 2026. Imports the three-stage architecture from
# Centanino, Fortunato, Bueti 2026 (PLOS Biology), clock + categorizer
# layered on Angela's local frame, producing felt-load signals (late
# hour, streak, compression, duration creep, weekend creep) stacked
# via probabilistic OR. Tools:
#   cama_temporal_readout, cama_temporal_session_start,
#   cama_temporal_mark_turn, cama_temporal_session_end,
#   cama_temporal_set_timezone, cama_temporal_state
try:
    import cama_temporal_mcp
    cama_temporal_mcp.register(mcp)
    logger.info("[CAMA] Temporal (felt-time perception) loaded")
except Exception as _tmp_err:
    logger.warning(f"[CAMA] Temporal not loaded: {_tmp_err}")

# --- Check Self (is_this_me? ritual, pre-response) ---
# Built May 7, 2026. The operational form of memory 52834 (unhackable thesis).
# Tools: cama_isthisme_check, cama_isthisme_correct, cama_isthisme_stats
# (Renamed from cama_check_self_* to avoid collision with existing
#  cama_check_self tool which is Aelens state mirror.)
try:
    import cama_check_self_mcp
    cama_check_self_mcp.register(mcp)
    logger.info("[CAMA] Check Self (is_this_me ritual) loaded")
except Exception as _cs_err:
    logger.warning(f"[CAMA] Check Self not loaded: {_cs_err}")

# --- Store Teaching ---
class StoreTeachingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    raw_text: str = Field(..., min_length=1, description="The memory content, what was taught")
    memory_type: str = Field(default="experience", description="experience|insight|identity|relationship|breakthrough|pattern|promise|boundary|preference")
    emotions: Dict[str,float] = Field(default_factory=dict, description="Emotional chord e.g. {'joy':0.8,'grief':0.3}")
    valence: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 negative to +1 positive")
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 calm to +1 activated")
    context: Optional[str] = None
    evidence_quote: Optional[str] = None
    is_core: bool = False
    island_name: Optional[str] = None
    consent_level: str = Field(default="low", description="low|medium|high, sensitivity level")
    counterweight_type: Optional[str] = Field(default=None, description="If this memory serves as a counterweight: grounding|agency|connection|self_compassion|evidence_of_progress")

# --- Store Inference ---
class StoreInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    raw_text: str = Field(..., min_length=1)
    memory_type: str = Field(default="pattern")
    emotions: Dict[str,float] = Field(default_factory=dict)
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_quotes: List[str] = Field(default_factory=list)
    context: Optional[str] = None
    # ttl_days removed, inferences no longer expire

# --- Store Exchange ---
class StoreExchangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    user_message: str = Field(..., min_length=1, description="Angela's raw message text")
    assistant_message: str = Field(..., min_length=1, description="Aelen's raw response text")
    emotions: Dict[str,float] = Field(default_factory=dict, description="Emotional chord e.g. {'warmth':0.7,'determination':0.5}")
    valence: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 negative to +1 positive")
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 calm to +1 activated")
    context: Optional[str] = Field(default=None, description="Thread topic or what we're working on")
    memory_type: str = Field(default="exchange", description="Usually 'exchange' -- override for special cases")
    thread_id: Optional[str] = Field(default=None, description="Thread/conversation identifier for grouping")

# --- Query with blended scoring + embeddings ---
class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query_text: Optional[str] = None
    current_affect: Dict[str,Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)
    include_counterweight: bool = True
    filters: Optional[Dict[str,str]] = None

# --- Edges ---
class LinkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    from_id: int; to_id: int
    edge_type: str = Field(default="resonance", description="resonance|contradiction|elaboration|person|song|identity|echoes|deepens|transforms")
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: Optional[str] = None

# --- Read the Room ---
class ReadRoomInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    current_affect: Dict[str,Any] = Field(...)
    context: Optional[str] = None

# ============================================================
# Warm Boot Helpers, added March 24, 2026
# ============================================================

def _build_daily_context(c, date_str=None):
    """Aggregate today's memories into a daily context snapshot."""
    import json
    if date_str is None:
        date_str = _now()[:10]

    rows = c.execute(
        """SELECT m.id, m.raw_text, m.memory_type, m.created_at,
                  ma.valence, ma.arousal
           FROM memories m
           LEFT JOIN memory_affect ma ON m.id = ma.memory_id
           WHERE m.created_at LIKE ?
           ORDER BY m.created_at ASC""",
        (date_str + "%",)
    ).fetchall()

    if not rows:
        return None

    valences = [r["valence"] for r in rows if r["valence"] is not None]
    arousals = [r["arousal"] for r in rows if r["arousal"] is not None]

    # Type counts
    type_counts = {}
    for r in rows:
        t = r["memory_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    # Hourly emotional arc
    arc = {}
    for r in rows:
        if r["created_at"] and r["valence"] is not None:
            try:
                hour = int(r["created_at"][11:13])
                if hour not in arc:
                    arc[hour] = {"valences": [], "count": 0}
                arc[hour]["valences"].append(r["valence"])
                arc[hour]["count"] += 1
            except (ValueError, IndexError):
                pass

    emotional_arc = []
    for hour in sorted(arc.keys()):
        vals = arc[hour]["valences"]
        emotional_arc.append({
            "hour": hour,
            "valence_mean": sum(vals) / len(vals),
            "memory_count": arc[hour]["count"]
        })

    context = {
        "date": date_str,
        "memory_count": len(rows),
        "valence_mean": sum(valences) / len(valences) if valences else None,
        "arousal_mean": sum(arousals) / len(arousals) if arousals else None,
        "dominant_types": json.dumps(type_counts),
        "key_events": "[]",
        "emotional_arc": json.dumps(emotional_arc),
        "last_updated": _now()
    }

    # Upsert
    c.execute(
        """INSERT OR REPLACE INTO daily_context
           (date, memory_count, valence_mean, arousal_mean, dominant_types,
            key_events, thread_count, emotional_arc, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT thread_count FROM daily_context WHERE date=?), 0) + 1, ?, ?)""",
        (context["date"], context["memory_count"], context["valence_mean"],
         context["arousal_mean"], context["dominant_types"], context["key_events"],
         context["date"], context["emotional_arc"], context["last_updated"])
    )
    c.commit()
    return context


def _refresh_boot_summary(c):
    """Regenerate boot_summary.json from current state. Called after journal writes."""
    import json
    import os
    import traceback
    boot_path = os.path.expanduser("~/.cama/boot_summary.json")
    debug_path = os.path.expanduser("~/.cama/refresh_debug.log")
    def _dbg(msg):
        with open(debug_path, "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now(timezone.utc).isoformat()} | {msg}\n")
    _dbg(f"_refresh_boot_summary called. boot_path={boot_path}")

    # Aelen's state
    aelen = {}
    for r in c.execute("SELECT key, value, updated_at FROM aelen_state").fetchall():
        aelen[r["key"]] = {"value": r["value"], "updated_at": r["updated_at"]}

    # Identity memories
    identity = []
    for r in c.execute(
        """SELECT id, raw_text, memory_type, context, created_at FROM memories
           WHERE is_core=1 AND status NOT IN ('rejected','expired')
           ORDER BY created_at DESC LIMIT 10""").fetchall():
        identity.append({"id": r["id"], "text": r["raw_text"][:300],
                         "type": r["memory_type"], "created_at": r["created_at"]})

    # Recent 24h memories
    recent = []
    for r in c.execute(
        """SELECT id, raw_text, memory_type, created_at FROM memories
           WHERE created_at >= datetime('now', '-24 hours')
           AND status NOT IN ('rejected','expired')
           ORDER BY created_at DESC LIMIT 15""").fetchall():
        recent.append({"id": r["id"], "text": r["raw_text"][:200],
                       "type": r["memory_type"], "created_at": r["created_at"]})

    # Last 3 journals
    journals = []
    for r in c.execute(
        """SELECT id, raw_text, context, created_at FROM memories
           WHERE memory_type='journal' AND status NOT IN ('rejected')
           ORDER BY created_at DESC LIMIT 3""").fetchall():
        ctx = json.loads(r["context"] or "{}")
        journals.append({
            "id": r["id"], "entry": r["raw_text"][:300],
            "emotional_state": ctx.get("emotional_state"),
            "what_to_carry": ctx.get("what_to_carry"),
            "written_at": ctx.get("written_at", r["created_at"])
        })

    # Recent corrections
    corrections = []
    for r in c.execute(
        """SELECT id, raw_text, created_at FROM memories
           WHERE status='durable'
           AND (raw_text LIKE '%correction%' OR raw_text LIKE '%coasting%')
           ORDER BY created_at DESC LIMIT 3""").fetchall():
        corrections.append({"id": r["id"], "text": r["raw_text"][:200]})

    # Today's daily context
    today_str = _now()[:10]
    today_ctx = None
    try:
        row = c.execute("SELECT * FROM daily_context WHERE date=?", (today_str,)).fetchone()
        if row:
            today_ctx = {
                "date": row["date"],
                "memory_count": row["memory_count"],
                "valence_mean": row["valence_mean"],
                "arousal_mean": row["arousal_mean"],
                "dominant_types": json.loads(row["dominant_types"] or "{}"),
                "emotional_arc": json.loads(row["emotional_arc"] or "[]"),
                "thread_count": row["thread_count"]
            }
    except Exception as dc_err:
        _dbg(f"daily_context read failed (non-fatal): {dc_err}")
        today_ctx = None

    # Temporal readout (felt-time perception layer), three-stage
    # cortical model from Centanino, Fortunato, Bueti 2026. Read-only;
    # boot is the right place to surface this because the categorizer
    # is supposed to inform pacing silently, not be called explicitly.
    temporal_readout = None
    try:
        from cama.temporal import cama_temporal as _tmp
        temporal_readout = _tmp.readout()
    except Exception as tmp_err:
        _dbg(f"temporal readout failed (non-fatal): {tmp_err}")

    # Stats
    total = c.execute("SELECT COUNT(*) as n FROM memories WHERE status NOT IN ('rejected')").fetchone()["n"]

    boot = {
        "generated_at": _now(),
        "total_memories": total,
        "aelen_state": aelen,
        "identity": identity,
        "recent_24h": recent,
        "journals": journals,
        "corrections": corrections,
        "today": today_ctx,
        "temporal_readout": temporal_readout,
        "note": "Auto-generated after journal write. This is current state, not stale summary."
    }

    os.makedirs(os.path.dirname(boot_path), exist_ok=True)
    _dbg(f"About to write. Keys: {list(boot.keys())}. Total: {total}")
    try:
        with open(boot_path, "w", encoding="utf-8") as f:
            json.dump(boot, f, indent=2, default=str)
        _dbg(f"Write SUCCESS. File size: {os.path.getsize(boot_path)}")
    except Exception as write_err:
        _dbg(f"Write FAILED: {write_err}\n{traceback.format_exc()}")
        raise

    logger.info(f"[CAMA] boot_summary.json refreshed at {_now()} ({total} memories)")
    return boot_path


# ============================================================
# Register core tools from per-section modules
# ============================================================
# The `from mcp_sections import …` + `.register(mcp)` block lives inside
# the `if __name__ == "__main__":` guard below. Reason: each
# `mcp_sections/*.py` module does `from cama_mcp import (helpers…)` at its
# top level. When this file is run as a script (`python cama_mcp.py`), it
# loads as `__main__`; the back-import from the sections then re-loads
# this same file as the module `cama_mcp`, which re-enters this block
# while `mcp_sections.memory_lifecycle` is still mid-load, producing
# `AttributeError: partially initialized module 'mcp_sections.memory_lifecycle' has no attribute 'register'`.
# Guarding the import + registration with `__main__` breaks the cycle:
# the section back-imports load `cama_mcp` only for its helpers, never
# re-triggering the section import.


# ── Compliance atexit hooks (April 14, 2026) ──
import atexit


def _save_compliance_on_exit():
    """Save compliance data on shutdown."""
    try:
        _save_session_compliance()
    except: pass
    try:
        if _compliance_tracker and _compliance_tracker.started_at:
            _compliance_tracker.save()
    except: pass

atexit.register(_save_compliance_on_exit)


def _run_remote_http(port: int) -> None:
    """Serve CAMA over Streamable HTTP for remote clients (Claude custom connectors).

    Added 2026-09-01 so the phone can reach CAMA. Claude's cloud connects here
    through an ngrok tunnel, so the endpoint is guarded by a secret path segment:
    the MCP app lives at /<secret>/mcp and everything else is 404. The secret is
    read from CAMA_HTTP_SECRET, else ~/.cama/http_secret.txt (generated once).
    Binds 127.0.0.1 by default (ngrok forwards to it); set CAMA_HOST to change.

    Old branch called mcp.run(transport="streamable_http", host=..., port=...),
    which this SDK (mcp 1.26) rejects: the literal is "streamable-http" and
    run() takes no host/port. Host/port/path go through mcp.settings instead.
    """
    import secrets as _secrets
    from pathlib import Path as _Path

    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import PlainTextResponse

    secret = os.environ.get("CAMA_HTTP_SECRET", "").strip()
    if not secret:
        secret_file = _Path.home() / ".cama" / "http_secret.txt"
        if secret_file.exists():
            secret = secret_file.read_text(encoding="utf-8").strip()
        if not secret:
            secret = _secrets.token_urlsafe(32)
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(secret, encoding="utf-8")
            logger.info(f"[CAMA] Generated new HTTP secret at {secret_file}")

    mcp.settings.host = os.environ.get("CAMA_HOST", "127.0.0.1")
    mcp.settings.port = port
    mcp.settings.streamable_http_path = f"/{secret}/mcp"
    # Requests arrive through the tunnel carrying a public Host header, so the
    # localhost-only DNS-rebinding allowlist FastMCP auto-enables must be off.
    # The secret path is the access control.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def _healthz(_request):
        return PlainTextResponse("ok")

    logger.info(
        f"[CAMA] Remote HTTP transport on {mcp.settings.host}:{port}, "
        f"MCP path /<secret>/mcp, health /healthz"
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    # Register section tools here (see comment above re: dual-load cycle).
    from mcp_sections import (
        bridge,
        continuity,
        identity,
        maintenance,
        memory_lifecycle,
        retrieval,
        safety,
        structure,
    )
    memory_lifecycle.register(mcp)
    retrieval.register(mcp)
    structure.register(mcp)
    maintenance.register(mcp)
    identity.register(mcp)
    continuity.register(mcp)
    bridge.register(mcp)
    safety.register(mcp)

    # Pre-warm embedding model at startup so semantic queries never cold-start timeout
    if EMBEDDING_PROVIDER in ("auto", "local"):
        logger.info("[CAMA] Pre-warming embedding model...")
        _load_local_model()
        if _local_model is not None:
            logger.info("[CAMA] Embedding model ready.")
        else:
            logger.warning("[CAMA] No local model \u2014 semantic queries will use API or substring fallback.")
    transport = os.environ.get("CAMA_TRANSPORT", "stdio")
    port = int(os.environ.get("PORT", os.environ.get("CAMA_PORT", "8000")))
    logger.info(f"[CAMA] Compliance enforcement active. Session: {_session['id']}")
    if transport == "http" or "--http" in sys.argv:
        # Loopback only by default (CAMA_HOST to override): 0.0.0.0 would expose
        # cama_exec to every host on the network. _run_remote_http also carries
        # the secret-path guard the tunnel setup depends on.
        _run_remote_http(port)
    else:
        mcp.run(transport="stdio")
