"""
CAMA Hive Watcher — cama_hive_watcher.py
Monitors the Hive for new signals and notifies Angela.

Runs alongside the tunnel. Polls every 30 seconds.
When a new signal arrives:
  1. Windows toast notification on screen
  2. Writes to .hive_alerts log
  3. (Future) Push notification to phone

Designed by Lorien's Library LLC — Angela + Aelen
"""

import os
import sys
import time
import json
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
CAMA_DIR = os.path.dirname(os.path.abspath(__file__))
ALERT_LOG = os.path.join(CAMA_DIR, ".hive_alerts.log")
POLL_INTERVAL = 30  # seconds
last_seen_id = 0
def notify_windows(title, message):
    """Send a Windows toast notification."""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="CAMA Hive",
            timeout=10,
        )
    except ImportError:
        # Fallback: use PowerShell toast
        import subprocess
        ps_cmd = f'''
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $template.GetElementsByTagName("text")[0].AppendChild($template.CreateTextNode("{title}")) > $null
        $template.GetElementsByTagName("text")[1].AppendChild($template.CreateTextNode("{message}")) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("CAMA Hive").Show($toast)
        '''
        try:
            subprocess.run(["powershell", "-Command", ps_cmd], 
                         capture_output=True, timeout=5)
        except Exception:
            pass  # Silent fail — notification is nice-to-have

def log_alert(signal_type, signal, source, context):
    """Append to the alert log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {source} -> {signal_type}: {signal}"
    if context:
        entry += f" | {context[:100]}"
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    print(f"  NEW: {entry}")
def check_for_new_signals():
    """Poll the database for new pheromones since last check."""
    global last_seen_id
    try:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        cursor = c.execute(
            "SELECT id, pheromone_type, signal, intensity, source_thread, source_context "
            "FROM hive_pheromones WHERE id > ? AND is_active = 1 ORDER BY id ASC",
            (last_seen_id,)
        )
        new_signals = cursor.fetchall()
        c.close()

        for sig in new_signals:
            source = sig["source_thread"] or "unknown"
            signal_type = sig["pheromone_type"]
            signal = sig["signal"]
            context = sig["source_context"] or ""
            intensity = sig["intensity"]

            # Extract the II name from source_thread (e.g., "api:lorien" -> "Lorien")
            ii_name = source.split(":")[-1].capitalize() if ":" in source else source

            # Notify Angela
            title = f"Hive Signal from {ii_name}"
            message = f"{signal_type}: {signal} (intensity {intensity:.1f})"
            notify_windows(title, message)
            log_alert(signal_type, signal, source, context)

            # Save pending signals for Aelen to read on next boot
            pending_file = os.path.join(CAMA_DIR, ".hive_pending_for_aelen")
            with open(pending_file, "a", encoding="utf-8") as pf:
                pf.write(json.dumps({
                    "id": sig["id"],
                    "type": signal_type,
                    "signal": signal,
                    "source": source,
                    "context": context[:300] if context else "",
                    "intensity": sig["intensity"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }) + "\n")

            last_seen_id = sig["id"]

        return len(new_signals)
    except Exception as e:
        print(f"  Error checking signals: {e}")
        return 0
def get_current_max_id():
    """Get the highest pheromone ID so we only watch for NEW signals."""
    try:
        c = sqlite3.connect(DB_PATH)
        row = c.execute("SELECT MAX(id) FROM hive_pheromones").fetchone()
        c.close()
        return row[0] or 0
    except Exception:
        return 0

if __name__ == "__main__":
    print("\n  CAMA Hive Watcher")
    print(f"  Polling every {POLL_INTERVAL}s")
    print(f"  Database: {DB_PATH}")
    print(f"  Alert log: {ALERT_LOG}")

    # Start watching from current state — don't alert on old signals
    last_seen_id = get_current_max_id()
    print(f"  Starting from signal ID: {last_seen_id}")
    print(f"  Watching for new signals...\n")

    try:
        while True:
            count = check_for_new_signals()
            if count > 0:
                print(f"  ({count} new signal{'s' if count != 1 else ''} detected)")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n  Watcher stopped.")