"""Offline regressions for source/context separation found in live verification."""

import copy
import unittest
from unittest.mock import AsyncMock, patch

from backend.graph_architect import commit_architect_output, validate_architect_graph
from backend.scratchpad import InvestigationScratchpad


class ArchitectProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        pad = InvestigationScratchpad("offline")
        await pad.record_tool_result("source", "mcp__osint__search_public_web", {"url": "https://example.org/profile"})
        eid = pad.raw_evidence[0]["evidence_id"]
        pad.human_decisions.append({"request_id": "human-choice", "answer": {"selected": ["This profile"]}})
        node = {"node_id": "person", "label": "Person", "title": "Synthetic profile", "summary": "Operator-selected public profile, unverified.",
                "evidence_ids": [eid, "human-choice"], "evidence_state": "unverified", "metadata": {}}
        anchor = {**node, "node_id": "anchor", "label": "IdentityAnchor"}
        edge = {"source_id": "person", "target_id": "anchor", "relationship": "HAS_IDENTITY", "reason": "Operator selected this lead.", "evidence_ids": ["human-choice"]}
        data = {"summary": "Unverified public lead.", "detected_archetype": "Unknown", "signal_to_hype_score": None,
                "nodes": [node, anchor], "edges": [edge], "portrait": []}
        return pad, data, eid

    async def test_recorded_human_ids_are_context_not_source_evidence(self):
        pad, data, eid = await self.fixture()
        original = copy.deepcopy(data)
        graph = validate_architect_graph(pad, data)
        self.assertEqual(data, original)
        self.assertEqual(len(graph.nodes), 2)
        self.assertTrue(all(node.evidence_ids == [eid] for node in graph.nodes))
        self.assertEqual(graph.edges, [])
        self.assertEqual(pad.human_decisions[0]["request_id"], "human-choice")
        events = []
        while not pad.sse_queue.empty(): events.append(pad.sse_queue.get_nowait())
        self.assertEqual(events[-1]["type"], "graph.provenance_adjusted")
        self.assertEqual(events[-1]["adjustments"][-1]["action"], "context_only_item_omitted")

    async def test_invented_ids_still_fail_and_context_only_person_is_never_sourced(self):
        pad, data, _ = await self.fixture()
        data["nodes"][0]["evidence_ids"].append("made-up-source")
        with self.assertRaises(ValueError): validate_architect_graph(pad, data)
        data["nodes"][0]["evidence_ids"] = ["human-choice"]
        with self.assertRaises(ValueError): validate_architect_graph(pad, data)

    async def test_saved_output_can_commit_without_model_and_never_commits_before_write(self):
        pad, data, _ = await self.fixture()
        with patch("backend.graph_architect.ClaudeSDKClient", side_effect=AssertionError("No new provider call")), patch("backend.graph_architect.commit_graph", AsyncMock()) as write:
            await commit_architect_output(pad, data, "configured-model")
            write.assert_awaited_once()
        events = []
        while not pad.sse_queue.empty(): events.append(pad.sse_queue.get_nowait())
        self.assertEqual(events[-1]["type"], "graph.final_commit")
        pad, data, _ = await self.fixture()
        with patch("backend.graph_architect.commit_graph", AsyncMock(side_effect=RuntimeError("Offline write failure"))):
            with self.assertRaises(RuntimeError): await commit_architect_output(pad, data, "configured-model")
        events = []
        while not pad.sse_queue.empty(): events.append(pad.sse_queue.get_nowait())
        self.assertFalse(any(event["type"] == "graph.final_commit" for event in events))
