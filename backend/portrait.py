"""Legacy v1 model-only portrait enrichment (explicit authorization required).

Recursive v2 uses backend.research.projection.synthesize_portrait with a bounded
reviewed-finding packet. The offline milestone leaves missing sections explicit.
"""

import argparse
import asyncio
import fcntl
import json
import math
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.graph_models import AgentConstructedGraph, PortraitSection, GraphNodeSpec, GraphEdgeSpec
from backend.graph_store import commit_graph
from backend.investigations import Investigation
from backend.orchestrator import model_for, usage_event
from backend.prompts import ARCHITECT_PROMPT
from backend.storage import RunStore, database_path


class Portrait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[PortraitSection] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def distinct_sections(self):
        if len({s.section for s in self.sections}) != 5:
            raise ValueError("All five distinct portrait sections are required.")
        return self


async def enrich(profile_id: str):
    path = database_path()
    with path.with_suffix(".api.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = RunStore(path)
        try:
            snapshot = store.load(profile_id)
            if not snapshot or not snapshot["done"]:
                raise ValueError("An inactive, completed investigation is required.")
            costs = [e.get("cost_usd") for e in snapshot["events"] if e["type"] == "agent.usage"]
            if not costs or any(c is None or not math.isfinite(c) or c < 0 for c in costs):
                raise ValueError("Known usage is required.")
            remaining = snapshot["budget"] - sum(costs)
            if remaining <= 0:
                raise ValueError("Budget exhausted.")
            run = Investigation.restore(snapshot, store)
            graph = AgentConstructedGraph.model_validate(next(e["graph"] for e in reversed(run.events) if e["type"] == "graph.final_commit"))
            options = ClaudeAgentOptions(model=model_for("architect"), cwd=str(Path(".arc").resolve()),
                tools=[], allowed_tools=[], mcp_servers={}, strict_mcp_config=True,
                permission_mode="dontAsk", setting_sources=[], max_turns=5, max_budget_usd=remaining,
                system_prompt=ARCHITECT_PROMPT + "\nFor this task return only the five portrait sections, not a replacement graph.",
                output_format={"type": "json_schema", "schema": Portrait.model_json_schema()})
            run.done = False
            run.scratchpad.emit({"type": "agent.status", "stage": "portrait", "text": "Building the professional portrait from cached public evidence."})
            result = None
            try:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(json.dumps({"target": run.target, "graph": graph.model_dump(),
                        "evidence": run.scratchpad.architect_evidence(), "human_decisions": run.scratchpad.human_decisions}, ensure_ascii=False))
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            result = message
                if result:
                    run.scratchpad.emit(usage_event(result, "portrait"))
                if not result or result.is_error or result.subtype != "success" or not result.structured_output:
                    raise RuntimeError("Portrait did not complete.")
                portrait = Portrait.model_validate(result.structured_output)
                known = {e["evidence_id"] for e in run.scratchpad.architect_evidence()}
                if any(not set(s.evidence_ids) <= known for s in portrait.sections):
                    raise ValueError("Portrait cites unknown evidence.")
                graph.portrait = portrait.sections
                graph.nodes = [n for n in graph.nodes if not n.node_id.startswith("portrait_")]
                graph.edges = [e for e in graph.edges if not e.target_id.startswith("portrait_") and not e.source_id.startswith("portrait_")]
                person = next(n for n in graph.nodes if n.label == "Person")
                for section in portrait.sections:
                    node_id = f"portrait_{section.section}"
                    graph.nodes.append(GraphNodeSpec(node_id=node_id, label="AIJudgment", title=section.title,
                        summary=section.analysis, evidence_ids=section.evidence_ids, evidence_state="interpretation",
                        metadata={"portrait_section": section.section, "limitations": section.limitations}))
                    graph.edges.append(GraphEdgeSpec(source_id=person.node_id, target_id=node_id, relationship="JUDGED_AS",
                        reason="ARC interpretation of cited public evidence, not a peer judgment.", evidence_ids=section.evidence_ids))
                graph = AgentConstructedGraph.model_validate(graph.model_dump())
                await commit_graph(run.scratchpad, graph, options.model)
                run.scratchpad.emit({"type": "graph.final_commit", "graph": graph.model_dump()})
                run.scratchpad.emit({"type": "investigation.complete"})
                return {"sections": len(portrait.sections), "nodes": len(graph.nodes), "source_calls": 0,
                        "reported_cost_usd": result.total_cost_usd}
            except BaseException:
                run.chat_blocked = True
                run.scratchpad.emit({"type": "investigation.failed", "phase": "portrait", "error": "Portrait update did not complete; no automatic retry."})
                raise
            finally:
                run.finish()
        finally:
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_id")
    parser.add_argument("--execute-authorized-portrait", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(enrich(args.profile_id))))
