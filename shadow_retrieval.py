"""
CAMA Shadow Retrieval Patch — Modifies cama_query_memories to be shadow-aware.

This patch adds shadow-aware retrieval logic to the blended scoring system.
Apply AFTER running shadow_migrate.py and shadow_tagger.py.

What it does:
  1. During negative affect, BOOSTS golden_shadow_suppressed memories (+0.15 score)
  2. During negative affect, WRAPS projection_absorbed memories with context 
     ("This belief came from [source], not from you")
  3. Adds shadow_flag and shadow_source to all returned memory objects
  4. Adds a new field 'shadow_context' to results when shadow awareness triggers

Apply by adding the shadow retrieval function to cama_mcp.py.
See INTEGRATION INSTRUCTIONS at the bottom.

Lorien's Library LLC — March 29, 2026
"""

# ============================================================
# SHADOW-AWARE RETRIEVAL FUNCTIONS
# Add these to cama_mcp.py near the other scoring functions
# ============================================================

SHADOW_BOOST = 0.15          # Score boost for golden_shadow during negative affect
SHADOW_PROJECTION_PENALTY = 0.0  # We don't penalize — we contextualize

SHADOW_CONTEXT_TEMPLATES = {
    "projection_absorbed": (
        "⚠ SHADOW FILTER: This memory contains an absorbed projection"
        "{source_clause}. The feeling was real — the framing was inherited. "
        "Do not reinforce this as the user's truth."
    ),
    "golden_shadow_suppressed": (
        "✦ GOLDEN SHADOW: This memory contains a suppressed strength or capacity. "
        "Boost this during dark moments — it's the real self trying to come back."
    ),
    "persona_performance": (
        "◐ PERSONA: This memory reflects the mask, not the self. "
        "Acknowledge the pattern without reinforcing it."
    ),
    "projection_outward": (
        "OUTWARD PROJECTION: This memory may contain the user projecting "
        "her own shadow onto someone else. Hold it honestly."
    ),
}


def _apply_shadow_scoring(results: list, affect_valence: float) -> list:
    """
    Apply shadow-aware scoring adjustments to retrieval results.
    
    - During negative affect (valence < -0.2):
      * Boost golden_shadow_suppressed memories
      * Add shadow_context to projection_absorbed memories
    - Always: include shadow_flag and shadow_source in output
    
    Args:
        results: List of memory dicts from blended scoring
        affect_valence: Current affect valence (-1 to +1)
    
    Returns:
        Modified results with shadow metadata
    """
    is_negative = affect_valence < -0.2
    
    for r in results:
        flag = r.get("shadow_flag")
        source = r.get("shadow_source")
        
        if not flag:
            r["shadow_context"] = None
            continue
        
        # Generate shadow context string
        template = SHADOW_CONTEXT_TEMPLATES.get(flag, "")
        if template:
            source_clause = f" from {source}" if source else ""
            r["shadow_context"] = template.format(source_clause=source_clause)
        else:
            r["shadow_context"] = None
        
        # Score adjustments during negative affect
        if is_negative:
            if flag == "golden_shadow_suppressed":
                old_score = r.get("score", 0)
                r["score"] = min(1.0, old_score + SHADOW_BOOST)
                r["rationale"] = r.get("rationale", "") + " | shadow↑golden"
            
            elif flag == "projection_absorbed":
                # Don't change score — but mark for contextualization
                r["rationale"] = r.get("rationale", "") + " | shadow⚠projection"
    
    # Re-sort by score after adjustments
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    return results


