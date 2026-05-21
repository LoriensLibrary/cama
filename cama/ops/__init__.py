"""CAMA operator tooling.

The cama-ops CLI is the operator-side interface — issuing API keys,
viewing the audit log, listing dyads. It uses file-based auth (the
operator must be able to read CAMA_API_KEY_DB) rather than HTTP
because elevated-privilege actions over HTTP enlarge the attack
surface (see THREAT_MODEL.md row #2 / API.md section 3).
"""
