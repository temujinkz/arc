"""Use the Agent SDK to structure evidence, then commit the validated graph."""

import json
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from backend.graph_models import AgentConstructedGraph
from backend.graph_store import commit_graph
from backend.orchestrator import model_for, usage_event
from backend.prompts import ARCHITECT_PROMPT
from backend.scratchpad import InvestigationScratchpad


def validate_architect_graph(scratchpad: InvestigationScratchpad, data: dict) -> AgentConstructedGraph:
    """Keep recorded operator context separate without inventing source citations.

    A model may accidentally cite a human-answer ID alongside evidence IDs.
    Only IDs of actual recorded decisions can be removed this way; invented IDs
    still fail. Items supported solely by operator context are omitted, never
    promoted to sourced claims. The untouched response is retained separately.
    """
    graph = AgentConstructedGraph.model_validate(data)
    known = {item["evidence_id"] for item in scratchpad.architect_evidence()}
    decisions = {item["request_id"] for item in scratchpad.human_decisions if item.get("request_id")}
    adjustments = []
    for collection in ("nodes", "edges", "portrait"):
        kept = []
        for index, item in enumerate(getattr(graph, collection)):
            references = set(item.evidence_ids)
            if not references <= known | decisions:
                raise ValueError("Graph cites evidence that was not collected.")
            context = references & decisions - known
            if context:
                item.evidence_ids = [reference for reference in item.evidence_ids if reference in known]
                adjustments.append({"path": f"{collection}[{index}]", "operator_decision_ids": sorted(context),
                                    "action": "context_removed_from_source_citations" if item.evidence_ids else "context_only_item_omitted"})
            if item.evidence_ids:
                kept.append(item)
        setattr(graph, collection, kept)
    node_ids = {node.node_id for node in graph.nodes}
    graph.edges = [edge for edge in graph.edges if edge.source_id in node_ids and edge.target_id in node_ids]
    # Recheck central-person, schema and source requirements after any omissions.
    graph = AgentConstructedGraph.model_validate(graph.model_dump())
    if adjustments:
        scratchpad.emit({"type": "graph.provenance_adjusted", "adjustments": adjustments,
                         "text": "Operator context was kept separate from source citations. Context-only graph items were omitted."})
    return graph


async def commit_architect_output(scratchpad: InvestigationScratchpad, data: dict, model: str) -> None:
    """Validate and commit an already generated response; never invokes a model."""
    graph = validate_architect_graph(scratchpad, data)
    scratchpad.emit({"type": "agent.architect_decision", "archetype": graph.detected_archetype,
                     "score": graph.signal_to_hype_score, "summary": graph.summary})
    scratchpad.emit({"type": "graph.proposed", "graph": graph.model_dump()})
    await commit_graph(scratchpad, graph, model)
    # A proposal is never displayed as committed until the transaction succeeds.
    scratchpad.emit({"type": "graph.final_commit", "graph": graph.model_dump()})


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
    # Retain paid output even if local validation/Neo4j fails, so explicit repair
    # can reuse the same response without another provider request.
    scratchpad.emit({"type": "graph.architect_output", "output": result.structured_output,
                     "model": options.model, "sdk_session_id": result.session_id})
    await commit_architect_output(scratchpad, result.structured_output, options.model)
