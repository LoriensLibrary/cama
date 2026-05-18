# CAMA demo container — dashboard-only quickstart.
#
# This image runs `cama_dashboard.py` against a synthetic demo database
# populated by `seed_demo.py` on first start. It does NOT run the MCP
# server (cama_mcp.py), because MCP integration is stdio-based and meant
# to be wired into Claude Desktop on the host — see the README's
# "Connecting the MCP server to Claude Desktop" section for that path.
#
# Reviewer purpose served by this image: clone the repo, run
# `docker compose up`, see the dashboard render with realistic data
# at http://localhost:5555.

FROM python:3.11-slim

WORKDIR /app

# The dashboard + seed script use stdlib only (http.server, sqlite3, json,
# datetime). No pip install needed for the demo path. The full requirements
# (sentence-transformers, mcp, fastapi, etc.) are only needed when running
# the actual MCP server outside this image.

COPY cama_dashboard.py cama_dashboard.html seed_demo.py /app/
COPY benchmark_results.json /app/
COPY benchmark_boot_relevance_results.json /app/
COPY benchmark_continuity_results.json /app/
COPY benchmark_counterweight_results.json /app/
COPY benchmark_retrieval_results.json /app/
COPY benchmark_stale_results.json /app/

ENV CAMA_DB_PATH=/data/demo.db \
    CAMA_DASHBOARD_HOST=0.0.0.0 \
    CAMA_DASHBOARD_PORT=5555 \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 5555

# Seed runs only if /data/demo.db is empty (idempotent), then exec the
# dashboard. Using `exec` so the dashboard becomes PID 1 and receives
# SIGTERM cleanly on `docker compose down`.
CMD ["sh", "-c", "python /app/seed_demo.py && exec python /app/cama_dashboard.py"]
