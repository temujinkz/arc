import asyncio
import unittest
from unittest.mock import patch
from backend.tests import test_research_blackboard as fixtures
from backend.research.models import (
    RunPolicy,
    TaskProposal,
    WorkerResult,
    Finding,
    ReviewedEntity,
)
from backend.research.critic import CriticDecision, FindingReview, apply_review
from backend.research.scheduler import ResearchGoal, run_research
from backend.research.workers import ReplayWorker


class RegressionTests(unittest.TestCase):
    setUp = fixtures.BoardTests.setUp
    tearDown = fixtures.BoardTests.tearDown
    proposal = fixtures.BoardTests.proposal

    def running(self):
        return self.board.start_task(
            self.board.admit_task("r", self.proposal(), RunPolicy()).id, RunPolicy()
        )

    def test_cancelled_usage_can_be_reconciled_without_accepting_findings(self):
        task = self.running()
        self.board.stop("r", "CANCELLED")
        self.assertIsNone(self.board.task(task.id).cost_usd)
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.board.record_result(
                    task.id, WorkerResult(cost_usd=0.25), task.version
                )
        self.assertEqual(self.board.task(task.id).cost_usd, 0.25)
        self.assertEqual(self.board.snapshot("r").findings, [])
        terminal = [e for e in self.board.events("r") if e["type"] == "task.finished"]
        self.assertEqual(terminal[-1]["status"], "CANCELLED")

    def test_restart_consumes_children_and_reviews_completed_wave(self):
        task = self.running()
        self.board.record_result(
            task.id,
            WorkerResult(
                children=[
                    TaskProposal(
                        hypothesis_id=self.h.id, goal="Saved child", role="artifact"
                    )
                ]
            ),
            task.version,
        )
        calls = []
        reviews = []

        def resolve(t, p):
            calls.append(t.goal)
            return WorkerResult()

        async def critic(report, reader):
            reviews.append(report.wave)
            return CriticDecision()

        out = asyncio.run(
            run_research(
                ResearchGoal(run_id="r", target="Investigate builder"),
                self.board,
                ReplayWorker(resolve),
                RunPolicy(),
                critic=critic,
            )
        )
        self.assertEqual(calls, ["Saved child"])
        self.assertEqual(reviews, [1])
        self.assertIsNotNone(out.stop_reason)

    def test_critic_failure_is_checkpointed_and_not_retried(self):
        async def critic(report, reader):
            raise ValueError("invalid review")

        out = asyncio.run(
            run_research(
                ResearchGoal(
                    run_id="r",
                    target="Investigate builder",
                    initial_tasks=[self.proposal()],
                ),
                self.board,
                ReplayWorker(lambda t, p: WorkerResult()),
                RunPolicy(),
                critic=critic,
            )
        )
        self.assertEqual(out.stop_reason, "NEEDS_HUMAN")
        again = asyncio.run(
            run_research(
                ResearchGoal(run_id="r", target="Investigate builder"),
                self.board,
                ReplayWorker(lambda t, p: WorkerResult()),
                RunPolicy(),
                critic=critic,
            )
        )
        self.assertEqual(again.stop_reason, "NEEDS_HUMAN")

    def test_known_self_origin_cannot_be_upgraded_by_citing_only_mirror(self):
        self.board.add_source(
            "r",
            "mirror",
            {"quote": "Claims shipped a VS Code extension."},
            source_kind="THIRD_PARTY",
            origin_group="subject",
        )
        span = self.reader.index_source("mirror")[0]
        task = self.running()
        self.board.record_result(
            task.id,
            WorkerResult(
                findings=[
                    Finding(
                        hypothesis_id=self.h.id,
                        statement="Mirror repeats claim",
                        span_ids=[span.id],
                        relation="supports",
                    )
                ]
            ),
            task.version,
        )
        f = self.board.snapshot("r").findings[0]
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id, accepted=True, reason="Source repeats subject"
                    )
                ]
            ),
        )
        self.assertEqual(
            self.board.snapshot("r").hypotheses[0].support, "SELF_REPORTED_ONLY"
        )

    def test_retract_entity_finding_without_rolling_back_review(self):
        task = self.running()
        self.board.record_result(
            task.id,
            WorkerResult(
                findings=[
                    Finding(
                        hypothesis_id=self.h.id,
                        statement=s,
                        span_ids=[self.spans[0].id],
                        relation="supports",
                    )
                    for s in ["Claim one", "Claim two"]
                ]
            ),
            task.version,
        )
        findings = self.board.snapshot("r").findings
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id, accepted=True, reason="Reviewed fixture"
                    )
                    for f in findings
                ],
                entities=[
                    ReviewedEntity(
                        id="org",
                        kind="Organization",
                        title="Example",
                        url="https://example.org",
                        finding_ids=[findings[0].id],
                        review_note="Original interpretation",
                    )
                ],
            ),
        )
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=findings[0].id,
                        accepted=False,
                        reason="Interpretation retracted",
                    )
                ]
            ),
        )
        self.assertFalse(self.board.snapshot("r").findings[0].reviewed)
        self.assertEqual(self.board.snapshot("r").entities, [])
        self.assertTrue(self.board._list("r", "projection_history"))

    def test_refresh_identity_survives_http_boundary_and_rejects_forgery(self):
        from backend.source_cache import refresh_headers, verified_refresh_id

        headers = refresh_headers("explicit-refresh-2", "test-key")
        self.assertEqual(verified_refresh_id(headers, "test-key"), "explicit-refresh-2")
        with self.assertRaises(ValueError):
            verified_refresh_id(headers, "wrong-key")

    def test_replay_refuses_unreviewed_session_before_reading_sources(self):
        from backend.replay_research import replay

        with patch(
            "backend.replay_research.load_cached_records",
            side_effect=AssertionError("Unexpected source read"),
        ):
            with self.assertRaisesRegex(ValueError, "reviewed session"):
                asyncio.run(
                    replay(self.path, "another-person", self.path.parent / "other")
                )

    def test_sdk_teardown_error_preserves_received_usage(self):
        from claude_agent_sdk import ResultMessage
        from backend.research.workers import ClaudeResearchWorker

        task = self.running()
        packet = self.reader.build_packet(task, 10000)

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                raise RuntimeError("SDK teardown failed")

            async def query(self, prompt):
                pass

            async def receive_response(self):
                yield ResultMessage(
                    subtype="success",
                    duration_ms=0,
                    duration_api_ms=0,
                    is_error=False,
                    num_turns=1,
                    session_id="unit",
                    total_cost_usd=0.25,
                    structured_output=WorkerResult().model_dump(),
                )

        with patch("backend.research.workers.ClaudeSDKClient", Client):
            worker = ClaudeResearchWorker(
                RunPolicy(execution_mode="authorized_model", max_input_tokens=10000),
                self.board,
                authorized=True,
                model="unit-only",
            )
            result = asyncio.run(worker.run(task, packet, 1))
        self.assertEqual(result.cost_usd, 0.25)
        self.assertEqual(self.board.task(task.id).cost_usd, 0.25)

    def test_saved_critic_decision_is_applied_without_second_review(self):
        task = self.running()
        before = self.board.snapshot("r")
        self.board.record_result(task.id, WorkerResult(), task.version)
        decision = CriticDecision(reason="Saved completed review", cost_usd=0)
        with self.board.transaction():
            self.board._put(
                "r",
                "wave_checkpoint",
                "1",
                {
                    "wave": 1,
                    "status": "REVIEW_READY",
                    "before": before.model_dump(),
                    "decision": decision.model_dump(),
                },
            )

        async def forbidden(report, reader):
            raise AssertionError("Repeated critic call")

        out = asyncio.run(
            run_research(
                ResearchGoal(run_id="r", target="Investigate builder"),
                self.board,
                ReplayWorker(lambda t, p: WorkerResult()),
                RunPolicy(),
                critic=forbidden,
            )
        )
        self.assertEqual(out.stop_reason, "NO_NEW_EVIDENCE")
        self.assertEqual(
            len([e for e in self.board.events("r") if e["type"] == "critic.reviewed"]),
            1,
        )

    def test_proxy_sends_signed_refresh_on_osint_http_transport(self):
        from backend.mcp_servers.cached_proxy import build_proxy
        from backend.source_cache import verified_refresh_id
        from unittest.mock import MagicMock

        proxy = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {"ARC_CACHE_REFRESH_ID": "fresh-2", "APIFY_API_TOKEN": "unit-secret"},
            ),
            patch(
                "backend.mcp_servers.cached_proxy.database_path",
                return_value=self.path.parent / "proxy.sqlite3",
            ),
            patch(
                "backend.mcp_servers.cached_proxy.upstream_registry",
                return_value=(
                    {"osint": {"type": "http", "url": "http://127.0.0.1:8001/mcp"}},
                    ["mcp__osint__scrape_linkedin_profile"],
                ),
            ),
            patch(
                "backend.mcp_servers.cached_proxy.create_proxy", return_value=proxy
            ) as factory,
        ):
            build_proxy("osint")
            headers = factory.call_args.args[0]["mcpServers"]["osint"]["headers"]
            self.assertEqual(verified_refresh_id(headers, "unit-secret"), "fresh-2")
            proxy.add_middleware.call_args.args[0].cache.close()

    def test_known_attempt_usage_is_not_downgraded_by_unknown_result(self):
        task = self.running()
        self.board.record_usage(task.id, task.version, 0.25)
        self.board.record_result(
            task.id, WorkerResult(status="blocked", cost_usd=None), task.version
        )
        self.assertEqual(self.board.task(task.id).cost_usd, 0.25)

    def test_you_only_proxy_refresh_does_not_require_apify_key(self):
        from backend.mcp_servers.cached_proxy import build_proxy
        from unittest.mock import MagicMock

        proxy = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {"ARC_CACHE_REFRESH_ID": "fresh-you", "APIFY_API_TOKEN": ""},
            ),
            patch(
                "backend.mcp_servers.cached_proxy.database_path",
                return_value=self.path.parent / "you-proxy.sqlite3",
            ),
            patch(
                "backend.mcp_servers.cached_proxy.upstream_registry",
                return_value=(
                    {"osint": {"type": "http", "url": "http://127.0.0.1:8001/mcp"}},
                    ["mcp__osint__search_web_you"],
                ),
            ),
            patch("backend.mcp_servers.cached_proxy.create_proxy", return_value=proxy),
        ):
            build_proxy("osint")
            proxy.add_middleware.call_args.args[0].cache.close()
