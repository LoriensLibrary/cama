"""Per-user / per-participant path resolution for CAMA.

Single source of truth for where the database and user-supplied calibration
files live. Used by cama_mcp.py, cama_eval.py, and cama_librarians.py so
that the per-participant convention is enforced consistently.

Convention:
  - Default (single-user, the historical mode):  ~/.cama/
  - Study mode (CAMA_PARTICIPANT_ID=P03):        ~/.cama/participant_P03/

Per-participant loaders look ONLY in the participant dir. They do NOT fall
back to ~/.cama/, that fallback would let one participant read another
participant's calibration (or the operator's), which is exactly what
per-DB isolation is supposed to prevent. Isolation by construction.

Override hierarchy for DB path:
  1. Explicit CAMA_DB_PATH env var (used by tests, advanced operators)
  2. Per-participant default if CAMA_PARTICIPANT_ID is set
  3. Single-user default ~/.cama/memory.db

The module is deliberately tiny and imports nothing from the rest of CAMA,
so it can be imported anywhere without circular-import risk.
"""

from __future__ import annotations

import os
from pathlib import Path


def _participant_id() -> str:
    return os.environ.get("CAMA_PARTICIPANT_ID", "").strip()


def cama_user_dir() -> Path:
    """The per-user CAMA directory.

    With CAMA_PARTICIPANT_ID set:  ~/.cama/participant_<id>/
    Without it:                    ~/.cama/
    """
    base = Path.home() / ".cama"
    pid = _participant_id()
    if pid:
        return base / f"participant_{pid}"
    return base


def default_db_path() -> str:
    """Default SQLite path. CAMA_DB_PATH overrides this."""
    return str(cama_user_dir() / "memory.db")


def identity_sentinels_path() -> Path:
    """Path to the per-user identity_sentinels.json calibration file."""
    return cama_user_dir() / "identity_sentinels.json"


def user_aliases_path() -> Path:
    """Path to the per-user user_aliases.json calibration file."""
    return cama_user_dir() / "user_aliases.json"


def is_participant_mode() -> bool:
    """True iff CAMA is running under a specific participant identity."""
    return bool(_participant_id())
