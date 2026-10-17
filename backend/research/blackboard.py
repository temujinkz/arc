"""SQLite authoritative case ledger. State and its event commit together."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from .models import (
    BoardDelta,
    BoardSnapshot,
    EvidenceSpan,
    Hypothesis,
    Rejected,
    ResearchTask,
    RunPolicy,
    TaskProposal,
    WorkerResult,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Blackboard:
    def __init__(self, path: Path, *, event_sink=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.event_sink = event_sink
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS research_runs(id TEXT PRIMARY KEY, goal TEXT NOT NULL, stop_reason TEXT);
        CREATE TABLE IF NOT EXISTS research_records(run_id TEXT NOT NULL REFERENCES research_runs(id), kind TEXT NOT NULL,
          id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,kind,id));
        CREATE TABLE IF NOT EXISTS research_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES research_runs(id), payload TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(run_id UNINDEXED, span_id UNINDEXED, text, tokenize='unicode61');
        """)
        self.db.execute("""INSERT INTO evidence_fts(run_id,span_id,text)
          SELECT run_id,id,json_extract(payload,'$.text') FROM research_records r
          WHERE kind='span' AND NOT EXISTS(SELECT 1 FROM evidence_fts f WHERE f.run_id=r.run_id AND f.span_id=r.id)""")
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        before = (
            self.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM research_events"
            ).fetchone()[0]
            if self.event_sink
            else 0
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        if self.event_sink:
            for row in self.db.execute(
                "SELECT seq,payload FROM research_events WHERE seq>? ORDER BY seq",
                (before,),
            ):
                self.event_sink({**json.loads(row["payload"]), "sequence": row["seq"]})

    def _put(self, run, kind, id, value):
        self.db.execute(
            "INSERT INTO research_records VALUES(?,?,?,?) ON CONFLICT(run_id,kind,id) DO UPDATE SET payload=excluded.payload",
            (run, kind, id, encoded(value)),
        )
        if kind == "span":
            self.db.execute(
                "DELETE FROM evidence_fts WHERE run_id=? AND span_id=?", (run, id)
            )
            self.db.execute(
                "INSERT INTO evidence_fts(run_id,span_id,text) VALUES(?,?,?)",
                (run, id, value["text"]),
            )

    def _get(self, run, kind, id):
        row = self.db.execute(
            "SELECT payload FROM research_records WHERE run_id=? AND kind=? AND id=?",
            (run, kind, id),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown scoped {kind} record")
        return json.loads(row["payload"])

    def _list(self, run, kind):
        return [
            json.loads(r["payload"])
            for r in self.db.execute(
                "SELECT payload FROM research_records WHERE run_id=? AND kind=? ORDER BY rowid",
                (run, kind),
            )
        ]

    def _event(self, run, type, **data):
        self.db.execute(
            "INSERT INTO research_events(run_id,payload) VALUES(?,?)",
            (run, encoded({"type": type, "session_id": run, **data})),
        )

    def emit(self, run, type, **data):
        with self.transaction():
            self._event(run, type, **data)

    def events(self, run):
        return [
            {**json.loads(row["payload"]), "sequence": row["seq"]}
            for row in self.db.execute(
                "SELECT * FROM research_events WHERE run_id=? ORDER BY seq", (run,)
            )
        ]

    def create_run(self, run, goal):
        with self.transaction():
            previous = self.db.execute(
                "SELECT goal FROM research_runs WHERE id=?", (run,)
            ).fetchone()
            if previous:
                if previous["goal"] != goal:
                    raise ValueError("Run belongs to another goal")
                return
            self.db.execute("INSERT INTO research_runs VALUES(?,?,NULL)", (run, goal))
            self._event(run, "research.created", goal=goal)

    def add_source(self, run, id, data, **metadata):
        payload = {"data": data, "hash": digest(data), **metadata}
        with self.transaction():
            old = self.db.execute(
                "SELECT payload FROM research_records WHERE run_id=? AND kind='source' AND id=?",
                (run, id),
            ).fetchone()
            if old:
                if json.loads(old["payload"]) != payload:
                    raise ValueError("Source snapshots are immutable")
                return
            self._put(run, "source", id, payload)

    def add_hypothesis(self, run, subject, statement, priority):
        id = digest([subject, statement])[:24]
        with self.transaction():
            old = next(
                (h for h in self._list(run, "hypothesis") if h["id"] == id), None
            )
            if old:
                return Hypothesis.model_validate(old)
            h = Hypothesis(
                id=id, subject=subject, statement=statement, priority=priority
            )
            self._put(run, "hypothesis", id, h.model_dump())
            self._event(
                run,
                "hypothesis.updated",
                hypothesis_id=id,
                statement=statement,
                support=h.support,
            )
        return h

    def task(self, id):
        rows = self.db.execute(
            "SELECT payload FROM research_records WHERE kind='task' AND id=?", (id,)
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("Unknown task")
        return ResearchTask.model_validate_json(rows[0]["payload"])

    def admit_task(self, run, proposal: TaskProposal, policy: RunPolicy):
        with self.transaction():
            snap = self.snapshot(run)
            if snap.stop_reason:
                return Rejected(reason="RUN_STOPPED")
            h = self._get(run, "hypothesis", proposal.hypothesis_id)
            priorities = sorted(snap.hypotheses, key=lambda x: (x.priority, x.id))[
                : policy.top_claims
            ]
            if h["id"] not in {x.id for x in priorities}:
                return Rejected(reason="CLAIM_OUT_OF_SCOPE")
            if len(snap.tasks) >= policy.max_total_tasks:
                return Rejected(reason="TASK_LIMIT")
            parent = self.task(proposal.parent_id) if proposal.parent_id else None
            if parent and (
                parent.run_id != run or parent.hypothesis_id != proposal.hypothesis_id
            ):
                return Rejected(reason="PARENT_SCOPE")
            depth = parent.depth + 1 if parent else 0
            if depth > policy.max_depth:
                return Rejected(reason="DEPTH_LIMIT")
            if proposal.wave > policy.max_waves:
                return Rejected(reason="WAVE_LIMIT")
            if parent and proposal.wave < parent.wave:
                return Rejected(reason="INVALID_WAVE")
            for sid in proposal.span_ids:
                self._get(run, "span", sid)
            if proposal.lead_id:
                lead = self._get(run, "lead", proposal.lead_id)
                if lead["hypothesis_id"] != h["id"]:
                    return Rejected(reason="LEAD_SCOPE")
            # Wave and parent are excluded: moving the same job around does not make it new work.
            fingerprint = digest(
                [
                    run,
                    proposal.hypothesis_id,
                    proposal.role,
                    proposal.goal.casefold().strip(),
                    sorted(proposal.span_ids),
                    proposal.lead_id,
                ]
            )
            if any(
                t.get("fingerprint") == fingerprint for t in self._list(run, "task_key")
            ):
                return Rejected(reason="DUPLICATE_TASK")
            task = ResearchTask(
                **proposal.model_dump(), id=str(uuid4()), run_id=run, depth=depth
            )
            self._put(run, "task", task.id, task.model_dump())
            self._put(run, "task_key", task.id, {"fingerprint": fingerprint})
            if proposal.lead_id:
                lead["status"] = "ADMITTED"
                self._put(run, "lead", lead["id"], lead)
            self._event(
                run,
                "task.queued",
                task_id=task.id,
                parent_id=task.parent_id,
                depth=depth,
                wave=task.wave,
                goal=task.goal,
                role=task.role,
            )
            return task

    def start_task(self, id, policy):
        with self.transaction():
            task = self.task(id)
            snap = self.snapshot(task.run_id)
            if snap.stop_reason or task.status != "QUEUED":
                raise ValueError("Task cannot start")
            if (
                sum(t.status == "RUNNING" for t in snap.tasks)
                >= policy.max_concurrent_workers
            ):
                raise ValueError("CONCURRENCY_LIMIT")
            task.status = "RUNNING"
            task.version += 1
            task.attempt += 1
            task.cost_usd = None
            self._put(
                task.run_id,
                "attempt_usage",
                f"{id}:{task.version}",
                {"task_id": id, "version": task.version, "cost_usd": None},
            )
            self._put(task.run_id, "task", id, task.model_dump())
            self._event(
                task.run_id,
                "task.started",
                task_id=id,
                parent_id=task.parent_id,
                wave=task.wave,
                depth=task.depth,
            )
            return task

    def record_usage(self, id, expected_version, cost_usd):
        """Usage is independent of whether a late result may change findings."""
        import math

        if cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0):
            raise ValueError("Invalid attempt usage")
        with self.transaction():
            task = self.task(id)
            key = f"{id}:{expected_version}"
            usage = self._get(task.run_id, "attempt_usage", key)
            if usage["cost_usd"] is not None:
                if cost_usd is not None and usage["cost_usd"] != cost_usd:
                    raise ValueError("Conflicting usage for the same attempt")
                return
            if cost_usd is None:
                return
            usage["cost_usd"] = cost_usd
            self._put(task.run_id, "attempt_usage", key, usage)
            attempts = [
                u["cost_usd"]
                for u in self._list(task.run_id, "attempt_usage")
                if u["task_id"] == id
            ]
            task.cost_usd = None if any(c is None for c in attempts) else sum(attempts)
            self._put(task.run_id, "task", id, task.model_dump())
            self._event(task.run_id, "task.usage", task_id=id, cost_usd=task.cost_usd)

    def record_result(self, id, result: WorkerResult, expected_version: int):
        self.record_usage(id, expected_version, result.cost_usd)
        with self.transaction():
            task = self.task(id)
            run = task.run_id
            if (
                self.snapshot(run).stop_reason
                or task.status != "RUNNING"
                or task.version != expected_version
            ):
                raise ValueError("Stale or stopped task result")
            fids = []
            lids = []
            for finding in result.findings:
                if finding.hypothesis_id != task.hypothesis_id:
                    raise ValueError("Finding outside task hypothesis")
                for sid in finding.span_ids:
                    self._get(run, "span", sid)
                fid = digest(
                    [id, finding.statement, finding.span_ids, finding.relation]
                )[:24]
                f = finding.model_copy(
                    update={
                        "id": fid,
                        "task_id": id,
                        "reviewed": False,
                        "review_note": "",
                    }
                )
                self._put(run, "finding", fid, f.model_dump())
                fids.append(fid)
            for lead in result.leads:
                if lead.hypothesis_id != task.hypothesis_id:
                    raise ValueError("Lead outside task hypothesis")
                span = EvidenceSpan.model_validate(self._get(run, "span", lead.span_id))
                url = urlsplit(lead.url)
                if (
                    url.scheme not in ("http", "https")
                    or not url.hostname
                    or url.username
                    or url.password
                ):
                    raise ValueError("Invalid lead URL")
                if lead.url not in span.text and lead.url != span.url:
                    raise ValueError("Lead URL not present in cited source")
                canonical = urlunsplit(
                    (
                        url.scheme,
                        url.netloc.lower(),
                        url.path.rstrip("/"),
                        url.query,
                        "",
                    )
                )
                lid = digest([run, lead.hypothesis_id, canonical])[:24]
                existing = next(
                    (item for item in self._list(run, "lead") if item["id"] == lid),
                    None,
                )
                if not existing:
                    saved_lead = lead.model_copy(
                        update={
                            "id": lid,
                            "url": canonical,
                            "discovered_by": id,
                            "status": "NEW",
                        }
                    )
                    self._put(run, "lead", lid, saved_lead.model_dump())
                    self._event(
                        run,
                        "lead.discovered",
                        lead_id=lid,
                        task_id=id,
                        hypothesis_id=saved_lead.hypothesis_id,
                        url=canonical,
                        reason=saved_lead.reason,
                    )
                lids.append(lid)
            self._put(
                run,
                "child_proposals",
                id,
                {
                    "task_id": id,
                    "proposals": [p.model_dump() for p in result.children],
                    "consumed": False,
                },
            )
            task.status = "COMPLETED" if result.status == "completed" else "BLOCKED"
            task.version += 1
            self._put(run, "task", id, task.model_dump())
            self._event(
                run,
                "task.finished",
                task_id=id,
                status=task.status,
                unresolved=result.unresolved,
                cost_usd=result.cost_usd,
            )
            return BoardDelta(task_id=id, finding_ids=fids, lead_ids=lids)

    def stop(self, run, reason):
        with self.transaction():
            row = self.db.execute(
                "SELECT stop_reason FROM research_runs WHERE id=?", (run,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown run")
            if row["stop_reason"]:
                return
            self.db.execute(
                "UPDATE research_runs SET stop_reason=? WHERE id=?", (reason, run)
            )
            for raw in self._list(run, "task"):
                if raw["status"] in ("QUEUED", "RUNNING"):
                    raw["status"] = "CANCELLED" if reason == "CANCELLED" else "BLOCKED"
                    raw["version"] += 1
                    self._event(
                        run,
                        "task.finished",
                        task_id=raw["id"],
                        status=raw["status"],
                        reason=reason,
                        cost_usd=raw["cost_usd"],
                    )
                    self._put(run, "task", raw["id"], raw)
            self._event(run, "research.stopped", reason=reason)

    def snapshot(self, run):
        row = self.db.execute(
            "SELECT * FROM research_runs WHERE id=?", (run,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown run")
        return BoardSnapshot(
            run_id=run,
            goal=row["goal"],
            stop_reason=row["stop_reason"],
            hypotheses=self._list(run, "hypothesis"),
            tasks=self._list(run, "task"),
            findings=self._list(run, "finding"),
            leads=self._list(run, "lead"),
            spans=self._list(run, "span"),
            recoveries=self._list(run, "recovery"),
            source_hashes={
                row["id"]: json.loads(row["payload"])["hash"]
                for row in self.db.execute(
                    "SELECT id,payload FROM research_records WHERE run_id=? AND kind='source'",
                    (run,),
                )
            },
            entities=self._list(run, "entity"),
            relationships=self._list(run, "relationship"),
        )
