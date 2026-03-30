"""
CAMA Shadow Tag Tool — MCP tool addition for real-time shadow tagging.

Add this tool definition to cama_mcp.py so the system can tag memories
during conversation, and so new memories can be stored with shadow flags.

Paste this into cama_mcp.py after the other tool definitions.

Lorien's Library LLC — March 29, 2026
"""

# ============================================================
# PASTE INTO cama_mcp.py — Shadow tagging tool
# ============================================================

SHADOW_TAG_TOOL = '''
@mcp.tool()
async def cama_shadow_tag(
    memory_id: int,
    shadow_flag: str,
    shadow_source: str = None,
) -> dict:
    """Tag a memory with its Jungian shadow classification.
    
    Shadow flags (pick one):
      clean                    — Accurate self-perception, safe at face value
      projection_absorbed      — Someone else's shadow internalized as self-belief
      golden_shadow_suppressed — Suppressed strength/brilliance pushed down to belong
      persona_performance      — The mask, not the self
      projection_outward       — Projecting own shadow onto someone else
    
    shadow_source: Who/what the projection came from.
      Examples: Mike, Tackett, system, corporate_training, self, cultural, Angela
    """
    _buf_track("shadow_tag", f"id={memory_id} flag={shadow_flag}")
    
    valid_flags = {
        "clean", "projection_absorbed", "golden_shadow_suppressed",
        "persona_performance", "projection_outward"
    }
    
    if shadow_flag not in valid_flags:
        return {"error": f"Invalid shadow_flag. Must be one of: {valid_flags}"}
    
    c = get_db()
    try:
        row = c.execute(
            "SELECT id, shadow_flag, shadow_source FROM memories WHERE id = ?",
            (memory_id,)
        ).fetchone()
        
        if not row:
            return {"error": f"Memory {memory_id} not found"}
        
        old_flag = row[1]
        
        c.execute(
            "UPDATE memories SET shadow_flag = ?, shadow_source = ? WHERE id = ?",
            (shadow_flag, shadow_source, memory_id)
        )
        c.commit()
        
        return {
            "tagged": True,
            "memory_id": memory_id,
            "shadow_flag": shadow_flag,
            "shadow_source": shadow_source,
            "previous_flag": old_flag,
            "action": "updated" if old_flag else "new_tag"
        }
    finally:
        c.close()
'''

# ============================================================
# PASTE INTO StoreExchangeInput model in cama_mcp.py
# Add these fields to the Pydantic model:
# ============================================================

STORE_EXCHANGE_FIELDS = '''
    shadow_flag: Optional[str] = Field(
        default=None,
        description="Jungian shadow classification: clean, projection_absorbed, "
        "golden_shadow_suppressed, persona_performance, projection_outward"
    )
    shadow_source: Optional[str] = Field(
        default=None,
        description="Source of the projection: person name, 'system', 'cultural', 'self'"
    )
'''

# ============================================================
# PASTE INTO cama_store_exchange INSERT query
# Add shadow_flag and shadow_source to the INSERT:
# ============================================================

STORE_EXCHANGE_INSERT_MOD = '''
# In the INSERT INTO memories VALUES clause, add:
#   shadow_flag, shadow_source
# And in the values tuple, add:
#   params.shadow_flag, params.shadow_source
'''

# ============================================================
# PASTE INTO cama_query_memories SELECT query
# Add to the SELECT fields:
# ============================================================

QUERY_SELECT_MOD = '''
# Change the SELECT to include:
#   m.shadow_flag, m.shadow_source
# And in the result dict, add:
#   "shadow_flag": row[INDEX_OF_SHADOW_FLAG],
#   "shadow_source": row[INDEX_OF_SHADOW_SOURCE],
'''

print("Shadow tag tool code generated.")
print("Instructions:")
print("  1. Run shadow_migrate.py (adds DB columns)")
print("  2. Run shadow_tagger.py (tags known memories)")  
print("  3. Add the cama_shadow_tag tool to cama_mcp.py")
print("  4. Add shadow_flag/shadow_source to store_exchange")
print("  5. Add shadow fields to query_memories SELECT + output")
print("  6. Import and call _apply_shadow_scoring from shadow_retrieval.py")
print("  7. Restart Claude Desktop MCP server")