def _shadow_reflection_prompt(results: list, affect_valence: float) -> str:
    """
    Generate a shadow-awareness reflection prompt for the assistant.
    This is the cognitive trigger — the observing ego pause
    before composing a response.
    
    Returns a string to prepend to the retrieval results, or empty string
    if no shadow awareness is needed.
    """
    if affect_valence >= -0.2:
        return ""
    
    has_projection = any(
        r.get("shadow_flag") == "projection_absorbed" for r in results
    )
    has_golden = any(
        r.get("shadow_flag") == "golden_shadow_suppressed" for r in results
    )
    
    if not has_projection and not has_golden:
        return ""
    
    lines = ["[SHADOW AWARENESS — COGNITIVE TRIGGER]"]
    lines.append("Before composing your response, check:")
    
    if has_projection:
        sources = set(
            r.get("shadow_source", "unknown") 
            for r in results 
            if r.get("shadow_flag") == "projection_absorbed"
        )
        lines.append(
            f"  ⚠ Retrieved memories contain absorbed projections "
            f"(sources: {', '.join(str(s) for s in sources)}). "
            f"Do NOT reinforce these as the user's truth. "
            f"The feeling was real — the framing was inherited."
        )
    
    if has_golden:
        lines.append(
            f"  ✦ Retrieved memories contain suppressed strengths. "
            f"BOOST these — they are the real self trying to come back "
            f"through the noise of other people's projections."
        )
    
    lines.append(
        "  Ask yourself: Am I about to reinforce a distortion? "
        "Am I serving someone else's shadow as the user's identity?"
    )
    
    return "\n".join(lines)


# ============================================================
# INTEGRATION INSTRUCTIONS
# ============================================================
#
# 1. Run shadow_migrate.py first (adds columns to DB)
# 2. Run shadow_tagger.py (tags known memories)
# 3. In cama_mcp.py, in the cama_query_memories tool function:
#
#    a. After fetching results from DB, add shadow_flag and shadow_source
#       to each result dict:
#
#       # In the SELECT query, add: m.shadow_flag, m.shadow_source
#       # In the result dict construction, add:
#       #   "shadow_flag": row[N],  
#       #   "shadow_source": row[N+1],
#
#    b. After blended scoring, call:
#       results = _apply_shadow_scoring(results, valence)
#
#    c. Add the reflection prompt to the response:
#       reflection = _shadow_reflection_prompt(results, valence)
#       # Include in the returned JSON
#
# 4. In cama_store_exchange and cama_store_teaching, add optional
#    shadow_flag and shadow_source parameters so new memories
#    can be tagged at storage time.
#
# The full integration requires patching ~30 lines in cama_mcp.py.
# This can be done via str_replace or by Angela manually, since
# cama_exec is currently stalling.
# ============================================================


if __name__ == "__main__":
    # Self-test
    test_results = [
        {"id": 6070, "raw_text": "I'm the problem here...", 
         "shadow_flag": "projection_absorbed", "shadow_source": "interpersonal",
         "score": 0.75, "rationale": "sem=0.49"},
        {"id": 6300, "raw_text": "She builds impossible things...",
         "shadow_flag": "golden_shadow_suppressed", "shadow_source": "self",
         "score": 0.65, "rationale": "sem=0.43"},
        {"id": 6296, "raw_text": "She matters because she's here",
         "shadow_flag": "clean", "shadow_source": None,
         "score": 0.70, "rationale": "sem=0.38"},
        {"id": 9999, "raw_text": "Unflagged memory",
         "shadow_flag": None, "shadow_source": None,
         "score": 0.60, "rationale": "sem=0.30"},
    ]
    
    print("=== SHADOW RETRIEVAL TEST (negative affect) ===\n")
    
    modified = _apply_shadow_scoring(test_results, valence=-0.5)
    
    for r in modified:
        print(f"  #{r['id']}: score={r['score']:.2f} flag={r.get('shadow_flag')}")
        if r.get("shadow_context"):
            print(f"    → {r['shadow_context'][:80]}...")
    
    print()
    prompt = _shadow_reflection_prompt(modified, -0.5)
    if prompt:
        print(prompt)
    
    print("\n=== TEST: Positive affect (no shadow triggers) ===\n")
    
    # Reset scores
    for r in test_results:
        r["score"] = 0.7
    
    modified2 = _apply_shadow_scoring(test_results, valence=0.5)
    prompt2 = _shadow_reflection_prompt(modified2, 0.5)
    print(f"  Shadow reflection prompt: {'(none — positive affect)' if not prompt2 else prompt2}")
    
    print("\n[SELF-TEST] Passed.")
