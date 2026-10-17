import asyncio
import unittest
from backend.tests import test_research_blackboard as fixtures
from backend.research.models import RunPolicy, TaskProposal, WorkerResult, Finding
from backend.research.workers import ReplayWorker
from backend.research.scheduler import run_research, ResearchGoal
from backend.research.critic import CriticDecision, FindingReview


class SchedulerTests(unittest.TestCase):
    setUp = fixtures.BoardTests.setUp
    tearDown = fixtures.BoardTests.tearDown
    proposal = fixtures.BoardTests.proposal

    def test_critic_local_preflight_rejection_has_zero_known_cost(self):
        from backend.research.critic import CriticInputError
        async def critic(report, reader):
            raise CriticInputError()
        out = asyncio.run(run_research(
            ResearchGoal(run_id="r", target="Investigate builder", initial_tasks=[self.proposal()], remaining_budget=1),
            self.board, ReplayWorker(lambda t, p: WorkerResult(cost_usd=0)),
            RunPolicy(execution_mode="authorized_model"), critic=critic,
        ))
        self.assertEqual(out.stop_reason, "NEEDS_HUMAN")
        self.assertEqual(self.board._get("r", "critic_usage", "1")["cost_usd"], 0)
        event = next(e for e in self.board.events("r") if e["type"] == "critic.failed")
        self.assertIn("No critic call was sent", event["reason"])

    def test_critic_rework_and_stop_on_no_new_work(self):
        calls = []

        def resolve(t, p):
            calls.append((t.wave, t.depth))
            return WorkerResult(
                findings=[
                    Finding(
                        hypothesis_id=self.h.id,
                        statement="Profile claims an extension",
                        span_ids=[self.spans[0].id],
                        relation="supports",
                    )
                ]
            )

        async def critic(report, reader):
            reviews = [
                FindingReview(
                    finding_id=f.id,
                    accepted=True,
                    reason="Human-reviewed fixture: quotation is a self-claim",
                )
                for f in report.after.findings
                if not f.reviewed
            ]
            proposals = (
                [
                    TaskProposal(
                        hypothesis_id=self.h.id,
                        goal="Seek independent publication",
                        role="corroboration",
                        parent_id=report.after.tasks[-1].id,
                        wave=2,
                        span_ids=[self.spans[0].id],
                    )
                ]
                if report.wave == 1
                else []
            )
            return CriticDecision(
                reviews=reviews,
                proposals=proposals,
                reason="No independent sources remain in cache",
            )

        out = asyncio.run(
            run_research(
                ResearchGoal(
                    run_id="r",
                    target="Investigate builder",
                    initial_tasks=[self.proposal()],
                ),
                self.board,
                ReplayWorker(resolve),
                RunPolicy(),
                critic=critic,
            )
        )
        self.assertEqual(calls, [(1, 0), (2, 1)])
        self.assertEqual(out.stop_reason, "SOURCES_EXHAUSTED")
        self.assertEqual(
            self.board.snapshot("r").hypotheses[0].support, "SELF_REPORTED_ONLY"
        )
        self.assertTrue(
            any(e["type"] == "critic.reviewed" for e in self.board.events("r"))
        )

    def test_unknown_cost_blocks_later_waves(self):
        worker = ReplayWorker(
            lambda t, p: WorkerResult(cost_usd=None, status="blocked")
        )
        out = asyncio.run(
            run_research(
                ResearchGoal(
                    run_id="r",
                    target="Investigate builder",
                    initial_tasks=[self.proposal()],
                ),
                self.board,
                worker,
                RunPolicy(),
            )
        )
        self.assertEqual(out.stop_reason, "UNKNOWN_COST")

    def test_interrupted_running_task_is_not_replayed(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        self.board.start_task(task.id, RunPolicy())

        def forbidden(t, p):
            raise AssertionError("Interrupted task was replayed")

        out = asyncio.run(
            run_research(
                ResearchGoal(run_id="r", target="Investigate builder"),
                self.board,
                ReplayWorker(forbidden),
                RunPolicy(),
            )
        )
        self.assertEqual(out.stop_reason, "UNKNOWN_COST")

    def test_bounded_same_wave_children_and_cancellation(self):
        seen = []

        def resolve(t, p):
            seen.append(t.depth)
            return WorkerResult(
                children=[
                    TaskProposal(
                        hypothesis_id=self.h.id,
                        goal=f"child {t.depth}",
                        role="artifact",
                        span_ids=[self.spans[0].id],
                    )
                ]
            )

        out = asyncio.run(
            run_research(
                ResearchGoal(
                    run_id="r",
                    target="Investigate builder",
                    initial_tasks=[self.proposal()],
                ),
                self.board,
                ReplayWorker(resolve),
                RunPolicy(),
            )
        )
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual(len(out.tasks), 3)

    def test_invalid_result_rolls_back_findings_but_keeps_cost(self):
        worker = ReplayWorker(
            lambda t, p: WorkerResult(
                cost_usd=0.05,
                findings=[
                    Finding(
                        hypothesis_id="wrong",
                        statement="invalid",
                        span_ids=[self.spans[0].id],
                    )
                ],
            )
        )
        out = asyncio.run(
            run_research(
                ResearchGoal(
                    run_id="r",
                    target="Investigate builder",
                    initial_tasks=[self.proposal()],
                ),
                self.board,
                worker,
                RunPolicy(),
            )
        )
        self.assertEqual(out.findings, [])
        self.assertAlmostEqual(out.tasks[0].cost_usd, 0.05)

    def test_concurrency_task_and_wave_bounds(self):
        async def scenario():
            active = 0
            maximum = 0

            class Worker:
                async def run(inner, t, p, b):
                    nonlocal active, maximum
                    active += 1
                    maximum = max(maximum, active)
                    await asyncio.sleep(0.001)
                    active -= 1
                    return WorkerResult()

            async def critic(report, reader):
                return CriticDecision(
                    proposals=[
                        TaskProposal(
                            hypothesis_id=self.h.id,
                            goal=f"wave {report.wave + 1} task {i}",
                            role="artifact",
                            span_ids=[self.spans[0].id],
                        )
                        for i in range(5)
                    ]
                )

            goal = ResearchGoal(
                run_id="r",
                target="Investigate builder",
                initial_tasks=[
                    self.proposal(),
                    TaskProposal(
                        hypothesis_id=self.h.id,
                        goal="Second independent task",
                        role="artifact",
                    ),
                ],
            )
            out = await run_research(
                goal, self.board, Worker(), RunPolicy(), critic=critic
            )
            self.assertEqual(maximum, 2)
            self.assertEqual(len(out.tasks), 8)
            self.assertLessEqual(max(t.wave for t in out.tasks), 3)

        asyncio.run(scenario())

    def test_async_cancellation_stops_active_case(self):
        async def scenario():
            started = asyncio.Event()

            class Worker:
                async def run(inner, t, p, b):
                    started.set()
                    await asyncio.Event().wait()

            job = asyncio.create_task(
                run_research(
                    ResearchGoal(
                        run_id="r",
                        target="Investigate builder",
                        initial_tasks=[self.proposal()],
                    ),
                    self.board,
                    Worker(),
                    RunPolicy(),
                )
            )
            await started.wait()
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
            self.assertEqual(self.board.snapshot("r").stop_reason, "CANCELLED")

        asyncio.run(scenario())
