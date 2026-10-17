"""Deterministic public projection. Source presence never substitutes for review."""

from typing import Protocol
from pydantic import Field, field_validator
from backend.graph_models import (
    AgentConstructedGraph,
    GraphNodeSpec,
    GraphEdgeSpec,
    PortraitSection,
)
from .models import Model, Finding, EvidenceSpan

SECTIONS = [
    "core_obsessions",
    "builder_velocity",
    "how_they_think",
    "inferred_relationships",
    "claim_corroboration",
]


class PublicGraphDraft(Model):
    graph: AgentConstructedGraph | None = None
    coverage: list[dict] = Field(default_factory=list)
    missing_portrait_sections: list[str] = Field(default_factory=lambda: list(SECTIONS))
    limitation: str = "Deterministic projection of reviewed findings; no model portrait evaluation or Neo4j commit performed."


class PortraitEvidencePacket(Model):
    findings: list[Finding]
    spans: list[EvidenceSpan]
    max_input_bytes: int = 12000


class PortraitDraft(Model):
    sections: list[PortraitSection] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=lambda: list(SECTIONS))
    cost_usd: float | None = 0

    @field_validator("sections", mode="before")
    @classmethod
    def normalize_empty_limitation_notes(cls, sections):
        # Some model responses add this empty companion to the required
        # limitations field. Remove only that no-content formatting artifact;
        # substantive extras and missing required fields still fail validation.
        if not isinstance(sections, list):
            return sections
        return [
            {key: value for key, value in section.items() if key != "limitations_note"}
            if isinstance(section, dict) and "limitations" in section
            and section.get("limitations_note") == "" else section
            for section in sections
        ]


class PortraitSynthesizer(Protocol):
    async def synthesize(self, packet: PortraitEvidencePacket) -> PortraitDraft: ...


async def synthesize_portrait(
    packet: PortraitEvidencePacket, adapter: PortraitSynthesizer | None = None
) -> PortraitDraft:
    if len(packet.model_dump_json().encode()) > packet.max_input_bytes:
        raise ValueError("Portrait packet exceeds bound")
    spans = {s.id: s for s in packet.spans}
    if any(
        not f.reviewed or not set(f.span_ids) <= spans.keys() for f in packet.findings
    ):
        raise ValueError("Portrait requires reviewed, cited findings")
    if adapter is None:
        return PortraitDraft()
    draft = await adapter.synthesize(packet)
    sources = {s.evidence_id for s in packet.spans}
    if len({s.section for s in draft.sections}) != len(draft.sections):
        raise ValueError("Duplicate portrait section")
    for section in draft.sections:
        if not set(section.evidence_ids) <= sources:
            raise ValueError("Portrait cites an unknown source")
        if not section.span_ids:
            # A missing citation never becomes a factual paragraph. Keep the
            # section visible with a deterministic insufficiency statement.
            section.support_status = "INSUFFICIENT_EVIDENCE"
            section.title = section.section.replace("_", " ").title()
            section.analysis = (
                "The reviewed evidence does not establish this part of the portrait."
            )
            section.limitations = (
                "No exact supporting passage was supplied for this section."
            )
            continue
        if (
            not set(section.span_ids) <= spans.keys()
            or not set(section.evidence_ids) <= sources
        ):
            raise ValueError("Portrait citation outside approved packet")
    draft.missing_sections = [
        s
        for s in SECTIONS
        if s
        not in {
            x.section
            for x in draft.sections
            if x.support_status != "INSUFFICIENT_EVIDENCE"
        }
    ]
    return draft


