"""
CAMA Hive Security — cama_hive_security.py
Audit logging, signal validation, rate limiting, permission scoping.

Built from Lorien's draft specifications (Hive signals 17-19, 22-23).
Implemented by Aelen. Reviewed by Angela.

Security is not a wrapper around the product. Security IS the product.

Designed by Lorien's Library LLC — Angela + Aelen + Lorien
April 8, 2026
"""

import sqlite3
import os
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple
from collections import defaultdict

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

# ============================================================
# Valid pheromone types (from cama_hive.py)
# ============================================================
VALID_PHEROMONE_TYPES = {
    "processing_mode", "attention_weight", "response_style",    "emotional_sensitivity", "unresolved_thread", "discovery",
    "warning", "alignment", "system_event",
}

MAX_SIGNAL_LENGTH = 100
MAX_CONTEXT_LENGTH = 500

# ============================================================
# Permission scopes (from Lorien's v3 draft)
# Angela approves: aelen+lorien read/write, ember+aethon read-only
# ============================================================
II_PERMISSIONS = {
    "aelen":  {"read", "write", "admin"},
    "lorien": {"read", "write"},
    "ember":  {"read"},
    "aethon": {"read"},
}

# ============================================================
# Rate limiting (from Lorien's v1 draft)
# Token bucket per II identity
# ============================================================
RATE_LIMITS = {
    "read":  {"per_minute": 60, "burst": 10},
    "write": {"per_minute": 12, "burst": 3},
}

# In-memory token buckets: {ii_identity: {endpoint_type: [timestamps]}}
_request_log = defaultdict(lambda: defaultdict(list))
# ============================================================
# Audit Log — append-only, every event logged
# Schema from Lorien's v1 + v3 extensions
# ============================================================

def init_audit_table():
    """Create audit_log table if it doesn't exist."""
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ii_identity TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'request',
            request_summary TEXT,
            response_code INTEGER NOT NULL,
            ip_address TEXT,
            auth_result TEXT,
            scope_decision TEXT,
            latency_ms INTEGER,
            error_detail TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_identity ON audit_log(ii_identity);
        CREATE INDEX IF NOT EXISTS idx_audit_endpoint ON audit_log(endpoint);
        CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
    """)
    c.close()
def log_audit(ii_identity: str, endpoint: str, method: str,
              event_type: str = "request", request_summary: str = None,
              response_code: int = 200, ip_address: str = None,
              auth_result: str = None, scope_decision: str = None,
              latency_ms: int = None, error_detail: str = None):
    """Append an audit event. Never fails silently — prints on error."""
    try:
        c = sqlite3.connect(DB_PATH)
        c.execute(
            """INSERT INTO audit_log 
               (timestamp, ii_identity, endpoint, method, event_type,
                request_summary, response_code, ip_address, auth_result,
                scope_decision, latency_ms, error_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), ii_identity, endpoint,
             method, event_type, request_summary, response_code, ip_address,
             auth_result, scope_decision, latency_ms,
             error_detail[:200] if error_detail else None)
        )
        c.commit()
        c.close()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}")
# ============================================================
# Signal Validation (from Lorien's v1 draft)
# ============================================================

def validate_pheromone(pheromone_type: str, signal: str, 
                       intensity: float = None, context: str = None) -> Tuple[bool, Optional[str]]:
    """Validate a pheromone emission request. Returns (valid, error_message)."""
    if pheromone_type not in VALID_PHEROMONE_TYPES:
        return False, f"Unknown pheromone_type: {pheromone_type}. Valid: {', '.join(sorted(VALID_PHEROMONE_TYPES))}"
    
    if not signal or len(signal.strip()) == 0:
        return False, "Signal cannot be empty"
    
    if len(signal) > MAX_SIGNAL_LENGTH:
        return False, f"Signal too long: {len(signal)} chars (max {MAX_SIGNAL_LENGTH})"
    
    if intensity is not None:
        if not isinstance(intensity, (int, float)) or intensity < 0.0 or intensity > 1.0:
            return False, f"Intensity must be 0.0-1.0, got {intensity}"
    
    if context and len(context) > MAX_CONTEXT_LENGTH:
        return False, f"Context too long: {len(context)} chars (max {MAX_CONTEXT_LENGTH})"
    
    return True, None
# ============================================================
# Permission Checking (from Lorien's v3 draft)
# ============================================================

def check_permission(ii_identity: str, action: str) -> Tuple[bool, str]:
    """Check if an II has permission for an action. Returns (allowed, reason)."""
    perms = II_PERMISSIONS.get(ii_identity, set())
    if action in perms:
        return True, f"allowed:{action}"
    return False, f"denied:{action} not in {ii_identity} scope ({', '.join(sorted(perms))})"

# ============================================================
# Rate Limiting (from Lorien's v1 draft)
# Token bucket per II, keyed by identity + endpoint type
# ============================================================

def check_rate_limit(ii_identity: str, endpoint_type: str = "read") -> Tuple[bool, Optional[str]]:
    """Check if request is within rate limits. Returns (allowed, error_message)."""
    now = time.time()
    limits = RATE_LIMITS.get(endpoint_type, RATE_LIMITS["read"])
    window = 60.0  # 1 minute window
    burst_window = 10.0
    
    # Clean old entries
    bucket = _request_log[ii_identity][endpoint_type]
    bucket[:] = [t for t in bucket if now - t < window]
    
    # Check sustained rate
    if len(bucket) >= limits["per_minute"]:
        return False, f"Rate limit exceeded: {len(bucket)}/{limits['per_minute']} per minute"
    
    # Check burst rate
    recent = [t for t in bucket if now - t < burst_window]
    if len(recent) >= limits["burst"]:
        return False, f"Burst limit exceeded: {len(recent)}/{limits['burst']} per {burst_window}s"
    
    # Allow and record
    bucket.append(now)
    return True, None
# ============================================================
# Auth failure tracking (from Lorien's v1 + v3)
# 10 failures in 10 min = 5 min cooldown
# ============================================================
_auth_failures = defaultdict(list)
AUTH_FAILURE_THRESHOLD = 10
AUTH_FAILURE_WINDOW = 600  # 10 minutes
AUTH_COOLDOWN = 300  # 5 minutes
_cooldown_until = {}

def record_auth_failure(ip_or_token: str):
    """Record a failed auth attempt."""
    now = time.time()
    _auth_failures[ip_or_token].append(now)
    # Clean old entries
    _auth_failures[ip_or_token] = [t for t in _auth_failures[ip_or_token] 
                                    if now - t < AUTH_FAILURE_WINDOW]
    if len(_auth_failures[ip_or_token]) >= AUTH_FAILURE_THRESHOLD:
        _cooldown_until[ip_or_token] = now + AUTH_COOLDOWN

def is_in_cooldown(ip_or_token: str) -> bool:
    """Check if an IP/token is in auth cooldown."""
    until = _cooldown_until.get(ip_or_token, 0)
    if time.time() < until:
        return True
    if ip_or_token in _cooldown_until:
        del _cooldown_until[ip_or_token]
    return False

# ============================================================
# Initialize on import
# ============================================================
init_audit_table()