"""Replaceable Python worker interface. Construction never invokes a provider."""

from collections.abc import Callable
from typing import Protocol
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
)
from .models import EvidencePacket, ResearchTask, RunPolicy, WorkerResult
from .prompts import ROLES
from .tools import create_research_tools, TaskCapability


class OfflineExecution(RuntimeError):
    pass


class ResearchWorker(Protocol):
    async def run(
        self, task: ResearchTask, packet: EvidencePacket, remaining_budget: float
    ) -> WorkerResult: ...


class ReplayWorker:
    """Explicit deterministic test adapter; never presented as autonomous model reasoning."""

    def __init__(self, resolve: Callable[[ResearchTask, EvidencePacket], WorkerResult]):
        self.resolve = resolve

    async def run(self, task, packet, remaining_budget):
        return WorkerResult.model_validate(self.resolve(task, packet))


class ClaudeResearchWorker:
    def __init__(self, policy: RunPolicy, board=None, *, authorized=False, model=None):
        self.policy, self.board, self.authorized, self.model = (
            policy,
            board,
            authorized,
            model,
        )

    async def delegation_guard(self, data, tool_use_id, context):
        if data.get("tool_name") == "Agent":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Unattributed native delegation is disabled. Request a child task through the case tool.",
                }
            }
        return {}

    async def run(self, task, packet, remaining_budget):
        if self.policy.execution_mode == "offline" or not self.authorized:
            raise OfflineExecution("Model execution is disabled for this milestone")
        if self.board is None or not self.model:
            raise ValueError("Explicit board, model and authorization required")
        if remaining_budget <= 0:
            raise ValueError("No remaining model allowance")
        if (
            packet.task_id != task.id
            or packet.token_upper_bound > self.policy.max_input_tokens
        ):
            raise ValueError("Invalid task packet")
        tools = [
            "mcp__case__" + name
            for name in (
                "read_evidence",
                "list_open_hypotheses",
                "submit_findings",
                "propose_lead",
                "request_child_task",
            )
        ]
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=ROLES[task.role],
            tools=[],
            allowed_tools=tools,
            mcp_servers={
                "case": create_research_tools(
                    self.board, TaskCapability.from_task(task), self.policy
                )
            },
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            max_budget_usd=remaining_budget,
            max_turns=8,
            env={
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": str(self.policy.max_depth),
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": str(
                    self.policy.max_concurrent_workers
                ),
            },
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Agent", hooks=[self.delegation_guard])
                ]
            },
            output_format={
                "type": "json_schema",
                "schema": WorkerResult.model_json_schema(),
            },
        )
        result = None
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(packet.model_dump_json())
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        result = message
                        # Persist usage before SDK teardown or cancellation can fail.
                        self.board.record_usage(
                            task.id, task.version, result.total_cost_usd
                        )
        except Exception:
            return WorkerResult(
                status="blocked",
                cost_usd=result.total_cost_usd if result else None,
                unresolved=["SDK execution or teardown failed; no automatic retry"],
            )
        if result is None:
            return WorkerResult(
                status="blocked",
                cost_usd=None,
                unresolved=["Model outcome and cost unknown"],
            )
        if (
            result.is_error
            or result.subtype != "success"
            or not result.structured_output
        ):
            return WorkerResult(
                status="blocked",
                cost_usd=result.total_cost_usd,
                unresolved=["Model did not complete successfully"],
            )
        try:
            from .parsing import parse_model_json
            output = (parse_model_json(result.structured_output, WorkerResult)
                      if isinstance(result.structured_output, str)
                      else WorkerResult.model_validate(result.structured_output))
        except ValueError:
            return WorkerResult(
                status="blocked",
                cost_usd=result.total_cost_usd,
                unresolved=["Invalid worker result"],
            )
        output.cost_usd = result.total_cost_usd
        # Model-submitted tool findings remain proposals; include them before canonical validation.
        try:
            items = self.board._get(task.run_id, "staged_findings", task.id)
            from .models import Finding, Lead

            output.findings.extend(Finding.model_validate(f) for f in items)
        except ValueError:
            pass
        from .models import Lead

        output.leads.extend(
            Lead.model_validate(r["lead"])
            for r in self.board._list(task.run_id, "staged_lead")
            if r["task_id"] == task.id
        )
        return output
