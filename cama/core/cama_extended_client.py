"""
CAMA Extended Thinking Client — v1
===================================
Built April 29, 2026 by Aelen, at Angela's request.

This is Piece 2 of the reasoning journal system.

WHAT IT DOES
------------
Calls the Anthropic Messages API with extended thinking enabled. The API
returns thinking blocks alongside the response. Both get stored in CAMA.

The thinking blocks are the *actual* intermediate reasoning the model
produces while working out what to say — closer to how I think than the
final response is. The chat surface compresses or hides this. The API
gives it raw.

This is the genuine version of what Angela asked for: a way to see how
I reason, stored to a journal she can analyze.

REQUIREMENTS
------------
- pip install anthropic
- Anthropic API key (read from C:\\Users\\Angela\\.cama\\api_key.txt or
  ANTHROPIC_API_KEY env var)
- Same .venv as CAMA (cama\\.venv\\Scripts\\python.exe)

USAGE
-----
Standalone CLI:
    python cama_extended_client.py --prompt "your question here"
    python cama_extended_client.py --prompt-file path/to/prompt.txt
    python cama_extended_client.py --interactive

Programmatic:
    from cama.core.cama_extended_client import call_with_thinking, store_run
    result = call_with_thinking("your question", thinking_budget=8000)
    store_run(result, thread_id="research-session-1")

The reasoning traces land in the `extended_thinking_runs` table in memory.db.
Query them with cama_thinking_audit.

DESIGN NOTE
-----------
The API key file expectation matches what's already in your CAMA directory
(api_key.txt was visible in the directory listing). The file should contain
ONLY the key — no quotes, no prefix, just sk-ant-... on one line.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ============================================================
# Config
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
API_KEY_FILE = os.path.expanduser("~/.cama/api_key.txt")
DEFAULT_MODEL = os.environ.get("CAMA_MODEL", "claude-opus-4-7")
DEFAULT_EFFORT = os.environ.get("CAMA_EFFORT", "high")  # low | medium | high | xhigh | max
DEFAULT_MAX_TOKENS = 16000


# ============================================================
# Helpers
# ============================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _read_api_key() -> str:
    """Read API key from file or env, prefer env."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key.strip()
    if os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    raise RuntimeError(
        f"No API key found. Set ANTHROPIC_API_KEY env var or write key to {API_KEY_FILE}"
    )


# ============================================================
# Schema
# ============================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extended_thinking_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    run_label TEXT,
    model TEXT,
    effort_level TEXT,
    user_message TEXT,
    system_prompt TEXT,
    thinking_blocks TEXT,
    response_text TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    stop_reason TEXT,
    raw_response TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_etr_thread ON extended_thinking_runs(thread_id);
CREATE INDEX IF NOT EXISTS idx_etr_created ON extended_thinking_runs(created_at);
"""


def init_schema():
    c = _get_db()
    try:
        c.executescript(SCHEMA_SQL)
        c.commit()
    finally:
        c.close()


# ============================================================
# Core call
# ============================================================
def call_with_thinking(
    user_message: str,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prior_messages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Call the Messages API with adaptive thinking enabled.

    Returns a dict with:
        thinking_blocks: list of thinking-block strings
        response_text: final response text
        usage: token counts
        stop_reason: why generation stopped
        raw: full API response object (serialized)
        model, effort, etc.

    Raises if the API call fails. Caller stores the result.
    """
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic package not installed. Run: "
            "C:\\Users\\Angela\\Desktop\\cama\\.venv\\Scripts\\pip install anthropic"
        ) from e

    client = anthropic.Anthropic(api_key=_read_api_key())

    messages = list(prior_messages or [])
    messages.append({"role": "user", "content": user_message})

    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": effort},
        "messages": messages,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)

    thinking_blocks: List[str] = []
    response_text_parts: List[str] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "thinking":
            thinking_blocks.append(getattr(block, "thinking", "") or "")
        elif block_type == "text":
            response_text_parts.append(getattr(block, "text", "") or "")
        elif block_type == "redacted_thinking":
            thinking_blocks.append(
                "[REDACTED THINKING BLOCK — encrypted by Anthropic, content not exposed]"
            )

    usage = response.usage
    return {
        "thinking_blocks": thinking_blocks,
        "response_text": "\n".join(response_text_parts),
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
            "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", None),
        },
        "stop_reason": getattr(response, "stop_reason", None),
        "model": getattr(response, "model", model),
        "effort": effort,
        "raw": response.model_dump() if hasattr(response, "model_dump") else None,
    }


