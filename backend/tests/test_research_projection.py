import json
import unittest
from pydantic import ValidationError
from backend.tests import test_research_blackboard as fixtures
from backend.research.models import RunPolicy, WorkerResult, Finding
from backend.research.critic import apply_review, CriticDecision, FindingReview
from backend.research.projection import project_case
from backend.graph_models import AgentConstructedGraph


class PortraitParsingTests(unittest.TestCase):
    def section(self):
        return dict(section="core_obsessions", title="Project interests",
                    analysis="The author describes building an editor.",
                    limitations="Self-reported; implementation not verified.",
                    evidence_ids=["source-1"], span_ids=["span-1"])

    def test_empty_extra_limitation_note_preserves_required_fields_and_citations(self):
        from backend.research.parsing import parse_model_json
        from backend.research.projection import PortraitDraft
        section = self.section()
        result = parse_model_json(json.dumps({"sections": [
            {**section, "limitations_note": ""}
        ]}), PortraitDraft)
        parsed = result.sections[0].model_dump()
        for key, value in section.items():
            self.assertEqual(parsed[key], value)
        self.assertNotIn("limitations_note", parsed)

    def test_substantive_extra_notes_and_unknown_fields_are_not_discarded(self):
        from backend.research.projection import PortraitDraft
        for extra in ({"limitations_note": "Contradictory evidence exists."},
                      {"limitations_note": None}, {"made_up_field": ""}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                PortraitDraft.model_validate({"sections": [{**self.section(), **extra}]})

    def test_empty_note_does_not_replace_required_limitations(self):
        from backend.research.projection import PortraitDraft
        section = self.section()
        del section["limitations"]
        with self.assertRaises(ValidationError):
            PortraitDraft.model_validate({"sections": [{**section, "limitations_note": ""}]})


class ProjectionTests(unittest.TestCase):
    setUp = fixtures.BoardTests.setUp
    tearDown = fixtures.BoardTests.tearDown
    proposal = fixtures.BoardTests.proposal

    def finding(self, relation="supports", statement="Profile claims an extension"):
        task = self.board.start_task(
            self.board.admit_task("r", self.proposal(), RunPolicy()).id, RunPolicy()
        )
        self.board.record_result(
            task.id,
            WorkerResult(
                findings=[
                    Finding(
                        hypothesis_id=self.h.id,
                        statement=statement,
                        span_ids=[self.spans[0].id],
                        relation=relation,
                    )
                ]
            ),
            task.version,
        )
        return self.board.snapshot("r").findings[0]

    def test_valid_but_irrelevant_citation_does_not_become_proof(self):
        f = self.finding(statement="Ten thousand users adopted it")
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id,
                        accepted=False,
                        reason="Passage says nothing about adoption",
                    )
                ]
            ),
        )
        draft = project_case(self.board.snapshot("r"))
        self.assertIsNone(draft.graph)
        self.assertEqual(draft.coverage[0]["support"], "UNCHECKED")
        self.assertEqual(len(draft.missing_portrait_sections), 5)

    def test_self_authored_and_conflicting_evidence_retained(self):
        f = self.finding()
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id,
                        accepted=True,
                        reason="Only the profile claim is supported",
                    )
                ]
            ),
        )
        draft = project_case(self.board.snapshot("r"))
        self.assertEqual(draft.graph.root_person_id, "person_root")
        claim = next(n for n in draft.graph.nodes if n.label == "Claim")
        self.assertEqual(claim.evidence_state, "unverified")
        self.assertEqual(claim.metadata["support"], "SELF_REPORTED_ONLY")
        self.assertFalse(
            any(n.label in ("PeerClaim", "AIJudgment") for n in draft.graph.nodes)
        )
        self.assertTrue(all(n.span_ids for n in draft.graph.nodes))
        snapshot = self.board.snapshot("r")
        snapshot.spans[0].evidence_id = "invented"
        with self.assertRaises(ValueError):
            project_case(snapshot)

    def test_legacy_graph_infers_root_and_extra_people_need_distinct_anchors(self):
        node = dict(
            node_id="p",
            label="Person",
            title="Person",
            summary="",
            evidence_ids=["s"],
            evidence_state="unverified",
            metadata={},
        )
        data = dict(
            summary="",
            detected_archetype="Unknown",
            signal_to_hype_score=None,
            nodes=[node],
            edges=[],
        )
        self.assertEqual(AgentConstructedGraph.model_validate(data).root_person_id, "p")
        data["nodes"].append({**node, "node_id": "p2"})
        with self.assertRaises(ValueError):
            AgentConstructedGraph.model_validate(data)
        data["root_person_id"] = "p"
        data["nodes"][1]["url"] = "https://example.com/person-2"
        self.assertEqual(len(AgentConstructedGraph.model_validate(data).nodes), 2)
        data["nodes"][0]["url"] = "https://example.com/person-2"
        with self.assertRaises(ValueError):
            AgentConstructedGraph.model_validate(data)

    def test_conflicting_spans_keep_both_edges_and_no_confidence(self):
        task = self.board.start_task(
            self.board.admit_task("r", self.proposal(), RunPolicy()).id, RunPolicy()
        )
        self.board.add_source(
            "r",
            "refutation",
            {"statement": "The extension was never released."},
            source_kind="THIRD_PARTY",
            origin_group="publisher",
        )
        from backend.research.evidence import EvidenceReader

        span = EvidenceReader(self.board, "r").index_source("refutation")[0]
        self.board.record_result(
            task.id,
            WorkerResult(
                findings=[
                    Finding(
                        hypothesis_id=self.h.id,
                        statement="Profile claims extension release",
                        span_ids=[self.spans[0].id],
                        relation="supports",
                    ),
                    Finding(
                        hypothesis_id=self.h.id,
                        statement="Publisher disputes release",
                        span_ids=[span.id],
                        relation="refutes",
                    ),
                ]
            ),
            task.version,
        )
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id,
                        accepted=True,
                        reason="Explicitly reviewed synthetic fixture",
                    )
                    for f in self.board.snapshot("r").findings
                ]
            ),
        )
        draft = project_case(self.board.snapshot("r"))
        self.assertEqual(draft.coverage[0]["support"], "CONFLICTING")
        self.assertEqual(
            {e.relationship for e in draft.graph.edges},
            {"MAKES_CLAIM", "PROVEN_BY", "CONTRADICTED_BY"},
        )
        self.assertIsNone(draft.graph.signal_to_hype_score)

    def test_mentioned_person_cannot_be_projected_without_reviewed_relationship(self):
        f = self.finding()
        apply_review(
            self.board,
            "r",
            CriticDecision(
                reviews=[
                    FindingReview(
                        finding_id=f.id, accepted=True, reason="Self-claim only"
                    )
                ]
            ),
        )
        from backend.research.models import ReviewedEntity

        snapshot = self.board.snapshot("r")
        snapshot.entities = [
            ReviewedEntity(
                id="colleague",
                title="Same name",
                kind="Person",
                url="https://example.com/unrelated",
                finding_ids=[f.id],
                review_note="Mention only",
            )
        ]
        with self.assertRaises(ValueError):
            project_case(snapshot)
