"""Public research contracts shared by workers, scheduler and projections."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RunPolicy(Model):
    max_waves: int = Field(default=3, ge=1, le=3)
    max_depth: int = Field(default=2, ge=0, le=2)
    max_concurrent_workers: int = Field(default=2, ge=1, le=2)
    max_total_tasks: int = Field(default=8, ge=1, le=8)
    top_claims: int = Field(default=3, ge=1, le=3)
    max_input_tokens: int = Field(default=4096, ge=512)
    execution_mode: Literal["offline", "authorized_model"] = "offline"


class EvidenceSpan(Model):
    id: str
    run_id: str
    evidence_id: str
    snapshot_hash: str
    pointer: str
    text: str
    start: int | None = None
    end: int | None = None
    url: str = ""
    observed_at: str = ""
    source_kind: Literal["SELF_AUTHORED", "ARTIFACT", "THIRD_PARTY", "UNCLASSIFIED"] = (
        "UNCLASSIFIED"
    )
    origin_group: str = ""


Support = Literal[
    "UNCHECKED",
    "SELF_REPORTED_ONLY",
    "ARTIFACT_SUPPORTED",
    "THIRD_PARTY_SUPPORTED",
    "CONFLICTING",
    "CONTRADICTED",
]


class Hypothesis(Model):
    id: str
    subject: str
    statement: str
    priority: int
    lifecycle: Literal["OPEN", "INVESTIGATING", "RESOLVED", "EXHAUSTED", "BLOCKED"] = (
        "OPEN"
    )
    support: Support = "UNCHECKED"
    rationale: str = ""
    supporting: list[str] = Field(default_factory=list)
    refuting: list[str] = Field(default_factory=list)
    version: int = 0


class TaskProposal(Model):
    hypothesis_id: str
    goal: str = Field(min_length=1, max_length=2000)
    role: Literal["identity", "artifact", "corroboration"]
    span_ids: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    wave: int = Field(default=1, ge=1, le=3)
    lead_id: str | None = None


class ResearchTask(TaskProposal):
    id: str
    run_id: str
    depth: int
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "BLOCKED", "CANCELLED"] = "QUEUED"
    version: int = 0
    attempt: int = 0
    sdk_agent_id: str | None = None
    cost_usd: float | None = 0


class Rejected(Model):
    reason: str


class Finding(Model):
    id: str = ""
    hypothesis_id: str
    statement: str = Field(min_length=1, max_length=3000)
    span_ids: list[str] = Field(min_length=1)
    relation: Literal["supports", "refutes", "context"] = "context"
    reviewed: bool = False
    review_note: str = ""
    task_id: str = ""


class Lead(Model):
    id: str = ""
    hypothesis_id: str
    url: str
    span_id: str
    reason: str
    kind: Literal["artifact", "organization", "person"] = "artifact"
    purpose: Literal["claim_resolution", "evidenced_collaboration"] = "claim_resolution"
    status: Literal["NEW", "ADMITTED", "BLOCKED"] = "NEW"
    discovered_by: str = ""


class WorkerResult(Model):
    findings: list[Finding] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)
    children: list[TaskProposal] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    status: Literal["completed", "blocked"] = "completed"
    cost_usd: float | None = Field(default=0, ge=0)


class EvidencePacket(Model):
    task_id: str
    goal: str
    hypothesis: Hypothesis
    identity_context: dict[str, Any] = Field(default_factory=dict)
    spans: list[EvidenceSpan] = Field(default_factory=list)
    excluded_span_ids: list[str] = Field(default_factory=list)
    excluded_span_count: int = 0
    manifest_id: str = ""
    missing_span_ids: list[str] = Field(default_factory=list)
    token_upper_bound: int = 0
    measurement: str = "UTF-8 bytes of serialized packet; conservative input estimate, not tokenizer usage"


class ReviewedEntity(Model):
    id: str
    title: str
    kind: Literal["Person", "Organization", "Artifact"]
    url: str
    finding_ids: list[str] = Field(min_length=1)
    review_note: str = Field(min_length=1)


class ReviewedRelationship(Model):
    source_id: str
    target_id: str
    relationship: Literal["WORKED_AT", "CONTRIBUTED_TO", "COLLABORATED_ON"]
    finding_ids: list[str] = Field(min_length=1)
    review_note: str = Field(min_length=1)


class BoardSnapshot(Model):
    run_id: str
    goal: str
    stop_reason: str | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    tasks: list[ResearchTask] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)
    spans: list[EvidenceSpan] = Field(default_factory=list)
    recoveries: list[dict] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    entities: list[ReviewedEntity] = Field(default_factory=list)
    relationships: list[ReviewedRelationship] = Field(default_factory=list)


class BoardDelta(Model):
    task_id: str
    finding_ids: list[str]
    lead_ids: list[str]
