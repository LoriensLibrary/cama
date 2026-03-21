"""
Apply this patch to cama_mcp.py:
Replace the entire if __name__ == "__main__": block at the bottom with this.
"""

# ============================================================
# What to replace — find this at the bottom of cama_mcp.py:
# ============================================================
# if __name__ == "__main__":
#     import sys
#     transport = os.environ.get("CAMA_TRANSPORT", "stdio")
#     port = int(os.environ.get("PORT", os.environ.get("CAMA_PORT", "8000")))
#     if transport == "http" or "--http" in sys.argv:
#         mcp.run(transport="streamable_http", host="0.0.0.0", port=port)
#     else:
#         mcp.run()

# ============================================================
# Replace with:
# ============================================================
# if __name__ == "__main__":
#     import sys
#     # Pre-warm embedding model at startup so semantic queries never cold-start timeout
#     # This shifts the 30-60s model load to server start instead of first query
#     if EMBEDDING_PROVIDER in ("auto", "local"):
#         print("[CAMA] Pre-warming embedding model...", file=sys.stderr)
#         _load_local_model()
#         if _local_model is not None:
#             print("[CAMA] Embedding model ready.", file=sys.stderr)
#         else:
#             print("[CAMA] No local model available — semantic queries will use API or substring fallback.", file=sys.stderr)
#     transport = os.environ.get("CAMA_TRANSPORT", "stdio")
#     port = int(os.environ.get("PORT", os.environ.get("CAMA_PORT", "8000")))
#     if transport == "http" or "--http" in sys.argv:
#         mcp.run(transport="streamable_http", host="0.0.0.0", port=port)
#     else:
#         mcp.run()
