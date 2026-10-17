"""Runtime state backed by an append-only durable event history."""

import asyncio
from dataclasses import dataclass, field

from backend.scratchpad import InvestigationScratchpad
from backend.storage import RunStore


@dataclass
class Investigation:
    target: str
    events: list[dict] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    task: asyncio.Task | None = None
    scratchpad: InvestigationScratchpad | None = None
    budget: float | None = None
    chat_session_id: str | None = None
    chat_cost: float = 0
    chat_evidence_ids: list[str] = field(default_factory=list)
    chat_blocked: bool = False
    chat_requests: dict[str, dict] = field(default_factory=dict)
    refresh_sources: bool = False
    collection_mode: str = "research"
    source_requests: dict[str, dict] = field(default_factory=dict)
    store: RunStore | None = None
    saved_events: int = 0
    saved_evidence: int = 0
    cached_source_session_id: str | None = None
    research_case_path: str | None = None
    research_run_id: str | None = None

    def persist(self):
        if self.store:
            self.store.save(self)

    def emit(self, event: dict) -> None:
        self.events.append(event)
        self.persist()
        self.changed.set()

    def finish(self):
        self.done = True
        self.persist()
        self.changed.set()

    @classmethod
    def restore(cls, snapshot: dict, store: RunStore):
        run = cls(snapshot["target"], events=snapshot["events"], store=store)
        for key in ("done", "budget", "chat_session_id", "chat_cost", "chat_evidence_ids", "chat_blocked", "chat_requests", "refresh_sources", "collection_mode", "source_requests", "cached_source_session_id", "research_case_path", "research_run_id"):
            if key in snapshot:
                setattr(run, key, snapshot[key])
        pad = InvestigationScratchpad(snapshot["session_id"], run.emit)
        pad.restore(snapshot["evidence"], snapshot.get("human_decisions", []))
        pad.refresh_sources = run.refresh_sources
        run.scratchpad = pad
        run.saved_events = len(run.events)
        run.saved_evidence = len(pad.raw_evidence)
        return run

    def interrupt_after_restart(self):
        if self.done:
            return
        # Even a terminal event can precede the final metadata transaction.
        # Keep its result visible but block spending with uncertain accounting.
        last_operation = next((e["type"] for e in reversed(self.events)
                               if e["type"].startswith(("conversation.", "investigation.", "source."))), "")
        self.done = True
        if last_operation.startswith("source."):
            for request in self.source_requests.values():
                if request["state"] == "pending":
                    request["state"] = "interrupted"
            self.scratchpad.emit({"type": "source.failed", "error": "Source collection was interrupted. No provider call was resumed or retried."})
            return
        self.chat_blocked = True
        if last_operation.endswith((".complete", ".failed", ".cancelled")):
            self.persist()
            return
        phase = "conversation" if last_operation.startswith("conversation.") else "investigation"
        self.scratchpad.emit({"type": f"{phase}.failed", "phase": "interrupted",
                              "error": "Server stopped before completion. Saved evidence is retained; no paid work was restarted."})
