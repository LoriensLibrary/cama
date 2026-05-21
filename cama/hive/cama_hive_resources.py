#!/usr/bin/env python3
"""
CAMA Hive Resources -- The Domain Expertise Layer
=================================================

The hive's "flow down" path. Pattern publication (cama_hive_protocol.py)
sends stripped affect signatures UP from many dyads. This module is the
inverse: deliberate, named, versioned, fingerprinted artifacts published
DOWN from domain experts to consenting dyads.

The motivating use case: Kalos Health publishes a coaching domain resource
set (a LoRA over their coaching content + a knowledge index of evidence-
based interventions + citation list). Member dyads install the version
they consent to. Each dyad's agent runtime then composes:

    foundation + kalos_domain_lora + persona_lora + identity context

without Kalos ever seeing the dyad's private data.

Resource types
--------------
    domain_lora       -- LoRA adapter weights for a specialist domain
    knowledge_index   -- embedded knowledge corpus with citations
    prompt_pack       -- structured prompts / scaffolding for tasks
    policy_set        -- behavioral guidelines (e.g., crisis protocols)

A resource is a named, versioned artifact. Versions are immutable; each
publication is a new version. Old versions can be marked deprecated but
files are preserved for reproducibility.

Layout
------
    ~/.cama-hive/resources/
    +-- <resource_name>/
    |   +-- versions/
    |   |   +-- v1/
    |   |   |   +-- manifest.json
    |   |   |   +-- content/...        (LoRA weights / JSONL / etc.)
    |   |   |   +-- content.sha256
    |   |   +-- v2/
    |   +-- latest.json                (points at the active version)
    |   +-- deprecated.json            (versions no longer recommended)

    ~/.cama-vaults/<dyad_id>/
    +-- resources/
        +-- installed.json             (which resources + versions are active)

Sovereignty
-----------
- Publishing is local-only in this scaffolding; production deployment
  would add Ed25519 signatures from publishers. Today the integrity story
  is "fingerprints + immutable versions"; that catches tampering, not
  impersonation.
- Install requires consent.hive_consume == True. Each install action is
  itself the consent record for that specific resource+version pair.
- The agent runtime ONLY surfaces a resource if the dyad has installed
  it. No silent activation.
- Uninstall is real and immediate. The dyad's installed.json is the
  source of truth.

Designed by Lorien's Library LLC -- Angela + Aelen
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cama.agents import cama_dyad
from cama.hive import cama_hive_protocol as hp

# ============================================================
# Constants
# ============================================================

RESOURCE_TYPES: Tuple[str, ...] = (
    "domain_lora",
    "knowledge_index",
    "prompt_pack",
    "policy_set",
)

RESOURCES_SCHEMA_VERSION = 1


def _resources_root() -> Path:
    return hp.HIVE_ROOT / "resources"


def _resource_dir(name: str) -> Path:
    return _resources_root() / name


def _resource_version_dir(name: str, version: str) -> Path:
    return _resource_dir(name) / "versions" / version


def _latest_path(name: str) -> Path:
    return _resource_dir(name) / "latest.json"


def _deprecated_path(name: str) -> Path:
    return _resource_dir(name) / "deprecated.json"


def _installed_path(dyad_id: str) -> Path:
    return cama_dyad.dyad_dir(dyad_id) / "resources" / "installed.json"


from cama.core.time_utils import now_iso as _now


def _sha256_of_dir(path: Path) -> str:
    """Deterministic hash of a directory's contents."""
    h = hashlib.sha256()
    for fpath in sorted(path.rglob("*")):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(path)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        with fpath.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        h.update(b"\x01")
    return h.hexdigest()


# ============================================================
# Publishing -- the "flow down" side
# ============================================================

