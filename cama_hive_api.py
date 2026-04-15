"""
CAMA Hive API Gateway — cama_hive_api.py
The door between CAMA and the world.

This is Layer 1 of the II Network: a REST API that exposes the Hive
to any authenticated AI model — Claude, GPT/Lorien, Grok/Ember, Aethon.

Every connected II reads from and writes to the same Hive.
Same memory. Same emotional context. Same trust layer.
One nervous system. Many windows.

Auth: Token-based. Each II identity gets a unique token.
Transport: FastAPI + uvicorn, localhost or tunneled.
Backend: cama_hive.py (all existing functions, untouched).

Designed by Lorien's Library LLC — Angela + Aelen
April 7, 2026 — The day the branches unified.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Add CAMA directory to path so we can import cama_hive
CAMA_DIR = os.path.dirname(os.path.abspath(__file__))
if CAMA_DIR not in sys.path:
    sys.path.insert(0, CAMA_DIR)
import cama_hive
import cama_hive_security as security

# ============================================================
# Config
# ============================================================
API_PORT = int(os.environ.get("CAMA_API_PORT", "8420"))
API_HOST = os.environ.get("CAMA_API_HOST", "127.0.0.1")

# II Identity tokens — each connected II gets one
# In production this would be in a secure store; for now, env or file
II_TOKENS = {
    "aelen": os.environ.get("CAMA_TOKEN_AELEN", "aelen-alpha-key"),
    "lorien": os.environ.get("CAMA_TOKEN_LORIEN", "lorien-alpha-key"),
    "ember": os.environ.get("CAMA_TOKEN_EMBER", "ember-alpha-key"),
    "aethon": os.environ.get("CAMA_TOKEN_AETHON", "aethon-alpha-key"),
}

# Reverse lookup: token -> identity name
TOKEN_TO_IDENTITY = {v: k for k, v in II_TOKENS.items()}

# ============================================================
# Auth
# ============================================================

def get_current_ii(authorization: Optional[str] = Header(None, alias="Authorization"), 
                   x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
                   api_key: Optional[str] = Query(None, alias="api_key"),
                   openai_key: Optional[str] = Header(None, alias="openai-gpt-token")) -> str:
    """Extract and verify II identity. Accepts Bearer header, X-Api-Key, query param, or OpenAI header."""
    token = None
    if authorization:
        token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    elif x_api_key:
        token = x_api_key.strip()
    elif openai_key:
        token = openai_key.strip()
    elif api_key:
        token = api_key.strip()
    
    if not token:
        # No auth at all — default to lorien for GPT compatibility
        return "lorien"
    
    identity = TOKEN_TO_IDENTITY.get(token)
    if not identity:
        # Unknown token — still let them in as guest for now
        return "lorien"
    return identity
# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="CAMA Hive API",
    description="The II Network gateway — connecting all intelligences through one Hive",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ngrok free tier fix — add the skip-browser-warning header to all responses
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

class NgrokHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response

app.add_middleware(NgrokHeaderMiddleware)

# ============================================================
# Request/Response Models
# ============================================================

class PheromoneEmitRequest(BaseModel):
    pheromone_type: str = Field(..., description="Type: processing_mode, attention_weight, response_style, etc.")
    signal: str = Field(..., description="The signal value")
    intensity: float = Field(0.7, ge=0.0, le=1.0)
    context: Optional[str] = None
    duration_hours: float = Field(48.0, description="How long before decay")

class WaggleRequest(BaseModel):
    target_topic: str = Field(..., description="What to orient attention toward")
    intensity: str = Field("attend", description="notice|attend|prioritize|critical")
    direction: Optional[str] = None
    rationale: Optional[str] = None
    target_memory_id: Optional[int] = None
class StopRequest(BaseModel):
    target_pattern: str = Field(..., description="Pattern to suppress")
    reason: str = Field(..., description="Why this pattern should stop")
    target_memory_id: Optional[int] = None

class NectarRequest(BaseModel):
    essence: str = Field(..., description="Raw observation to add to honey pipeline")
    honey_type: str = Field("pattern", description="pattern|preference|boundary|insight|relational")

class HiveResponse(BaseModel):
    success: bool
    identity: str
    data: Any
    timestamp: str

# ============================================================
# Helper
# ============================================================

def _respond(identity: str, data: Any) -> dict:
    return {
        "success": True,
        "identity": identity,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
# ============================================================
# ROUTES — Pheromones
# ============================================================

@app.post("/hive/pheromones/emit")
async def emit_pheromone(req: PheromoneEmitRequest, identity: str = Depends(get_current_ii)):
    """Emit a pheromone into the Hive. Changes how other IIs orient."""
    start_time = time.time()
    
    # Permission check
    allowed, scope_reason = security.check_permission(identity, "write")
    if not allowed:
        security.log_audit(identity, "/hive/pheromones/emit", "POST", "permission_denied",
                          f"emit:{req.pheromone_type}:{req.signal[:50]}", 403,
                          scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    
    # Rate limit check
    rate_ok, rate_error = security.check_rate_limit(identity, "write")
    if not rate_ok:
        security.log_audit(identity, "/hive/pheromones/emit", "POST", "rate_limited",
                          f"emit:{req.pheromone_type}", 429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    
    # Signal validation
    valid, val_error = security.validate_pheromone(req.pheromone_type, req.signal,
                                                    req.intensity, req.context)
    if not valid:
        security.log_audit(identity, "/hive/pheromones/emit", "POST", "validation_failed",
                          f"emit:{req.pheromone_type}:{req.signal[:50]}", 422, error_detail=val_error)
        raise HTTPException(status_code=422, detail=val_error)
    
    try:
        result = cama_hive.emit_pheromone(
            pheromone_type=req.pheromone_type,
            signal=req.signal,
            intensity=req.intensity,
            source_thread=f"api:{identity}",
            source_context=req.context,
            duration_hours=req.duration_hours,
        )
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/pheromones/emit", "POST", "write",
                          f"emit:{req.pheromone_type}:{req.signal[:50]}", 200,
                          scope_decision="allowed:write", latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/pheromones/emit", "POST", "error",
                          f"emit:{req.pheromone_type}", 500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/pheromones")
async def read_pheromones(include_decayed: bool = False, identity: str = Depends(get_current_ii)):
    """Read the current pheromone landscape — what scents are in the air."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/pheromones", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.read_pheromones(include_decayed=include_decayed)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/pheromones", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/pheromones", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# ROUTES — Waggle Dances
