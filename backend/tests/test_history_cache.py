"""Offline persistence/cost regressions. Never launch Claude or remote providers."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import mcp.types as mt
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult

from backend import main
from backend.investigations import Investigation
from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
from backend.mcp_servers.osint_mcp import mcp as osint
from backend.scratchpad import InvestigationScratchpad
from backend.source_cache import CacheBlocked, SourceCache, cacheable, request_key
from backend.storage import RunStore
from backend.tool_outputs import normalize_output, source_cache_info


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_preserves_evidence_answers_cost_and_request_ids(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ARC_DATABASE_PATH": str(Path(directory) / "history.sqlite3")}):
            async with main.lifespan(main.app):
                run = Investigation("Saved professional", budget=1.5, store=main.store)
                run.scratchpad = InvestigationScratchpad("saved", run.emit)
                main.runs["saved"] = run
                pad = run.scratchpad
                pad.emit({"type": "investigation.started", "target": run.target})
                await pad.record_tool_start("t1", "mcp__osint__search_identity_exa", {"query": "person"})
                await pad.record_tool_result("t1", "mcp__osint__search_identity_exa", {"title": "Source", "url": "https://example.com"})
                waiting = asyncio.create_task(pad.request_human_input("q", "Which one?", [{"label": "First"}]))
                await asyncio.sleep(0)
                request_id = run.events[-1]["request_id"]
                pad.resolve_human_input(request_id, {"selected": ["First"], "text": ""})
                await waiting
                pad.emit({"type": "agent.usage", "cost_usd": 0.2})
                pad.emit({"type": "investigation.complete"})
                run.chat_session_id = "sdk-saved"
                run.chat_cost = 0.05
                run.chat_requests["reply"] = {"message": "Question", "after": len(run.events)}
                pad.emit({"type": "conversation.started"})
                pad.emit({"type": "agent.usage", "cost_usd": 0.05})
                pad.emit({"type": "conversation.complete"})
                run.finish()
                expected = json.loads(json.dumps(run.events))
                evidence = pad.raw_evidence
            self.assertFalse(main.runs)
            async with main.lifespan(main.app), httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://offline") as client:
                with patch("backend.main.run_workspace_research", side_effect=AssertionError("No paid research")), patch("backend.conversation.ClaudeSDKClient", side_effect=AssertionError("No paid chat")):
                    history = (await client.get("/api/investigations")).json()["investigations"]
                    self.assertEqual(history[0]["session_id"], "saved")
                    self.assertFalse(history[0]["active"])
                    replay = await client.get("/api/investigate/stream/saved")
                    events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]
                    self.assertEqual(events[:-1], expected)
                    self.assertEqual(events[-1]["type"], "stream.end")
                    restored = main.get_run("saved")
                    self.assertEqual(restored.scratchpad.raw_evidence, evidence)
                    self.assertEqual(restored.scratchpad.human_decisions[0]["answer"]["selected"], ["First"])
                    self.assertLess(restored.scratchpad.remaining_evidence_bytes, 500_000)
                    self.assertEqual((restored.chat_session_id, restored.chat_cost, restored.budget), ("sdk-saved", 0.05, 1.5))
                    duplicate = await client.post("/api/investigate/saved/messages", json={"message": "Question", "request_id": "reply"})
                    self.assertEqual(duplicate.status_code, 202)
                    self.assertEqual((await client.post("/api/investigations", json={"target": "Saved professional", "request_id": "saved"})).status_code, 202)
                    self.assertEqual((await client.post("/api/investigations", json={"target": "Other", "request_id": "saved"})).status_code, 409)
                    self.assertEqual((await client.get(f"/api/investigate/saved/evidence/{evidence[0]['evidence_id']}")).json(), evidence[0])
                    tail = await client.get(f"/api/investigate/stream/saved?after={len(expected)}")
                    self.assertNotIn("investigation.started", tail.text)
                    self.assertIn("stream.end", tail.text)

    async def test_crash_does_not_resume_work_or_reopen_human_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = RunStore(path)
            for sid, phase in (("harvest", "investigation"), ("chat", "conversation")):
                run = Investigation("Interrupted", store=store)
                run.scratchpad = InvestigationScratchpad(sid, run.emit)
                run.scratchpad.emit({"type": f"{phase}.started"})
                run.scratchpad.emit({"type": "human.interrupt", "request_id": "old"})
            store.close()  # abrupt shutdown: no terminal event/metadata update
            with patch.dict(os.environ, {"ARC_DATABASE_PATH": str(path)}):
                async with main.lifespan(main.app):
                    for sid in ("harvest", "chat"):
                        run = main.get_run(sid)
                        self.assertTrue(run.done and run.chat_blocked)
                        self.assertIsNone(run.task)
                        self.assertTrue(run.events[-1]["type"].endswith(".failed"))
                        self.assertFalse(run.scratchpad._pending_input)
                async with main.lifespan(main.app):
                    self.assertEqual(len(main.get_run("chat").events), 3)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_osint_proxy_reuses_success_and_keeps_schema(self):
        # Use ARC's actual MCP tool/server; only the HTTP transport is replaced.
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"results": {"web": [
                {"title": "Person", "url": "https://example.com", "snippets": ["Public source"]}
            ]}})
        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            cache = SourceCache(path)
            proxy = create_proxy(osint)
            proxy.add_middleware(SourceCacheMiddleware(cache, "offline", {"search_web_you"}))
            with patch("httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)), patch.dict(os.environ, {"YDC_API_KEY": "offline", "ARC_RESEARCH_OFFLINE": "0"}):
                async with Client(proxy) as client:
                    self.assertIn("search_web_you", [tool.name for tool in await client.list_tools()])
                    first = await client.call_tool("search_web_you", {"query": "person"})
                    second = await client.call_tool("search_web_you", {"query": " person "})
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(first.structured_content, second.structured_content)
                    payload = {"content": [item.model_dump(mode="json") for item in second.content], "structuredContent": second.structured_content}
                    info = source_cache_info(payload)
                    self.assertTrue(info["hit"])
                    pad = InvestigationScratchpad("reused")
                    await pad.record_tool_result("tool", "mcp__osint__search_web_you", payload)
                    self.assertEqual(pad.raw_evidence[0]["observed_at"], info["observed_at"])
                    self.assertEqual(pad.raw_evidence[0]["data"], normalize_output(payload))
            cache.close()
            reopened = SourceCache(path)
            _, hit = reopened.claim("offline", "search_web_you", {"query": "person"})
            self.assertTrue(hit["hit"])
            reopened.close()

    async def test_concurrency_refresh_failures_and_key_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            one, two = SourceCache(path), SourceCache(path)
            middleware = SourceCacheMiddleware(one, "account-one", {"scrape_linkedin_posts"})
            context = MiddlewareContext(message=mt.CallToolRequestParams(name="scrape_linkedin_posts", arguments={"linkedin_url": "https://www.linkedin.com/in/person/", "max_posts": 5}))
            started, release = asyncio.Event(), asyncio.Event()
            calls = 0
            async def provider(_):
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()
                return ToolResult(structured_content={"result": [{"text": "Post"}]})
            task = asyncio.create_task(middleware.on_call_tool(context, provider))
            await started.wait()
            with self.assertRaises(CacheBlocked):
                two.claim("account-one", "scrape_linkedin_posts", {"linkedin_url": "https://linkedin.com/in/person", "max_posts": 5})
            release.set()
            first = await task
            hit = await middleware.on_call_tool(context, provider)
            self.assertEqual(calls, 1)
            self.assertEqual(first.meta["arc_source_cache"]["observed_at"], hit.meta["arc_source_cache"]["observed_at"])
            self.assertTrue(hit.meta["arc_source_cache"]["hit"])
            fresh = SourceCacheMiddleware(two, "account-one", {"scrape_linkedin_posts"}, "explicit-fresh-run")
            await fresh.on_call_tool(context, provider)
            await fresh.on_call_tool(context, provider)
            self.assertEqual(calls, 2)
            args = context.message.arguments
            self.assertNotEqual(request_key("account-one", "scrape_linkedin_posts", args), request_key("account-two", "scrape_linkedin_posts", args))
            self.assertNotEqual(request_key("account-one", "scrape_linkedin_posts", args), request_key("account-one", "scrape_linkedin_posts", {**args, "max_posts": 1}))
            context.message.arguments = {**args, "max_posts": 2}
            failed = AsyncMock(side_effect=RuntimeError("Provider outcome unknown"))
            with self.assertRaises(RuntimeError): await middleware.on_call_tool(context, failed)
            with self.assertRaises(ToolError): await middleware.on_call_tool(context, failed)
            self.assertEqual(failed.await_count, 1)
            self.assertFalse(cacheable({"structuredContent": {"errors": [{"status": 429}]}}))
            self.assertFalse(cacheable({"isError": True}))
            self.assertFalse(cacheable({"content": [{"type": "text", "text": "Error: result (100 characters) exceeds maximum allowed tokens. Output has been saved to /tmp/mcp-file.txt.\n"}]}))
            one.close(); two.close()


if __name__ == "__main__":
    unittest.main()