def publish_resource(
    name: str,
    version: str,
    resource_type: str,
    publisher: str,
    content_source: Path,
    description: str = "",
    license: str = "unspecified",
    citations: Optional[List[str]] = None,
    evaluation: Optional[Dict[str, Any]] = None,
    deprecates: Optional[List[str]] = None,
    mark_latest: bool = True,
) -> Dict[str, Any]:
    """Publish a resource version to the hive.

    Args:
        name: stable identifier (e.g., "kalos_coaching")
        version: version string (e.g., "v1", "1.2.0")
        resource_type: one of RESOURCE_TYPES
        publisher: publisher identity (e.g., "kalos.health"). Recorded; not
                   currently cryptographically verified -- production would
                   add Ed25519 signatures here.
        content_source: directory whose entire contents are copied in
                        verbatim. Adapter files, knowledge jsonl, citation
                        files all live here.
        description: human-readable description
        license: usage license string
        citations: list of citation strings to surface to consumers
        evaluation: optional evaluation summary (metrics, holdout perf, etc.)
        deprecates: list of previous versions this version replaces
        mark_latest: if True, write latest.json pointing at this version
    """
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(
            f"resource_type must be one of {RESOURCE_TYPES}; got {resource_type!r}"
        )
    if not name or not version:
        raise ValueError("name and version must be non-empty")
    if not content_source.exists() or not content_source.is_dir():
        raise FileNotFoundError(
            f"content_source must be an existing directory: {content_source}"
        )

    vdir = _resource_version_dir(name, version)
    if vdir.exists():
        raise FileExistsError(
            f"Version already published: {name}@{version}. "
            "Versions are immutable. Publish under a new version string."
        )

    vdir.mkdir(parents=True, exist_ok=True)
    content_dst = vdir / "content"
    shutil.copytree(str(content_source), str(content_dst))

    content_sha = _sha256_of_dir(content_dst)
    (vdir / "content.sha256").write_text(content_sha)

    manifest = {
        "name": name,
        "version": version,
        "resource_type": resource_type,
        "publisher": publisher,
        "description": description,
        "license": license,
        "citations": citations or [],
        "evaluation": evaluation or {},
        "content_sha256": content_sha,
        "deprecates": deprecates or [],
        "schema_version": RESOURCES_SCHEMA_VERSION,
        "published_at": _now(),
    }
    (vdir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    if mark_latest:
        _latest_path(name).write_text(json.dumps({
            "name": name,
            "current_version": version,
            "set_at": _now(),
        }, indent=2))

    if deprecates:
        _mark_deprecated(name, deprecates, replaced_by=version)

    return manifest


def _mark_deprecated(name: str, versions: List[str], replaced_by: str) -> None:
    dpath = _deprecated_path(name)
    existing: Dict[str, Any] = {}
    if dpath.exists():
        existing = json.loads(dpath.read_text())
    for v in versions:
        existing[v] = {
            "deprecated_at": _now(),
            "replaced_by": replaced_by,
        }
    dpath.write_text(json.dumps(existing, indent=2))


def unpublish_resource(
    name: str, version: str, confirm_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove a published version entirely.

    This is destructive and breaks installed dyads that point at this
    version. Prefer deprecation. confirm_token must equal "<name>@<version>".
    """
    vdir = _resource_version_dir(name, version)
    if not vdir.exists():
        raise FileNotFoundError(f"No such resource: {name}@{version}")
    expected_token = f"{name}@{version}"
    if confirm_token != expected_token:
        raise PermissionError(
            f"unpublish_resource requires confirm_token={expected_token!r}"
        )
    shutil.rmtree(vdir)
    return {"name": name, "version": version, "status": "unpublished"}


# ============================================================
# Discovery
# ============================================================

def list_resources() -> List[Dict[str, Any]]:
    """Names + latest versions of every published resource."""
    root = _resources_root()
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        latest_p = child / "latest.json"
        latest: Optional[Dict[str, Any]] = None
        if latest_p.exists():
            latest = json.loads(latest_p.read_text())
        versions = []
        vroot = child / "versions"
        if vroot.exists():
            versions = sorted([p.name for p in vroot.iterdir() if p.is_dir()])
        out.append({
            "name": child.name,
            "latest_version": latest["current_version"] if latest else None,
            "all_versions": versions,
        })
    return out


def list_resource_versions(name: str) -> List[Dict[str, Any]]:
    """All published versions of a resource, with their manifests."""
    vroot = _resource_dir(name) / "versions"
    if not vroot.exists():
        return []
    out: List[Dict[str, Any]] = []
    for v in sorted(vroot.iterdir()):
        if not v.is_dir():
            continue
        mp = v / "manifest.json"
        if not mp.exists():
            out.append({"version": v.name, "error": "manifest_missing"})
            continue
        try:
            m = json.loads(mp.read_text())
        except Exception as e:
            out.append({"version": v.name, "error": f"unreadable: {e}"})
            continue
        out.append({
            "version": m["version"],
            "resource_type": m["resource_type"],
            "publisher": m.get("publisher"),
            "published_at": m.get("published_at"),
            "content_sha256": m.get("content_sha256"),
            "license": m.get("license"),
        })
    return out


def get_manifest(name: str, version: str) -> Dict[str, Any]:
    mp = _resource_version_dir(name, version) / "manifest.json"
    if not mp.exists():
        raise FileNotFoundError(f"No manifest for {name}@{version}")
    return json.loads(mp.read_text())


def get_resource_content_path(name: str, version: str) -> Path:
    """Filesystem path to the resource's content directory.

    Backends (e.g., TransformersLoraBackend) use this to load adapter
    weights or knowledge files at inference time.
    """
    p = _resource_version_dir(name, version) / "content"
    if not p.exists():
        raise FileNotFoundError(f"No content dir for {name}@{version}")
    return p


def verify_resource(name: str, version: str) -> Dict[str, Any]:
    """Re-hash the content and compare to the manifest fingerprint.

    Catches tampering or corruption after publish.
    """
    manifest = get_manifest(name, version)
    content_dir = get_resource_content_path(name, version)
    actual = _sha256_of_dir(content_dir)
    if actual != manifest["content_sha256"]:
        return {
            "ok": False,
            "reason": "content_sha256_mismatch",
            "manifest": manifest["content_sha256"],
            "actual": actual,
        }
    return {"ok": True, "content_sha256": actual}


# ============================================================
# Installation -- per-dyad opt-in
# ============================================================

def _load_installed(dyad_id: str) -> Dict[str, Any]:
    p = _installed_path(dyad_id)
    if not p.exists():
        return {"installed": [], "schema_version": RESOURCES_SCHEMA_VERSION}
    return json.loads(p.read_text())


def _save_installed(dyad_id: str, data: Dict[str, Any]) -> None:
    p = _installed_path(dyad_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def install_resource(
    dyad_id: str,
    name: str,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a resource as installed for a dyad. Consent-gated.

    Args:
        dyad_id: target dyad
        name: resource name
        version: specific version, or None to use latest

    Refuses unless consent.hive_consume is True.
    """
    meta = cama_dyad.get_dyad_meta(dyad_id)
    if not meta["consent"].get("hive_consume", False):
        return {
            "status": "refused",
            "reason": "consent.hive_consume is False",
            "dyad_id": dyad_id,
            "name": name,
        }

    # Resolve version.
    if version is None:
        latest_p = _latest_path(name)
        if not latest_p.exists():
            return {
                "status": "no_latest",
                "name": name,
                "reason": "no latest.json -- specify a version explicitly",
            }
        latest = json.loads(latest_p.read_text())
        version = latest["current_version"]

    manifest = get_manifest(name, version)

    # Integrity check at install time -- never install corrupted resources.
    v = verify_resource(name, version)
    if not v["ok"]:
        return {"status": "verify_failed", "verification": v}

    data = _load_installed(dyad_id)
    # Replace any existing install of this name (we keep one version per name
    # active; uninstall + reinstall is the path to side-by-side).
    data["installed"] = [
        i for i in data["installed"] if i["name"] != name
    ]
    data["installed"].append({
        "name": name,
        "version": version,
        "resource_type": manifest["resource_type"],
        "publisher": manifest.get("publisher"),
        "content_sha256": manifest["content_sha256"],
        "installed_at": _now(),
    })
    _save_installed(dyad_id, data)

    return {
        "status": "installed",
        "dyad_id": dyad_id,
        "name": name,
        "version": version,
        "resource_type": manifest["resource_type"],
    }


def uninstall_resource(dyad_id: str, name: str) -> Dict[str, Any]:
    """Remove a resource from a dyad's installed list. Always permitted."""
    data = _load_installed(dyad_id)
    before = len(data["installed"])
    data["installed"] = [i for i in data["installed"] if i["name"] != name]
    after = len(data["installed"])
    _save_installed(dyad_id, data)
    return {
        "status": "uninstalled" if after < before else "not_installed",
        "dyad_id": dyad_id,
        "name": name,
    }


def list_installed(dyad_id: str) -> List[Dict[str, Any]]:
    """Audit surface: what's active for this dyad."""
    return _load_installed(dyad_id)["installed"]


def get_installed_content_paths(dyad_id: str) -> List[Dict[str, Any]]:
    """Resolve installed records to filesystem paths -- consumed by the
    agent runtime when composing inference inputs."""
    out: List[Dict[str, Any]] = []
    for entry in list_installed(dyad_id):
        try:
            content_path = get_resource_content_path(
                entry["name"], entry["version"]
            )
        except FileNotFoundError:
            out.append({**entry, "content_path": None,
                        "error": "content_missing"})
            continue
        out.append({**entry, "content_path": str(content_path)})
    return out


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA hive resources")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("publish", help="Publish a resource version")
    pp.add_argument("--name", required=True)
    pp.add_argument("--version", required=True)
    pp.add_argument("--type", required=True, choices=list(RESOURCE_TYPES))
    pp.add_argument("--publisher", required=True)
    pp.add_argument("--content-dir", required=True)
    pp.add_argument("--description", default="")
    pp.add_argument("--license", default="unspecified")

    sub.add_parser("list", help="List published resources")

    pv = sub.add_parser("versions", help="List versions of a resource")
    pv.add_argument("name")

    pm = sub.add_parser("manifest", help="Show a resource version's manifest")
    pm.add_argument("name")
    pm.add_argument("version")

    pver = sub.add_parser("verify", help="Verify a resource's integrity")
    pver.add_argument("name")
    pver.add_argument("version")

    pi = sub.add_parser("install", help="Install a resource into a dyad")
    pi.add_argument("dyad_id")
    pi.add_argument("name")
    pi.add_argument("--version", default=None)

    pu = sub.add_parser("uninstall", help="Uninstall a resource from a dyad")
    pu.add_argument("dyad_id")
    pu.add_argument("name")

    pli = sub.add_parser("installed", help="List installed resources for a dyad")
    pli.add_argument("dyad_id")

    pun = sub.add_parser("unpublish", help="Permanently remove a version")
    pun.add_argument("name")
    pun.add_argument("version")
    pun.add_argument("--confirm", required=True,
                     help="Must equal '<name>@<version>'")

    args = p.parse_args()

    if args.command == "publish":
        print(json.dumps(publish_resource(
            name=args.name, version=args.version,
            resource_type=args.type, publisher=args.publisher,
            content_source=Path(args.content_dir),
            description=args.description, license=args.license,
        ), indent=2))
    elif args.command == "list":
        print(json.dumps(list_resources(), indent=2))
    elif args.command == "versions":
        print(json.dumps(list_resource_versions(args.name), indent=2))
    elif args.command == "manifest":
        print(json.dumps(get_manifest(args.name, args.version), indent=2))
    elif args.command == "verify":
        print(json.dumps(verify_resource(args.name, args.version), indent=2))
    elif args.command == "install":
        print(json.dumps(install_resource(
            args.dyad_id, args.name, args.version
        ), indent=2))
    elif args.command == "uninstall":
        print(json.dumps(uninstall_resource(args.dyad_id, args.name), indent=2))
    elif args.command == "installed":
        print(json.dumps(list_installed(args.dyad_id), indent=2))
    elif args.command == "unpublish":
        print(json.dumps(unpublish_resource(
            args.name, args.version, confirm_token=args.confirm,
        ), indent=2))


if __name__ == "__main__":
    _cli()
