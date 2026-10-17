"""Validated public graph schema and evidence references."""

from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GraphNodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    node_id: str = Field(min_length=1, max_length=100)
    label: Literal["Person", "IdentityAnchor", "DomainPillar", "Artifact", "Claim",
                   "EvidenceNode", "AIJudgment", "Organization"]
    title: str
    summary: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    url: str = ""
    span_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    evidence_state: Literal["observed", "unverified", "supported", "disputed", "interpretation"]
    metadata: dict[str, str | int | float | bool]

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("Source URLs must be HTTP(S) URLs without credentials.")
        return value


class GraphEdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    target_id: str
    relationship: Literal["HAS_IDENTITY", "DEFINED_BY_PILLAR", "BUILT_ARTIFACT",
                          "MAKES_CLAIM", "PROVEN_BY", "CONTRADICTED_BY", "JUDGED_AS",
                          "WORKED_AT", "CONTRIBUTED_TO", "COLLABORATED_ON"]
    reason: str
    evidence_state: Literal["observed", "unverified", "supported", "disputed", "interpretation"] = "unverified"
    span_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)


class PortraitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: Literal["core_obsessions", "builder_velocity", "how_they_think", "inferred_relationships", "claim_corroboration"]
    title: str
    analysis: str = Field(max_length=2500)
    span_ids: list[str] = Field(default_factory=list)
    support_status: Literal["INTERPRETATION", "INSUFFICIENT_EVIDENCE"] = "INTERPRETATION"
    evidence_ids: list[str] = Field(min_length=1)
    limitations: str = Field(max_length=1000)


class AgentConstructedGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_person_id: str | None = None
    summary: str = Field(description="Brief public explanation of the graph and its limitations.")
    detected_archetype: Literal["Developer", "Founder_Operator", "Creator_Designer", "Researcher_General", "Unknown"]
    signal_to_hype_score: int | None = Field(ge=0, le=100, description="Subjective evidence assessment; null if insufficient evidence.")
    nodes: list[GraphNodeSpec] = Field(min_length=1, max_length=100)
    edges: list[GraphEdgeSpec] = Field(max_length=300)
    portrait: list[PortraitSection] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_references(self):
        ids = {node.node_id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Duplicate graph node IDs.")
        people = [node for node in self.nodes if node.label == "Person"]
        if self.root_person_id is None and len(people) == 1:
            self.root_person_id = people[0].node_id
        if self.root_person_id not in {p.node_id for p in people}:
            raise ValueError("A central Person must be explicitly identified for multi-person graphs.")
        if any(not p.url for p in people if p.node_id != self.root_person_id):
            raise ValueError("Additional people require distinct source anchors.")
        anchors = [p.url.rstrip("/").casefold() for p in people if p.url]
        if len(anchors) != len(set(anchors)):
            raise ValueError("Two people cannot share an identity anchor.")
        if any(n.label == "AIJudgment" and n.evidence_state != "interpretation" for n in self.nodes):
            raise ValueError("AI judgments must be labeled as interpretations.")
        if any(edge.source_id not in ids or edge.target_id not in ids for edge in self.edges):
            raise ValueError("Graph edge references an unknown node.")
        return self
