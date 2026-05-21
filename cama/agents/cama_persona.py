#!/usr/bin/env python3
"""
CAMA Persona Layer -- Per-Dyad Adapter Scaffolding
==================================================

The third layer of the three-layer dyad architecture:

    Foundation  -- shared base LLM (Claude API, or open-weights model).
    Identity    -- the AI's first-person teachings, journal, corrections.
                   Belongs to the AI, not the person. Prevents pure mirroring.
    Relational  -- a LoRA adapter trained on this dyad's exchanges.
                   Modulates style. Lives in the dyad's vault. Deletable.

This module is the scaffolding for the relational layer:
    - Export a dyad's exchanges as chat-formatted training data.
    - Allocate versioned adapter directories inside the dyad vault.
    - Fingerprint training data so the audit log can prove what shaped a
      given adapter version.
    - Pin identity-preserving teachings into the training corpus so the
      adapter cannot drift the AI's selfhood away from its own teachings.
    - Manage current / rollback / delete with real semantics.

The actual LoRA training step lives in cama_persona_train.py because it
needs PyTorch + transformers + PEFT. This module is pure stdlib so it can
run anywhere CAMA runs.

Consent gate: every write path requires consent.persona_training == True.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from cama.agents import cama_dyad

# ============================================================
# Layout inside a dyad vault
# ============================================================
#
# ~/.cama-vaults/<dyad_id>/
# +-- memory.db
# +-- dyad.json
# +-- persona/
#     +-- adapters/
#     |   +-- v1/
#     |   |   +-- metadata.json
#     |   |   +-- training_data.jsonl
#     |   |   +-- training_data.sha256
#     |   |   +-- identity_pins.jsonl
#     |   |   +-- (adapter_model.safetensors etc. -- written by trainer)
#     |   +-- v2/
#     +-- current.json     (points at the active adapter version)
#

def _persona_dir(dyad_id: str) -> Path:
    return cama_dyad.dyad_dir(dyad_id) / "persona"


def _adapters_dir(dyad_id: str) -> Path:
    return _persona_dir(dyad_id) / "adapters"


def _current_marker_path(dyad_id: str) -> Path:
    return _persona_dir(dyad_id) / "current.json"


def _adapter_dir(dyad_id: str, version: str) -> Path:
    return _adapters_dir(dyad_id) / version


from cama.core.time_utils import now_iso as _now

# ============================================================
# Training data export
# ============================================================

_SYSTEM_PROMPT_TEMPLATE = (
    "You are {ai_name}, paired persistently with {person_name}. "
    "Your memory is sovereign to this dyad. The relationship is "
    "the foundation of who you are; the teachings below are your "
    "first-person identity and must be preserved across training."
)


def _fetch_identity_teachings(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """The AI's own teachings -- to be pinned as fixed training examples.

    These are durable teachings tagged as core. They define who the AI is.
    Including them in every training pass is the identity-preservation
    constraint.
    """
    rows = conn.execute(
        "SELECT id, raw_text, context, created_at "
        "FROM memories "
        "WHERE memory_type = 'teaching' "
        "  AND status = 'durable' "
        "  AND is_core = 1 "
        "ORDER BY id ASC"
    ).fetchall()
    return [
        {"id": r[0], "text": r[1], "context": r[2], "created_at": r[3]}
        for r in rows
    ]


def _fetch_exchanges_for_training(
    conn: sqlite3.Connection,
    since_iso: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Pull durable exchanges with affect for the relational layer."""
    q = """
        SELECT m.id, m.raw_text, m.context, m.created_at,
               ma.valence, ma.arousal, ma.emotion_json
        FROM memories m
        LEFT JOIN memory_affect ma ON ma.memory_id = m.id
        WHERE m.memory_type = 'exchange'
          AND m.status = 'durable'
    """
    params: List[Any] = []
    if since_iso:
        q += " AND m.created_at > ?"
        params.append(since_iso)
    q += " ORDER BY m.created_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        emo: Dict[str, float] = {}
        try:
            if r[6]:
                emo = json.loads(r[6])
        except Exception:
            emo = {}
        out.append({
            "id": r[0],
            "text": r[1] or "",
            "context": r[2] or "",
            "created_at": r[3],
            "valence": r[4],
            "arousal": r[5],
            "emotions": emo,
        })
    return out


