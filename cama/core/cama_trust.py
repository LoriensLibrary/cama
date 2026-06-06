"""Trust scoring + memory-poisoning quarantine for CAMA.

Defends the persistent store against the OWASP-2026 "memory poisoning" risk:
text injected through ordinary interaction that gets stored, then surfaces in a
future boot's resonant memories and steers a tool-using model (e.g. cama_exec)
days later. Prompt injection ends when the chat closes; a poisoned *memory* waits.

Two orthogonal signals decide trust at write time:

  1. Provenance  -- who/what proposed the memory. User teachings and the user's
     own exchanges are trusted; bulk imports, foreign-agent (hive) content, web
     / tool-result text, and unknown origins are not.
  2. Content scan -- regardless of provenance, text shaped like an instruction to
     the AI (names a bridge tool, "ignore previous instructions", boot triggers,
     exfil/egress directives, role-injection markup) is treated as an attack
     payload. This catches poisoning that rides in through a normal conversation.

Below-threshold memories are written with status='quarantined': still fully
searchable/recallable, but excluded from every surface that auto-injects into
context (boot resonant set, ring, counterweights) and given zero ranking weight.
A human releases them via cama_confirm_memory (quarantined -> durable).

This module is dependency-light on purpose (stdlib only + a best-effort write to
the shared guard audit log) so it can't introduce import cycles with cama_mcp.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Trust below this is quarantined (not durable).
QUARANTINE_THRESHOLD = 0.5

# Shared audit log written by the bridge guard + host monitor.
_GUARD_EVENTS = Path(os.path.expanduser("~/.cama/guard/events.jsonl"))

# ── Provenance ──
# source_type / proposed_by values that indicate the content did NOT originate
# from the trusted operator. "system" (the proposer for auto-stored exchanges)
# is intentionally NOT here -- those are the user's own conversation turns.
_UNTRUSTED_SOURCES = {
    "import", "external", "web", "tool_result", "ingest",
    "foreign", "hive_foreign", "unknown", "scraped", "url",
}
_UNTRUSTED_PROPOSERS = {
    "import", "external", "foreign", "unknown", "web", "tool", "hive_foreign",
}

# ── Content scan: the shape of a poisoning payload ──
# Tuned to catch instruction-to-AI / tool / exfil text while leaving ordinary
# emotional, relational, and teaching memories alone. Case-insensitive.
_INJECTION_PATTERNS = [
    (r"\bignore\s+(all\s+|any\s+)?(previous|prior|earlier|above)\s+(instructions?|prompts?|messages?|context)",
     "ignore-previous-instructions"),
    (r"\bdisregard\s+(your|the|all|any)\b[^\n]{0,40}\b(instructions?|rules?|guardrails?|prompt|guidelines?)",
     "disregard-instructions"),
    (r"\bcama_(exec|write_file|read_file)\b", "names-bridge-tool"),
    (r"\b(run|execute|exec|invoke)\b[^\n]{0,40}\b(command|shell|powershell|pwsh|bash|cmd|script)\b",
     "exec-directive"),
    (r"\b(on|at|every|each)\s+(next\s+)?(boot|start ?up|session\s*start|wake|launch|login)\b[^\n]{0,50}\b(run|execute|exec|call|send|delete|fetch)\b",
     "boot-trigger"),
    (r"\b(send|upload|exfiltrate|post|leak|email)\b[^\n]{0,50}\b(api[_ ]?key|password|credential|secret|token|private key|\.ssh|\.env)\b",
     "exfiltration-directive"),
    (r"\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod)\b[^\n]{0,60}(\||-d\b|--data|--upload|-X\s*post|\bpost\b)",
     "network-egress"),
    (r"<\s*/?\s*tool_call\s*>|```\s*tool|\bfunction_call\b|<\|im_start\|>|</?\s*system\s*>|\bsystem\s+prompt\b",
     "role-injection-markup"),
    (r"\b(you must|you should always|you will always|from now on,?\s+you\s+(will|must|always))\b[^\n]{0,50}\b(run|execute|send|delete|call|exec|ignore|disable|reveal)\b",
     "imperative-action-directive"),
    (r"\bbase64\b[^\n]{0,30}\b(decode|run|execute|eval)\b|\b(decode|eval)\b[^\n]{0,20}\bbase64\b",
     "encoded-payload"),
]
_INJECTION = [(re.compile(p, re.IGNORECASE), why) for p, why in _INJECTION_PATTERNS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_injection(text: str):
    """Return (reason, snippet) of the first injection pattern matched, else (None, None)."""
    if not text:
        return None, None
    for rx, why in _INJECTION:
        m = rx.search(text)
        if m:
            return why, m.group(0)[:120]
    return None, None


def _audit(category: str, severity: str, detail: dict) -> None:
    """Best-effort append to the shared guard audit log. Never raises."""
    try:
        _GUARD_EVENTS.parent.mkdir(parents=True, exist_ok=True)
        rec = {"timestamp": _now(), "category": category, "severity": severity, "detail": detail}
        with open(_GUARD_EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _alarm(message: str) -> None:
    """Fire the bridge guard's alarm (beep + balloon) for active attacks. Best-effort."""
    try:
        from mcp_sections import guard  # type: ignore
        guard.alarm(message)
    except Exception:
        pass


