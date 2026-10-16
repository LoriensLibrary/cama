"""Restricted HTTP surface for agents with signed personal-Hive lineage."""
import os
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from cama.hive.cama_hive_guard import GuardDenied, from_directory
from cama.hive.cama_hive_security import check_rate_limit

router = APIRouter(prefix="/hive/guard", tags=["guarded agents"])


@lru_cache(maxsize=1)
def get_guard():
    directory = os.environ.get("CAMA_HIVE_GUARD_DIR")
    if not directory:
        raise HTTPException(503, "Guard has not been provisioned")
    try:
        return from_directory(directory)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Guard configuration unavailable") from exc


def principal(request: Request, authorization: str = Header(""), guard=Depends(get_guard)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer credential required")
    try:
        who = guard.authenticate(authorization[7:])
    except GuardDenied as exc:
        raise HTTPException(401, str(exc)) from exc
    allowed, reason = check_rate_limit("guard:" + who["agent"], "read" if request.method == "GET" else "write")
    if not allowed:
        raise HTTPException(429, reason)
    return who


def execute(fn, *args):
    try:
        return fn(*args)
    except GuardDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class Ack(BaseModel):
    version: int = Field(ge=1)
    digest: str = Field(min_length=64, max_length=64)


class Record(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    evidence: list[str] = Field(min_length=1, max_length=10)


class Containment(BaseModel):
    target: str = Field(min_length=32, max_length=32)
    alarm_id: str = Field(min_length=32, max_length=32)


@router.get("/boot")
def boot(who=Depends(principal), guard=Depends(get_guard)):
    return execute(guard.boot, who["agent"])


@router.post("/ack")
def acknowledge(req: Ack, who=Depends(principal), guard=Depends(get_guard)):
    execute(guard.acknowledge, who["agent"], req.version, req.digest)
    return {"acknowledged": True, "behavior_verified": False}


@router.post("/findings")
def finding(req: Record, who=Depends(principal), guard=Depends(get_guard)):
    return execute(guard.submit, who["agent"], "finding", req.body, req.evidence)


@router.post("/alarms")
def alarm(req: Record, who=Depends(principal), guard=Depends(get_guard)):
    return execute(guard.submit, who["agent"], "alarm", req.body, req.evidence)


@router.get("/review")
def review(who=Depends(principal), guard=Depends(get_guard)):
    return {"records": execute(guard.records, who["agent"])}


@router.post("/quarantine")
def quarantine(req: Containment, who=Depends(principal), guard=Depends(get_guard)):
    execute(guard.quarantine, who["agent"], req.target, req.alarm_id)
    return {"status": "quarantined", "target": req.target}
