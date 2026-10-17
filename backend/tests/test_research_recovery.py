import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.tests import test_research_blackboard as fixtures
from backend.research.recovery import (
    ToolFailure,
    choose_recovery,
    recover_cached,
    classify_failure,
)
from backend.research.models import RunPolicy
from backend.source_cache import SourceCache, CacheBlocked


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.BoardTests.setUp
    tearDown = fixtures.BoardTests.tearDown
    proposal = fixtures.BoardTests.proposal

    def test_cached_alternative_is_validated_and_recorded(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        failure = ToolFailure(
            operation_id="op",
            kind="INACCESSIBLE",
            alternate_span_ids=[self.spans[0].id],
        )
        action = choose_recovery(failure, task, self.board.snapshot("r"), "offline")
        spans = recover_cached(self.board, task, failure, action)
        self.assertEqual(spans[0].id, self.spans[0].id)
        events = [e for e in self.board.events("r") if e["type"] == "tool.recovery"]
        self.assertEqual([e["state"] for e in events], ["planned", "executed"])
        self.assertEqual(recover_cached(self.board, task, failure, action), spans)
        self.assertEqual(len(self.board.events("r")), len(events) + 3)

    def test_unknown_charge_does_not_restart_and_miss_is_terminal(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        unknown = ToolFailure(
            operation_id="op", kind="UNKNOWN_OUTCOME", provider_run_id="remote-1"
        )
        self.assertEqual(
            choose_recovery(
                unknown, task, self.board.snapshot("r"), "authorized_model"
            ).kind,
            "RECONCILE_REQUIRED",
        )
        miss = ToolFailure(operation_id="miss", kind="EMPTY")
        self.assertEqual(
            choose_recovery(miss, task, self.board.snapshot("r"), "offline").kind,
            "MISSING_CACHED_SOURCE",
        )
        self.assertEqual(
            classify_failure("op", status=429, retry_after="60").retry_after, "60"
        )

    def test_operation_id_survives_restart_and_prevents_second_start(self):
        path = Path(self.tmp.name) / "cache.sqlite3"
        cache = SourceCache(path)
        key = cache.begin_operation("apify", "actor", {"urls": ["https://example.com"]})
        cache.record_operation(key, provider_run_id="run-123", status="RUNNING")
        cache.close()
        cache = SourceCache(path)
        self.assertEqual(cache.operation(key)["provider_run_id"], "run-123")
        with self.assertRaises(CacheBlocked):
            cache.begin_operation("apify", "actor", {"urls": ["https://example.com"]})
        cache.close()

    def test_offline_proxy_miss_never_invokes_upstream(self):
        from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
        from fastmcp.exceptions import ToolError

        cache = SourceCache(Path(self.tmp.name) / "proxy.sqlite3")
        proxy = SourceCacheMiddleware(cache, "test", {"search"}, offline=True)
        upstream = AsyncMock()
        context = SimpleNamespace(message=SimpleNamespace(name="search", arguments={}))
        with self.assertRaisesRegex(ToolError, "MISSING_CACHED_SOURCE"):
            asyncio.run(proxy.on_call_tool(context, upstream))
        upstream.assert_not_called()
        cache.close()

    def test_apify_wait_timeout_retains_run_id_without_second_start(self):
        from unittest.mock import patch, MagicMock
        from backend.mcp_servers.osint_mcp import run_apify
        from fastmcp.exceptions import ToolError

        path = Path(self.tmp.name) / "apify.sqlite3"
        client = MagicMock()
        client.actor.return_value.start = AsyncMock(
            return_value=SimpleNamespace(id="accepted-123")
        )
        client.run.return_value.wait_for_finish = AsyncMock(side_effect=TimeoutError)
        client.http_client.aclose = AsyncMock()
        with (
            patch.dict(
                "os.environ",
                {
                    "APIFY_API_TOKEN": "unit-test",
                    "APIFY_MAX_TOTAL_CHARGE_USD": ".01",
                    "ARC_DATABASE_PATH": str(path),
                    "ARC_RESEARCH_OFFLINE": "0",
                },
            ),
            patch(
                "backend.mcp_servers.osint_mcp.ApifyClientAsync", return_value=client
            ),
        ):
            with self.assertRaises(ToolError):
                asyncio.run(run_apify("actor", {"urls": ["https://example.com"]}, 1))
            with self.assertRaises(ToolError):
                asyncio.run(run_apify("actor", {"urls": ["https://example.com"]}, 1))
        client.actor.return_value.start.assert_awaited_once()
        cache = SourceCache(path)
        row = cache.db.execute(
            "SELECT provider_run_id,status FROM provider_operations"
        ).fetchone()
        self.assertEqual(tuple(row), ("accepted-123", "RUNNING"))
        cache.close()
