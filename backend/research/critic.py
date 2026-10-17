"""Focused review contract; semantic judgments are distinct from mechanical checks."""

from typing import Protocol
from pydantic import Field
from .models import (
    Model,
    BoardSnapshot,
    TaskProposal,
    ReviewedEntity,
    ReviewedRelationship,
)
from .evidence import EvidenceReader


class CriticInputError(ValueError):
    """Local preflight rejection: no reviewer request was sent or charged."""

    def __init__(self):
        super().__init__("Evidence review input exceeds the configured model-request size. No critic call was sent; sources are saved and no graph was committed.")


class FindingReview(Model):
    finding_id: str
    accepted: bool
    reason: str = Field(min_length=1)


class CriticDecision(Model):
    reviews: list[FindingReview] = Field(default_factory=list)
    proposals: list[TaskProposal] = Field(default_factory=list)
    entities: list[ReviewedEntity] = Field(default_factory=list)
    relationships: list[ReviewedRelationship] = Field(default_factory=list)
    reason: str = "No additional scoped work proposed"
    cost_usd: float | None = Field(default=0, ge=0)


class ProgressReport(Model):
    before: BoardSnapshot
    after: BoardSnapshot
    wave: int
    new_findings: int
    new_leads: int


class ProgressReviewer(Protocol):
    async def __call__(
        self, report: ProgressReport, reader: EvidenceReader
    ) -> CriticDecision: ...


def check_progress(before, after, policy):
    return ProgressReport(
        before=before.model_copy(update={"spans": []}),
        after=after.model_copy(update={"spans": []}),
        wave=max((t.wave for t in after.tasks), default=1),
        new_findings=len(after.findings) - len(before.findings),
        new_leads=len(after.leads) - len(before.leads),
    )


async def review_progress(
    report: ProgressReport, reader: EvidenceReader
) -> CriticDecision:
    # Fail closed without a semantic reviewer. Exact excerpts alone do not entail claims.
    return CriticDecision(
        reason="No semantic reviewer configured; candidate findings remain unreviewed"
    )


def apply_review(board, run, decision: CriticDecision, *, checkpoint_wave=None):
    reader = EvidenceReader(board, run)
    with board.transaction():
        for review in decision.reviews:
            finding = board._get(run, "finding", review.finding_id)
            for sid in finding["span_ids"]:
                reader.read_span(sid)
            finding["reviewed"] = review.accepted
            finding["review_note"] = review.reason
            board._put(run, "finding", review.finding_id, finding)
            board._put(
                run,
                "review",
                f"{review.finding_id}:{len(board._list(run, 'review'))}",
                review.model_dump(),
            )
        accepted = {f["id"] for f in board._list(run, "finding") if f["reviewed"]}
        invalid_entities = {
            e["id"]
            for e in board._list(run, "entity")
            if not set(e["finding_ids"]) <= accepted
        }
        # Retraction wins over downstream projection dependencies. Keep the old assessments as history.
        for kind in ("relationship", "entity"):
            for row in board.db.execute(
                "SELECT id,payload FROM research_records WHERE run_id=? AND kind=?",
                (run, kind),
            ).fetchall():
                import json

                item = json.loads(row["payload"])
                invalid = not set(item["finding_ids"]) <= accepted
                invalid = invalid or (
                    kind == "relationship"
                    and bool({item["source_id"], item["target_id"]} & invalid_entities)
                )
                if invalid:
                    history_id = f"{kind}:{row['id']}:{len(board._list(run, 'projection_history'))}"
                    board._put(
                        run,
                        "projection_history",
                        history_id,
                        {"kind": kind, "value": item, "reason": "Finding retracted"},
                    )
                    board.db.execute(
                        "DELETE FROM research_records WHERE run_id=? AND kind=? AND id=?",
                        (run, kind, row["id"]),
                    )
                    board._event(run, "projection.retracted", kind=kind, id=row["id"])
        self_origins = {
            s.get("origin_group")
            for s in board._list(run, "source")
            if s.get("source_kind") == "SELF_AUTHORED" and s.get("origin_group")
        }
        for h in board._list(run, "hypothesis"):
            findings = [
                f
                for f in board._list(run, "finding")
                if f["hypothesis_id"] == h["id"] and f["reviewed"]
            ]
            supporting = list(
                dict.fromkeys(
                    s
                    for f in findings
                    if f["relation"] == "supports"
                    for s in f["span_ids"]
                )
            )
            refuting = list(
                dict.fromkeys(
                    s
                    for f in findings
                    if f["relation"] == "refutes"
                    for s in f["span_ids"]
                )
            )
            supports = [reader.read_span(s) for s in supporting]
            # Independent classification must be present on the source, not fabricated by workers.
            independent = [
                s
                for s in supports
                if s.source_kind == "THIRD_PARTY"
                and s.origin_group
                and s.origin_group not in self_origins
            ]
            status = (
                "CONFLICTING"
                if supporting and refuting
                else "CONTRADICTED"
                if refuting
                else "THIRD_PARTY_SUPPORTED"
                if independent
                else "ARTIFACT_SUPPORTED"
                if any(s.source_kind == "ARTIFACT" for s in supports)
                else "SELF_REPORTED_ONLY"
                if any(
                    s.source_kind == "SELF_AUTHORED" or s.origin_group in self_origins
                    for s in supports
                )
                else "UNCHECKED"
            )
            h.update(
                support=status,
                supporting=supporting,
                refuting=refuting,
                rationale=decision.reason,
                lifecycle="RESOLVED"
                if status
                in ("THIRD_PARTY_SUPPORTED", "ARTIFACT_SUPPORTED", "CONTRADICTED")
                else "OPEN",
                version=h["version"] + 1,
            )
            board._put(run, "hypothesis", h["id"], h)
            board._event(
                run,
                "hypothesis.updated",
                hypothesis_id=h["id"],
                statement=h["statement"],
                support=status,
                lifecycle=h["lifecycle"],
            )
        for entity in decision.entities:
            board._put(run, "entity", entity.id, entity.model_dump())
        for relationship in decision.relationships:
            key = f"{relationship.source_id}:{relationship.relationship}:{relationship.target_id}"
            board._put(run, "relationship", key, relationship.model_dump())
        from .projection import project_case

        draft = project_case(board.snapshot(run))
        board._event(
            run,
            "graph.research_preview",
            nodes=len(draft.graph.nodes) if draft.graph else 0,
            edges=len(draft.graph.edges) if draft.graph else 0,
            committed=False,
        )
        board._event(
            run,
            "critic.reviewed",
            reason=decision.reason,
            reviewed=len(decision.reviews),
            proposed_tasks=len(decision.proposals),
            cost_usd=decision.cost_usd,
        )

        if checkpoint_wave is not None:
            checkpoint = board._get(run, "wave_checkpoint", str(checkpoint_wave))
            checkpoint["status"] = "REVIEW_APPLIED"
            board._put(run, "wave_checkpoint", str(checkpoint_wave), checkpoint)
