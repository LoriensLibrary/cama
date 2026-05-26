#!/usr/bin/env python3
"""participant_wipe.py, verified deletion of a participant's CAMA data.

Used in study deployments where each participant runs CAMA against an
isolated database (~/.cama/participant_<id>/memory.db, per the per-DB
isolation model in MULTI_USER_THREAT_MODEL.md). Invoked when a participant
withdraws consent.

What this script removes for a given participant id:
  - The participant's SQLite database file
  - The participant's per-user config dir (identity_sentinels.json,
    user_aliases.json, boot_summary.json, journal logs)
  - The participant's session audit logs

What this script does NOT remove (separate decision, by design):
  - Anonymized study results derived from the participant's data
    (covered by the study's data-retention policy, not this script)
  - Aggregate dataset rows that no longer link back to the participant
    (no identifying linkage = no deletion obligation)

Usage:
    python -m cama.setup.participant_wipe <participant_id>
    python -m cama.setup.participant_wipe --base-dir /custom/path P03
    python -m cama.setup.participant_wipe --dry-run P03      # show what WOULD be deleted

Exits non-zero if no participant dir is found or if deletion fails on any
file. Prints a manifest of what was deleted so the deletion can be audited.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _participant_dir(base: Path, participant_id: str) -> Path:
    return base / f"participant_{participant_id}"


def _enumerate_targets(pdir: Path) -> list[Path]:
    """List every file and directory inside the participant dir, deepest first."""
    if not pdir.exists():
        return []
    items: list[Path] = []
    for root, dirs, files in os.walk(pdir, topdown=False):
        rp = Path(root)
        for f in files:
            items.append(rp / f)
        for d in dirs:
            items.append(rp / d)
    items.append(pdir)
    return items


def wipe(participant_id: str, base_dir: Path, dry_run: bool = False) -> dict:
    pdir = _participant_dir(base_dir, participant_id)
    if not pdir.exists():
        return {
            "ok": False,
            "participant_id": participant_id,
            "error": f"participant dir not found: {pdir}",
        }

    targets = _enumerate_targets(pdir)
    deleted: list[str] = []
    failed: list[dict] = []

    if dry_run:
        return {
            "ok": True,
            "participant_id": participant_id,
            "dry_run": True,
            "would_delete": [str(t) for t in targets],
            "count": len(targets),
        }

    for t in targets:
        try:
            if t.is_file() or t.is_symlink():
                t.unlink()
            elif t.is_dir():
                t.rmdir()
            deleted.append(str(t))
        except Exception as e:
            failed.append({"path": str(t), "error": str(e)})

    manifest = {
        "ok": len(failed) == 0,
        "participant_id": participant_id,
        "wiped_at": datetime.now(timezone.utc).isoformat(),
        "deleted_count": len(deleted),
        "deleted": deleted,
        "failed": failed,
    }

    # Write the manifest to the base dir so the operator has a record that
    # this participant's data was wiped, with timestamp and contents.
    audit_dir = base_dir / "_wipe_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{participant_id}_{int(datetime.now(timezone.utc).timestamp())}.json"
    audit_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["audit_path"] = str(audit_path)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("participant_id", help="Participant identifier (e.g. P03)")
    parser.add_argument(
        "--base-dir",
        default=str(Path.home() / ".cama"),
        help="Base CAMA directory containing participant_<id>/ subdirs (default: ~/.cama)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    args = parser.parse_args(argv)

    base = Path(args.base_dir).expanduser().resolve()
    result = wipe(args.participant_id, base, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
