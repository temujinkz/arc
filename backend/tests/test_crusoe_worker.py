"""Real adapter with intercepted HTTP, isolated public case, no provider calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.research.blackboard import Blackboard
from backend.research.evidence import EvidenceReader
from backend.research.models import RunPolicy, TaskProposal
from backend.research.crusoe_worker import CrusoeConfig, CrusoeWorker, inspect_model


CATALOG = {"data": [{
    "id": "fixture/model", "context_length": 128000,
    "supported_parameters": ["response_format", "max_completion_tokens"],
    "top_provider": {"max_completion_tokens": 8192},
    "pricing": {"prompt": "0.20", "completion": "0.60"},
}]}


class CrusoeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.board = Blackboard(self.root / "case.sqlite3")
        self.board.create_run("r", "Example artist")
        self.board.add_source("r", "source", "The artist reports a solo exhibition.",
                              source_kind="SELF_AUTHORED", url="https://example.org/exhibit")
        reader = EvidenceReader(self.board, "r")
        self.span = reader.index_source("source")[0]
        hypothesis = self.board.add_hypothesis("r", "Example artist", "Held an exhibition", 0)
        self.task = self.board.start_task(self.board.admit_task("r", TaskProposal(
            hypothesis_id=hypothesis.id, goal="Check attribution", role="corroboration",
            span_ids=[self.span.id]), RunPolicy()).id, RunPolicy())
        self.packet = reader.build_packet(self.task, 12000)
        self.config = CrusoeConfig(api_key="test-secret", model="fixture/model")
        self.requests = []

    def tearDown(self):
        self.board.close()
        self.tmp.cleanup()

    def response(self, request, *, content=None, usage=True, finish="stop"):
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        data = {"model": "fixture/model", "choices": [{"finish_reason": finish,
                "message": {"content": content or json.dumps({"findings": [{
                    "hypothesis_id": self.task.hypothesis_id,
                    "statement": "The artist reports an exhibition.",
                    "span_ids": [self.span.id], "relation": "supports"}]})}}]}
        if usage:
            data["usage"] = {"prompt_tokens": 1000, "completion_tokens": 100}
        return httpx.Response(200, json=data)

    async def make_worker(self, client):
        model = await inspect_model(client, self.config)
        return CrusoeWorker(client, self.config, model, self.root / "crusoe", self.board)

    async def test_valid_cited_result_and_usage_without_tools_or_private_context(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.response)) as client:
            worker = await self.make_worker(client)
            result = await worker.run(self.task, self.packet, 1.50)
        self.assertEqual(result.status, "completed")
        self.assertAlmostEqual(result.cost_usd, 0.00026)
        self.assertEqual(result.findings[0].span_ids, [self.span.id])
        self.assertFalse(result.findings[0].reviewed)
        wire = json.loads(self.requests[-1].content)
        self.assertEqual(wire["model"], "fixture/model")
        self.assertNotIn("tools", wire)
        self.assertEqual(wire["response_format"], {"type": "json_object"})
        self.assertEqual(wire["max_completion_tokens"], 4096)
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer test-secret")
        self.assertEqual(self.requests[-1].headers["Content-Type"], "application/json")
        receipt = (self.root / "crusoe/usage.json").read_text()
        self.assertNotIn("test-secret", receipt)
        self.assertEqual(json.loads(receipt)["calls"][0]["status"], "complete")

    async def test_unsupported_model_and_insufficient_budget_make_no_generation(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.response)) as client:
            with self.assertRaises(ValueError):
                await inspect_model(client, self.config.model_copy(update={"model": "missing"}))
            worker = await self.make_worker(client)
            result = await worker.run(self.task, self.packet, 0)
            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.cost_usd, 0)
        self.assertTrue(all(r.method == "GET" for r in self.requests))

    async def test_invalid_citation_and_bad_json_keep_known_usage(self):
        for content in ('not JSON', json.dumps({"findings": [{"hypothesis_id": self.task.hypothesis_id,
                       "statement": "Invented", "span_ids": ["not-supplied"], "relation": "supports"}]})):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.response(r, content=content))) as client:
                    worker = CrusoeWorker(client, self.config, await inspect_model(client, self.config), Path(directory), self.board)
                    result = await worker.run(self.task, self.packet, 1.50)
                    self.assertEqual(result.status, "blocked")
                    self.assertGreater(result.cost_usd, 0)
                    self.assertEqual(result.findings, [])

    async def test_timeout_or_missing_usage_blocks_more_spend(self):
        for timeout in (True, False):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                def respond(r):
                    if r.method == "POST" and timeout:
                        self.requests.append(r)
                        raise httpx.ReadTimeout("Do not leak test-secret", request=r)
                    return self.response(r, usage=False)
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    worker = CrusoeWorker(client, self.config, await inspect_model(client, self.config), Path(directory), self.board)
                    first = await worker.run(self.task, self.packet, 1.50)
                    count = len(self.requests)
                    second = await worker.run(self.task, self.packet, 1.50)
                    self.assertIsNone(first.cost_usd)
                    self.assertIsNone(second.cost_usd)
                    self.assertEqual(len(self.requests), count)
                    self.assertTrue(worker.usage_unknown)
                    self.assertNotIn("test-secret", first.model_dump_json())

    async def test_rate_limit_has_no_retry_or_provider_fallback(self):
        def respond(r):
            if r.method == "GET": return self.response(r)
            self.requests.append(r)
            return httpx.Response(429, headers={"retry-after": "60"}, json={"error": "sensitive provider body"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            worker = await self.make_worker(client)
            first = await worker.run(self.task, self.packet, 1.50)
            await worker.run(self.task, self.packet, 1.50)
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)
        self.assertNotIn("sensitive provider body", first.model_dump_json())
        self.assertEqual(worker.calls[0]["retry_after"], "60")

    async def test_offline_mode_and_endpoint_validation(self):
        with self.assertRaises(ValueError):
            CrusoeConfig(api_key="secret", model="x", base_url="http://external.example/v1")
        with self.assertRaises(ValueError):
            CrusoeConfig(api_key="secret", model="x", base_url="https://user:secret@example.org/v1")
        with patch.dict("os.environ", {"ARC_RESEARCH_OFFLINE": "1"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("Network attempted"))) as client:
                with self.assertRaises(ValueError): await inspect_model(client, self.config)


class PipelineRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_crusoe_config_fails_before_harvesting(self):
        from unittest.mock import AsyncMock
        from backend.investigations import Investigation
        from backend.scratchpad import InvestigationScratchpad
        from backend.research.pipeline import MODEL, run_workspace_research

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {
            "ARC_RESEARCH_DIR": tmp, "ARC_RESEARCH_WORKER_PROVIDER": "crusoe",
            "CRUSOE_API_KEY": "", "CRUSOE_WORKER_MODEL": "",
        }), patch("backend.orchestrator.model_for", return_value=MODEL), patch(
            "backend.orchestrator.harvest", new_callable=AsyncMock
        ) as harvest:
            run = Investigation("Artist", budget=1.5)
            run.scratchpad = InvestigationScratchpad("missing-key", run.emit)
            await run_workspace_research(run)
            harvest.assert_not_awaited()
            self.assertTrue(any(e["type"] == "investigation.failed" for e in run.events))

    async def test_pipeline_dispatches_public_worker_and_accounts_both_providers(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from backend.investigations import Investigation
        from backend.scratchpad import InvestigationScratchpad
        from backend.research.pipeline import MODEL, run_workspace_research
        from backend.research.crusoe_worker import ModelSpec
        from backend.research.eval_adapters import ExtractedClaims

        spec = ModelSpec(model="fixture/model", context_length=128000,
                         max_output_tokens=4096, output_parameter="max_completion_tokens",
                         input_per_million="0.20", output_per_million="0.60")

        async def extract(model, stage, instruction, payload, schema):
            ticket = model.ledger.reserve(stage)
            cost = model.ledger.finish(ticket, {"input_tokens": 100, "output_tokens": 100})
            return ExtractedClaims(claims=[{"statement": "Reports an exhibition",
                "span_ids": [payload["passages"][0]["id"]], "limitation": "Self-report"}],
                source_assessments=[{"evidence_id": s, "status": "relevant", "reason": "Target source"}
                                    for s in {p["evidence_id"] for p in payload["passages"]}],
                selection_reason="Relevant activity"), cost

        async def schedule(goal, board, worker, policy, *, critic):
            self.assertIsInstance(worker, CrusoeWorker)
            task = board.start_task(board.admit_task(goal.run_id, goal.initial_tasks[0], policy).id, policy)
            packet = EvidenceReader(board, goal.run_id).build_packet(task, 12000)
            def respond(request):
                self.assertEqual(request.url.host, "api.inference.crusoecloud.com")
                return httpx.Response(200, json={"model": "fixture/model",
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                worker.client = client
                result = await worker.run(task, packet, goal.remaining_budget)
            self.assertEqual(result.status, "completed")
            return board.snapshot(goal.run_id)

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {
            "ARC_RESEARCH_DIR": tmp, "ARC_RESEARCH_WORKER_PROVIDER": "crusoe",
            "CRUSOE_API_KEY": "test-secret", "CRUSOE_WORKER_MODEL": "fixture/model",
            "ANTHROPIC_API_KEY": "test-only", "ARC_RESEARCH_OFFLINE": "0",
        }), patch("backend.orchestrator.model_for", return_value=MODEL), patch(
            "backend.orchestrator.harvest", new=AsyncMock(return_value=SimpleNamespace(total_cost_usd=0.01, result="Collected public evidence"))
        ), patch("backend.research.pipeline.inspect_model", new=AsyncMock(return_value=spec)), patch(
            "backend.research.pipeline.EvaluationModel.generate", new=extract
        ), patch("backend.research.pipeline.run_research", new=schedule), patch(
            "backend.research.pipeline.project_case",
            return_value=SimpleNamespace(graph=None, coverage={}, missing_portrait_sections=[]),
        ):
            run = Investigation("Artist", budget=1.5)
            run.scratchpad = InvestigationScratchpad("routing", run.emit)
            run.scratchpad.capture("mcp__github__get_file_contents", "tool", "I held an exhibition", {})
            await run_workspace_research(run)
            self.assertFalse(any(e["type"] == "investigation.failed" for e in run.events))
            usage = next(e for e in run.events if e["type"] == "agent.usage")
            self.assertAlmostEqual(usage["cost_usd"], 0.00032)
            self.assertAlmostEqual(usage["provider_costs_usd"]["crusoe"], 0.00026)
            self.assertTrue(any(e["type"] == "investigation.complete" for e in run.events))
