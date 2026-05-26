"""``cama-ops``, operator CLI.

Subcommands::

    cama-ops keys create --dyad=<id> --kind=live|dev [--name=...]
    cama-ops keys list [--dyad=<id>]
    cama-ops keys revoke <fingerprint>
    cama-ops audit-log [--since=<iso>] [--limit=50]
    cama-ops dyads list

Why a CLI and not HTTP: the keys DB is operator-controlled. Operator
actions over HTTP would require an admin-API-key, which would be the
single most valuable credential in the system. File-based auth means
operator-level actions require shell access to the host, a much
larger barrier than guessing or stealing an HTTP token.

Run ``cama-ops --help`` for the full surface.
"""

from __future__ import annotations

import argparse
import json

from cama.api.auth import _open_keys_db, create_key, init_keys_schema, revoke_key


def _cmd_keys_create(args: argparse.Namespace) -> int:
    init_keys_schema()
    plaintext, fingerprint = create_key(
        dyad_id=args.dyad,
        kind=args.kind,
        name=args.name,
        expires_at=args.expires_at,
    )
    print(json.dumps({
        "ok": True,
        "fingerprint": fingerprint,
        "dyad_id": args.dyad,
        "kind": args.kind,
        "key": plaintext,
        "note": (
            "The key plaintext above is shown ONCE and not recoverable. "
            "Store it in your secrets manager now."
        ),
    }, indent=2))
    return 0


def _cmd_keys_list(args: argparse.Namespace) -> int:
    init_keys_schema()
    c = _open_keys_db()
    try:
        if args.dyad:
            rows = c.execute(
                "SELECT fingerprint, dyad_id, kind, name, created_at, "
                "expires_at, revoked_at, last_used_at "
                "FROM api_keys WHERE dyad_id = ? ORDER BY created_at DESC",
                (args.dyad,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT fingerprint, dyad_id, kind, name, created_at, "
                "expires_at, revoked_at, last_used_at "
                "FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
    finally:
        c.close()
    items = [dict(r) for r in rows]
    print(json.dumps({"keys": items, "count": len(items)}, indent=2))
    return 0


def _cmd_keys_revoke(args: argparse.Namespace) -> int:
    init_keys_schema()
    if revoke_key(args.fingerprint):
        print(json.dumps({"ok": True, "fingerprint": args.fingerprint}))
        return 0
    print(json.dumps({
        "ok": False,
        "fingerprint": args.fingerprint,
        "error": "no matching active key",
    }))
    return 1


def _cmd_audit_log(args: argparse.Namespace) -> int:
    init_keys_schema()
    c = _open_keys_db()
    try:
        if args.since:
            rows = c.execute(
                "SELECT ts, key_fingerprint, dyad_id, http_method, endpoint, "
                "status_code, latency_ms, error_code "
                "FROM api_audit_log WHERE ts >= ? "
                "ORDER BY ts DESC LIMIT ?",
                (args.since, args.limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT ts, key_fingerprint, dyad_id, http_method, endpoint, "
                "status_code, latency_ms, error_code "
                "FROM api_audit_log ORDER BY ts DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
    finally:
        c.close()
    items = [dict(r) for r in rows]
    print(json.dumps({"audit_log": items, "count": len(items)}, indent=2))
    return 0


def _cmd_dyads_list(args: argparse.Namespace) -> int:
    init_keys_schema()
    c = _open_keys_db()
    try:
        rows = c.execute(
            "SELECT DISTINCT dyad_id, COUNT(*) AS keys "
            "FROM api_keys GROUP BY dyad_id ORDER BY dyad_id"
        ).fetchall()
    finally:
        c.close()
    items = [{"dyad_id": r["dyad_id"], "key_count": r["keys"]} for r in rows]
    print(json.dumps({"dyads": items, "count": len(items)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cama-ops",
        description="Operator CLI for the CAMA HTTP API",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # keys
    pk = sub.add_parser("keys", help="API key management")
    pks = pk.add_subparsers(dest="keys_cmd", required=True)

    pkc = pks.add_parser("create", help="Mint a new key")
    pkc.add_argument("--dyad", required=True, help="Dyad ID to bind the key to")
    pkc.add_argument("--kind", choices=["live", "dev", "tenant"], default="live")
    pkc.add_argument("--name", default=None, help="Human-readable name")
    pkc.add_argument("--expires-at", default=None, help="ISO-8601 expiry timestamp")
    pkc.set_defaults(func=_cmd_keys_create)

    pkl = pks.add_parser("list", help="List keys")
    pkl.add_argument("--dyad", default=None, help="Filter by dyad")
    pkl.set_defaults(func=_cmd_keys_list)

    pkr = pks.add_parser("revoke", help="Revoke a key by fingerprint")
    pkr.add_argument("fingerprint", help="12-char fingerprint from `keys list`")
    pkr.set_defaults(func=_cmd_keys_revoke)

    # audit-log
    pal = sub.add_parser("audit-log", help="Read the request audit log")
    pal.add_argument("--since", default=None, help="ISO-8601 lower bound")
    pal.add_argument("--limit", type=int, default=50)
    pal.set_defaults(func=_cmd_audit_log)

    # dyads
    pd = sub.add_parser("dyads", help="Dyad management")
    pds = pd.add_subparsers(dest="dyads_cmd", required=True)
    pdl = pds.add_parser("list", help="List known dyads (derived from issued keys)")
    pdl.set_defaults(func=_cmd_dyads_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
