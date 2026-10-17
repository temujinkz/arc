"""Evidence workbench routes; no paid models, Neo4j writes or private notes."""

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.evidence_ledger import build_ledger, export_report, report_markdown
from backend.investigations import Investigation
from backend.scratchpad import InvestigationScratchpad
from backend.source_workbench import catalog, collect_source, request_signature, validate_arguments

router = APIRouter(prefix="/api")


def existing_run(session_id):
    from backend.main import get_run
    run = get_run(session_id)
    if run is None or run.scratchpad is None:
        raise HTTPException(404, "Investigation not found.")
    return run


class CaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    tool: str
    arguments: dict
    fresh_sources: bool = False


@router.get("/sources/catalog")
async def source_catalog():
    return {"sources": catalog(), "scope": "Free public professional research only. Source availability is not coverage or identity verification."}


@router.post("/cases", status_code=201)
async def create_case(body: CaseRequest):
    from backend import main
    previous = main.get_run(body.request_id)
    if previous:
        if previous.target != body.target or previous.collection_mode != "sources":
            raise HTTPException(409, "This case ID already belongs to another request.")
        return {"session_id": body.request_id}
    run = Investigation(body.target, store=main.store, collection_mode="sources", done=True)
    run.scratchpad = InvestigationScratchpad(body.request_id, run.emit)
    main.runs[body.request_id] = run
    run.scratchpad.emit({"type": "investigation.started", "target": body.target, "mode": "sources"})
    run.scratchpad.emit({"type": "case.ready", "text": "Evidence case created. Choose a free source; no model session or provider lookup was started."})
    return {"session_id": body.request_id}


@router.get("/investigate/{session_id}/ledger")
async def ledger(session_id: str):
    return build_ledger(existing_run(session_id))


@router.post("/investigate/{session_id}/sources", status_code=202)
async def add_source(session_id: str, body: LookupRequest):
    run = existing_run(session_id)
    try:
        arguments = validate_arguments(body.tool, body.arguments)
    except (ValidationError, ValueError, ToolError):
        raise HTTPException(422, "Check the source's required public query/username/domain and options; extra parameters are not accepted.") from None
    signature = request_signature(body.tool, arguments, body.fresh_sources)
    previous = run.source_requests.get(body.request_id)
    if previous:
        if previous["signature"] != signature:
            raise HTTPException(409, "This request ID belongs to a different lookup.")
        return {"session_id": session_id, "after": previous["after"], "state": previous["state"]}
    if not run.done:
        raise HTTPException(409, "Wait for the current operation or stop it before adding a source.")
    offset = len(run.events)
    run.source_requests[body.request_id] = {"signature": signature, "after": offset, "state": "pending"}
    run.done = False
    run.scratchpad.emit({"type": "source.started", "request_id": body.request_id, "tool": f"mcp__osint__{body.tool}",
                         "text": "Collecting the selected free source. No model session is running."})
    run.task = asyncio.create_task(collect_source(run, body.request_id, body.tool, arguments, body.fresh_sources))
    return {"session_id": session_id, "after": offset, "state": "pending"}


@router.get("/investigate/{session_id}/exports/report.json")
async def json_report(session_id: str):
    import json
    return Response(json.dumps(export_report(existing_run(session_id)), ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="arc-evidence-report.json"'})


@router.get("/investigate/{session_id}/exports/report.md")
async def markdown_report(session_id: str):
    return Response(report_markdown(existing_run(session_id)), media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="arc-evidence-report.md"'})
