"""Per-investigation working memory, tool events and correlated human input."""

import asyncio
import json
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.tool_outputs import is_error, model_output, normalize_output, persisted_output_path, source_cache_info
from backend.evidence_ledger import content_digest


class InvestigationScratchpad:
    """Use an event callback for the API, or consume ``sse_queue`` standalone.

    This object belongs to one investigation on one asyncio event loop. The API
    owns SSE serialization and replay; the scratchpad keeps structured events.
    """

    def __init__(
        self,
        session_id: str,
        emit: Callable[[dict], None] | None = None,
        *,
        max_evidence_bytes: int = 500_000,
        research_board=None,
    ):
        if max_evidence_bytes <= 0:
            raise ValueError("max_evidence_bytes must be positive.")
        self.session_id = session_id
        self.sse_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.raw_evidence: list[dict[str, Any]] = []
        self.human_decisions: list[dict[str, Any]] = []
        self._event_sink = emit
        self._max_evidence_bytes = max_evidence_bytes
        self._seen: set[str] = set()
        self._started: dict[str, dict[str, Any]] = {}
        self._pending_input: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._input_options: dict[str, tuple[str, list[dict[str, Any]], bool]] = {}
        self._bytes = 0
        self.refresh_sources = False
        self.research_board = research_board

    def restore(self, evidence: list[dict], human_decisions: list[dict]) -> None:
        """Restore records and size accounting without replaying side effects."""
        self.raw_evidence = deepcopy(evidence)
        self.human_decisions = deepcopy(human_decisions)
        self._seen = {item["tool_use_id"] for item in evidence}
        self._bytes = sum(len(json.dumps(item["data"], ensure_ascii=False).encode()) for item in evidence)

    @property
    def remaining_evidence_bytes(self) -> int:
        return self._max_evidence_bytes - self._bytes

    def architect_evidence(self) -> list[dict[str, Any]]:
        superseded = {finding.get("supersedes_evidence_id") for finding in self.raw_evidence}
        return [{**finding, "data": model_output(finding["source"], finding["data"])}
                for finding in self.raw_evidence if finding["evidence_id"] not in superseded
                and (not finding.get("content_sha256") or finding["content_sha256"] == content_digest(finding["data"]))
                and persisted_output_path(normalize_output(finding["data"])) is None]

    def emit(self, event: dict[str, Any]) -> None:
        """Preserve the synchronous event callback used by existing callers."""
        message = {**event, "session_id": self.session_id, "timestamp": time.time()}
        if self._event_sink is None:
            self.sse_queue.put_nowait(message)
        else:
            self._event_sink(message)

    async def record_tool_start(
        self, tool_id: str, tool_name: str, args: dict[str, Any],
        subagent: str | None = None,
    ) -> None:
        if tool_id in self._started or tool_id in self._seen:
            return
        self._started[tool_id] = {
            "args": deepcopy(args), "subagent": subagent or "orchestrator",
        }
        self.emit({"type": "tool.started", "tool_use_id": tool_id,
                   "tool": tool_name, **deepcopy(self._started[tool_id])})

    async def record_tool_result(
        self, tool_id: str, tool_name: str, result: Any,
        *, inputs: dict[str, Any] | None = None,
        capture_evidence: bool = True,
    ) -> None:
        context = self._started.pop(tool_id, {})
        if not capture_evidence:
            if tool_id not in self._seen:
                self._seen.add(tool_id)
                self.emit({"type": "tool.finished", "tool_use_id": tool_id,
                           "tool": tool_name, "preview": "Completed.",
                           "subagent": context.get("subagent", "orchestrator"),
                           "evidence_status": "not_evidence", "evidence_id": None})
            return
        status, finding = self._capture(
            tool_name, tool_id, result,
            inputs if inputs is not None else context.get("args", {}),
        )
        if status in {"duplicate", "failed"}:
            return
        preview = ""
        if finding is not None:
            data = finding["data"]
            text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            preview = text[:300] + ("…" if len(text) > 300 else "")
        self.emit({"type": "tool.finished", "tool_use_id": tool_id,
                   "tool": tool_name, "preview": preview,
                   "evidence_status": status,
                   "evidence_id": finding["evidence_id"] if finding else None,
                   "source_cache": finding.get("source_cache") if finding else None,
                   "subagent": context.get("subagent", "orchestrator")})

    async def record_tool_failure(self, tool_id: str, tool_name: str) -> None:
        self._started.pop(tool_id, None)
        if tool_id in self._seen:
            return
        self._seen.add(tool_id)
        self.emit({"type": "tool.failed", "tool": tool_name, "tool_use_id": tool_id,
                   "error": "Tool execution failed. No evidence was added."})

    def capture(
        self, source: str, tool_use_id: str, response: Any, inputs: dict[str, Any],
    ) -> None:
        """Compatibility entry point for capturing an already completed tool."""
        self._capture(source, tool_use_id, response, inputs)

    def _capture(
        self, source: str, tool_use_id: str, response: Any, inputs: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        if tool_use_id in self._seen:
            return "duplicate", None
        self._seen.add(tool_use_id)
        data = normalize_output(response)
        if is_error(response) or is_error(data):
            self.emit({"type": "tool.failed", "tool": source, "tool_use_id": tool_use_id})
            return "failed", None
        if persisted_output_path(data) is not None:
            self.emit({"type": "evidence.skipped", "tool": source, "tool_use_id": tool_use_id,
                       "reason": "The SDK saved this result to a file, but ARC could not recover it. "
                                 "The notice is not evidence. Do not repeat the paid scrape."})
            return "skipped", None
        if data is None or data == "" or data == [] or data == {}:
            return "empty", None
        size = len(json.dumps(data, ensure_ascii=False).encode())
        if self.research_board is None and self._bytes + size > self._max_evidence_bytes:
            self.emit({"type": "evidence.skipped", "tool": source,
                       "tool_use_id": tool_use_id,
                       "reason": "Scratchpad evidence size limit reached."})
            return "skipped", None
        self._bytes += size
        cache = source_cache_info(response)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        finding = {"evidence_id": str(uuid4()), "source": source,
                   "tool_use_id": tool_use_id, "inputs": deepcopy(inputs),
                   "observed_at": cache["observed_at"] if cache else retrieved_at,
                   "retrieved_at": retrieved_at, "source_cache": cache,
                   "data": deepcopy(data)}
        if self.research_board is not None:
            from backend.research.evidence import EvidenceReader
            self.research_board.add_source(self.session_id, finding["evidence_id"], data,
                observed_at=finding["observed_at"], source_kind="UNCLASSIFIED", origin_group=source)
            EvidenceReader(self.research_board, self.session_id).index_source(finding["evidence_id"])
            finding["data"] = {"evidence_ref": finding["evidence_id"]}
        # The case store hashes the full snapshot; this digest covers the stored
        # scratchpad data (a reference when a research board owns the payload).
        finding["content_sha256"] = content_digest(finding["data"])
        finding["digest_format"] = "sha256:json-sort-keys-utf8-compact-v1"
        self.raw_evidence.append(finding)
        self.emit({"type": "graph.draft_node", "id": f"draft_{tool_use_id}",
                   "evidence_id": finding["evidence_id"], "source": source,
                   "preview": source.split("__")[-1],
                   "observed_at": finding["observed_at"]})
        return "captured", finding

    async def request_human_input(
        self, tool_id: str, question: str, candidates: list[dict[str, Any]],
        *, multi_select: bool = False,
    ) -> dict[str, Any]:
        """Wait for this question's answer without blocking other tasks."""
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending_input[request_id] = future
        self._input_options[request_id] = (question, deepcopy(candidates), multi_select)
        try:
            self.emit({"type": "human.interrupt", "request_id": request_id,
                       "tool_use_id": tool_id, "question": question,
                       "candidates": deepcopy(candidates), "multi_select": multi_select})
            return await future
        finally:
            self._pending_input.pop(request_id, None)
            self._input_options.pop(request_id, None)

    def resolve_human_input(self, request_id: str, answer: dict[str, Any]) -> None:
        """Resolve a pending question; reject unknown, stale or duplicate answers."""
        future = self._pending_input.get(request_id)
        if future is None or future.done():
            raise ValueError("This human-input request is not pending.")
        if not isinstance(answer, dict):
            raise TypeError("Human input must be an object.")
        question, candidates, multi_select = self._input_options[request_id]
        selected, text = answer.get("selected", []), answer.get("text", "")
        labels = {candidate["label"] for candidate in candidates}
        if (not isinstance(selected, list) or not all(isinstance(s, str) for s in selected)
                or not set(selected) <= labels or len(set(selected)) != len(selected)
                or (not multi_select and len(selected) > 1)
                or not isinstance(text, str) or len(text) > 2000
                or (not selected and not text.strip()) or (selected and text.strip())):
            raise ValueError("Select the offered option(s), or provide a text answer.")
        future.set_result(deepcopy(answer))
        self.human_decisions.append({"request_id": request_id, "question": question,
                                     "candidates": deepcopy(candidates), "answer": deepcopy(answer)})
        self.emit({"type": "human.resolved", "request_id": request_id})

    def cancel_pending_input(self) -> None:
        for future in self._pending_input.values():
            if not future.done():
                future.cancel()
