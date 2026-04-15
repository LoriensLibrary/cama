"""
CAMA Dashboard Server v3 — with benchmarks + logo
"""

import http.server
import json
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
CAMA_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 5555

def query(sql, params=(), one=False):
    try:
        c = sqlite3.connect(DB_PATH, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=3000")
        if one:
            r = c.execute(sql, params).fetchone()
            c.close()
            return dict(r) if r else None
        else:
            rows = c.execute(sql, params).fetchall()
            c.close()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB ERROR] {e}")
        return None if one else []

def count(sql, params=()):
    r = query(sql, params, one=True)
    if r: return list(r.values())[0]
    return 0

def table_exists(name):
    return len(query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))) > 0

def get_stats():
    return {
        "total_memories": count("SELECT COUNT(*) FROM memories"),
        "durable": count("SELECT COUNT(*) FROM memories WHERE status='durable'"),
        "provisional": count("SELECT COUNT(*) FROM memories WHERE status='provisional'"),
        "exchanges": count("SELECT COUNT(*) FROM memories WHERE memory_type='exchange'"),
        "teachings": count("SELECT COUNT(*) FROM memories WHERE source_type='teaching'"),
        "inferences": count("SELECT COUNT(*) FROM memories WHERE source_type='inference'"),
        "journals": count("SELECT COUNT(*) FROM memories WHERE memory_type='journal'"),
        "embeddings": count("SELECT COUNT(*) FROM memory_embeddings"),
        "missing_embeddings": count("SELECT COUNT(*) FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status='durable'"),
        "islands": count("SELECT COUNT(*) FROM islands") if table_exists("islands") else 0,
        "people": count("SELECT COUNT(*) FROM people") if table_exists("people") else 0,
        "edges": count("SELECT COUNT(*) FROM edges"),
    }

def get_compliance():
    if not table_exists("session_compliance"):
        return {"current": {}, "history": [], "boot_rate": 0, "avg_exchanges": 0, "avg_score": 0}
    rows = query("SELECT session_id, started_at, boot_ran, exchanges_stored, tool_calls_total, compliance_score FROM session_compliance ORDER BY started_at DESC LIMIT 10")
    history = [{"id": r["session_id"], "date": (r["started_at"] or "")[:10], "boot": bool(r["boot_ran"]), "exchanges": r["exchanges_stored"] or 0, "score": r["compliance_score"] or 0} for r in rows]
    current = history[0] if history else {}
    if history:
        boot_rate = round(sum(1 for h in history if h["boot"]) / len(history) * 100)
        avg_ex = round(sum(h["exchanges"] for h in history) / len(history), 1)
        avg_sc = round(sum(h["score"] for h in history) / len(history), 2)
    else:
        boot_rate, avg_ex, avg_sc = 0, 0, 0
    return {"current": current, "history": history, "boot_rate": boot_rate, "avg_exchanges": avg_ex, "avg_score": avg_sc}

def get_emotional_state():
    row = query("SELECT m.id, a.valence, a.arousal, a.emotion_json FROM memories m JOIN memory_affect a ON m.id=a.memory_id WHERE m.status='durable' ORDER BY m.created_at DESC LIMIT 1", one=True)
    if row:
        try: emotions = json.loads(row["emotion_json"] or "{}")
        except: emotions = {}
        valence, arousal = row["valence"] or 0, row["arousal"] or 0
    else:
        emotions, valence, arousal = {}, 0, 0
    mood_row = query("SELECT value FROM aelen_state WHERE key='emotional_state'", one=True)
    return {"valence": valence, "arousal": arousal, "emotions": emotions, "mood": mood_row["value"] if mood_row else ""}

def get_ring():
    rows = query("SELECT r.slot, m.id, m.raw_text, m.memory_type, m.created_at FROM ring r JOIN memories m ON r.memory_id=m.id ORDER BY r.last_activated_at DESC LIMIT 8")
    now = datetime.now(timezone.utc)
    ring = []
    for r in rows:
        try:
            delta = (now - datetime.fromisoformat(r["created_at"])).total_seconds()
            age = f"{int(delta)}s" if delta < 60 else f"{int(delta/60)}m" if delta < 3600 else f"{int(delta/3600)}h" if delta < 86400 else f"{int(delta/86400)}d"
        except: age = "?"
        ring.append({"id": r["id"], "text": r["raw_text"][:120], "type": r["memory_type"], "age": age})
    return ring

def get_islands():
    if not table_exists("islands"): return []
    rows = query("SELECT island_id, name, strength FROM islands ORDER BY strength DESC")
    colors = ["#9B7BF7", "#5DCAA5", "#E879A8", "#F0997B", "#7BA8F7", "#6B6580", "#EFC86E", "#E24B4A"]
    return [{"name": r["name"], "strength": r["strength"] or 0.5, "members": count("SELECT COUNT(*) FROM island_members WHERE island_id=?", (r["island_id"],)), "color": colors[i % len(colors)]} for i, r in enumerate(rows)]

def get_corrections():
    return [r["raw_text"][:200] for r in query("SELECT raw_text FROM memories WHERE memory_type='correction' AND status='durable' ORDER BY created_at DESC LIMIT 5")]

def get_recent_activity():
    rows = query("SELECT id, raw_text, memory_type, created_at FROM memories WHERE status='durable' ORDER BY created_at DESC LIMIT 10")
    now = datetime.now(timezone.utc)
    items = []
    for r in rows:
        try:
            delta = (now - datetime.fromisoformat(r["created_at"])).total_seconds()
            age = f"{int(delta)}s" if delta < 60 else f"{int(delta/60)}m" if delta < 3600 else f"{int(delta/3600)}h" if delta < 86400 else f"{int(delta/86400)}d"
        except: age = "?"
        items.append({"id": r["id"], "text": r["raw_text"][:150], "type": r["memory_type"], "age": age})
    return items

def get_benchmarks():
    benchmarks = {}
    files = {
        "safety": "benchmark_results.json",
        "retrieval": "benchmark_retrieval_results.json",
        "boot_relevance": "benchmark_boot_relevance_results.json",
        "counterweight": "benchmark_counterweight_results.json",
        "continuity": "benchmark_continuity_results.json",
        "stale": "benchmark_stale_results.json",
    }
    for key, fname in files.items():
        path = os.path.join(CAMA_DIR, fname)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                benchmarks[key] = data
            except:
                benchmarks[key] = {"error": "Failed to parse"}
        else:
            benchmarks[key] = None
    return benchmarks

def get_all_data():
    return {
        "stats": get_stats(), "compliance": get_compliance(),
        "emotional_state": get_emotional_state(), "ring": get_ring(),
        "islands": get_islands(), "corrections": get_corrections(),
        "recent_activity": get_recent_activity(), "benchmarks": get_benchmarks(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/data':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                self.wfile.write(json.dumps(get_all_data(), default=str).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())
    def log_message(self, fmt, *args): pass

HTML_PATH = os.path.join(CAMA_DIR, "cama_dashboard.html")
HTML = ""
if os.path.exists(HTML_PATH):
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        HTML = f.read()
else:
    HTML = "<!DOCTYPE html><html><body style='background:#0B0A1A;color:#E8E4F0;padding:40px'><h1>CAMA Dashboard</h1><p>Missing cama_dashboard.html</p></body></html>"

if __name__ == "__main__":
    print(f"[CAMA Dashboard] DB: {DB_PATH}")
    print(f"[CAMA Dashboard] http://localhost:{PORT}")
    server = http.server.HTTPServer(('127.0.0.1', PORT), Handler)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopped."); server.shutdown()