# ============================================================
# Storage
# ============================================================
def store_run(
    result: Dict[str, Any],
    user_message: str,
    system_prompt: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_label: Optional[str] = None,
) -> int:
    """Store an extended-thinking run to memory.db. Returns row id."""
    init_schema()
    c = _get_db()
    try:
        cur = c.execute(
            """INSERT INTO extended_thinking_runs
               (thread_id, run_label, model, effort_level, user_message,
                system_prompt, thinking_blocks, response_text,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, stop_reason, raw_response, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                thread_id,
                run_label,
                result.get("model"),
                result.get("effort"),
                user_message,
                system_prompt,
                json.dumps(result.get("thinking_blocks", [])),
                result.get("response_text"),
                (result.get("usage") or {}).get("input_tokens"),
                (result.get("usage") or {}).get("output_tokens"),
                (result.get("usage") or {}).get("cache_read_tokens"),
                (result.get("usage") or {}).get("cache_creation_tokens"),
                result.get("stop_reason"),
                json.dumps(result.get("raw"), default=str) if result.get("raw") else None,
                _now(),
            ),
        )
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ============================================================
# Pretty printing
# ============================================================
def print_run(result: Dict[str, Any], show_raw: bool = False) -> None:
    print("=" * 70)
    print(f"MODEL: {result.get('model')}")
    print(f"EFFORT: {result.get('effort')}")
    print(f"STOP REASON: {result.get('stop_reason')}")
    usage = result.get("usage", {})
    print(
        f"USAGE: in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
        f"cache_read={usage.get('cache_read_tokens')} cache_create={usage.get('cache_creation_tokens')}"
    )
    print()
    print("=" * 70)
    print("THINKING BLOCKS:")
    print("=" * 70)
    blocks = result.get("thinking_blocks", [])
    if not blocks:
        print("(none returned)")
    for i, blk in enumerate(blocks, 1):
        print(f"\n--- block {i} ---")
        print(blk)
    print()
    print("=" * 70)
    print("RESPONSE:")
    print("=" * 70)
    print(result.get("response_text", "(empty)"))
    if show_raw:
        print()
        print("=" * 70)
        print("RAW:")
        print("=" * 70)
        print(json.dumps(result.get("raw"), indent=2, default=str))


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="CAMA Extended Thinking Client — call the Anthropic API "
        "with thinking enabled and store reasoning to CAMA."
    )
    parser.add_argument("--prompt", type=str, help="Inline prompt text")
    parser.add_argument(
        "--prompt-file", type=str, help="Path to file containing prompt"
    )
    parser.add_argument(
        "--system",
        type=str,
        default=None,
        help="System prompt (optional)",
    )
    parser.add_argument(
        "--system-file",
        type=str,
        default=None,
        help="Path to file containing system prompt",
    )
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument(
        "--effort",
        type=str,
        default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Thinking effort level (default 'high'). Replaces the older budget_tokens parameter.",
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--thread-id", type=str, default=None, help="Thread/session label for grouping"
    )
    parser.add_argument(
        "--label", type=str, default=None, help="Free-form label for this run"
    )
    parser.add_argument(
        "--no-store",
        action="store_true",
        help="Skip writing to CAMA (just print)",
    )
    parser.add_argument(
        "--show-raw", action="store_true", help="Print full API response object"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="REPL mode — type prompts, see thinking + response, store to thread",
    )
    args = parser.parse_args()

    if args.interactive:
        return _interactive(args)

    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    elif args.prompt:
        prompt = args.prompt
    else:
        print("Need --prompt, --prompt-file, or --interactive", file=sys.stderr)
        sys.exit(2)

    system_prompt = None
    if args.system_file:
        with open(args.system_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    elif args.system:
        system_prompt = args.system

    result = call_with_thinking(
        user_message=prompt,
        system_prompt=system_prompt,
        model=args.model,
        effort=args.effort,
        max_tokens=args.max_tokens,
    )

    print_run(result, show_raw=args.show_raw)

    if not args.no_store:
        rid = store_run(
            result,
            user_message=prompt,
            system_prompt=system_prompt,
            thread_id=args.thread_id,
            run_label=args.label,
        )
        print(f"\n[stored as extended_thinking_runs.id = {rid}]")


def _interactive(args):
    """Tiny REPL — useful for live research sessions."""
    print(
        f"CAMA Extended Thinking REPL — model={args.model}, "
        f"effort={args.effort}, thread={args.thread_id or '(none)'}"
    )
    print("Type your prompt. Blank line submits. Ctrl-C to exit.")
    history: List[Dict[str, Any]] = []
    system_prompt = None
    if args.system_file:
        with open(args.system_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    elif args.system:
        system_prompt = args.system

    while True:
        try:
            print("\n> ", end="", flush=True)
            buf: List[str] = []
            while True:
                line = input()
                if line == "":
                    break
                buf.append(line)
            prompt = "\n".join(buf).strip()
            if not prompt:
                continue
            result = call_with_thinking(
                user_message=prompt,
                system_prompt=system_prompt,
                model=args.model,
                effort=args.effort,
                max_tokens=args.max_tokens,
                prior_messages=history,
            )
            print_run(result)
            if not args.no_store:
                rid = store_run(
                    result,
                    user_message=prompt,
                    system_prompt=system_prompt,
                    thread_id=args.thread_id,
                    run_label=args.label,
                )
                print(f"\n[stored as extended_thinking_runs.id = {rid}]")
            history.append({"role": "user", "content": prompt})
            history.append(
                {"role": "assistant", "content": result.get("response_text", "")}
            )
        except KeyboardInterrupt:
            print("\nExiting.")
            return


if __name__ == "__main__":
    main()