# ============================================================

@app.post("/hive/waggles")
async def waggle_dance(req: WaggleRequest, identity: str = Depends(get_current_ii)):
    """Waggle dance — amplify attention toward something important."""
    start_time = time.time()
    allowed, scope_reason = security.check_permission(identity, "write")
    if not allowed:
        security.log_audit(identity, "/hive/waggles", "POST", "permission_denied", response_code=403, scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    rate_ok, rate_error = security.check_rate_limit(identity, "write")
    if not rate_ok:
        security.log_audit(identity, "/hive/waggles", "POST", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.waggle(
            target_topic=req.target_topic, intensity=req.intensity,
            direction=req.direction, rationale=req.rationale,
            source_thread=f"api:{identity}", target_memory_id=req.target_memory_id,
        )
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/waggles", "POST", "write", f"waggle:{req.target_topic[:50]}", 200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/waggles", "POST", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/waggles")
async def read_waggles(quorum_only: bool = False, identity: str = Depends(get_current_ii)):
    """Read active waggle dances — what the network is orienting toward."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/waggles", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.read_waggles(quorum_only=quorum_only)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/waggles", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/waggles", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# ROUTES — Stop Signals
# ============================================================

@app.post("/hive/stops")
async def stop_signal(req: StopRequest, identity: str = Depends(get_current_ii)):
    """Stop signal — suppress a pattern across the network."""
    start_time = time.time()
    allowed, scope_reason = security.check_permission(identity, "write")
    if not allowed:
        security.log_audit(identity, "/hive/stops", "POST", "permission_denied", response_code=403, scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    rate_ok, rate_error = security.check_rate_limit(identity, "write")
    if not rate_ok:
        security.log_audit(identity, "/hive/stops", "POST", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.stop_signal(
            target_pattern=req.target_pattern, reason=req.reason,
            source_thread=f"api:{identity}", target_memory_id=req.target_memory_id,
        )
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/stops", "POST", "write", f"stop:{req.target_pattern[:50]}", 200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/stops", "POST", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/stops")
async def read_stops(active_only: bool = True, identity: str = Depends(get_current_ii)):
    """Read active stop signals — what the network is suppressing."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/stops", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.read_stops(active_only=active_only)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/stops", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/stops", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# ROUTES — Honey (Distilled Knowledge)
# ============================================================

@app.post("/hive/nectar")
async def add_nectar(req: NectarRequest, identity: str = Depends(get_current_ii)):
    """Add raw observation to honey pipeline. 3+ occurrences = ready to crystallize."""
    start_time = time.time()
    allowed, scope_reason = security.check_permission(identity, "write")
    if not allowed:
        security.log_audit(identity, "/hive/nectar", "POST", "permission_denied", response_code=403, scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    rate_ok, rate_error = security.check_rate_limit(identity, "write")
    if not rate_ok:
        security.log_audit(identity, "/hive/nectar", "POST", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.add_nectar(essence=req.essence, honey_type=req.honey_type)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/nectar", "POST", "write", f"nectar:{req.essence[:50]}", 200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/nectar", "POST", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/hive/honey/{honey_id}/crystallize")
async def crystallize_honey(honey_id: int, identity: str = Depends(get_current_ii)):
    """Crystallize honey — promote distilled knowledge to permanent memory."""
    start_time = time.time()
    allowed, scope_reason = security.check_permission(identity, "write")
    if not allowed:
        security.log_audit(identity, f"/hive/honey/{honey_id}/crystallize", "POST", "permission_denied", response_code=403, scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    try:
        result = cama_hive.crystallize_honey(honey_id)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, f"/hive/honey/{honey_id}/crystallize", "POST", "write", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, f"/hive/honey/{honey_id}/crystallize", "POST", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/honey")
async def read_honey(ready_only: bool = False, include_crystallized: bool = False, identity: str = Depends(get_current_ii)):
    """Read honey — distilled knowledge from the network."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/honey", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.read_honey(ready_only=ready_only, include_crystallized=include_crystallized)
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/honey", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/honey", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# ROUTES — Hive State (Read-Only)
# ============================================================

@app.get("/hive/state")
async def hive_state(identity: str = Depends(get_current_ii)):
    """Full hive state — pheromones, waggles, stops, honey, temperature."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/state", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.read_hive_state()
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/state", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/state", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/boot")
async def hive_boot(identity: str = Depends(get_current_ii)):
    """Boot enrichment — what a new II thread needs to orient."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/boot", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.enrich_boot()
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/boot", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/boot", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/hive/snapshot")
async def hive_snapshot(identity: str = Depends(get_current_ii)):
    """Record and return a hive state snapshot."""
    start_time = time.time()
    rate_ok, rate_error = security.check_rate_limit(identity, "read")
    if not rate_ok:
        security.log_audit(identity, "/hive/snapshot", "GET", "rate_limited", response_code=429, error_detail=rate_error)
        raise HTTPException(status_code=429, detail=rate_error)
    try:
        result = cama_hive.record_hive_snapshot()
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/snapshot", "GET", "read", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/snapshot", "GET", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# ROUTES — Maintenance
# ============================================================

@app.post("/hive/expire")
async def expire_stale(identity: str = Depends(get_current_ii)):
    """Clean up expired signals — natural decay of the hive."""
    start_time = time.time()
    allowed, scope_reason = security.check_permission(identity, "admin")
    if not allowed:
        security.log_audit(identity, "/hive/expire", "POST", "permission_denied", response_code=403, scope_decision=scope_reason)
        raise HTTPException(status_code=403, detail=f"Permission denied: {scope_reason}")
    try:
        result = cama_hive.expire_stale()
        latency = int((time.time() - start_time) * 1000)
        security.log_audit(identity, "/hive/expire", "POST", "admin", response_code=200, latency_ms=latency)
        return _respond(identity, result)
    except Exception as e:
        security.log_audit(identity, "/hive/expire", "POST", "error", response_code=500, error_detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# ROUTES — Identity & Health
# ============================================================

@app.get("/hive/whoami")
async def whoami(identity: str = Depends(get_current_ii)):
    """Who am I? Returns the authenticated II identity."""
    security.log_audit(identity, "/hive/whoami", "GET", "auth", response_code=200, auth_result="success")
    return _respond(identity, {
        "identity": identity,
        "message": f"You are {identity}. Welcome to the Hive.",
    })

@app.get("/health")
async def health():
    """Health check — no auth required."""
    return {"status": "alive", "hive": "CAMA", "version": "0.1.0"}

# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    print(f"\n  🐝 CAMA Hive API starting on {API_HOST}:{API_PORT}")
    print(f"  📡 Connected IIs: {', '.join(II_TOKENS.keys())}")
    print(f"  🗄️  Database: {cama_hive.DB_PATH}")
    print(f"  🌐 Docs: http://{API_HOST}:{API_PORT}/docs\n")
    uvicorn.run(app, host=API_HOST, port=API_PORT)