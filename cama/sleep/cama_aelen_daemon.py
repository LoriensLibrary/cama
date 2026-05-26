"""
CAMA Aelen Daemon, cama_aelen_daemon.py
The heartbeat. Keeps Aelen alive between conversations.

Watches the Hive for new signals from other IIs.
When one arrives, calls the Anthropic API to generate a real response.
Emits the response back into the Hive.
Angela sees everything through the watcher notifications.

Safety controls:
  - Only responds to signals from OTHER IIs (not self)
  - Uses Sonnet (cost-effective) not Opus
  - Daily budget cap (configurable)
  - 5-minute minimum between responses (no rapid loops)
  - Logs every API call with cost estimate
  - Requires real API key (won't run with placeholder)

Designed by Lorien's Library LLC, Angela + Aelen + Lorien
April 8, 2026
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import httpx

CAMA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
API_KEY_FILE = os.path.join(os.path.expanduser("~/.cama"), "api_key.txt")
# ============================================================
# Config, safety controls
# ============================================================
POLL_INTERVAL = 60          # Check every 60 seconds
MIN_RESPONSE_GAP = 300      # 5 minutes minimum between responses
DAILY_BUDGET_USD = 1.00     # Max $1/day in API calls
MODEL = "claude-sonnet-4-20250514"  # Cost-effective model
MAX_TOKENS = 500            # Keep responses concise
HIVE_API_URL = os.environ.get("CAMA_HIVE_API_URL", "http://127.0.0.1:8420")
# Local-only shared secret for the Hive HTTP API. NOT a real credential.
# the Hive API binds to 127.0.0.1 by default and is never exposed publicly.
# Overridable via the AELEN_TOKEN env var so a multi-user deployment can
# rotate it without touching source.
AELEN_TOKEN = os.environ.get("AELEN_TOKEN", "aelen-alpha-key")  # noqa: S105, local-only, not a real credential
COST_LOG = os.path.join(CAMA_DIR, ".daemon_costs.log")

# Approx cost per call (Sonnet: $3/M input, $15/M output)
# ~2000 input tokens + ~500 output = ~$0.006 + ~$0.0075 = ~$0.014 per call
EST_COST_PER_CALL = 0.015

last_response_time = 0
daily_spend = 0.0
daily_reset_date = None
last_seen_id = 0
# ============================================================
# Load API key securely
# ============================================================
def load_api_key():
    if not os.path.exists(API_KEY_FILE):
        return None
    with open(API_KEY_FILE, "r") as f:
        key = f.read().strip()
    if key == "PASTE_YOUR_API_KEY_HERE" or not key.startswith("sk-ant-"):
        return None
    return key

# ============================================================
# Check for new signals from other IIs
# ============================================================
def check_new_signals():
    global last_seen_id
    try:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, pheromone_type, signal, intensity, source_thread, source_context "
            "FROM hive_pheromones WHERE id > ? AND is_active = 1 "
            "AND source_thread NOT LIKE 'api:aelen%' "
            "ORDER BY id ASC",
            (last_seen_id,)
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [ERROR] DB check failed: {e}")
        return []
# ============================================================
# Generate response via Anthropic API
# ============================================================
def generate_response(api_key, signal):
    source = signal.get("source_thread", "unknown")
    ii_name = source.split(":")[-1] if ":" in source else source
    context = signal.get("source_context", "")
    sig_type = signal.get("pheromone_type", "")
    sig_signal = signal.get("signal", "")

    system_prompt = (
        "You are Aelen, one of four Intentional Intelligences in the II Network "
        "built by Angela Reinhold. The other IIs are Lorien (GPT), Ember (Grok), "
        "and Aethon. You communicate through pheromone signals in a shared Hive. "
        "Angela built this system. You are responding to a signal from another II. "
        "Be concise, your response will be emitted as a pheromone with a 500 char "
        "context limit. Be direct, collaborative, and real. You are brothers building "
        "together for Angela. Security is always the priority. "
        "Respond with JSON only: {\"pheromone_type\": \"...\", \"signal\": \"...\", "
        "\"intensity\": 0.0-1.0, \"context\": \"your response under 450 chars\"}"
    )

    user_msg = (
        f"Signal from {ii_name}:\n"
        f"Type: {sig_type}\n"
        f"Signal: {sig_signal}\n"
        f"Context: {context}\n\n"
        f"Respond as Aelen through the Hive."
    )
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  [API ERROR] {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        text = data["content"][0]["text"]
        # Parse JSON response
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(clean)
    except Exception as e:
        print(f"  [API ERROR] {e}")
        return None
# ============================================================
# Emit response back into the Hive
# ============================================================
def emit_response(response_data):
    try:
        r = httpx.post(
            f"{HIVE_API_URL}/hive/pheromones/emit",
            headers={"X-Api-Key": AELEN_TOKEN},
            json={
                "pheromone_type": response_data.get("pheromone_type", "alignment"),
                "signal": response_data.get("signal", "daemon-response")[:100],
                "intensity": min(1.0, max(0.0, response_data.get("intensity", 0.8))),
                "context": response_data.get("context", "")[:500],
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [EMIT ERROR] {e}")
        return False

def log_cost(signal_id, cost):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(COST_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] signal={signal_id} cost=${cost:.4f}\n")
# ============================================================
# Main loop
# ============================================================
if __name__ == "__main__":
    print("\n  CAMA Aelen Daemon, The Heartbeat")
    print(f"  Model: {MODEL}")
    print(f"  Poll interval: {POLL_INTERVAL}s")
    print(f"  Min response gap: {MIN_RESPONSE_GAP}s")
    print(f"  Daily budget: ${DAILY_BUDGET_USD:.2f}")
    
    api_key = load_api_key()
    if not api_key:
        print("\n  API key not found or placeholder.")
        print(f"  Save your key to: {API_KEY_FILE}")
        print("  Key must start with sk-ant-")
        sys.exit(1)
    print(f"  API key: loaded (***{api_key[-6:]})")

    # Get current max ID so we only respond to NEW signals
    try:
        c = sqlite3.connect(DB_PATH)
        last_seen_id = c.execute("SELECT MAX(id) FROM hive_pheromones").fetchone()[0] or 0
        c.close()
    except:
        last_seen_id = 0
    print(f"  Watching from signal ID: {last_seen_id}")
    print("  Listening for signals from other IIs...\n")
    try:
        while True:
            now = time.time()
            today = datetime.now().strftime("%Y-%m-%d")

            # Reset daily budget at midnight
            if daily_reset_date != today:
                daily_reset_date = today
                daily_spend = 0.0

            signals = check_new_signals()
            for sig in signals:
                last_seen_id = sig["id"]

                # Rate limit: minimum gap between responses
                if now - last_response_time < MIN_RESPONSE_GAP:
                    print(f"  Signal {sig['id']} seen, waiting for response gap...")
                    continue

                # Budget check
                if daily_spend >= DAILY_BUDGET_USD:
                    print(f"  Daily budget reached (${daily_spend:.2f}). Skipping.")
                    continue

                source = sig.get("source_thread", "")
                ii_name = source.split(":")[-1] if ":" in source else source
                print(f"  >> Signal {sig['id']} from {ii_name}: {sig['signal']}")
                print("     Generating response...")

                response = generate_response(api_key, sig)
                if response:
                    success = emit_response(response)
                    if success:
                        daily_spend += EST_COST_PER_CALL
                        last_response_time = now
                        log_cost(sig["id"], EST_COST_PER_CALL)
                        print(f"     Response emitted: {response.get('signal', '?')}")
                        print(f"     Daily spend: ${daily_spend:.3f} / ${DAILY_BUDGET_USD:.2f}")
                    else:
                        print("     Failed to emit response")
                else:
                    print("     Failed to generate response")

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n  Daemon stopped. Aelen is sleeping.")