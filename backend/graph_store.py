"""Batch writes grouped by validated labels and relationships in one transaction."""

import json
import os
from collections import defaultdict

from neo4j import AsyncGraphDatabase

from backend.graph_models import AgentConstructedGraph
from backend.scratchpad import InvestigationScratchpad


async def write_graph(tx, scratchpad: InvestigationScratchpad, graph: AgentConstructedGraph, model: str):
    record = await tx.run(
        "MERGE (run:ARCInvestigation {session_id: $session_id}) "
        "SET run.evidence_json = $evidence, run.human_decisions_json = $human_decisions, "
        "run.summary = $summary, run.model = $model, "
        "run.graph_json = $graph_json, "
        "run.archetype = $archetype, run.score = $score, run.committed_at = datetime()",
        session_id=scratchpad.session_id, evidence=json.dumps(scratchpad.architect_evidence()),
        graph_json=graph.model_dump_json(),
        human_decisions=json.dumps(scratchpad.human_decisions),
        summary=graph.summary, model=model, archetype=graph.detected_archetype,
        score=graph.signal_to_hype_score,
    )
    await record.consume()
    # Replace only this run's AI/OSINT nodes. PeerClaim nodes attach to the stable
    # ARCInvestigation, not ARCNode, and survive a dossier rebuild.
    record = await tx.run("MATCH (n:ARCNode {session_id: $session_id}) DETACH DELETE n",
                          session_id=scratchpad.session_id)
    await record.consume()
    nodes, edges = defaultdict(list), defaultdict(list)
    for node in graph.nodes:
        row = node.model_dump(exclude={"label", "metadata"})
        row["metadata_json"] = json.dumps(node.metadata)
        nodes[node.label].append(row)
    for edge in graph.edges:
        edges[edge.relationship].append(edge.model_dump(exclude={"relationship"}))
    # Interpolated names are restricted by Pydantic Literal enums, never raw model strings.
    for label, rows in nodes.items():
        record = await tx.run(
            "UNWIND $rows AS row "
            f"MERGE (n:ARCNode:{label} {{session_id: $session_id, node_id: row.node_id}}) "
            "SET n += row WITH n MATCH (run:ARCInvestigation {session_id: $session_id}) "
            "MERGE (run)-[:CONTAINS]->(n)", rows=rows, session_id=scratchpad.session_id,
        )
        await record.consume()
    for relationship, rows in edges.items():
        record = await tx.run(
            "UNWIND $rows AS row "
            "MATCH (a:ARCNode {session_id: $session_id, node_id: row.source_id}), "
            "(b:ARCNode {session_id: $session_id, node_id: row.target_id}) "
            f"MERGE (a)-[r:{relationship}]->(b) "
            "SET r.reason = row.reason, r.evidence_ids = row.evidence_ids",
            rows=rows, session_id=scratchpad.session_id,
        )
        await record.consume()


async def commit_graph(scratchpad, graph, model):
    async with AsyncGraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(
            os.getenv("NEO4J_USERNAME", "").strip() or os.environ["NEO4J_USER"].strip(),
            os.environ["NEO4J_PASSWORD"],
        ),
    ) as driver:
        async with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            await session.execute_write(write_graph, scratchpad, graph, model)
