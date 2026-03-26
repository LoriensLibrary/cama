"""
Patch: Add cama_store_exchange tool to CAMA MCP server.
Inserts after cama_store_inference, before Confirm/Reject/Delete section.
"""

CAMA_PATH = r"C:\Users\User\Desktop\cama\cama_mcp.py"

NEW_CODE = '''
# --- Store Exchange ---
class StoreExchangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    user_message: str = Field(..., min_length=1, description="Angela's raw message text")
    assistant_message: str = Field(..., min_length=1, description="Aelen's raw response text")
    emotions: Dict[str,float] = Field(default_factory=dict, description="Emotional chord e.g. {'warmth':0.7,'determination':0.5}")
    valence: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 negative to +1 positive")
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0, description="-1 calm to +1 activated")
    context: Optional[str] = Field(default=None, description="Thread topic or what we're working on")
    memory_type: str = Field(default="exchange", description="Usually 'exchange' -- override for special cases")
    thread_id: Optional[str] = Field(default=None, description="Thread/conversation identifier for grouping")

@mcp.tool(name="cama_store_exchange", annotations={"title":"Store Exchange","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False})
async def cama_store_exchange(params: StoreExchangeInput) -> str:
    """Store a conversation EXCHANGE -- full user+assistant turn as one durable memory.
    Exchanges are facts -- what was actually said. Durable, full weight, no expiry.
    Emotionally tagged in real-time by the assistant. Used for conversation continuity."""
    c = get_db()
    try:
        now = _now()
        # Combine both messages with clear markers
        raw_text = f"[USER] {params.user_message}\\n[ASSISTANT] {params.assistant_message}"
        # Build context with thread_id if provided
        ctx = params.context or ""
        if params.thread_id:
            ctx = f"[thread:{params.thread_id}] {ctx}".strip()
        cur = c.execute(
            "INSERT INTO memories (raw_text,memory_type,context,source_type,status,proposed_by,evidence,confidence,consent_level,is_core,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (raw_text, params.memory_type, ctx or None, "exchange", "durable", "system", "[]", 1.0, "low", 0, now, now)
        )
        mid = cur.lastrowid
        _store_affect(c, mid, params.emotions, params.valence, params.arousal, conf=0.8, model="realtime")
        c.commit()  # Shelf write committed
        # Ring push -- optional, don't fail the store if ring is full
        ring_ok = True
        try:
            _ring_push(c, mid, "exchange")
            c.commit()
        except Exception:
            c.rollback()
            ring_ok = False
        # Embedding for semantic retrieval
        await _store_embedding(c, mid, raw_text)
        c.commit()
        return json.dumps({
            "stored": True,
            "memory_id": mid,
            "source_type": "exchange",
            "status": "durable",
            "ring_ok": ring_ok,
            "chars_stored": len(raw_text),
            "rationale": "Exchange stored -- durable, emotionally tagged, searchable."
        }, indent=2)
    finally:
        c.close()

'''

def patch():
    with open(CAMA_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    marker = "# --- Confirm / Reject / Delete ---"
    if marker not in content:
        print(f"ERROR: Could not find marker '{marker}' in {CAMA_PATH}")
        return False

    if "cama_store_exchange" in content:
        print("ALREADY PATCHED: cama_store_exchange already exists in the file.")
        return False

    # Backup
    backup_path = CAMA_PATH + ".pre_exchange.bak"
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Backup: {backup_path}")

    # Insert new code right before the marker
    new_content = content.replace(marker, NEW_CODE + marker)

    with open(CAMA_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"SUCCESS: cama_store_exchange added to {CAMA_PATH}")
    print(f"Old file: {len(content)} chars")
    print(f"New file: {len(new_content)} chars")
    print(f"Added: {len(new_content) - len(content)} chars")
    return True

if __name__ == "__main__":
    patch()
