"""Use the Agent SDK to structure evidence, then commit the validated graph."""

import json
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from backend.graph_models import AgentConstructedGraph
from backend.graph_store import commit_graph
from backend.orchestrator import model_for, usage_event
from backend.prompts import ARCHITECT_PROMPT
from backend.scratchpad import InvestigationScratchpad


async def execute_graph_architect_pass(
    scratchpad: InvestigationScratchpad, budget: float, *, target: str,
) -> None:
    if not scratchpad.raw_evidence:
        raise ValueError("No successful tool evidence was captured; graph commit skipped.")
    scratchpad.emit({"type": "agent.status", "stage": "architecting",
                     "text": "Structuring collected evidence into a graph."})
    options = ClaudeAgentOptions(
        model=model_for("architect"), cwd=str(Path(".arc").resolve()),
        tools=[], allowed_tools=[], mcp_servers={}, strict_mcp_config=True,
        permission_mode="dontAsk", setting_sources=[], max_turns=5, max_budget_usd=budget,
        system_prompt=ARCHITECT_PROMPT,
        output_format={"type": "json_schema", "schema": AgentConstructedGraph.model_json_schema()},
    )
    result = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(json.dumps({"target": target, "human_decisions": scratchpad.human_decisions,
                                       "evidence": scratchpad.architect_evidence()}, ensure_ascii=False))
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                result = message
    if result is not None:
        scratchpad.emit(usage_event(result, "architecting"))
    if result is None or result.is_error or result.subtype != "success" or not result.structured_output:
        raise RuntimeError("Graph architect did not return a successful structured result.")
    graph = AgentConstructedGraph.model_validate(result.structured_output)
    known = {e["evidence_id"] for e in scratchpad.architect_evidence()}
    for item in [*graph.nodes, *graph.edges, *graph.portrait]:
        if not set(item.evidence_ids) <= known:
            raise ValueError("Graph cites evidence that was not collected.")
    scratchpad.emit({"type": "agent.architect_decision", "archetype": graph.detected_archetype,
                     "score": graph.signal_to_hype_score, "summary": graph.summary})
    scratchpad.emit({"type": "graph.proposed", "graph": graph.model_dump()})
    await commit_graph(scratchpad, graph, options.model)
    # A proposal is never displayed as committed until the transaction succeeds.
    scratchpad.emit({"type": "graph.final_commit", "graph": graph.model_dump()})
