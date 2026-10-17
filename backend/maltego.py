"""Offline CSV bridge for Maltego's Import Graph from Table wizard."""

import csv
import io

from backend.graph_models import AgentConstructedGraph


def graph_csv(data: dict) -> str:
    graph = AgentConstructedGraph.model_validate(data)
    nodes = {node.node_id: node for node in graph.nodes}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["source_id", "source_type", "source_title", "source_url", "source_evidence_state",
                     "relationship", "target_id", "target_type", "target_title", "target_url",
                     "target_evidence_state", "reason", "evidence_ids"])

    def safe(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text

    def row(source, edge=None, target=None):
        writer.writerow([safe(value) for value in [
            source.node_id, source.label, source.title, source.url, source.evidence_state,
            edge.relationship if edge else "", target.node_id if target else "",
            target.label if target else "", target.title if target else "", target.url if target else "",
            target.evidence_state if target else "", edge.reason if edge else "",
            ";".join(edge.evidence_ids if edge else source.evidence_ids),
        ]])

    linked = set()
    for edge in graph.edges:
        row(nodes[edge.source_id], edge, nodes[edge.target_id])
        linked.update((edge.source_id, edge.target_id))
    for node in graph.nodes:
        if node.node_id not in linked:
            row(node)
    return stream.getvalue()
