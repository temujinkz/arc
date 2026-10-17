"""FastAPI entry point: collect evidence, architect a graph, stream progress."""

import asyncio
import json
import math
import os
import fcntl
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.orchestrator import run_osint_orchestrator
from backend.scratchpad import InvestigationScratchpad
from backend.investigations import Investigation
from backend.storage import RunStore, database_path
from backend.profile_routes import router as profile_router
from backend.vault_routes import router as vault_router
from backend.agent_routes import router as agent_router
from backend.evidence_routes import router as evidence_router


runs: dict[str, Investigation] = {}
store: RunStore | None = None


def get_run(session_id: str) -> Investigation | None:
    if session_id not in runs and store:
        snapshot = store.load(session_id)
        if snapshot:
            runs[session_id] = Investigation.restore(snapshot, store)
    return runs.get(session_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".api.lock").open("a") as lock:
        # Runtime human gates and tasks have one owner. Refuse a second API worker.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = RunStore(path)
        try:
            for item in store.history():
                if item["active"]:
                    get_run(item["session_id"]).interrupt_after_restart()
            yield
        finally:
            tasks = [run.task for run in runs.values() if run.task and not run.task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for run in runs.values():
                if not run.done:
                    run.interrupt_after_restart()
            runs.clear()
            store.close()
            store = None


app = FastAPI(title="ARC OSINT Engine", lifespan=lifespan)
app.include_router(profile_router)
app.include_router(vault_router)
app.include_router(agent_router)
app.include_router(evidence_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "ARC_FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if origin.strip()],
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "Last-Event-ID"], allow_credentials=True,
)


@app.get("/api/tools")
async def tool_status():
    from backend.osint_tools import EXECUTABLES, executable
    return {"local_tools": {name: {"installed": executable(name) is not None, "paid_provider": False}
                            for name in EXECUTABLES},
            "crossref": {"enabled": True, "paid_provider": False},
            "free_web_search": {"enabled": True, "paid_provider": False},
            "google_dorking": {"mode": "ddgs_google_and_search_links", "paid_provider": False},
            "maltego": {"mode": "csv_export", "paid_provider": False},
            "robin": {"enabled": os.getenv("ARC_ROBIN_ENABLED") == "1", "mode": "search_and_scrape_only"}}


@app.get("/api/tools/google-dorks")
async def google_dorks(subject: str = Query(min_length=1, max_length=1000), domain: str = ""):
    from backend.osint_tools import google_dork_links
    try:
        return google_dork_links(subject, domain)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/investigate/{session_id}/exports/maltego.csv")
