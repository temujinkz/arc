import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from backend.research.blackboard import Blackboard, Rejected
from backend.research.evidence import EvidenceReader
from backend.research.models import RunPolicy, TaskProposal, WorkerResult, Finding


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "case.sqlite3"
        self.board = Blackboard(self.path)
        self.board.create_run("r", "Investigate builder")
        self.board.add_source(
            "r",
            "s",
            {
                "roles": [
                    {
                        "company": "Example",
                        "description": "Claims shipped a VS Code extension.",
                    }
                ]
            },
            url="https://example.org",
            origin_group="subject",
            source_kind="SELF_AUTHORED",
            observed_at="2026-01-01",
        )
        self.reader = EvidenceReader(self.board, "r")
        self.spans = self.reader.index_source("s")
        self.h = self.board.add_hypothesis(
            "r", "person", "Shipped a VS Code extension", 1
        )

    def tearDown(self):
        self.board.close()
        self.tmp.cleanup()

    def proposal(self, **kwargs):
        return TaskProposal(
            hypothesis_id=self.h.id,
            goal="Inspect extension evidence",
            role="artifact",
            span_ids=[self.spans[0].id],
            **kwargs,
        )

    def test_duplicate_admission_and_atomic_events(self):
        def admit():
            with Blackboard(self.path) as b:
                return b.admit_task("r", self.proposal(), RunPolicy())

        with ThreadPoolExecutor(2) as ex:
            results = list(ex.map(lambda _: admit(), range(2)))
        self.assertEqual(sum(not isinstance(x, Rejected) for x in results), 1)
        events = self.board.events("r")
        self.assertEqual(sum(e["type"] == "task.queued" for e in events), 1)
        self.assertEqual(len(self.board.snapshot("r").tasks), 1)

    def test_immutable_source_and_exact_spans(self):
        with self.assertRaises(ValueError):
            self.board.add_source("r", "s", {"changed": True})
        span = self.spans[0]
        self.assertEqual(self.reader.read_span(span.id).text, span.text)
        with self.assertRaises(ValueError):
            self.reader.add_span("s", "/roles/0/description", "invented quotation")
        self.board.create_run("other", "Other person")
        with self.assertRaises(ValueError):
            EvidenceReader(self.board, "other").read_span(span.id)

    def test_result_rejects_stale_or_cross_case_evidence(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        running = self.board.start_task(task.id, RunPolicy())
        result = WorkerResult(
            findings=[
                Finding(
                    hypothesis_id=self.h.id,
                    statement="Profile claims an extension",
                    span_ids=[self.spans[0].id],
                )
            ]
        )
        with self.assertRaises(ValueError):
            self.board.record_result(task.id, result, expected_version=999)
        self.assertEqual(len(self.board.snapshot("r").findings), 0)
        self.board.record_result(task.id, result, expected_version=running.version)
        with self.assertRaises(ValueError):
            self.board.record_result(task.id, result, expected_version=running.version)
        self.assertEqual(len(self.board.snapshot("r").findings), 1)

    def test_packet_bounds_and_durable_restart(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        packet = self.reader.build_packet(task, 10000)
        self.assertTrue(packet.spans)
        self.assertLessEqual(packet.token_upper_bound, 10000)
        tiny = self.reader.build_packet(task, 1000)
        self.assertLessEqual(tiny.token_upper_bound, 1000)
        self.assertEqual(len(tiny.spans) + len(tiny.excluded_span_ids), len(self.spans))
        with Blackboard(self.path) as restored:
            self.assertEqual(restored.snapshot("r").tasks[0].id, task.id)
        with self.assertRaises(ValueError):
            self.reader.build_packet(task, 1)

    def test_scratchpad_research_sink_preserves_large_source_without_prompt_dump(self):
        from backend.scratchpad import InvestigationScratchpad

        pad = InvestigationScratchpad(
            "r", max_evidence_bytes=10, research_board=self.board
        )
        pad.capture(
            "mcp__github__get_file_contents",
            "large-call",
            {"text": "Public artifact " * 1000},
            {},
        )
        self.assertEqual(len(self.board._list("r", "source")), 2)
        self.assertLess(len(json.dumps(pad.raw_evidence[0]["data"])), 100)
        from backend.evidence_ledger import content_digest
        finding = pad.raw_evidence[0]
        self.assertEqual(finding["content_sha256"], content_digest(finding["data"]))
        self.assertEqual(len(pad.architect_evidence()), 1)
        finding["data"]["evidence_ref"] = "tampered-reference"
        self.assertEqual(pad.architect_evidence(), [])

    def test_cancel_rejects_late_results(self):
        task = self.board.admit_task("r", self.proposal(), RunPolicy())
        task = self.board.start_task(task.id, RunPolicy())
        self.board.stop("r", "CANCELLED")
        with self.assertRaises(ValueError):
            self.board.record_result(task.id, WorkerResult(), task.version)


if __name__ == "__main__":
    unittest.main()
