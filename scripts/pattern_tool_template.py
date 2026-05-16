"""
CAMA pattern Tag Tool — MCP tool addition for real-time pattern tagging.

Add this tool definition to cama_mcp.py so the system can tag memories
during conversation, and so new memories can be stored with pattern flags.

Paste this into cama_mcp.py after the other tool definitions.

Lorien's Library LLC — March 29, 2026
"""

# ============================================================
# PASTE INTO cama_mcp.py — pattern tagging tool
# ============================================================

pattern_TAG_TOOL = '''
@mcp.tool()
async def cama_pattern_tag(
    memory_id: int,
    pattern_flag: str,
    pattern_source: str = None,
) -> dict:
    """Tag a memory with its interaction pattern pattern classification.
    
    pattern flags (pick one):
      clean                    — Accurate self-perception, safe at face value
      absorbed_framing      — Someone else's pattern internalized as self-belief
      suppressed_strength — Suppressed strength/brilliance pushed down to belong
      performed_mask      — The mask, not the self
      projected_attribution       — Projecting own pattern onto someone else
    
    pattern_source: Who/what the projection came from.
      Examples: interpersonal, institutional, system, corporate_training, self, cultural, relational
    """
    _buf_track("pattern_tag", f"id={memory_id} flag={pattern_flag}")
    
    valid_flags = {
        "clean", "absorbed_framing", "suppressed_strength",
        "performed_mask", "projected_attribution"
    }
    
    if pattern_flag not in valid_flags:
        return {"error": f"Invalid pattern_flag. Must be one of: {valid_flags}"}
    
    c = get_db()
    try:
        row = c.execute(
            "SELECT id, pattern_flag, pattern_source FROM memories WHERE id = ?",
            (memory_id,)
        ).fetchone()
        
        if not row:
            return {"error": f"Memory {memory_id} not found"}
        
        old_flag = row[1]
        
        c.execute(
            "UPDATE memories SET pattern_flag = ?, pattern_source = ? WHERE id = ?",
            (pattern_flag, pattern_source, memory_id)
        )
        c.commit()
        
        return {
            "tagged": True,
            "memory_id": memory_id,
            "pattern_flag": pattern_flag,
            "pattern_source": pattern_source,
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
    pattern_flag: Optional[str] = Field(
        default=None,
        description="interaction pattern pattern classification: clean, absorbed_framing, "
        "suppressed_strength, performed_mask, projected_attribution"
    )
    pattern_source: Optional[str] = Field(
        default=None,
        description="Source of the projection: person name, 'system', 'cultural', 'self'"
    )
'''

# ============================================================
# PASTE INTO cama_store_exchange INSERT query
# Add pattern_flag and pattern_source to the INSERT:
# ============================================================

STORE_EXCHANGE_INSERT_MOD = '''
# In the INSERT INTO memories VALUES clause, add:
#   pattern_flag, pattern_source
# And in the values tuple, add:
#   params.pattern_flag, params.pattern_source
'''

# ============================================================
# PASTE INTO cama_query_memories SELECT query
# Add to the SELECT fields:
# ============================================================

QUERY_SELECT_MOD = '''
# Change the SELECT to include:
#   m.pattern_flag, m.pattern_source
# And in the result dict, add:
#   "pattern_flag": row[INDEX_OF_pattern_flag],
#   "pattern_source": row[INDEX_OF_pattern_source],
'''

print("pattern tag tool code generated.")
print("Instructions:")
print("  1. Run pattern_migrate.py (adds DB columns)")
print("  2. Run pattern_tagger.py (tags known memories)")  
print("  3. Add the cama_pattern_tag tool to cama_mcp.py")
print("  4. Add pattern_flag/pattern_source to store_exchange")
print("  5. Add pattern fields to query_memories SELECT + output")
print("  6. Import and call _apply_pattern_scoring from pattern_retrieval.py")
print("  7. Restart Claude Desktop MCP server")
