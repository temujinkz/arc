"""Bounded recursive scheduler with durable worker and review checkpoints."""

import asyncio
from pydantic import Field
from .models import (
    Model,
    RunPolicy,
    TaskProposal,
    WorkerResult,
    Rejected,
    BoardSnapshot,
)
from .blackboard import Blackboard
from .evidence import EvidenceReader
from .critic import check_progress, review_progress, apply_review, CriticDecision, CriticInputError
from .workers import ResearchWorker


class ResearchGoal(Model):
    run_id: str
    target: str
    initial_tasks: list[TaskProposal] = Field(default_factory=list)
    remaining_budget: float = Field(default=0, ge=0)


async def run_research(
    goal: ResearchGoal,
    board: Blackboard,
    worker: ResearchWorker,
    policy: RunPolicy,
    *,
    critic=review_progress,
):
    run = goal.run_id
    reader = EvidenceReader(board, run)
    board.create_run(run, goal.target)

    def stop(reason):
        board.stop(run, reason)
        return board.snapshot(run)

    snapshot = board.snapshot(run)
    if snapshot.stop_reason:
        return snapshot
    if any(t.status == "RUNNING" for t in snapshot.tasks):
        return stop("UNKNOWN_COST")
    for proposal in goal.initial_tasks:
        result = board.admit_task(run, proposal, policy)
        if isinstance(result, Rejected):
            board.emit(run, "task.rejected", reason=result.reason, goal=proposal.goal)

    def remaining():
        costs = [
            t.cost_usd
            for t in board.snapshot(run).tasks
            if t.status in ("COMPLETED", "BLOCKED")
        ]
        costs += [r.get("cost_usd") for r in board._list(run, "critic_usage")]
        return (
            None
            if any(c is None for c in costs)
            else goal.remaining_budget - sum(costs)
        )

    def save_checkpoint(wave, checkpoint):
        with board.transaction():
            board._put(run, "wave_checkpoint", str(wave), checkpoint)

    def drain_children():
        for record in board._list(run, "child_proposals"):
            if record.get("consumed"):
                continue
            parent = board.task(record["task_id"])
            if parent.status == "COMPLETED":
                for raw in record["proposals"]:
                    proposal = TaskProposal.model_validate(raw).model_copy(
                        update={
                            "parent_id": parent.id,
                            "wave": parent.wave,
                            "hypothesis_id": parent.hypothesis_id,
                        }
                    )
                    admitted = board.admit_task(run, proposal, policy)
                    if isinstance(admitted, Rejected):
                        board.emit(
                            run,
                            "task.rejected",
                            task_id=parent.id,
                            reason=admitted.reason,
                        )
            with board.transaction():
                record["consumed"] = True
                board._put(run, "child_proposals", parent.id, record)

    async def execute(task, allowance):
        current = board.start_task(task.id, policy)
        try:
            packet = reader.build_packet(current, policy.max_input_tokens)
            board.emit(
                run,
                "task.context",
                task_id=current.id,
                token_upper_bound=packet.token_upper_bound,
                included=len(packet.spans),
                excluded=packet.excluded_span_count,
                measurement=packet.measurement,
            )
            result = await worker.run(current, packet, allowance)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = WorkerResult(
                status="blocked",
                cost_usd=0 if policy.execution_mode == "offline" else None,
                unresolved=["Worker failed; no automatic paid retry"],
            )
        try:
            board.record_result(current.id, result, current.version)
        except ValueError:
            if board.snapshot(run).stop_reason:
                return
            board.record_result(
                current.id,
                WorkerResult(
                    status="blocked",
                    cost_usd=result.cost_usd,
                    unresolved=["Invalid or out-of-scope result rejected"],
                ),
                current.version,
            )

    try:
        # A result may have committed just before the process stopped. Consume its
        # local children even if no queued tasks were present at restart.
        drain_children()
        for wave in range(1, policy.max_waves + 1):
            checkpoints = board._list(run, "wave_checkpoint")
            checkpoint = next((c for c in checkpoints if c["wave"] == wave), None)
            if checkpoint and checkpoint.get("stop_reason"):
                return stop(checkpoint["stop_reason"])
            if checkpoint and checkpoint["status"] == "DONE":
                continue
            if checkpoint and checkpoint["status"] in ("REVIEW_STARTED", "FAILED"):
                return stop(
                    "UNKNOWN_COST"
                    if policy.execution_mode != "offline"
                    else "NEEDS_HUMAN"
                )
            current = board.snapshot(run)
            if not any(t.wave == wave for t in current.tasks):
                continue
            if checkpoint is None:
                checkpoint = {
                    "wave": wave,
                    "status": "WORKING",
                    "before": current.model_copy(update={"spans": []}).model_dump(),
                }
                save_checkpoint(wave, checkpoint)
                board.emit(run, "research.wave", wave=wave, max_waves=policy.max_waves)
            before = BoardSnapshot.model_validate(checkpoint["before"])
            while checkpoint["status"] == "WORKING":
                if board.snapshot(run).stop_reason:
                    return board.snapshot(run)
                budget = remaining()
                if budget is None:
                    return stop("UNKNOWN_COST")
                queued = [
                    t
                    for t in board.snapshot(run).tasks
                    if t.status == "QUEUED" and t.wave == wave
                ]
                if not queued:
                    break
                if policy.execution_mode != "offline" and budget <= 0:
                    return stop("BUDGET_LIMIT")
                batch = queued[: policy.max_concurrent_workers]
                await asyncio.gather(
                    *(execute(t, max(0, budget) / len(batch)) for t in batch)
                )
                drain_children()
            if remaining() is None:
                return stop("UNKNOWN_COST")
            after = board.snapshot(run)
            report = check_progress(before, after, policy)
            report.wave = wave
            if checkpoint["status"] == "WORKING":
                if policy.execution_mode != "offline" and remaining() <= 0:
                    return stop("BUDGET_LIMIT")
                checkpoint["status"] = "REVIEW_STARTED"
                save_checkpoint(wave, checkpoint)
                try:
                    decision = CriticDecision.model_validate(
                        await critic(report, reader)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    local_rejection = isinstance(exc, CriticInputError)
                    with board.transaction():
                        board._put(
                            run,
                            "critic_usage",
                            str(wave),
                            {
                                "cost_usd": 0
                                if policy.execution_mode == "offline" or local_rejection
                                else None
                            },
                        )
                        checkpoint["status"] = "FAILED"
                        board._put(run, "wave_checkpoint", str(wave), checkpoint)
                        board._event(
                            run,
                            "critic.failed",
                            wave=wave,
                            reason=str(exc) if local_rejection else "Review failed; no automatic model retry",
                        )
                    return stop(
                        "NEEDS_HUMAN"
                        if policy.execution_mode == "offline" or local_rejection
                        else "UNKNOWN_COST"
                    )
                checkpoint.update(status="REVIEW_READY", decision=decision.model_dump())
                with board.transaction():
                    board._put(
                        run, "critic_usage", str(wave), {"cost_usd": decision.cost_usd}
                    )
                    board._put(run, "wave_checkpoint", str(wave), checkpoint)
            decision = CriticDecision.model_validate(checkpoint["decision"])
            if decision.cost_usd is None:
                return stop("UNKNOWN_COST")
            if checkpoint["status"] == "REVIEW_READY":
                try:
                    apply_review(board, run, decision, checkpoint_wave=wave)
                except ValueError:
                    checkpoint["status"] = "FAILED"
                    save_checkpoint(wave, checkpoint)
                    board.emit(
                        run,
                        "critic.failed",
                        wave=wave,
                        reason="Invalid assessment rejected; prior graph preserved",
                    )
                    return stop("NEEDS_HUMAN")
                checkpoint["status"] = "REVIEW_APPLIED"
            after = board.snapshot(run)
            top = sorted(after.hypotheses, key=lambda h: (h.priority, h.id))[
                : policy.top_claims
            ]
            reason = None
            if top and all(h.lifecycle == "RESOLVED" for h in top):
                reason = "GOAL_SATISFIED"
            elif wave == policy.max_waves:
                reason = "WAVE_LIMIT"
            else:
                for proposed in decision.proposals:
                    result = board.admit_task(
                        run, proposed.model_copy(update={"wave": wave + 1}), policy
                    )
                    if isinstance(result, Rejected):
                        board.emit(
                            run,
                            "task.rejected",
                            reason=result.reason,
                            goal=proposed.goal,
                        )
                next_tasks = [
                    t
                    for t in board.snapshot(run).tasks
                    if t.status == "QUEUED" and t.wave == wave + 1
                ]
                if not next_tasks:
                    reason = (
                        "TASK_LIMIT"
                        if len(after.tasks) >= policy.max_total_tasks
                        else "NEEDS_HUMAN"
                        if any(h.lifecycle == "BLOCKED" for h in top)
                        else "NO_NEW_EVIDENCE"
                        if not report.new_findings and not report.new_leads
                        else "SOURCES_EXHAUSTED"
                    )
            checkpoint.update(status="DONE", stop_reason=reason)
            save_checkpoint(wave, checkpoint)
            if reason:
                return stop(reason)
        return stop("SOURCES_EXHAUSTED")
    except asyncio.CancelledError:
        board.stop(run, "CANCELLED")
        raise