async def export_maltego(session_id: str):
    from backend.maltego import graph_csv
    run = get_run(session_id)
    if run is None:
        raise HTTPException(404, "Investigation not found.")
    graph = next((event["graph"] for event in reversed(run.events) if event["type"] == "graph.final_commit"), None)
    if graph is None:
        raise HTTPException(409, "This investigation has no committed public graph to export.")
    return Response(graph_csv(graph), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="arc-maltego.csv"'})


async def run_pipeline(session_id: str, run: Investigation, budget: float | None):
    try:
        await run_osint_orchestrator(run.target, run.scratchpad, budget)
    finally:
        run.finish()


def start_run(session_id: str, target: str, refresh_sources: bool = False) -> Investigation:
    target = target.strip()
    if not target or len(target) > 1000:
        raise HTTPException(422, "Target must contain 1–1,000 characters.")
    existing = get_run(session_id)
    if existing:
        if existing.target != target or existing.refresh_sources != refresh_sources:
            raise HTTPException(409, "This session belongs to a different target or source-refresh setting.")
        return existing
    missing = [name for name in (
        "NEO4J_URI", "NEO4J_PASSWORD", "CLAUDE_MAX_BUDGET_USD",
    ) if not os.getenv(name, "").strip()]
    if not (os.getenv("NEO4J_USERNAME", "").strip() or os.getenv("NEO4J_USER", "").strip()):
        missing.append("NEO4J_USERNAME")
    if missing:
        raise HTTPException(503, f"Configure {', '.join(missing)} before starting an investigation.")
    try:
        budget = float(os.environ["CLAUDE_MAX_BUDGET_USD"])
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(503, "CLAUDE_MAX_BUDGET_USD must be a positive finite number.") from None
    if sum(not run.done for run in runs.values()) >= 100:
        raise HTTPException(503, "Active investigation capacity reached.")
    run = Investigation(target, budget=budget, store=store, refresh_sources=refresh_sources)
    run.scratchpad = InvestigationScratchpad(session_id, run.emit)
    run.scratchpad.refresh_sources = refresh_sources
    run.scratchpad.emit({"type": "investigation.started", "target": target})
    runs[session_id] = run
    run.task = asyncio.create_task(run_pipeline(session_id, run, budget))
    return run


class InvestigationRequest(BaseModel):
    target: str = Field(min_length=1, max_length=1000)
    request_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    refresh_sources: bool = False


@app.post("/api/investigations", status_code=202)
async def create_investigation(body: InvestigationRequest):
    session_id = body.request_id or str(uuid4())
    start_run(session_id, body.target, body.refresh_sources)
    return {"session_id": session_id, "stream_url": f"/api/investigate/stream/{session_id}"}


@app.get("/api/investigations")
async def list_investigations():
    if store:
        return {"investigations": store.history()}
    return {"investigations": [
        {"session_id": sid, "target": run.target, "active": not run.done,
         "created_at": run.events[0].get("timestamp", 0) if run.events else 0,
         "updated_at": run.events[-1].get("timestamp", 0) if run.events else 0}
        for sid, run in reversed(list(runs.items()))
    ]}


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


@app.post("/api/investigate/{session_id}/messages", status_code=202)
async def send_message(session_id: str, body: ConversationRequest):
    from backend.conversation import run_followup

    run = get_run(session_id)
    if run is None or run.scratchpad is None:
        raise HTTPException(404, "Investigation not found.")
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Message cannot be empty.")
    previous = run.chat_requests.get(body.request_id)
    if previous:
        if previous["message"] != message:
            raise HTTPException(409, "This request ID belongs to another message.")
        return {"session_id": session_id, "after": previous["after"]}
    if not run.done:
        raise HTTPException(409, "Wait for the current run or answer its identity question first.")
    if not any(e["type"] == "investigation.complete" for e in run.events):
        raise HTTPException(409, "Follow-up chat requires a completed investigation.")
    if run.chat_blocked:
        raise HTTPException(409, "Final usage for the previous reply is unknown; further spending is blocked.")
    try:
        budget = run.budget if run.budget is not None else float(os.environ["CLAUDE_MAX_BUDGET_USD"])
        costs = [e.get("cost_usd") for e in run.events if e["type"] == "agent.usage"]
        if not costs or any(c is None or not math.isfinite(c) or c < 0 for c in costs):
            raise ValueError
        remaining = budget - sum(costs)
        if not math.isfinite(remaining) or remaining <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(409, "No known remaining Claude budget is available for this investigation.") from None
    offset = len(run.events)
    run.chat_requests[body.request_id] = {"message": message, "after": offset}
    run.done = False
    run.scratchpad.emit({"type": "conversation.started", "request_id": body.request_id})
    run.scratchpad.emit({"type": "user.message", "text": message, "request_id": body.request_id})
    run.task = asyncio.create_task(run_followup(run, message, body.request_id, remaining))
    return {"session_id": session_id, "after": offset}


class HumanReplyPayload(BaseModel):
    request_id: str
    selected: list[str] = Field(default_factory=list, max_length=4)
    text: str = Field(default="", max_length=2000)


@app.post("/api/investigate/{session_id}/human-reply")
async def submit_human_reply(session_id: str, payload: HumanReplyPayload):
    run = get_run(session_id)
    if run is None:
        raise HTTPException(404, "Investigation not found.")
    if run.done or run.scratchpad is None:
        raise HTTPException(409, "This investigation is no longer awaiting input.")
    try:
        run.scratchpad.resolve_human_input(
            payload.request_id, {"selected": payload.selected, "text": payload.text},
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"status": "resumed", "request_id": payload.request_id}


@app.post("/api/investigate/{session_id}/cancel")
async def cancel_investigation(session_id: str):
    run = get_run(session_id)
    if run is None:
        raise HTTPException(404, "Investigation not found.")
    if run.task and not run.done:
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        # Also handles cancellation before the task entered its coroutine.
        if not run.done:
            run.emit({"type": "investigation.cancelled", "session_id": session_id})
            run.chat_blocked = True
            run.finish()
    return {"status": "stopped"}


@app.get("/api/investigate/{session_id}/evidence/{evidence_id}")
async def read_evidence(session_id: str, evidence_id: str):
    run = get_run(session_id)
    if run and run.scratchpad:
        for evidence in run.scratchpad.raw_evidence:
            if evidence["evidence_id"] == evidence_id:
                return evidence
    raise HTTPException(404, "Evidence not found in this investigation.")


@app.get("/api/investigate/stream/{session_id}")
async def stream_investigation(session_id: str, target: str | None = Query(default=None, max_length=1000),
                               after: int = Query(default=0, ge=0),
                               last_event_id: str | None = Header(default=None)):
    # Also supports the original sample's GET with target. Reconnect reuses the same run.
    if len(session_id) > 100:
        raise HTTPException(422, "Session ID is too long.")
    try:
        offset = int(last_event_id) if last_event_id else after
        if offset < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Last-Event-ID must be a nonnegative integer.") from None
    run = get_run(session_id)
    if run is None and last_event_id is not None:
        raise HTTPException(410, "This run is no longer available; create a new investigation.")
    if target is not None:
        run = start_run(session_id, target)
    if run is None:
        raise HTTPException(404, "Investigation not found.")
    if offset > len(run.events):
        raise HTTPException(422, "Last-Event-ID is ahead of this investigation.")

    async def events():
        cursor = offset
        while True:
            while cursor < len(run.events):
                event = run.events[cursor]
                cursor += 1
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if run.done:
                yield f"data: {json.dumps({'type': 'stream.end', 'session_id': session_id})}\n\n"
                break
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
