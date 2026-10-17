"""Local operator provisioning: python -m cama.hive.guard_ops --help.

Run outside agent sandboxes. Credential files are created exclusively, never
printed; protect the directory using OS permissions (Windows ACLs on Windows).
"""
import argparse
import json
import os
import secrets
from pathlib import Path

from cama.hive.cama_hive_guard import from_directory


def write_private(path, data):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--hive", required=True)
    init.add_argument("--queen", required=True)
    init.add_argument("--compass", required=True, type=Path)
    publish = sub.add_parser("publish-compass")
    publish.add_argument("--hive", required=True)
    publish.add_argument("--compass", required=True, type=Path)
    issue = sub.add_parser("issue")
    issue.add_argument("--hive", required=True)
    issue.add_argument("--role", choices=["queen", "worker", "librarian"], default="worker")
    issue.add_argument("--parent")
    issue.add_argument("--lifetime", type=int, default=3600)
    issue.add_argument("--output", required=True, type=Path)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--agent", required=True)
    revoke.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "init":
        compass = json.loads(args.compass.read_text(encoding="utf-8"))
        if not isinstance(compass, dict) or not compass:
            parser.error("Compass must be a nonempty JSON object")
        args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = args.directory / "signing.key"
        if not key_path.exists():
            write_private(key_path, secrets.token_bytes(32))
        from_directory(args.directory).create_hive(args.hive, args.queen, compass)
    else:
        guard = from_directory(args.directory)
        if args.command == "publish-compass":
            guard.publish_compass(args.hive, json.loads(args.compass.read_text(encoding="utf-8")))
        elif args.command == "issue":
            # Reserve the output before issuing so existing files are never overwritten.
            fd = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(guard.issue(args.hive, args.role, args.parent, args.lifetime), f, indent=2)
        else:
            guard.revoke(args.agent, args.reason)
    print(json.dumps({"ok": True, "operation": args.command}))


if __name__ == "__main__":
    main()
