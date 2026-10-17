"""Official SDK tools with server-bound task scope; no private-vault access."""

from dataclasses import dataclass
import json
from claude_agent_sdk import create_sdk_mcp_server, tool
from .blackboard import Blackboard
from .evidence import EvidenceReader
from .models import Finding, Lead, RunPolicy, TaskProposal


@dataclass(frozen=True)
class TaskCapability:
    task_id: str
    run_id: str
    version: int

    @classmethod
    def from_task(cls, task):
        return cls(task.id, task.run_id, task.version)


class ResearchTools:
    def __init__(
        self, board: Blackboard, capability: TaskCapability, policy: RunPolicy
    ):
        self.board, self.capability, self.policy = board, capability, policy
        self.reader = EvidenceReader(board, capability.run_id)

    def task(self):
        task = self.board.task(self.capability.task_id)
        if (
            task.run_id != self.capability.run_id
            or task.version != self.capability.version
            or task.status != "RUNNING"
            or self.board.snapshot(task.run_id).stop_reason
        ):
            raise ValueError("Expired task capability")
        return task

    def read_evidence(self, span_id):
        self.task()
        span = self.reader.read_span(span_id)
        if len(span.model_dump_json().encode()) > self.policy.max_input_tokens:
            raise ValueError(
                "Evidence span exceeds task input bound; request a narrower excerpt"
            )
        return span

    def list_open_hypotheses(self):
        task = self.task()
        return [
            h
            for h in self.board.snapshot(task.run_id).hypotheses
            if h.id == task.hypothesis_id
        ]

    def submit_findings(self, findings):
        task = self.task()
        items = [Finding.model_validate(f) for f in findings]
        for f in items:
            if f.hypothesis_id != task.hypothesis_id:
                raise ValueError("Hypothesis outside scope")
            for sid in f.span_ids:
                self.reader.read_span(sid)
        with self.board.transaction():
            self.board._put(
                task.run_id, "staged_findings", task.id, [f.model_dump() for f in items]
            )
            self.board._event(
                task.run_id, "findings.proposed", task_id=task.id, count=len(items)
            )
        return {"status": "pending_result_and_review", "count": len(items)}

    def propose_lead(self, value):
        task = self.task()
        lead = Lead.model_validate(value)
        if lead.hypothesis_id != task.hypothesis_id:
            raise ValueError("Hypothesis outside scope")
        span = self.reader.read_span(lead.span_id)
        if lead.url not in span.text and lead.url != span.url:
            raise ValueError("Lead URL lacks source reference")
        with self.board.transaction():
            staged = self.board._list(task.run_id, "staged_lead")
            self.board._put(
                task.run_id,
                "staged_lead",
                f"{task.id}:{len(staged)}",
                {"task_id": task.id, "lead": lead.model_dump()},
            )
        return {"status": "pending_result_and_review"}

    def request_child_task(self, goal, role, span_ids):
        task = self.task()
        return self.board.admit_task(
            task.run_id,
            TaskProposal(
                hypothesis_id=task.hypothesis_id,
                goal=goal,
                role=role,
                span_ids=span_ids,
                parent_id=task.id,
                wave=task.wave,
            ),
            self.policy,
        )


def create_research_tools(
    board: Blackboard, capability: TaskCapability, policy: RunPolicy | None = None
):
    scoped = ResearchTools(board, capability, policy or RunPolicy())

    def output(value):
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
        }

    @tool("read_evidence", "Read one exact public case-file span", {"span_id": str})
    async def read(args):
        return output(scoped.read_evidence(args["span_id"]))

    @tool("list_open_hypotheses", "Read the hypothesis assigned to this worker", {})
    async def hypotheses(args):
        return output([h.model_dump() for h in scoped.list_open_hypotheses()])

    @tool(
        "submit_findings",
        "Submit candidate findings for later review",
        {"findings_json": str},
    )
    async def submit(args):
        return output(scoped.submit_findings(json.loads(args["findings_json"])))

    @tool(
        "propose_lead",
        "Propose a source-backed lead for this hypothesis",
        {"lead_json": str},
    )
    async def lead(args):
        return output(scoped.propose_lead(json.loads(args["lead_json"])))

    @tool(
        "request_child_task",
        "Request a bounded child task; does not bypass scheduling",
        {"goal": str, "role": str, "span_ids": list[str]},
    )
    async def child(args):
        return output(scoped.request_child_task(**args))

    return create_sdk_mcp_server(
        name="case", version="1.0.0", tools=[read, hypotheses, submit, lead, child]
    )
