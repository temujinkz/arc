"""Stateful Claude SDK harvesting, native human input and graph orchestration."""

import asyncio
import math
import os
import json
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
    PermissionResultAllow, PermissionResultDeny, ResultMessage,
)
from claude_agent_sdk.types import StreamEvent

from backend.agent import mcp_registry
from backend.prompts import HARVESTER_PROMPT, ORCHESTRATOR_PROMPT
from backend.scratchpad import InvestigationScratchpad
from backend.tool_outputs import model_output, recover_persisted_output, source_cache_info


def model_for(purpose: str) -> str:
    return (os.getenv(f"CLAUDE_{purpose.upper()}_MODEL", "").strip()
            or os.getenv("CLAUDE_MODEL", "").strip() or "claude-haiku-5-5")


def usage_event(result: ResultMessage, phase: str) -> dict:
    usage = result.usage or {}
    return {"type": "agent.usage", "phase": phase,
            "cost_usd": result.total_cost_usd,
            **{key: usage.get(key, 0) for key in (
                "input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )}}


async def answer_questions(scratchpad, tool_id, input_data):
    """Native SDK permission callback: only actual UI replies become answers."""
    questions = input_data.get("questions", [])
    if not questions:
        return PermissionResultDeny(message="Provide a question and candidate options.")
    answers = {}
    for question in questions:
        reply = await scratchpad.request_human_input(
            tool_id, question["question"], question["options"],
            multi_select=question.get("multiSelect", False),
        )
        answers[question["question"]] = (
            reply.get("text", "").strip() or ", ".join(reply["selected"])
        )
    return PermissionResultAllow(updated_input={"questions": questions, "answers": answers})


def build_options(scratchpad: InvestigationScratchpad, budget: float) -> ClaudeAgentOptions:
    servers, allowed = mcp_registry(refresh_id=scratchpad.session_id if scratchpad.refresh_sources else "")

    def call_id(data, fallback):
        value = data.get("tool_use_id") or fallback
        if not value:
            raise ValueError("SDK hook did not include a tool use ID.")
        return value

    async def started(data, tool_use_id, context):
        name = data["tool_name"]
        if name not in {*allowed, "Agent", "AskUserQuestion"}:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "Tool is not enabled in ARC."}}
        await scratchpad.record_tool_start(
            call_id(data, tool_use_id), name, data.get("tool_input", {}),
            subagent=data.get("agent_id") or data.get("agent_type"),
        )
        return {}

    async def completed(data, tool_use_id, context):
        response = data.get("tool_response")
        recovered = False
        try:
            response, recovered = recover_persisted_output(
                response, data, max_bytes=scratchpad.remaining_evidence_bytes,
            )
        except (OSError, ValueError, KeyError):
            # Capture rejects the unresolved notice; never infer scraping failed
            # or request another paid run to work around a transport-size limit.
            await scratchpad.record_tool_result(
                call_id(data, tool_use_id), data["tool_name"], response,
                inputs=data.get("tool_input", {}),
            )
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                    "updatedToolOutput": "ARC could not read this SDK-persisted result. "
                    "Scraping may have succeeded; no source data was captured. "
                    "Do not repeat the paid operation or cite this notice as evidence."}}
        await scratchpad.record_tool_result(
            call_id(data, tool_use_id), data["tool_name"], response,
            inputs=data.get("tool_input", {}),
            capture_evidence=data["tool_name"].startswith("mcp__"),
        )
        if recovered or data["tool_name"] == "mcp__osint__scrape_linkedin_profile":
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                    "updatedToolOutput": json.dumps({"source_cache": source_cache_info(response),
                                                     "data": model_output(data["tool_name"], response)},
                                                    ensure_ascii=False, separators=(",", ":"))}}
        return {}

    async def failed(data, tool_use_id, context):
        await scratchpad.record_tool_failure(call_id(data, tool_use_id), data["tool_name"])
        return {}

    async def permission(tool_name, input_data, context):
        if tool_name == "AskUserQuestion":
            return await answer_questions(scratchpad, context.tool_use_id, input_data)
        # Pre-approved MCP reads execute normally; unexpected permission requests
        # must not broaden the allowlist or silently gain access to private data.
        return PermissionResultDeny(message="This operation is not pre-approved by ARC.")

    hooks = {name: [HookMatcher(matcher=".*", hooks=[callback])] for name, callback in (
        ("PreToolUse", started), ("PostToolUse", completed), ("PostToolUseFailure", failed),
    )}
    return ClaudeAgentOptions(
        model=model_for("orchestrator"), cwd=str(Path(".arc").resolve()),
        system_prompt=ORCHESTRATOR_PROMPT, tools=["Agent", "AskUserQuestion"],
        allowed_tools=["Agent", *allowed], permission_mode="default", can_use_tool=permission,
        setting_sources=[], strict_mcp_config=True, mcp_servers=servers,
        include_partial_messages=True, max_turns=15, max_budget_usd=budget, hooks=hooks,
        agents={"osint_harvester": AgentDefinition(
            description="Collect primary professional sources for the resolved identity.",
            prompt=HARVESTER_PROMPT, tools=allowed,
            model=model_for("harvester"), maxTurns=8,
        )},
    )


async def harvest(target: str, scratchpad: InvestigationScratchpad, budget: float):
    Path(".arc").mkdir(exist_ok=True)
    options = build_options(scratchpad, budget)
    scratchpad.emit({"type": "investigation.collecting", "target": target,
                     "model": options.model, "mcp_servers": list(options.mcp_servers)})
    result = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Investigate this public professional identity: {target}")
        async for message in client.receive_response():
            if isinstance(message, StreamEvent):
                delta = message.event.get("delta", {})
                if delta.get("type") == "text_delta":
                    scratchpad.emit({"type": "agent.text", "text": delta["text"],
                                     "subagent": message.parent_tool_use_id or "orchestrator"})
            elif isinstance(message, ResultMessage):
                result = message
    if result is None or result.is_error or result.subtype != "success":
        raise RuntimeError("Harvesting did not finish successfully.")
    scratchpad.emit(usage_event(result, "collecting"))
    scratchpad.emit({"type": "agent.result", "text": result.result,
                     "sdk_session_id": result.session_id})
    return result


async def run_osint_orchestrator(target: str, scratchpad: InvestigationScratchpad, budget: float):
    from backend.graph_architect import execute_graph_architect_pass

    phase = "collecting"
    try:
        result = await harvest(target, scratchpad, budget)
        phase = "architecting"
        cost = result.total_cost_usd
        if cost is None or not math.isfinite(cost) or cost < 0:
            raise RuntimeError("Harvest cost is unavailable; cannot allocate architect budget.")
        remaining = budget - cost
        if remaining <= 0:
            raise RuntimeError("Claude budget exhausted before the architect pass.")
        await execute_graph_architect_pass(scratchpad, remaining, target=target)
        scratchpad.emit({"type": "investigation.complete"})
    except asyncio.CancelledError:
        scratchpad.emit({"type": "investigation.cancelled"})
        raise
    except Exception as exc:
        # Do not expose raw provider exception strings: they may include secrets.
        scratchpad.emit({"type": "investigation.failed", "phase": phase,
                         "error": type(exc).__name__})
    finally:
        scratchpad.cancel_pending_input()