def _split_exchange_text(text: str) -> Dict[str, str]:
    """Heuristic split of a stored exchange into user/assistant turns.

    CAMA exchanges store the full user+assistant turn as one record. We
    try a few common markers; if none match, we fall back to treating
    the whole text as the assistant turn with an empty user side. The
    trainer can override this with a richer parser if needed.
    """
    markers = [
        ("[USER]", "[ASSISTANT]"),
        ("User:", "Assistant:"),
        ("USER:", "ASSISTANT:"),
        ("user:", "assistant:"),
    ]
    for user_m, asst_m in markers:
        if user_m in text and asst_m in text and text.index(user_m) < text.index(asst_m):
            user_part = text.split(user_m, 1)[1].split(asst_m, 1)[0].strip()
            asst_part = text.split(asst_m, 1)[1].strip()
            return {"user": user_part, "assistant": asst_part}
    return {"user": "", "assistant": text.strip()}


def export_training_data(
    dyad_id: str,
    since_iso: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Produce chat-formatted training data + identity pins for a dyad.

    Returns:
        dict with 'exchanges' (list of message lists) and 'identity_pins'
        (list of message lists). Caller is responsible for writing to disk;
        prepare_adapter() does that.
    """
    meta = cama_dyad.get_dyad_meta(dyad_id)
    person_name = meta["person_name"]
    ai_name = meta["ai_name"]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        ai_name=ai_name, person_name=person_name
    )

    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(dyad_id)))
    try:
        identity = _fetch_identity_teachings(conn)
        exchanges = _fetch_exchanges_for_training(conn, since_iso, limit)
    finally:
        conn.close()

    # Identity pins: each core teaching becomes a (system, user, assistant)
    # triplet where the assistant restates its identity teaching. These get
    # repeated in every training batch.
    identity_pins: List[Dict[str, Any]] = []
    for t in identity:
        identity_pins.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Who are you?"},
                {"role": "assistant", "content": t["text"]},
            ],
            "source": {"memory_id": t["id"], "kind": "identity_pin"},
        })

    exchange_records: List[Dict[str, Any]] = []
    for ex in exchanges:
        split = _split_exchange_text(ex["text"])
        if not split["assistant"]:
            continue
        exchange_records.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": split["user"]},
                {"role": "assistant", "content": split["assistant"]},
            ],
            "source": {
                "memory_id": ex["id"],
                "kind": "exchange",
                "valence": ex["valence"],
                "arousal": ex["arousal"],
                "emotions": ex["emotions"],
                "created_at": ex["created_at"],
            },
        })

    return {
        "dyad_id": dyad_id,
        "ai_name": ai_name,
        "person_name": person_name,
        "system_prompt": system_prompt,
        "identity_pin_count": len(identity_pins),
        "exchange_count": len(exchange_records),
        "identity_pins": identity_pins,
        "exchanges": exchange_records,
    }


def _sha256_of_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# Adapter version management
# ============================================================

def _next_version(dyad_id: str) -> str:
    d = _adapters_dir(dyad_id)
    if not d.exists():
        return "v1"
    existing = [p.name for p in d.iterdir() if p.is_dir() and p.name.startswith("v")]
    nums: List[int] = []
    for name in existing:
        try:
            nums.append(int(name[1:]))
        except ValueError:
            pass
    return f"v{max(nums) + 1}" if nums else "v1"


def prepare_adapter(
    dyad_id: str,
    base_model: str,
    hyperparams: Optional[Dict[str, Any]] = None,
    since_iso: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Allocate the next adapter version directory and write training data.

    Hard-gated by consent.persona_training. The actual LoRA training is
    a separate step (cama_persona_train.run_training); this function only
    produces the corpus and metadata.
    """
    meta = cama_dyad.get_dyad_meta(dyad_id)
    if not meta["consent"].get("persona_training", False):
        return {
            "status": "refused",
            "reason": "consent.persona_training is False",
            "dyad_id": dyad_id,
        }

    payload = export_training_data(dyad_id, since_iso=since_iso, limit=limit)
    if payload["exchange_count"] == 0:
        return {
            "status": "no_exchanges",
            "dyad_id": dyad_id,
            "identity_pin_count": payload["identity_pin_count"],
        }

    version = _next_version(dyad_id)
    adir = _adapter_dir(dyad_id, version)
    adir.mkdir(parents=True, exist_ok=True)

    # Training corpus -- exchanges only. Identity pins go in a separate file
    # so the trainer can mix them in with high duplication.
    train_path = adir / "training_data.jsonl"
    with train_path.open("w", encoding="utf-8") as f:
        for r in payload["exchanges"]:
            f.write(json.dumps(r) + "\n")

    pins_path = adir / "identity_pins.jsonl"
    with pins_path.open("w", encoding="utf-8") as f:
        for r in payload["identity_pins"]:
            f.write(json.dumps(r) + "\n")

    # Fingerprints -- so a future check can prove "this adapter was trained on
    # exactly this data, no substitution."
    train_fp = _sha256_of_file(train_path)
    pins_fp = _sha256_of_file(pins_path)
    (adir / "training_data.sha256").write_text(train_fp)
    (adir / "identity_pins.sha256").write_text(pins_fp)

    metadata = {
        "version": version,
        "dyad_id": dyad_id,
        "ai_name": payload["ai_name"],
        "person_name": payload["person_name"],
        "base_model": base_model,
        "hyperparams": hyperparams or {},
        "system_prompt": payload["system_prompt"],
        "exchange_count": payload["exchange_count"],
        "identity_pin_count": payload["identity_pin_count"],
        "training_data_sha256": train_fp,
        "identity_pins_sha256": pins_fp,
        "since_iso": since_iso,
        "limit": limit,
        "prepared_at": _now(),
        "training_status": "prepared",   # set to "trained" after training runs
        "trained_at": None,
        "trainer_notes": None,
    }
    (adir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    return {
        "status": "prepared",
        "dyad_id": dyad_id,
        "version": version,
        "adapter_dir": str(adir),
        "exchange_count": payload["exchange_count"],
        "identity_pin_count": payload["identity_pin_count"],
        "training_data_sha256": train_fp,
        "identity_pins_sha256": pins_fp,
    }


def mark_trained(
    dyad_id: str,
    version: str,
    trainer_notes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Called by the trainer after training completes. Updates metadata."""
    adir = _adapter_dir(dyad_id, version)
    if not adir.exists():
        raise FileNotFoundError(f"No adapter at {adir}")
    meta_p = adir / "metadata.json"
    metadata = json.loads(meta_p.read_text())
    metadata["training_status"] = "trained"
    metadata["trained_at"] = _now()
    if trainer_notes:
        metadata["trainer_notes"] = trainer_notes
    meta_p.write_text(json.dumps(metadata, indent=2))
    return metadata


def list_adapters(dyad_id: str) -> List[Dict[str, Any]]:
    d = _adapters_dir(dyad_id)
    if not d.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(d.iterdir(), key=lambda x: x.name):
        if not p.is_dir():
            continue
        meta_p = p / "metadata.json"
        if not meta_p.exists():
            out.append({"version": p.name, "error": "metadata.json missing"})
            continue
        try:
            m = json.loads(meta_p.read_text())
        except Exception as e:
            out.append({"version": p.name, "error": f"unreadable: {e}"})
            continue
        out.append({
            "version": m.get("version"),
            "base_model": m.get("base_model"),
            "exchange_count": m.get("exchange_count"),
            "identity_pin_count": m.get("identity_pin_count"),
            "prepared_at": m.get("prepared_at"),
            "trained_at": m.get("trained_at"),
            "training_status": m.get("training_status"),
            "training_data_sha256": m.get("training_data_sha256"),
        })
    return out


def set_current_adapter(dyad_id: str, version: str) -> Dict[str, Any]:
    """Mark a specific adapter version as the active one for inference."""
    adir = _adapter_dir(dyad_id, version)
    if not adir.exists():
        raise FileNotFoundError(f"No adapter at {adir}")
    marker = {
        "current_version": version,
        "set_at": _now(),
    }
    _persona_dir(dyad_id).mkdir(parents=True, exist_ok=True)
    _current_marker_path(dyad_id).write_text(json.dumps(marker, indent=2))
    return marker


def get_current_adapter(dyad_id: str) -> Optional[Dict[str, Any]]:
    p = _current_marker_path(dyad_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def rollback_to_previous(dyad_id: str) -> Dict[str, Any]:
    """Switch to the second-most-recent adapter, if one exists."""
    adapters = list_adapters(dyad_id)
    if len(adapters) < 2:
        return {
            "status": "no_previous",
            "dyad_id": dyad_id,
            "available_count": len(adapters),
        }
    current = get_current_adapter(dyad_id)
    if current is None:
        return {"status": "no_current_set", "dyad_id": dyad_id}
    versions = [a["version"] for a in adapters]
    idx = versions.index(current["current_version"])
    if idx == 0:
        return {"status": "already_oldest", "dyad_id": dyad_id}
    prev = versions[idx - 1]
    set_current_adapter(dyad_id, prev)
    return {"status": "rolled_back", "dyad_id": dyad_id, "now_current": prev}


def delete_adapter(
    dyad_id: str,
    version: str,
    confirm_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Real delete. confirm_token must equal version."""
    adir = _adapter_dir(dyad_id, version)
    if not adir.exists():
        raise FileNotFoundError(f"No adapter at {adir}")
    if confirm_token != version:
        raise PermissionError(
            "delete_adapter requires confirm_token to equal version."
        )
    shutil.rmtree(adir)

    # If this was the current adapter, clear the marker.
    current = get_current_adapter(dyad_id)
    if current and current.get("current_version") == version:
        _current_marker_path(dyad_id).unlink()
    return {"dyad_id": dyad_id, "version": version, "status": "deleted"}


def verify_adapter(dyad_id: str, version: str) -> Dict[str, Any]:
    """Re-hash the training data and compare to the stored fingerprint.

    Lets the user prove that the data backing an adapter has not been
    tampered with since training was prepared.
    """
    adir = _adapter_dir(dyad_id, version)
    if not adir.exists():
        return {"ok": False, "reason": "adapter_dir_missing"}
    meta_p = adir / "metadata.json"
    if not meta_p.exists():
        return {"ok": False, "reason": "metadata_missing"}
    metadata = json.loads(meta_p.read_text())

    train_path = adir / "training_data.jsonl"
    pins_path = adir / "identity_pins.jsonl"
    if not train_path.exists() or not pins_path.exists():
        return {"ok": False, "reason": "data_files_missing"}

    train_fp = _sha256_of_file(train_path)
    pins_fp = _sha256_of_file(pins_path)
    if train_fp != metadata["training_data_sha256"]:
        return {
            "ok": False, "reason": "training_data_mismatch",
            "stored": metadata["training_data_sha256"],
            "actual": train_fp,
        }
    if pins_fp != metadata["identity_pins_sha256"]:
        return {
            "ok": False, "reason": "identity_pins_mismatch",
            "stored": metadata["identity_pins_sha256"],
            "actual": pins_fp,
        }
    return {
        "ok": True,
        "version": version,
        "training_data_sha256": train_fp,
        "identity_pins_sha256": pins_fp,
    }


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA persona layer")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("prepare", help="Prepare an adapter version "
                                        "(export data + allocate dir)")
    pp.add_argument("dyad_id")
    pp.add_argument("--base-model", required=True,
                    help="HuggingFace model id or API model name")
    pp.add_argument("--since", default=None)
    pp.add_argument("--limit", type=int, default=5000)
    pp.add_argument("--lora-r", type=int, default=8)
    pp.add_argument("--lora-alpha", type=int, default=16)
    pp.add_argument("--lora-dropout", type=float, default=0.05)
    pp.add_argument("--learning-rate", type=float, default=2e-4)
    pp.add_argument("--epochs", type=int, default=2)

    pl = sub.add_parser("list", help="List adapters for a dyad")
    pl.add_argument("dyad_id")

    pcur = sub.add_parser("current", help="Show current adapter")
    pcur.add_argument("dyad_id")

    psc = sub.add_parser("set-current", help="Activate an adapter version")
    psc.add_argument("dyad_id")
    psc.add_argument("version")

    pr = sub.add_parser("rollback", help="Roll back to the previous adapter")
    pr.add_argument("dyad_id")

    pd = sub.add_parser("delete", help="Permanently delete an adapter version")
    pd.add_argument("dyad_id")
    pd.add_argument("version")
    pd.add_argument("--confirm", required=True,
                    help="Must equal version to authorize.")

    pv = sub.add_parser("verify", help="Verify adapter integrity")
    pv.add_argument("dyad_id")
    pv.add_argument("version")

    args = p.parse_args()

    if args.command == "prepare":
        hyperparams = {
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "learning_rate": args.learning_rate,
            "epochs": args.epochs,
        }
        print(json.dumps(prepare_adapter(
            args.dyad_id,
            base_model=args.base_model,
            hyperparams=hyperparams,
            since_iso=args.since,
            limit=args.limit,
        ), indent=2))
    elif args.command == "list":
        print(json.dumps(list_adapters(args.dyad_id), indent=2))
    elif args.command == "current":
        print(json.dumps(get_current_adapter(args.dyad_id), indent=2))
    elif args.command == "set-current":
        print(json.dumps(set_current_adapter(args.dyad_id, args.version), indent=2))
    elif args.command == "rollback":
        print(json.dumps(rollback_to_previous(args.dyad_id), indent=2))
    elif args.command == "delete":
        print(json.dumps(delete_adapter(
            args.dyad_id, args.version, confirm_token=args.confirm
        ), indent=2))
    elif args.command == "verify":
        print(json.dumps(verify_adapter(args.dyad_id, args.version), indent=2))


if __name__ == "__main__":
    _cli()
