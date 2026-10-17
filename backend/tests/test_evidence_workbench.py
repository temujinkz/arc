"""Offline workbench, integrity and privacy regressions. No live model sessions."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from claude_agent_sdk import ResultMessage
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy

from backend import main
from backend.conversation import run_followup
from backend.domain_sources import inspect_domain_archive, inspect_domain_dns
from backend.evidence_ledger import build_ledger, source_observations
from backend.investigations import Investigation
from backend.mcp_servers.osint_mcp import mcp
from backend.scratchpad import InvestigationScratchpad
from backend.source_workbench import execute_source, request_signature
from backend.storage import RunStore


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def test_withdrawn_assessment_disappears_from_ledger_and_exports(self):
        from backend.evidence_ledger import export_report, report_markdown
        run = Investigation("Public professional")
        run.scratchpad = InvestigationScratchpad("withdrawn", run.emit)
        run.scratchpad.emit({"type": "graph.final_commit", "graph": {
            "summary": "WRONG IDENTITY ASSERTION", "nodes": [], "edges": []}})
        self.assertTrue(build_ledger(run)["assessment"]["present"])
        run.scratchpad.emit({"type": "graph.withdrawn", "reason": "Identity mismatch"})
        assessment = export_report(run)["assessment"]
        self.assertFalse(assessment["present"])
        self.assertEqual(assessment["withdrawal_reason"], "Identity mismatch")
        self.assertNotIn("WRONG IDENTITY ASSERTION", report_markdown(run))
        self.assertEqual(run.events[0]["graph"]["summary"], "WRONG IDENTITY ASSERTION")
        run.scratchpad.emit({"type": "graph.final_commit", "graph": {
            "summary": "Corrected later", "nodes": [], "edges": []}})
        self.assertTrue(build_ledger(run)["assessment"]["present"])

    async def test_case_lookup_idempotency_provenance_and_exports_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ARC_DATABASE_PATH": str(Path(directory) / "history.sqlite3")}, clear=True):
            result = {"structuredContent": {"source_url": "https://example.org/project", "results": [{"href": "https://github.com/public/project", "title": "<script>untrusted source text</script>"}]}}
            with patch("backend.source_workbench.execute_source", AsyncMock(return_value=result)) as collect, patch("backend.main.run_workspace_research", side_effect=AssertionError("No paid workflow")), patch("backend.private_vault.open_vault", side_effect=AssertionError("No private vault access")):
                async with main.lifespan(main.app), httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://offline") as client:
                    created = await client.post("/api/cases", json={"target": "Public project", "request_id": "case"})
                    self.assertEqual(created.status_code, 201)
                    self.assertEqual(collect.await_count, 0)
                    body = {"tool": "search_public_web", "arguments": {"query": "public project"}, "request_id": "lookup"}
                    self.assertEqual((await client.post("/api/investigate/case/sources", json=body)).status_code, 202)
                    run = main.get_run("case")
                    await run.task
                    self.assertEqual((await client.post("/api/investigate/case/sources", json=body)).json()["state"], "complete")
                    self.assertEqual(collect.await_count, 1)
                    self.assertEqual((await client.post("/api/investigate/case/sources", json={**body, "arguments": {"query": "different"}})).status_code, 409)
                    self.assertEqual((await client.post("/api/investigate/case/sources", json={**body, "request_id": "paid", "tool": "scrape_linkedin_profile"})).status_code, 422)
                    ledger = (await client.get("/api/investigate/case/ledger")).json()
                    record = ledger["records"][0]
                    self.assertTrue(record["integrity_matches"])
                    self.assertTrue(record["digest_recorded"])
                    self.assertEqual(record["links"][0]["field_paths"], ['$["source_url"]'])
                    node = {"node_id": "p", "label": "Person", "title": "Public project owner", "summary": "Unverified", "evidence_ids": [record["evidence_id"]], "evidence_state": "unverified", "metadata": {}}
                    graph = {"summary": "Recorded assessment", "detected_archetype": "Unknown", "signal_to_hype_score": None, "nodes": [node], "edges": []}
                    run.scratchpad.emit({"type": "graph.final_commit", "graph": graph})
                    run.scratchpad.emit({"type": "peer.private-fixture", "text": "SECRET-NOTE"})
                    changed = {"structuredContent": {"source_url": "https://example.org/new"}}
                    collect.return_value = changed
                    self.assertEqual((await client.post("/api/investigate/case/sources", json={**body, "request_id": "refresh", "fresh_sources": True})).status_code, 202)
                    await run.task
                    ledger = (await client.get("/api/investigate/case/ledger")).json()
                    self.assertEqual(ledger["summary"]["added_after_assessment"], 1)
                    self.assertTrue(ledger["records"][1]["comparison"]["changed"])
                    self.assertEqual(ledger["assessment"]["graph"], graph)
                    report = await client.get("/api/investigate/case/exports/report.json")
                    markdown = await client.get("/api/investigate/case/exports/report.md")
                    self.assertNotIn("SECRET-NOTE", report.text + markdown.text)
                    self.assertNotIn("<script>", markdown.text)
                    self.assertEqual(report.json()["schema"], "arc.evidence-report.v1")
                    self.assertEqual(collect.await_count, 2)
                async with main.lifespan(main.app):
                    restored = main.get_run("case")
                    self.assertEqual(restored.collection_mode, "sources")
                    self.assertEqual(restored.source_requests["refresh"]["state"], "complete")
                    self.assertEqual(len(build_ledger(restored)["records"]), 2)
                    self.assertEqual(collect.await_count, 2)

    async def test_interrupted_source_does_not_resume_or_duplicate_on_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = RunStore(path)
            run = Investigation("Public project", store=store, collection_mode="sources")
            run.scratchpad = InvestigationScratchpad("interrupted", run.emit)
            args = {"query": "project", "backend": "auto"}
            run.source_requests["req"] = {"signature": request_signature("search_public_web", args, False), "state": "pending", "after": 0}
            run.scratchpad.emit({"type": "source.started"})
            await run.scratchpad.record_tool_start("pending", "mcp__osint__search_public_web", args)
            store.close()
            with patch.dict(os.environ, {"ARC_DATABASE_PATH": str(path)}), patch("backend.source_workbench.execute_source", side_effect=AssertionError("No restart")):
                async with main.lifespan(main.app), httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://offline") as client:
                    run = main.get_run("interrupted")
                    self.assertTrue(run.done)
                    self.assertEqual(run.source_requests["req"]["state"], "interrupted")
                    self.assertEqual(build_ledger(run)["summary"]["unfinished_attempts"], 1)
                    duplicate = await client.post("/api/investigate/interrupted/sources", json={"tool": "search_public_web", "arguments": args, "request_id": "req"})
                    self.assertEqual(duplicate.json()["state"], "interrupted")

    async def test_free_workbench_uses_real_mcp_cache_without_paid_tools(self):
        data = ({"Status": 0, "Answer": [{"name": "example.org.", "type": 1, "data": "192.0.2.1"}]}, "https://dns.google/resolve?name=example.org&type=A")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ARC_DATABASE_PATH": str(Path(directory) / "cache.sqlite3")}, clear=True), patch("backend.source_workbench.create_proxy", side_effect=lambda *_a, **_kw: create_proxy(Client(mcp), name="Real ARC offline source")), patch("backend.domain_sources.public_json", AsyncMock(return_value=data)) as provider:
            first = await execute_source("inspect_domain_dns", {"domain": "example.org"})
            second = await execute_source("inspect_domain_dns", {"domain": "EXAMPLE.ORG.", "record_type": "A"})
            self.assertEqual(provider.await_count, 1)
            self.assertEqual(first["structuredContent"], second["structuredContent"])
            observations = source_observations("mcp__osint__inspect_domain_dns", first["structuredContent"])
            self.assertTrue(any(item["field_path"] == '$["answers"][0]["data"]' and item["value"] == "192.0.2.1" for item in observations))

    async def test_integrity_mismatches_are_excluded_from_model_evidence(self):
        run = Investigation("Public project")
        run.scratchpad = InvestigationScratchpad("integrity", run.emit)
        await run.scratchpad.record_tool_result("one", "mcp__osint__search_public_web", {"url": "https://example.org"})
        run.scratchpad.raw_evidence[0]["data"]["url"] = "https://tampered.example"
        self.assertFalse(build_ledger(run)["records"][0]["integrity_matches"])
        self.assertEqual(run.scratchpad.architect_evidence(), [])
        del run.scratchpad.raw_evidence[0]["content_sha256"]
        self.assertIsNone(build_ledger(run)["records"][0]["integrity_matches"])

    async def test_archive_allows_slow_index_response_and_reports_timeout_without_retry(self):
        rows = [["timestamp", "original", "mimetype", "statuscode", "digest"],
                ["20200102030405", "https://example.org/about", "text/html", "200", "digest"]]
        calls = []
        def slow_index(request):
            calls.append(request)
            if request.extensions["timeout"]["read"] <= 5:
                raise httpx.ReadTimeout("Index needs more than the generic five seconds", request=request)
            return httpx.Response(200, json=rows, request=request)
        real_client = httpx.AsyncClient
        with patch("backend.domain_sources.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(slow_index), **kw)):
            result = await inspect_domain_archive("example.org", 1)
        self.assertEqual(result["pages"][0]["url"], "https://example.org/about")
        self.assertEqual(len(calls), 1)
        calls.clear()
        def outage(request):
            calls.append(request)
            raise httpx.ReadTimeout("No response", request=request)
        with patch("backend.domain_sources.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(outage), **kw)):
            with self.assertRaisesRegex(ToolError, "timed out"):
                await inspect_domain_archive("example.org", 1)
        self.assertEqual(len(calls), 1)

    async def test_dns_and_archive_errors_are_not_negative_identity_evidence(self):
        with patch("backend.domain_sources.public_json", AsyncMock(return_value=({"Status": 2}, "https://dns.google/resolve"))):
            with self.assertRaises(ToolError):
                await inspect_domain_dns("example.org")
        rows = [["timestamp", "original", "mimetype", "statuscode", "digest"], ["20200102030405", "https://example.org/about", "text/html", "200", "archive-digest"]]
        with patch("backend.domain_sources.public_json", AsyncMock(return_value=(rows, "https://web.archive.org/cdx/search/cdx"))):
            report = await inspect_domain_archive("example.org", 1)
            self.assertEqual(report["pages"][0]["source_url"], "https://web.archive.org/web/20200102030405/https://example.org/about")
            self.assertFalse(report["identity_verified"])

    async def test_resumed_chat_receives_new_evidence_without_resetting_cost(self):
        run = Investigation("Public project", chat_session_id="existing", chat_cost=0.05)
        run.scratchpad = InvestigationScratchpad("chat", run.emit)
        await run.scratchpad.record_tool_result("one", "mcp__osint__search_public_web", {"url": "https://example.org/one"})
        run.chat_evidence_ids = [run.scratchpad.raw_evidence[0]["evidence_id"]]
        await run.scratchpad.record_tool_result("two", "mcp__osint__inspect_domain_dns", {"url": "https://dns.google/resolve"})
        result = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                               session_id="existing", total_cost_usd=0.08, usage={}, result="Captured sources only")
        async def responses():
            yield result
        fake = AsyncMock()
        fake.receive_response = responses
        with patch("backend.conversation.ClaudeSDKClient") as sdk:
            sdk.return_value.__aenter__.return_value = fake
            await run_followup(run, "What changed?", "reply", 1.0)
        payload = json.loads(fake.query.call_args.args[0])
        self.assertEqual(len(payload["additional_captured_evidence"]), 1)
        self.assertEqual(payload["additional_captured_evidence"][0]["tool_use_id"], "two")
        usage = [event for event in run.events if event["type"] == "agent.usage"]
        self.assertAlmostEqual(usage[0]["cost_usd"], 0.03)
        self.assertEqual(len(run.chat_evidence_ids), 2)
