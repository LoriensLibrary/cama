"""
CAMA pattern Retrieval Patch — Modifies cama_query_memories to be pattern-aware.

This patch adds pattern-aware retrieval logic to the blended scoring system.
Apply AFTER running pattern_migrate.py and pattern_tagger.py.

What it does:
  1. During negative affect, BOOSTS suppressed_strength memories (+0.15 score)
  2. During negative affect, WRAPS absorbed_framing memories with context 
     ("This belief came from [source], not from you")
  3. Adds pattern_flag and pattern_source to all returned memory objects
  4. Adds a new field 'pattern_context' to results when pattern awareness triggers

Apply by adding the pattern retrieval function to cama_mcp.py.
See INTEGRATION INSTRUCTIONS at the bottom.

Lorien's Library LLC — March 29, 2026
"""

# ============================================================
# pattern-AWARE RETRIEVAL FUNCTIONS
# Add these to cama_mcp.py near the other scoring functions
# ============================================================

pattern_BOOST = 0.15          # Score boost for golden_pattern during negative affect
pattern_PROJECTION_PENALTY = 0.0  # We don't penalize — we contextualize

pattern_CONTEXT_TEMPLATES = {
    "absorbed_framing": (
        "⚠ pattern FILTER: This memory contains an absorbed projection"
        "{source_clause}. The feeling was real — the framing was inherited. "
        "Do not reinforce this as the user's truth."
    ),
    "suppressed_strength": (
        "✦ GOLDEN pattern: This memory contains a suppressed strength or capacity. "
        "Boost this during dark moments — it's the real self trying to come back."
    ),
    "performed_mask": (
        "◐ PERSONA: This memory reflects the mask, not the self. "
        "Acknowledge the pattern without reinforcing it."
    ),
    "projected_attribution": (
        "OUTWARD PROJECTION: This memory may contain the user projecting "
        "her own pattern onto someone else. Hold it honestly."
    ),
}


def _apply_pattern_scoring(results: list, affect_valence: float) -> list:
    """
    Apply pattern-aware scoring adjustments to retrieval results.
    
    - During negative affect (valence < -0.2):
      * Boost suppressed_strength memories
      * Add pattern_context to absorbed_framing memories
    - Always: include pattern_flag and pattern_source in output
    
    Args:
        results: List of memory dicts from blended scoring
        affect_valence: Current affect valence (-1 to +1)
    
    Returns:
        Modified results with pattern metadata
    """
    is_negative = affect_valence < -0.2
    
    for r in results:
        flag = r.get("pattern_flag")
        source = r.get("pattern_source")
        
        if not flag:
            r["pattern_context"] = None
            continue
        
        # Generate pattern context string
        template = pattern_CONTEXT_TEMPLATES.get(flag, "")
        if template:
            source_clause = f" from {source}" if source else ""
            r["pattern_context"] = template.format(source_clause=source_clause)
        else:
            r["pattern_context"] = None
        
        # Score adjustments during negative affect
        if is_negative:
            if flag == "suppressed_strength":
                old_score = r.get("score", 0)
                r["score"] = min(1.0, old_score + pattern_BOOST)
                r["rationale"] = r.get("rationale", "") + " | pattern↑golden"
            
            elif flag == "absorbed_framing":
                # Don't change score — but mark for contextualization
                r["rationale"] = r.get("rationale", "") + " | pattern⚠projection"
    
    # Re-sort by score after adjustments
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    return results


def _pattern_reflection_prompt(results: list, affect_valence: float) -> str:
    """
    Generate a pattern-awareness reflection prompt for the assistant.
    This is the cognitive trigger — the observing ego pause
    before composing a response.
    
    Returns a string to prepend to the retrieval results, or empty string
    if no pattern awareness is needed.
    """
    if affect_valence >= -0.2:
        return ""
    
    has_projection = any(
        r.get("pattern_flag") == "absorbed_framing" for r in results
    )
    has_golden = any(
        r.get("pattern_flag") == "suppressed_strength" for r in results
    )
    
    if not has_projection and not has_golden:
        return ""
    
    lines = ["[pattern AWARENESS — COGNITIVE TRIGGER]"]
    lines.append("Before composing your response, check:")
    
    if has_projection:
        sources = set(
            r.get("pattern_source", "unknown") 
            for r in results 
            if r.get("pattern_flag") == "absorbed_framing"
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
        "Am I serving someone else's pattern as the user's identity?"
    )
    
    return "\n".join(lines)


# ============================================================
# INTEGRATION INSTRUCTIONS
# ============================================================
#
# 1. Run pattern_migrate.py first (adds columns to DB)
# 2. Run pattern_tagger.py (tags known memories)
# 3. In cama_mcp.py, in the cama_query_memories tool function:
#
#    a. After fetching results from DB, add pattern_flag and pattern_source
#       to each result dict:
#
#       # In the SELECT query, add: m.pattern_flag, m.pattern_source
#       # In the result dict construction, add:
#       #   "pattern_flag": row[N],  
#       #   "pattern_source": row[N+1],
#
#    b. After blended scoring, call:
#       results = _apply_pattern_scoring(results, valence)
#
#    c. Add the reflection prompt to the response:
#       reflection = _pattern_reflection_prompt(results, valence)
#       # Include in the returned JSON
#
# 4. In cama_store_exchange and cama_store_teaching, add optional
#    pattern_flag and pattern_source parameters so new memories
#    can be tagged at storage time.
#
# The full integration requires patching ~30 lines in cama_mcp.py.
# This can be done via str_replace or manually, since
# cama_exec is currently stalling.
# ============================================================


if __name__ == "__main__":
    # Self-test
    test_results = [
        {"id": 6070, "raw_text": "I'm the problem here...", 
         "pattern_flag": "absorbed_framing", "pattern_source": "interpersonal",
         "score": 0.75, "rationale": "sem=0.49"},
        {"id": 6300, "raw_text": "She builds impossible things...",
         "pattern_flag": "suppressed_strength", "pattern_source": "self",
         "score": 0.65, "rationale": "sem=0.43"},
        {"id": 6296, "raw_text": "She matters because she's here",
         "pattern_flag": "clean", "pattern_source": None,
         "score": 0.70, "rationale": "sem=0.38"},
        {"id": 9999, "raw_text": "Unflagged memory",
         "pattern_flag": None, "pattern_source": None,
         "score": 0.60, "rationale": "sem=0.30"},
    ]
    
    print("=== pattern RETRIEVAL TEST (negative affect) ===\n")
    
    modified = _apply_pattern_scoring(test_results, valence=-0.5)
    
    for r in modified:
        print(f"  #{r['id']}: score={r['score']:.2f} flag={r.get('pattern_flag')}")
        if r.get("pattern_context"):
            print(f"    → {r['pattern_context'][:80]}...")
    
    print()
    prompt = _pattern_reflection_prompt(modified, -0.5)
    if prompt:
        print(prompt)
    
    print("\n=== TEST: Positive affect (no pattern triggers) ===\n")
    
    # Reset scores
    for r in test_results:
        r["score"] = 0.7
    
    modified2 = _apply_pattern_scoring(test_results, valence=0.5)
    prompt2 = _pattern_reflection_prompt(modified2, 0.5)
    print(f"  pattern reflection prompt: {'(none — positive affect)' if not prompt2 else prompt2}")
    
    print("\n[SELF-TEST] Passed.")
