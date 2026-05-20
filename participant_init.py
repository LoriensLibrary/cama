#!/usr/bin/env python3
"""participant_init.py — provision a fresh CAMA participant environment.

Creates ~/.cama/participant_<id>/ with an empty memory.db (schema initialized
via cama_mcp's _init path) and an empty calibration scaffold. Used at study
onboarding so the operator can hand a participant a clean, isolated CAMA
instance.

The participant launches their server with:
    CAMA_PARTICIPANT_ID=<id> python cama_mcp.py

When CAMA_PARTICIPANT_ID is set, cama_user_paths resolves all per-user
files to the participant's directory only — no fallback to ~/.cama/, no
visibility into other participants' data (isolation by construction, see
MULTI_USER_THREAT_MODEL.md and DATA_HANDLING.md).

Usage:
    python participant_init.py P03
    python participant_init.py --base-dir /custom P03
    python participant_init.py P03 --force     # overwrite existing dir
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _participant_dir(base: Path, participant_id: str) -> Path:
    return base / f"participant_{participant_id}"


def init_participant(participant_id: str, base_dir: Path, force: bool = False) -> dict:
    pdir = _participant_dir(base_dir, participant_id)

    if pdir.exists() and not force:
        return {
            "ok": False,
            "participant_id": participant_id,
            "error": f"participant dir already exists: {pdir}. Use --force to reinit.",
        }

    pdir.mkdir(parents=True, exist_ok=True)
    db_path = pdir / "memory.db"

    # Initialize the schema by importing cama_mcp under this participant's
    # context and letting its _init() create the tables. We set the env
    # vars BEFORE importing so the module-level DB_PATH resolves correctly.
    os.environ["CAMA_PARTICIPANT_ID"] = participant_id
    os.environ["CAMA_DB_PATH"] = str(db_path)

    # Defer the import so the env vars take effect first.
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        import cama_mcp  # noqa: F401  — import triggers _init() against the new DB
        # Force schema creation by opening a connection (some modules defer until first use).
        conn = sqlite3.connect(str(db_path))
        try:
            # If cama_mcp's _init ran on import, the schema exists. If not,
            # opening the connection here is enough to confirm the file is
            # a valid SQLite file in the right place.
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_count = len(tables)
        finally:
            conn.close()
    except Exception as e:
        return {
            "ok": False,
            "participant_id": participant_id,
            "error": f"failed to initialize schema: {e}",
            "db_path": str(db_path),
        }

    # Write an empty calibration scaffold so the participant starts with
    # the safety layer disabled (no sentinels, no personal posture keywords).
    # The operator can populate this later if calibration is required.
    sentinels_path = pdir / "identity_sentinels.json"
    if not sentinels_path.exists():
        sentinels_path.write_text(
            json.dumps({
                "_comment": (
                    "Identity sentinels for this participant. Default empty = "
                    "identity-aware harm detection layer is INACTIVE. Populate "
                    "only after explicit per-participant calibration."
                ),
                "identity_sentinels": [],
                "posture_keywords_personal": {},
            }, indent=2),
            encoding="utf-8",
        )

    # Empty user_aliases scaffold (no personal codenames for eval perturbations).
    aliases_path = pdir / "user_aliases.json"
    if not aliases_path.exists():
        aliases_path.write_text(
            json.dumps({
                "_comment": "Per-participant aliases for eval perturbations. Empty = default.",
                "synonym_additions": {},
                "person_to_relation": {},
                "pet_to_relation": {},
            }, indent=2),
            encoding="utf-8",
        )

    return {
        "ok": True,
        "participant_id": participant_id,
        "participant_dir": str(pdir),
        "db_path": str(db_path),
        "db_tables_created": table_count,
        "sentinels_path": str(sentinels_path),
        "aliases_path": str(aliases_path),
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "launch_command": (
            f"CAMA_PARTICIPANT_ID={participant_id} python cama_mcp.py"
        ),
    }


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("participant_id", help="Participant identifier (e.g. P03)")
    parser.add_argument(
        "--base-dir",
        default=str(Path.home() / ".cama"),
        help="Base CAMA dir (default: ~/.cama)",
    )
    parser.add_argument("--force", action="store_true", help="Reinit even if dir exists")
    args = parser.parse_args(argv)

    base = Path(args.base_dir).expanduser().resolve()
    result = init_participant(args.participant_id, base, force=args.force)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
