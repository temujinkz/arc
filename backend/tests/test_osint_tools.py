"""Adapter and export regressions; no provider or model calls."""

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import mcp.types as mt
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult

from backend import main
from backend.agent import upstream_registry
from backend.investigations import Investigation
from backend.mcp_servers.osint_mcp import mcp
from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
from backend.source_cache import SourceCache
from backend.osint_tools import (
    google_dork_links, inspect_domain_theharvester, run_report,
    search_username_maigret, search_username_sherlock,
)


class OSINTTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_environment_has_no_credentials_and_arguments_are_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fixture"
            script.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
assert not any(key.endswith(("KEY", "TOKEN", "PASSWORD")) for key in os.environ)
pathlib.Path("result.json").write_text(json.dumps({"args": sys.argv[1:], "isolated": pathlib.Path.cwd() == pathlib.Path(os.environ["HOME"])}))
''')
            script.chmod(0o700)
            with patch.dict(os.environ, {"ARC_SHERLOCK_BIN": str(script), "ANTHROPIC_API_KEY": "fixture-secret"}):
                report = json.loads(await run_report("sherlock", ["$(touch should-not-exist)", "--site", "GitHub"], "result.json"))
                self.assertEqual(report["args"][0], "$(touch should-not-exist)")
                self.assertTrue(report["isolated"])

    async def test_username_reports_keep_only_claimed_professional_leads(self):
        sherlock = "username,name,url_user,exists\nuser,GitHub,https://github.com/user,Claimed\nuser,GitLab,https://gitlab.com/user,Available\n"
        with patch("backend.osint_tools.run_report", AsyncMock(return_value=sherlock)):
            result = await search_username_sherlock("user", ["GitHub", "GitLab"])
            self.assertEqual(len(result["matches"]), 1)
            self.assertFalse(result["matches"][0]["identity_verified"])
        maigret = {"NPM": {"url_user": "https://www.npmjs.com/~user", "status": {"status": "Claimed"}},
                   "GitHub": {"url_user": "https://github.com/user", "status": {"status": "Unknown"}}}
        with patch("backend.osint_tools.run_report", AsyncMock(return_value=json.dumps(maigret))) as runner:
            result = await search_username_maigret("user", ["npm", "GitHub"])
            self.assertEqual([m["site"] for m in result["matches"]], ["npm"])
            self.assertIn("NPM", runner.call_args.args[1])
        for value in ("--help", "../user", "user;date", "user{?}"):
            with self.assertRaises(ToolError):
                await search_username_sherlock(value, ["GitHub"])

    async def test_harvester_outages_are_not_absence_evidence(self):
        summary = {"type": "summary", "target": "example.com", "evidence_status": "complete",
                   "source_executions": [{"source": "crtsh", "status": "completed"}]}
        records = [summary, {"type": "hostname", "value": "www.example.com", "sources": ["crtsh"]},
                   {"type": "hostname", "value": "unrelated.com"}, {"type": "email", "value": "private@example.com"}]
        report = "\n".join(json.dumps(item) for item in records)
        with patch("backend.osint_tools.run_report", AsyncMock(return_value=report)) as runner:
            result = await inspect_domain_theharvester("example.com", 1)
            self.assertEqual([h["value"] for h in result["hostnames"]], ["www.example.com"])
            self.assertEqual(runner.call_args.args[1][3], "crtsh")
        summary["evidence_status"] = "failed"
        with patch("backend.osint_tools.run_report", AsyncMock(return_value=json.dumps(summary))):
            with self.assertRaises(ToolError):
                await inspect_domain_theharvester("example.com", 1)

    async def test_actual_mcp_free_search_and_crossref_error_handling(self):
        with patch("backend.mcp_servers.osint_mcp.DDGS") as search:
            search.return_value.text.return_value = [{"title": "Public source", "href": "https://example.com", "body": "A lead"}]
            async with Client(mcp) as client:
                output = await client.call_tool("search_public_web", {"query": "site:example.com project", "backend": "google"})
                result = output.data
                self.assertEqual(result["backend"], "google")
                search.return_value.text.assert_called_once_with("site:example.com project", backend="google")
        transport = httpx.MockTransport(lambda request: httpx.Response(429, request=request))
        real_client = httpx.AsyncClient
        with patch("backend.mcp_servers.osint_mcp.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)):
            async with Client(mcp) as client:
                with self.assertRaises(Exception):
                    await client.call_tool("search_publications_crossref", {"query": "public paper"})

    async def test_robin_has_no_extra_model_tools_or_credentials(self):
        with patch.dict(os.environ, {"ARC_ROBIN_ENABLED": "1", "ANTHROPIC_API_KEY": "fixture-secret"}, clear=True):
            servers, allowed = upstream_registry()
            self.assertEqual([name for name in allowed if name.startswith("mcp__robin__")],
                             ["mcp__robin__robin_search", "mcp__robin__robin_scrape"])
            self.assertNotIn("env", servers["robin"])
            self.assertNotIn("-v", servers["robin"]["args"])

    async def test_robin_setup_and_failed_scrapes_never_become_cached_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory) / "cache.sqlite3")
            middleware = SourceCacheMiddleware(cache, "robin:fixture", {"robin_search", "robin_scrape"})
            cases = [("robin_search", "status: needs_domain\nChoose a domain", False),
                     ("robin_search", "status: engines_unreachable\nOutage", False),
                     ("robin_search", "Unexpected setup instructions", False),
                     ("robin_scrape", "status: ok\n0 of 1 pages read.", False),
                     ("robin_search", "status: no_results\nNo results.", True),
                     ("robin_scrape", "status: ok\n1 of 1 pages read.", True)]
            for index, (name, text, accepted) in enumerate(cases):
                context = MiddlewareContext(message=mt.CallToolRequestParams(name=name, arguments={"query": str(index)}))
                response = ToolResult(content=[mt.TextContent(type="text", text=text)])
                if accepted:
                    await middleware.on_call_tool(context, AsyncMock(return_value=response))
                else:
                    with self.assertRaises(ToolError):
                        await middleware.on_call_tool(context, AsyncMock(return_value=response))
            ready = cache.db.execute("SELECT count(*) FROM source_cache WHERE status='ready'").fetchone()[0]
            self.assertEqual(ready, 2)
            cache.close()

    async def test_maltego_uses_only_committed_graph_and_links_are_not_evidence(self):
        run = Investigation("Public person")
        node = {"node_id": "person", "label": "Person", "title": "=HYPERLINK(1)", "summary": "Public",
                "evidence_ids": ["e1"], "evidence_state": "unverified", "metadata": {}}
        graph = {"summary": "Public", "detected_archetype": "Unknown", "signal_to_hype_score": None,
                 "nodes": [node], "edges": []}
        run.events = [{"type": "graph.proposed", "graph": graph}, {"type": "private.note", "text": "SECRET"}]
        with patch.dict(main.runs, {"fixture-osint": run}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://offline") as client:
                response = await client.get("/api/investigate/fixture-osint/exports/maltego.csv")
                self.assertEqual(response.status_code, 409)
                run.events.append({"type": "graph.final_commit", "graph": graph})
                response = await client.get("/api/investigate/fixture-osint/exports/maltego.csv")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("SECRET", response.text)
                row = list(csv.DictReader(io.StringIO(response.text)))[0]
                self.assertEqual(row["source_title"], "'=HYPERLINK(1)")
                self.assertEqual(row["evidence_ids"], "e1")
                self.assertEqual((await client.get('/api/tools/google-dorks?subject=public%20person')).status_code, 200)
        self.assertFalse(google_dork_links("public person")["executed"])
