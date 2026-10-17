"""Shared boot-file contract for the MCP server and background writers."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def boot_path():
    return os.environ.get("CAMA_BOOT_SUMMARY", os.path.expanduser("~/.cama/boot_summary.json"))


def freshness(boot, now=None):
    """UTC timestamps; legacy naive timestamps were also written in UTC."""
    try:
        generated = datetime.fromisoformat(boot["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds() / 60
        if age < -5:
            return {"status": "invalid", "age_min": round(age, 1)}
        return {"status": "fresh" if age < 60 else "stale" if age < 360 else "cold",
                "age_min": round(max(0, age), 1)}
    except (KeyError, ValueError, TypeError, AttributeError):
        return {"status": "unknown", "age_min": None}


def write_boot(boot, path=None):
    """Replace atomically so concurrent readers never see half-written JSON."""
    target = Path(path or boot_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as out:
            temporary = out.name
            json.dump(boot, out, indent=2, default=str)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)
