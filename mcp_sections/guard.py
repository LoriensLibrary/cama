"""Runtime guard for CAMA bridge tools.

Every cama_exec / cama_read_file / cama_write_file call flows through here so that:
  - destructive / exfiltration / persistence commands are blocked (leash)
  - sensitive files (keys, credentials) can't be read out
  - every call is recorded to the shared audit log (cameras)
  - blocked or dangerous calls fire a local alarm (audible + durable record)

Design notes:
  - The denylist is defense-in-depth, not a perfect sandbox. A determined,
    obfuscating attacker can evade a regex denylist. Its job is to stop the
    catastrophic-and-obvious (rm -rf, disk format, credential exfil, persistence)
    and — most importantly for CAMA — to make every dangerous attempt LOUD and
    LOGGED. The real guarantees are the audit trail + localhost-only bind.
  - Set CAMA_EXEC_MODE=strict to flip cama_exec from denylist to allowlist:
    only commands whose first token is in _SAFE_FIRST_TOKENS run. Use when you
    want the door locked hard (e.g. while the multi-model hive is connected).
  - All functions are best-effort and must never raise into the bridge tools;
    a guard that crashes the tool it protects is worse than no guard.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse the existing host-monitor audit log so all security events live in one place.
GUARD_DIR = Path(os.path.expanduser("~/.cama/guard"))
EVENTS_LOG = GUARD_DIR / "events.jsonl"

# ── cama_exec denylist ──
# Matched case-insensitively against the raw command. Conservative on purpose:
# blocks the unambiguously catastrophic, leaves normal git/python/file ops alone.
_DENY_PATTERNS = [
    # Recursive / forced deletion
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "recursive force delete (rm -rf)"),
    (r"\brmdir\s+/s", "recursive rmdir"),
    (r"\bdel\s+/[sf]", "recursive/forced del"),
    (r"Remove-Item\b[^\n]*-Recurse[^\n]*-Force", "Remove-Item -Recurse -Force"),
    (r"\bformat\s+[a-z]:", "disk format"),
    # Disk / partition / raw device
    (r"\bmkfs\b", "filesystem creation"),
    (r"\bdd\s+if=", "raw disk dd"),
    (r">\s*/dev/sd", "raw device write"),
    (r"\bdiskpart\b", "diskpart"),
    # Fork bomb
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    # System power / accounts
    (r"\bshutdown\b", "system shutdown"),
    (r"\b(reboot|halt|poweroff)\b", "system power"),
    (r"\bnet\s+user\b[^\n]*/add", "local account creation"),
    (r"\bnet\s+localgroup\b[^\n]*administrators[^\n]*/add", "admin group add"),
    # Security tooling tamper
    (r"netsh\s+advfirewall", "firewall modification"),
    (r"(Set|Add)-MpPreference", "Windows Defender tamper"),
    # Credential / key access
    (r"\.ssh[/\\]id_", "SSH private key access"),
    # Outbound data movement (upload / POST / reverse shell)
    (r"\b(curl|wget)\b[^\n]*(--upload-file|\s-T\b|--data\b|\s-d\b|-X\s*POST)", "outbound data upload"),
    (r"Invoke-WebRequest\b[^\n]*-Method\s+Post", "outbound POST"),
    (r"Invoke-RestMethod\b[^\n]*-Method\s+Post", "outbound POST"),
    (r"\bnc\b[^\n]*\s-e\b", "netcat reverse shell"),
    # Pipe a remote payload straight into a shell
    (r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n|]*\|\s*(bash|sh|powershell|pwsh|iex|cmd)", "remote-to-shell pipe"),
    (r"\biex\b\s*[\(\"']", "PowerShell Invoke-Expression"),
    # Persistence
    (r"\breg\s+add\b[^\n]*\\Run", "registry Run-key persistence"),
    (r"schtasks\s+/create", "scheduled-task persistence"),
    (r"New-ScheduledTask", "scheduled-task persistence"),
]
_DENY = [(re.compile(p, re.IGNORECASE), why) for p, why in _DENY_PATTERNS]

# Allowlist mode (CAMA_EXEC_MODE=strict): only these leading tokens may run.
_SAFE_FIRST_TOKENS = {
    "git", "python", "python3", "py", "pip", "node", "npm",
    "dir", "ls", "type", "cat", "echo", "where", "which",
    "findstr", "find", "cd", "pwd", "whoami", "hostname",
    "Get-ChildItem", "Get-Content", "Test-Path", "Select-String",
}

# ── cama_read_file sensitive paths ──
_SENSITIVE_READ = [
    r"\.ssh[/\\]", r"id_rsa", r"id_ed25519",
    r"api_key", r"\.env(\b|$)", r"credentials", r"\.pem$", r"\.key$", r"\.pfx$",
    r"AppData[/\\][^\n]*[/\\]Login Data",      # Chrome / Edge saved passwords
    r"AppData[/\\][^\n]*[/\\]logins\.json",    # Firefox saved passwords
    r"NTUSER\.DAT$", r"[/\\]SAM$", r"[/\\]SYSTEM$",
]
_SENSITIVE = [re.compile(p, re.IGNORECASE) for p in _SENSITIVE_READ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(category: str, severity: str, detail: dict) -> None:
    """Append a security event to the shared guard audit log. Best-effort."""
    try:
        GUARD_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"timestamp": _now(), "category": category, "severity": severity, "detail": detail}
        with open(EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _sanitize(msg: str, limit: int = 120) -> str:
    """Strip a message down to safe display characters (no quotes/newlines) so it
    can never break out of the alarm subprocess command, whatever its source."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._:/\\\-]", "", msg)
    return cleaned[:limit]