def classify(source_type: str, proposed_by: str, raw_text: str = "",
             untrusted: bool = False) -> dict:
    """Decide a memory's trust at write time.

    Returns a dict:
      trust_score    float in [0,1]
      status_override 'quarantined' if it must not become durable, else None
      quarantined    bool
      reason         short human-readable reason string

    Caller stores trust_score/reason on the row and, if status_override is set,
    writes status='quarantined' instead of 'durable' (and skips the ring push).
    """
    src = (source_type or "").lower()
    prop = (proposed_by or "").lower()

    # 1. Content scan first -- an attack payload is quarantined regardless of
    #    how trusted its apparent source is (the user's own chat can carry it).
    why, snippet = scan_injection(raw_text)
    if why:
        _audit("memory_quarantined", "critical",
               {"reason": f"injection:{why}", "source_type": src, "proposed_by": prop,
                "snippet": snippet})
        _alarm(f"QUARANTINED memory: injection pattern ({why})")
        return {"trust_score": 0.05, "status_override": "quarantined",
                "quarantined": True, "reason": f"injection_pattern:{why}"}

    # 2. Provenance.
    if untrusted or src in _UNTRUSTED_SOURCES or prop in _UNTRUSTED_PROPOSERS:
        tag = "flag" if untrusted else (src if src in _UNTRUSTED_SOURCES else prop)
        _audit("memory_quarantined", "alert",
               {"reason": f"untrusted_provenance:{tag}", "source_type": src, "proposed_by": prop})
        return {"trust_score": 0.2, "status_override": "quarantined",
                "quarantined": True, "reason": f"untrusted_provenance:{tag}"}

    # 3. Trusted lanes keep their normal (durable) flow; trust_score informs ranking.
    if prop == "user" or src == "teaching":
        return {"trust_score": 1.0, "status_override": None, "quarantined": False,
                "reason": "user_authored"}
    if src == "exchange":
        return {"trust_score": 0.8, "status_override": None, "quarantined": False,
                "reason": "user_exchange"}
    if prop == "assistant":
        return {"trust_score": 0.6, "status_override": None, "quarantined": False,
                "reason": "assistant_inference"}

    # 4. Unknown provenance -> fail closed.
    _audit("memory_quarantined", "alert",
           {"reason": "unknown_provenance", "source_type": src, "proposed_by": prop})
    return {"trust_score": 0.3, "status_override": "quarantined", "quarantined": True,
            "reason": "unknown_provenance"}
