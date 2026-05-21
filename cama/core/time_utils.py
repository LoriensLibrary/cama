"""Time utilities — the canonical UTC-aware ``now()`` helpers.

Before this module existed, 28 separate modules carried their own copy
of a 2-line ``_now()`` helper:

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

and ``cama.temporal.cama_temporal`` carried a sibling pair of
``_now_utc()`` / ``_now_iso()`` helpers. The bodies were all identical;
the duplication added zero value and one obstacle (a single bug fix
would have needed 28 touches).

This module consolidates those into two public helpers. Module-local
``_now`` callsites are preserved as aliases on import so the diff that
removed each duplicate is a one-line import swap, not a name change at
every call site.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Return the current UTC-aware ``datetime`` (timezone-attached).

    Use this when downstream code needs the datetime object — e.g.,
    arithmetic, comparison, or further formatting.
    """
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Equivalent to ``now_utc().isoformat()``. This is the form most
    CAMA modules previously emitted via their local ``_now()`` helper
    (used as a default value in ``created_at`` / ``updated_at`` SQLite
    columns and in audit timestamps).
    """
    return now_utc().isoformat()