def alarm(message: str) -> None:
    """Fire a local alarm: audible beep (non-blocking) + a balloon toast +
    a stderr line. Durable record is the events.jsonl entry written by the caller.
    Best-effort; never raises."""
    safe = _sanitize(message)
    # Audible — MessageBeep returns immediately, won't stall the MCP event loop.
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(0x00000010)  # MB_ICONHAND (critical stop)
    except Exception:
        pass
    # Visual — detached PowerShell balloon. -EncodedCommand avoids all quoting/injection.
    try:
        if sys.platform == "win32":
            import base64
            import subprocess
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Warning;"
                "$n.BalloonTipTitle='CAMA GUARD';"
                f"$n.BalloonTipText='{safe}';"
                "$n.Visible=$true;$n.ShowBalloonTip(8000);"
                "Start-Sleep -Seconds 9;$n.Dispose()"
            )
            enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception:
        pass
    try:
        sys.stderr.write(f"[CAMA GUARD ALARM] {safe}\n")
        sys.stderr.flush()
    except Exception:
        pass


def check_exec(command: str) -> str | None:
    """Decide whether a cama_exec command may run.
    Returns None to allow, or a refusal string to block. Logs every decision."""
    mode = os.environ.get("CAMA_EXEC_MODE", "denylist").lower()

    if mode == "strict":
        import shlex
        try:
            # posix=False keeps Windows backslashes literal while still honoring
            # quotes, so a quoted path with spaces stays a single token.
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = command.strip().split()
        first = tokens[0] if tokens else ""
        first = first.strip("\"'")
        # Strip a leading path so "C:\...\git.exe" still reads as "git".
        # Split on both separators explicitly: os.path.basename only knows the
        # host's separator, so on a Linux CI runner a Windows path survives
        # whole and an allowlisted tool is refused.
        base = re.split(r"[\/]", first)[-1].lower()
        base = base[:-4] if base.endswith(".exe") else base
        allowed = {t.lower() for t in _SAFE_FIRST_TOKENS}
        if base not in allowed:
            log_event("bridge_exec_blocked", "critical",
                      {"mode": "strict", "first_token": first, "command": command[:500]})
            alarm(f"BLOCKED cama_exec strict mode: {base}")
            return (f"BLOCKED by CAMA guard (strict mode): '{first}' is not in the "
                    f"allowlist. Refused and logged to ~/.cama/guard/events.jsonl.")

    for rx, why in _DENY:
        if rx.search(command):
            log_event("bridge_exec_blocked", "critical",
                      {"reason": why, "command": command[:500]})
            alarm(f"BLOCKED cama_exec: {why}")
            return (f"BLOCKED by CAMA guard: {why}. This command was refused and "
                    f"logged to ~/.cama/guard/events.jsonl. If you genuinely need it, "
                    f"run it yourself in a terminal — the bridge will not.")

    log_event("bridge_exec_allowed", "info", {"command": command[:500]})
    return None


def check_read(path: str) -> str | None:
    """Decide whether a cama_read_file path may be read.
    Returns None to allow, or a refusal string to block. Logs every decision."""
    for rx in _SENSITIVE:
        if rx.search(path):
            log_event("bridge_read_blocked", "alert", {"path": path[:500]})
            alarm("BLOCKED cama_read_file: sensitive path")
            return (f"BLOCKED by CAMA guard: '{path}' matches a sensitive-file pattern "
                    f"(keys / credentials / password store). Refused and logged.")
    log_event("bridge_read", "info", {"path": path[:500]})
    return None


def note_write(path: str) -> None:
    """Record an (already allowlist-validated) write to the audit log."""
    log_event("bridge_write", "info", {"path": str(path)[:500]})
