import asyncio
from unittest.mock import patch
import unittest
from backend.tests import test_research_blackboard as fixtures
from backend.research.workers import (
    ClaudeResearchWorker,
    OfflineExecution,
    ReplayWorker,
)
from backend.research.tools import ResearchTools, TaskCapability
from backend.research.models import RunPolicy, WorkerResult, Rejected


class WorkerTests(unittest.TestCase):
    setUp = fixtures.BoardTests.setUp
    tearDown = fixtures.BoardTests.tearDown
    proposal = fixtures.BoardTests.proposal

    def test_offline_worker_rejects_before_sdk_creation(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        packet = self.reader.build_packet(task, 4096)
        with patch("backend.research.workers.ClaudeSDKClient") as sdk:
            with self.assertRaises(OfflineExecution):
                asyncio.run(ClaudeResearchWorker(RunPolicy()).run(task, packet, 1))
            sdk.assert_not_called()

    def test_tools_scope_and_child_depth(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        task = self.board.start_task(task.id, RunPolicy())
        tools = ResearchTools(self.board, TaskCapability.from_task(task), RunPolicy())
        child = tools.request_child_task(
            "Inspect another passage", "corroboration", [self.spans[0].id]
        )
        self.assertEqual(child.depth, 1)
        self.assertIsInstance(
            tools.request_child_task(
                "Inspect another passage", "corroboration", [self.spans[0].id]
            ),
            Rejected,
        )
        child = self.board.start_task(child.id, RunPolicy())
        childtools = ResearchTools(
            self.board, TaskCapability.from_task(child), RunPolicy()
        )
        grandchild = childtools.request_child_task(
            "Inspect third passage", "corroboration", [self.spans[0].id]
        )
        self.assertEqual(grandchild.depth, 2)
        # Release active slots before starting the grandchild.
        self.board.record_result(task.id, WorkerResult(), task.version)
        grandchild = self.board.start_task(grandchild.id, RunPolicy())
        last = ResearchTools(
            self.board, TaskCapability.from_task(grandchild), RunPolicy()
        )
        self.assertEqual(
            last.request_child_task("Too deep", "artifact", []).reason, "DEPTH_LIMIT"
        )
        self.board.stop("r", "CANCELLED")
        with self.assertRaises(ValueError):
            tools.read_evidence(self.spans[0].id)

    def test_replay_contract_and_no_unmatched_native_agent(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        packet = self.reader.build_packet(task, 4096)
        worker = ReplayWorker(
            lambda t, p: WorkerResult(unresolved=["No independent source"])
        )
        result = asyncio.run(worker.run(task, packet, 0))
        self.assertEqual(result.unresolved, ["No independent source"])
        gate = ClaudeResearchWorker(RunPolicy()).delegation_guard
        answer = asyncio.run(
            gate(
                {
                    "tool_name": "Agent",
                    "tool_input": {"subagent_type": "general-purpose"},
                },
                None,
                {},
            )
        )
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
