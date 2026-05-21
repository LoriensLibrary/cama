"""Maintenance tools: backfill_embeddings, recompute_rel_degrees."""

import json

from cama_mcp import (
    EMBEDDING_API_KEY,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    _get_embedding,
    _load_local_model,
    _now,
    get_db,
)


async def cama_backfill_embeddings(batch_size: int = 50) -> str:
    """Backfill embeddings for memories that don't have them yet. Works with local or API embeddings."""
    if EMBEDDING_PROVIDER == "none":
        return json.dumps({"error":"EMBEDDING_PROVIDER is 'none'","backfilled":0})
    if EMBEDDING_PROVIDER in ("api",) and not EMBEDDING_API_KEY:
        return json.dumps({"error":"No EMBEDDING_API_KEY set and provider is 'api'","backfilled":0})
    # For auto/local, check if local model loads
    if EMBEDDING_PROVIDER in ("auto", "local") and _load_local_model() is None and not EMBEDDING_API_KEY:
        return json.dumps({"error":"No local model and no API key — install sentence-transformers: pip install sentence-transformers","backfilled":0})
    c = get_db()
    try:
        rows = c.execute("SELECT m.id, m.raw_text FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL LIMIT ?", (batch_size,)).fetchall()
        count = 0
        for r in rows:
            vec = await _get_embedding(r["raw_text"])
            if vec:
                c.execute("INSERT OR REPLACE INTO memory_embeddings (memory_id,embedding_json,model,computed_at) VALUES (?,?,?,?)",
                          (r["id"], json.dumps(vec), EMBEDDING_MODEL, _now()))
                c.commit()  # Commit each embedding individually for resilience
                count += 1
        remaining = c.execute("SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL").fetchone()["c"]
        return json.dumps({"backfilled":count,"remaining":remaining,"batch_size":batch_size},indent=2)
    finally: c.close()


async def cama_recompute_rel_degrees() -> str:
    """Recompute all precomputed rel_degree values from edges."""
    c = get_db()
    try:
        c.execute("UPDATE memories SET rel_degree=0")
        rows = c.execute("SELECT from_id, to_id FROM edges").fetchall()
        counts = {}
        for r in rows:
            counts[r["from_id"]] = counts.get(r["from_id"], 0) + 1
            counts[r["to_id"]] = counts.get(r["to_id"], 0) + 1
        for mid, deg in counts.items():
            c.execute("UPDATE memories SET rel_degree=? WHERE id=?", (deg, mid))
        c.commit()
        return json.dumps({"recomputed":len(counts),"total_edges":len(rows)},indent=2)
    finally: c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_backfill_embeddings",
        annotations={"title":"Backfill Embeddings","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_backfill_embeddings)
    mcp.tool(
        name="cama_recompute_rel_degrees",
        annotations={"title":"Recompute Rel Degrees","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_recompute_rel_degrees)
