"""Recovery decisions preserve provenance and never imply permission to spend."""

from typing import Literal
from pydantic import Field
from .models import Model
from .blackboard import digest
from .evidence import EvidenceReader


class ToolFailure(Model):
    operation_id: str
    kind: Literal[
        "PERSISTED_OUTPUT",
        "EMPTY",
        "UNKNOWN_OUTCOME",
        "AUTHENTICATION",
        "RATE_LIMIT",
        "INACCESSIBLE",
        "PROVIDER_FAILURE",
    ]
    provider_run_id: str | None = None
    retry_after: str | None = None
    alternate_span_ids: list[str] = Field(default_factory=list)
    simulated: bool = False


class RecoveryAction(Model):
    kind: Literal[
        "LOCAL_OUTPUT",
        "CACHED_ALTERNATIVE",
        "RECONCILE_REQUIRED",
        "MISSING_CACHED_SOURCE",
        "BLOCKED",
    ]
    reason: str
    span_ids: list[str] = Field(default_factory=list)


def classify_failure(
    operation_id,
    *,
    status=None,
    timed_out=False,
    persisted=False,
    empty=False,
    provider_run_id=None,
    retry_after=None,
):
    kind = (
        "PERSISTED_OUTPUT"
        if persisted
        else "UNKNOWN_OUTCOME"
        if timed_out
        else "AUTHENTICATION"
        if status == 401
        else "RATE_LIMIT"
        if status == 429
        else "INACCESSIBLE"
        if status in (403, 404)
        else "EMPTY"
        if empty
        else "PROVIDER_FAILURE"
    )
    return ToolFailure(
        operation_id=operation_id,
        kind=kind,
        provider_run_id=provider_run_id,
        retry_after=retry_after,
    )


def choose_recovery(failure, task, board, execution_mode):
    if task.run_id != board.run_id:
        raise ValueError("Cross-case recovery")
    if failure.kind == "PERSISTED_OUTPUT":
        return RecoveryAction(
            kind="LOCAL_OUTPUT",
            reason="Read the existing SDK session output before considering another source",
        )
    spans = {s.id for s in board.spans}
    alternates = [sid for sid in failure.alternate_span_ids if sid in spans]
    if alternates:
        return RecoveryAction(
            kind="CACHED_ALTERNATIVE",
            span_ids=alternates,
            reason="Use existing passages with their original provenance; remote outcome remains unchanged",
        )
    if failure.kind == "UNKNOWN_OUTCOME":
        return RecoveryAction(
            kind="RECONCILE_REQUIRED",
            reason="Inspect the known provider operation; never start another charged request",
        )
    if execution_mode == "offline":
        return RecoveryAction(
            kind="MISSING_CACHED_SOURCE",
            reason="No cached alternative exists; offline mode forbids upstream calls",
        )
    return RecoveryAction(
        kind="BLOCKED",
        reason="A distinct admitted task and source authorization are required; no automatic paid retry",
    )


def recover_cached(board, task, failure, action):
    """Capture exact existing spans, not new evidence or semantic corroboration."""
    reader = EvidenceReader(board, task.run_id)
    key = digest([task.id, failure.operation_id, action.model_dump()])[:24]
    old = next(
        (r for r in board.snapshot(task.run_id).recoveries if r["id"] == key), None
    )
    if old:
        return (
            [reader.read_span(sid) for sid in old.get("span_ids", [])]
            if old["state"] == "executed"
            else []
        )
    record = {
        "id": key,
        "task_id": task.id,
        **failure.model_dump(),
        "strategy": action.kind,
        "reason": action.reason,
        "state": "planned",
        "span_ids": [],
    }

    def save():
        with board.transaction():
            board._put(task.run_id, "recovery", key, record)
            board._event(task.run_id, "tool.recovery", **record)

    save()
    try:
        if action.kind != "CACHED_ALTERNATIVE":
            raise ValueError("No cached replacement")
        spans = [reader.read_span(sid) for sid in action.span_ids]
        if not spans:
            raise ValueError("Empty replacement")
        record.update(state="executed", span_ids=[s.id for s in spans])
        save()
        return spans
    except ValueError:
        record.update(state="failed")
        save()
        return []


def recover_local_output(board, task, failure, response, hook, *, max_bytes):
    """The existing reader checks session ownership, path boundaries and size."""
    from backend.tool_outputs import recover_persisted_output

    board.emit(
        task.run_id,
        "tool.recovery",
        task_id=task.id,
        operation_id=failure.operation_id,
        state="planned",
        strategy="LOCAL_OUTPUT",
    )
    try:
        recovered, ok = recover_persisted_output(response, hook, max_bytes=max_bytes)
        if not ok:
            raise ValueError("No saved output")
        source_id = "recovered-" + digest([task.id, failure.operation_id])[:24]
        board.add_source(
            task.run_id,
            source_id,
            recovered,
            source_kind="UNCLASSIFIED",
            origin_group=failure.operation_id,
        )
        spans = EvidenceReader(board, task.run_id).index_source(source_id)
        board.emit(
            task.run_id,
            "tool.recovery",
            task_id=task.id,
            operation_id=failure.operation_id,
            state="executed",
            strategy="LOCAL_OUTPUT",
            span_ids=[s.id for s in spans],
        )
        return spans
    except (ValueError, OSError, KeyError):
        board.emit(
            task.run_id,
            "tool.recovery",
            task_id=task.id,
            operation_id=failure.operation_id,
            state="failed",
            strategy="LOCAL_OUTPUT",
        )
        return []


async def reconcile_apify_operation(
    cache, operation_id, *, client, limit, authorized=False, execution_mode="offline"
):
    """Explicit status/dataset reads of a known run. This never starts an Actor.

    API: https://docs.apify.com/api/client/python/reference/class/RunClientAsync
    Caller must separately authorize even read traffic; offline replay cannot use it.
    """
    if execution_mode == "offline" or not authorized:
        raise ValueError("Provider reads disabled")
    operation = cache.operation(operation_id)
    run_id = operation["provider_run_id"]
    if not run_id:
        raise ValueError("Unknown start outcome requires operator reconciliation")
    run = await client.run(run_id).get()
    if run is None:
        return None
    cache.record_operation(
        operation_id,
        provider_run_id=run_id,
        status=str(run.status),
        usage_usd=float(run.usage_total_usd)
        if run.usage_total_usd is not None
        else None,
    )
    if run.status != "SUCCEEDED":
        return None
    return (await client.dataset(run.default_dataset_id).list_items(limit=limit)).items
