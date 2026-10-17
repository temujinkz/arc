"""Explicit, idempotent judgment requests using the existing investigation budget."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.evidence_routes import existing_run
from backend.judgments import PERSONA_BY_ID, evidence_snapshot, public_personas, remaining_budget, run_council

router = APIRouter(prefix="/api")


class JudgmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    persona_ids: list[str] = Field(min_length=1, max_length=4)
    authorize_model: bool = False


@router.get("/judgments/personas")
async def personas():
    return {"personas": public_personas()}


@router.get("/investigate/{session_id}/judgments")
async def judgments(session_id: str):
    run = existing_run(session_id)
    evidence, snapshot = evidence_snapshot(run)
    reason = ""
    remaining = None
    try:
        remaining = remaining_budget(run)
    except ValueError as exc:
        reason = str(exc)
    if not evidence:
        reason = "Collect public evidence first. The council needs something to read."
    elif not run.done:
        reason = "Wait for the current operation to finish before calling the council."
    return {"personas": public_personas(), "evidence_count": len(evidence), "snapshot": snapshot,
            "ready": not reason, "reason": reason, "remaining_budget_usd": remaining,
            "requests": run.judgment_requests,
            "readings": [event for event in run.events if event["type"] == "judgment.complete"]}


@router.post("/investigate/{session_id}/judgments", status_code=202)
async def judge(session_id: str, body: JudgmentRequest):
    run = existing_run(session_id)
    if len(set(body.persona_ids)) != len(body.persona_ids) or not set(body.persona_ids) <= PERSONA_BY_ID.keys():
        raise HTTPException(422, "Choose one to four different council personas.")
    previous = run.judgment_requests.get(body.request_id)
    if previous:
        if previous["persona_ids"] != body.persona_ids:
            raise HTTPException(409, "This request ID belongs to a different council selection.")
        return {"session_id": session_id, "after": previous["after"], "state": previous["state"]}
    if not body.authorize_model:
        raise HTTPException(422, "Explicitly authorize this model reading before starting the council.")
    if not run.done:
        raise HTTPException(409, "Wait for the current operation to finish before calling the council.")
    evidence, snapshot = evidence_snapshot(run)
    if not evidence:
        raise HTTPException(409, "Collect public evidence before calling the council.")
    try:
        remaining = remaining_budget(run)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    offset = len(run.events)
    run.judgment_requests[body.request_id] = {"persona_ids": body.persona_ids, "after": offset,
                                            "snapshot": snapshot, "state": "pending"}
    run.done = False
    run.scratchpad.emit({"type": "judgment.started", "request_id": body.request_id,
                         "persona_ids": body.persona_ids, "snapshot": snapshot,
                         "text": "The council is reading the captured public evidence."})
    run.task = asyncio.create_task(run_council(run, body.request_id, evidence, remaining))
    return {"session_id": session_id, "after": offset, "state": "pending"}