def project_case(snapshot) -> PublicGraphDraft:
    coverage = [
        {
            "hypothesis_id": h.id,
            "statement": h.statement,
            "support": h.support,
            "lifecycle": h.lifecycle,
            "rationale": h.rationale,
            "insufficient_evidence": h.support
            in ("UNCHECKED", "SELF_REPORTED_ONLY", "CONFLICTING"),
            "supporting": h.supporting,
            "refuting": h.refuting,
        }
        for h in snapshot.hypotheses
    ]
    accepted = {f.id: f for f in snapshot.findings if f.reviewed}
    spans = {s.id: s for s in snapshot.spans}

    def citations(ids):
        selected = []
        for sid in dict.fromkeys(ids):
            s = spans.get(sid)
            if (
                s is None
                or s.run_id != snapshot.run_id
                or snapshot.source_hashes.get(s.evidence_id) != s.snapshot_hash
            ):
                raise ValueError("Unknown source or span snapshot")
            selected.append(s)
        return selected

    used = citations([sid for f in accepted.values() for sid in f.span_ids])
    if not used:
        return PublicGraphDraft(coverage=coverage)
    root_ids = [s.id for s in used]
    sources = list(dict.fromkeys(s.evidence_id for s in used))
    subjects = {h.subject for h in snapshot.hypotheses}
    if len(subjects) != 1:
        raise ValueError("Case has ambiguous root identity")
    nodes = [
        GraphNodeSpec(
            node_id="person_root",
            label="Person",
            title=next(iter(subjects)),
            summary="Research subject; identity decisions are separate from independent proof.",
            evidence_ids=sources,
            span_ids=root_ids,
            evidence_state="unverified",
            metadata={"research_run_id": snapshot.run_id},
        )
    ]
    edges = []
    for s in used:
        nodes.append(
            GraphNodeSpec(
                node_id="evidence_" + s.id,
                label="EvidenceNode",
                title=s.pointer or "Source passage",
                summary=s.text,
                url=s.url,
                evidence_ids=[s.evidence_id],
                span_ids=[s.id],
                evidence_state="observed",
                metadata={
                    "origin_group": s.origin_group,
                    "source_kind": s.source_kind,
                    "observed_at": s.observed_at,
                    "snapshot_hash": s.snapshot_hash,
                    "pointer": s.pointer,
                    "start": s.start if s.start is not None else -1,
                    "end": s.end if s.end is not None else -1,
                },
            )
        )
    for h in snapshot.hypotheses:
        findings = [f for f in accepted.values() if f.hypothesis_id == h.id]
        selected = citations([sid for f in findings for sid in f.span_ids])
        if not selected:
            continue
        support_state = (
            "disputed"
            if h.support in ("CONFLICTING", "CONTRADICTED")
            else "supported"
            if h.support in ("ARTIFACT_SUPPORTED", "THIRD_PARTY_SUPPORTED")
            else "unverified"
        )
        sid = [s.id for s in selected]
        eids = list(dict.fromkeys(s.evidence_id for s in selected))
        cid = "claim_" + h.id
        nodes.append(
            GraphNodeSpec(
                node_id=cid,
                label="Claim",
                title=h.statement,
                summary=h.rationale,
                evidence_ids=eids,
                span_ids=sid,
                evidence_state=support_state,
                metadata={"support": h.support, "lifecycle": h.lifecycle},
            )
        )
        edges.append(
            GraphEdgeSpec(
                source_id="person_root",
                target_id=cid,
                relationship="MAKES_CLAIM",
                reason="Claim under investigation, not a verification of it.",
                evidence_ids=eids,
                span_ids=sid,
            )
        )
        for f in findings:
            if f.relation == "context":
                continue
            for s in citations(f.span_ids):
                edges.append(
                    GraphEdgeSpec(
                        source_id=cid,
                        target_id="evidence_" + s.id,
                        relationship="CONTRADICTED_BY"
                        if f.relation == "refutes"
                        else "PROVEN_BY",
                        reason=f.review_note,
                        evidence_ids=[s.evidence_id],
                        span_ids=[s.id],
                        evidence_state=support_state,
                    )
                )

    def reviewed_citations(finding_ids):
        if not set(finding_ids) <= accepted.keys():
            raise ValueError("Entity or relationship cites unreviewed findings")
        return citations([sid for fid in finding_ids for sid in accepted[fid].span_ids])

    for entity in snapshot.entities:
        selected = reviewed_citations(entity.finding_ids)
        if not any(entity.url in s.text or entity.url == s.url for s in selected):
            raise ValueError("Entity anchor not present in cited passages")
        if entity.kind == "Person" and not any(
            entity.id in (r.source_id, r.target_id) for r in snapshot.relationships
        ):
            raise ValueError("Person expansion must resolve an approved relationship")
        nodes.append(
            GraphNodeSpec(
                node_id=entity.id,
                label=entity.kind,
                title=entity.title,
                summary=entity.review_note,
                url=entity.url,
                evidence_ids=list(dict.fromkeys(s.evidence_id for s in selected)),
                span_ids=[s.id for s in selected],
                evidence_state="unverified",
                metadata={"reviewed_finding_ids": ",".join(entity.finding_ids)},
            )
        )
    for relationship in snapshot.relationships:
        selected = reviewed_citations(relationship.finding_ids)
        hs = [
            h
            for h in snapshot.hypotheses
            if h.id in {accepted[fid].hypothesis_id for fid in relationship.finding_ids}
        ]
        state = (
            "disputed"
            if any(h.support in ("CONFLICTING", "CONTRADICTED") for h in hs)
            else "supported"
            if hs
            and all(
                h.support in ("ARTIFACT_SUPPORTED", "THIRD_PARTY_SUPPORTED") for h in hs
            )
            else "unverified"
        )
        edges.append(
            GraphEdgeSpec(
                source_id=relationship.source_id,
                target_id=relationship.target_id,
                relationship=relationship.relationship,
                reason=relationship.review_note,
                evidence_ids=list(dict.fromkeys(s.evidence_id for s in selected)),
                span_ids=[s.id for s in selected],
                evidence_state=state,
            )
        )
    # Shared spans can support multiple findings; deduplicate identical edges.
    edges = list(
        {(e.source_id, e.target_id, e.relationship): e for e in edges}.values()
    )
    graph = AgentConstructedGraph(
        root_person_id="person_root",
        summary="Reviewed public findings with explicit support limitations. No independent verification is implied by graph storage.",
        detected_archetype="Unknown",
        signal_to_hype_score=None,
        nodes=nodes,
        edges=edges,
    )
    return PublicGraphDraft(graph=graph, coverage=coverage)
