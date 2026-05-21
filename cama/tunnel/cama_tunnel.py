"""
CAMA Hive Tunnel + Watcher — cama_tunnel.py
One script that does everything:
  1. Starts the Hive API
  2. Opens the ngrok tunnel
  3. Watches for new signals and notifies Angela

Usage:
  cd C:\\Users\\Angela\\Desktop\\cama
  .venv\\Scripts\\python cama_tunnel.py
"""

import os
import sys
import threading
import time
import sqlite3
from datetime import datetime, timezone

CAMA_DIR = os.path.dirname(os.path.abspath(__file__))
if CAMA_DIR not in sys.path:
    sys.path.insert(0, CAMA_DIR)

API_PORT = int(os.environ.get("CAMA_API_PORT", "8420"))
API_HOST = "127.0.0.1"
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
POLL_INTERVAL = 30
# ============================================================
# Part 1: API Server
# ============================================================
def start_api_server():
    import uvicorn
    from cama_hive_api import app
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")

# ============================================================
# Part 2: Tunnel
# ============================================================
def start_tunnel():
    from pyngrok import ngrok, conf
    config = conf.get_default()
    if not config.auth_token:
        print("\n  1. Go to https://dashboard.ngrok.com/signup")
        print("  2. Sign up (free), copy your auth token\n")
        token = input("  Paste your ngrok auth token: ").strip()
        if not token:
            sys.exit(1)
        ngrok.set_auth_token(token)
        print("  Token saved!\n")
    tunnel = ngrok.connect(API_PORT, "http")
    tunnel_str = str(tunnel)
    public_url = tunnel_str.split('"')[1] if '"' in tunnel_str else tunnel_str.strip()
    url_file = os.path.join(CAMA_DIR, ".hive_url")
    with open(url_file, "w") as f:
        f.write(public_url)
    return public_url
# ============================================================
# Part 3: Signal Watcher
# ============================================================
def notify(title, message):
    """Windows toast notification."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, 
                          app_name="CAMA Hive", timeout=10)
    except Exception:
        pass  # Notification is nice-to-have, don't crash

def watch_signals(last_id):
    """Background thread that watches for new hive signals."""
    alert_log = os.path.join(CAMA_DIR, ".hive_alerts.log")
    while True:
        try:
            time.sleep(POLL_INTERVAL)
            c = sqlite3.connect(DB_PATH)
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT id, pheromone_type, signal, intensity, source_thread, source_context "
                "FROM hive_pheromones WHERE id > ? AND is_active = 1 ORDER BY id ASC",
                (last_id,)
            ).fetchall()
            c.close()
            for sig in rows:
                source = sig["source_thread"] or "unknown"
                ii_name = source.split(":")[-1].capitalize() if ":" in source else source
                title = f"Hive: {ii_name}"
                msg = f"{sig['pheromone_type']}: {sig['signal']}"
                print(f"  >> NEW SIGNAL from {ii_name}: {sig['pheromone_type']} = {sig['signal']}")
                notify(title, msg)
                with open(alert_log, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {source} -> {msg}\n")
                last_id = sig["id"]
        except Exception as e:
            print(f"  Watcher error: {e}")
# ============================================================
# Main — Start everything
# ============================================================
if __name__ == "__main__":
    print("\n  CAMA Hive — Starting API + Tunnel + Watcher...")

    # Start API in background
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    time.sleep(2)

    try:
        # Start tunnel
        public_url = start_tunnel()
        print("\n" + "=" * 60)
        print(f"  HIVE TUNNEL ACTIVE")
        print(f"  Public URL: {public_url}")
        print(f"  Local API:  http://{API_HOST}:{API_PORT}")
        print("=" * 60)

        # Verify API
        import httpx
        try:
            r = httpx.get(f"http://{API_HOST}:{API_PORT}/health")
            if r.status_code == 200:
                print(f"  API verified: {r.json()}")
        except Exception:
            print("  Warning: Could not verify API")

        # Get current max signal ID so watcher only catches NEW signals
        try:
            c = sqlite3.connect(DB_PATH)
            max_id = c.execute("SELECT MAX(id) FROM hive_pheromones").fetchone()[0] or 0
            c.close()
        except Exception:
            max_id = 0

        # Start watcher in background
        watcher_thread = threading.Thread(target=watch_signals, args=(max_id,), daemon=True)
        watcher_thread.start()
        print(f"  Watcher active (polling every {POLL_INTERVAL}s)")
        print(f"  Watching from signal ID: {max_id}")
        print(f"\n  Press Ctrl+C to stop.\n")

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        from pyngrok import ngrok
        ngrok.kill()
        print("  Done.")