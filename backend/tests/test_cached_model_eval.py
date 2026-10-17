"""Offline checks for the authorized evaluation's spend and network boundaries."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import closing

import httpx


class EvaluationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.research.eval_model"))
        from backend.research import eval_model

        return eval_model

    async def test_thirteen_reserved_requests_stay_under_ten_cents(self):
        m = self.module()
        self.assertLessEqual(m.MAX_CALLS * m.RESERVATION_USD, 0.10)
        with tempfile.TemporaryDirectory() as directory:
            ledger = m.EvaluationLedger(Path(directory))
            for i in range(m.MAX_CALLS):
                ticket = ledger.reserve(str(i))
                ledger.finish(ticket, {"input_tokens": 50000, "output_tokens": 4096})
            with self.assertRaises(m.EvaluationStopped):
                ledger.reserve("fourteenth")
            self.assertLessEqual(ledger.total_cost, 0.10)

    async def test_unknown_usage_prevents_any_further_paid_request(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            ledger = m.EvaluationLedger(Path(directory))
            ledger.reserve("worker")
            with self.assertRaises(m.EvaluationStopped):
                ledger.reserve("critic")
            self.assertEqual(
                json.loads((Path(directory) / "usage.json").read_text())["calls"][0][
                    "status"
                ],
                "pending",
            )

    async def test_scraper_hosts_and_extra_api_paths_rejected_before_transport(self):
        m = self.module()

        def unexpected(request):
            self.fail("Unapproved request reached network transport")

        transport = m.ModelOnlyTransport(httpx.MockTransport(unexpected))
        for url in (
            "https://api.exa.ai/search",
            "https://api.anthropic.com/v1/files",
            "http://api.anthropic.com/v1/messages",
        ):
            with self.assertRaises(m.EvaluationStopped):
                await transport.handle_async_request(
                    httpx.Request("POST", url, json={})
                )

    async def test_tool_access_and_other_models_rejected(self):
        m = self.module()
        transport = m.ModelOnlyTransport(
            httpx.MockTransport(lambda r: self.fail("Unapproved request"))
        )
        for body in ({"model": "other"}, {"model": m.MODEL, "tools": [{}]}):
            with self.assertRaises(m.EvaluationStopped):
                await transport.handle_async_request(
                    httpx.Request(
                        "POST", "https://api.anthropic.com/v1/messages", json=body
                    )
                )

    async def test_response_usage_is_saved_even_when_json_is_invalid(self):
        m = self.module()

        def respond(request):
            if request.url.path.endswith("count_tokens"):
                return httpx.Response(200, json={"input_tokens": 100})
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                    "content": [{"type": "text", "text": "invalid JSON"}],
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            ledger = m.EvaluationLedger(Path(directory))
            async with httpx.AsyncClient(
                transport=m.ModelOnlyTransport(httpx.MockTransport(respond))
            ) as client:
                model = m.EvaluationModel(client, ledger)
                from backend.research.models import WorkerResult

                with self.assertRaises(ValueError):
                    await model.generate("worker", "Review evidence", {}, WorkerResult)
            self.assertGreater(ledger.total_cost, 0)
            self.assertEqual(ledger.calls[0]["status"], "complete")

    async def test_oversized_prompt_rejected_without_request(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: self.fail("Request escaped"))
            ) as client:
                model = m.EvaluationModel(client, m.EvaluationLedger(Path(directory)))
                from backend.research.models import WorkerResult

                with self.assertRaises(m.EvaluationStopped):
                    await model.generate(
                        "worker", "Review evidence", {"text": "x" * 50000}, WorkerResult
                    )

    async def test_output_limit_cannot_be_overridden_at_transport(self):
        m = self.module()
        transport = m.ModelOnlyTransport(
            httpx.MockTransport(lambda r: self.fail("Output cap bypassed"))
        )
        with self.assertRaises(m.EvaluationStopped):
            await transport.handle_async_request(
                httpx.Request(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    json={"model": m.MODEL, "max_tokens": 100000},
                )
            )

    async def test_private_database_and_source_write_are_blocked(self):
        self.module()
        import sqlite3
        from backend.evaluate_research import evaluation_guard

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            sqlite3.connect(source).close()
            with evaluation_guard(source, root / "case.sqlite3"):
                for path in (source, root / "private.sqlite3"):
                    with self.assertRaises(RuntimeError):
                        sqlite3.connect(path)
                with closing(
                    sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
                ) as db:
                    with self.assertRaises(sqlite3.OperationalError):
                        db.execute("CREATE TABLE forbidden (id INT)")

    async def test_worker_cannot_cite_excluded_passages(self):
        self.module()
        from backend.research.eval_adapters import validate_worker_scope
        from backend.research.models import (
            EvidencePacket,
            Hypothesis,
            WorkerResult,
            Finding,
        )

        packet = EvidencePacket(
            task_id="t",
            goal="g",
            hypothesis=Hypothesis(id="h", subject="s", statement="claim", priority=0),
            excluded_span_ids=["excluded"],
        )
        result = WorkerResult(
            findings=[
                Finding(hypothesis_id="h", statement="invented", span_ids=["excluded"])
            ]
        )
        with self.assertRaises(ValueError):
            validate_worker_scope(result, packet)


if __name__ == "__main__":
    unittest.main()
