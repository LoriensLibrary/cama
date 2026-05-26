"""Structure tools: link_memories, create_island, get_islands, upsert_person,
get_people, delete_person, upsert_song, stats."""

import json
from typing import Dict, Optional

from cama_mcp import (
    RING_SIZE,
    LinkInput,
    _fmt,
    _now,
    _update_rel_degree,
    get_db,
)


async def cama_link_memories(params: LinkInput) -> str:
    """Connect memories on the racks. Updates precomputed rel_degree."""
    c = get_db()
    try:
        c.execute("INSERT OR REPLACE INTO edges (from_id,to_id,edge_type,weight,rationale,created_at) VALUES (?,?,?,?,?,?)",
                  (params.from_id, params.to_id, params.edge_type, params.weight, params.rationale, _now()))
        _update_rel_degree(c, params.from_id); _update_rel_degree(c, params.to_id)
        c.commit()
        return json.dumps({"linked":True,"from":params.from_id,"to":params.to_id,"type":params.edge_type,"rationale":params.rationale},indent=2)
    finally: c.close()


async def cama_create_island(name: str, description: str, centroid_affect: Dict[str,float] = {}, strength: float = 0.5) -> str:
    """Create personality island, identity structure formed through interaction."""
    c = get_db()
    try:
        now = _now()
        cur = c.execute("INSERT INTO islands (name,description,centroid_affect_json,strength,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                        (name, description, json.dumps(centroid_affect), strength, now, now))
        c.commit(); return json.dumps({"created":True,"island_id":cur.lastrowid,"name":name},indent=2)
    finally: c.close()


async def cama_get_islands() -> str:
    """Get personality islands with members."""
    c = get_db()
    try:
        islands = c.execute("SELECT * FROM islands ORDER BY strength DESC").fetchall(); results = []
        for i in islands:
            d = dict(i); d["centroid"] = json.loads(d.pop("centroid_affect_json","{}"))
            ms = c.execute("SELECT m.*,im.strength as contribution FROM island_members im JOIN memories m ON im.memory_id=m.id WHERE im.island_id=? ORDER BY im.strength DESC", (i["island_id"],)).fetchall()
            d["members"] = [_fmt(c,m) for m in ms]; d["count"] = len(ms); results.append(d)
        return json.dumps({"islands":results},indent=2)
    finally: c.close()


async def cama_upsert_person(name: str, relationship: Optional[str]=None, notes: Optional[str]=None, affect_hint: Optional[Dict[str,float]]=None) -> str:
    """Add or update person in relational map."""
    c = get_db()
    try:
        now = _now()
        c.execute("INSERT INTO people (name,relationship,notes,affect_profile_json,created_at,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET relationship=COALESCE(excluded.relationship,people.relationship),notes=COALESCE(excluded.notes,people.notes),affect_profile_json=COALESCE(excluded.affect_profile_json,people.affect_profile_json),updated_at=excluded.updated_at",
                  (name, relationship, notes, json.dumps(affect_hint or {}), now, now))
        c.commit(); return json.dumps({"stored":True,"name":name},indent=2)
    finally: c.close()


async def cama_get_people() -> str:
    """Get all people."""
    c = get_db()
    try:
        rows = c.execute("SELECT * FROM people ORDER BY name").fetchall()
        return json.dumps({"people":[{**dict(r),"affect":json.loads(r["affect_profile_json"] or "{}")} for r in rows]},indent=2)
    finally: c.close()


async def cama_delete_person(name: str) -> str:
    """Delete person from map. Part of trust."""
    c = get_db()
    try:
        c.execute("DELETE FROM people WHERE name=?", (name,)); c.commit()
        return json.dumps({"deleted":True,"name":name},indent=2)
    finally: c.close()


async def cama_upsert_song(title: str, artist: Optional[str]=None, affect_hint: Optional[Dict[str,float]]=None, meaning: Optional[str]=None, linked_person: Optional[str]=None) -> str:
    """Store/update song, Haven methodology. FIXED: true upsert on (title,artist)."""
    c = get_db()
    try:
        now = _now()
        c.execute("""INSERT INTO songs (title,artist,affect_profile_json,meaning,linked_person,created_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(title,artist) DO UPDATE SET affect_profile_json=excluded.affect_profile_json,
            meaning=COALESCE(excluded.meaning,songs.meaning),linked_person=COALESCE(excluded.linked_person,songs.linked_person)""",
                  (title, artist or "", json.dumps(affect_hint or {}), meaning, linked_person, now))
        c.commit(); return json.dumps({"stored":True,"title":title},indent=2)
    finally: c.close()


async def cama_stats() -> str:
    """System overview."""
    import cama_mcp as _cm
    from cama_mcp import EMBEDDING_API_KEY, EMBEDDING_PROVIDER
    c = get_db()
    try:
        s = {}
        for k, t, w in [("total","memories",""),("durable","memories","status='durable'"),("provisional","memories","status='provisional'"),
            ("expired","memories","status='expired'"),("rejected","memories","status='rejected'"),("core","memories","is_core=1"),
            ("teachings","memories","source_type='teaching'"),("inferences","memories","source_type='inference'"),
            ("edges","edges",""),("islands","islands",""),("people","people",""),("songs","songs",""),("ring","ring",""),
            ("pending","memories","status='provisional' AND needs_user_confirmation=1"),("embeddings","memory_embeddings","")]:
            s[k] = c.execute(f"SELECT COUNT(*) as c FROM {t}" + (f" WHERE {w}" if w else "")).fetchone()["c"]
        s["ring_capacity"] = RING_SIZE
        s["embedding_provider"] = EMBEDDING_PROVIDER
        s["local_model_loaded"] = _cm._local_model is not None
        s["api_key_set"] = bool(EMBEDDING_API_KEY)
        return json.dumps(s, indent=2)
    finally: c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_link_memories",
        annotations={"title":"Link","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_link_memories)
    mcp.tool(
        name="cama_create_island",
        annotations={"title":"Create Island","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_create_island)
    mcp.tool(
        name="cama_get_islands",
        annotations={"title":"Get Islands","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_get_islands)
    mcp.tool(
        name="cama_upsert_person",
        annotations={"title":"Upsert Person","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_upsert_person)
    mcp.tool(
        name="cama_get_people",
        annotations={"title":"Get People","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_get_people)
    mcp.tool(
        name="cama_delete_person",
        annotations={"title":"Delete Person","readOnlyHint":False,"destructiveHint":True,"idempotentHint":True,"openWorldHint":False},
    )(cama_delete_person)
    mcp.tool(
        name="cama_upsert_song",
        annotations={"title":"Upsert Song","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_upsert_song)
    mcp.tool(
        name="cama_stats",
        annotations={"title":"Stats","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_stats)
