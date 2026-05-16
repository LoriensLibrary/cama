#!/usr/bin/env python3
"""Provenance invariants — verifies that cama_store_* writes to the DB exactly
what it claims in its return value.

Regression test for the bug where cama_store_inference returned
{"source_type": "inference", "status": "provisional"} while inserting
("journal", "durable") into the memories table — breaking the safety
boundary that the README and the MCP design contract both depend on.

Run with: python test_provenance.py
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

# Sandbox the DB and disable embeddings BEFORE importing cama_mcp.
_TMPDIR = tempfile.mkdtemp(prefix="cama_test_")
os.environ["CAMA_DB_PATH"] = os.path.join(_TMPDIR, "memory.db")
os.environ["EMBEDDING_PROVIDER"] = "none"
os.environ["EMBEDDING_API_KEY"] = ""

import cama_mcp as m  # noqa: E402


def _row(memory_id: int):
    c = sqlite3.connect(os.environ["CAMA_DB_PATH"])
    c.row_factory = sqlite3.Row
    try:
        return c.execute(
            "SELECT id, source_type, status FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
    finally:
        c.close()


async def main() -> int:
    failures: list[str] = []

    # 1. Teaching: claims source_type=teaching, status=durable.
    res = await m.cama_store_teaching(m.StoreTeachingInput(
        raw_text="The Pacific Ocean is the largest ocean on Earth.",
        memory_type="fact",
    ))
    out = json.loads(res)
    row = _row(out["memory_id"])
    if not (out["source_type"] == "teaching" == row["source_type"]):
        failures.append(
            f"teaching source_type mismatch: returned={out['source_type']!r}, "
            f"stored={row['source_type']!r}"
        )
    if not (out["status"] == "durable" == row["status"]):
        failures.append(
            f"teaching status mismatch: returned={out['status']!r}, "
            f"stored={row['status']!r}"
        )

    # 2. Inference: claims source_type=inference, status=provisional.
    res = await m.cama_store_inference(m.StoreInferenceInput(
        raw_text="The user may prefer tea over coffee in the morning.",
        memory_type="pattern",
        confidence=0.6,
    ))
    out = json.loads(res)
    inf_id = out["memory_id"]
    row = _row(inf_id)
    if not (out["source_type"] == "inference" == row["source_type"]):
        failures.append(
            f"inference source_type mismatch: returned={out['source_type']!r}, "
            f"stored={row['source_type']!r}"
        )
    if not (out["status"] == "provisional" == row["status"]):
        failures.append(
            f"inference status mismatch: returned={out['status']!r}, "
            f"stored={row['status']!r}"
        )

    # 3. Confirming an inference promotes it to durable; source_type stays.
    res = await m.cama_confirm_memory(inf_id)
    out = json.loads(res)
    if out.get("error"):
        failures.append(f"confirm_memory rejected a valid inference: {out['error']}")
    row = _row(inf_id)
    if row["status"] != "durable":
        failures.append(
            f"after confirm: status should be 'durable', got {row['status']!r}"
        )
    if row["source_type"] != "inference":
        failures.append(
            f"after confirm: source_type should remain 'inference', got "
            f"{row['source_type']!r}"
        )

    # 4. Re-confirming a now-durable memory is rejected.
    res = await m.cama_confirm_memory(inf_id)
    out = json.loads(res)
    if not out.get("error"):
        failures.append("re-confirm of durable memory should error, got success")

    if failures:
        print("PROVENANCE TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PROVENANCE TEST PASSED (4 checks)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
