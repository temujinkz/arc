"""Deterministic cached acceptance replay. No model, scraper or Neo4j execution."""

import argparse
import asyncio
from contextlib import contextmanager, ExitStack, closing, nullcontext
import json
from pathlib import Path
import re
import sqlite3
from unittest.mock import patch

from backend.storage import database_path
from backend.tool_outputs import model_output, persisted_output_path
from backend.research.blackboard import Blackboard, encoded
from backend.research.evidence import EvidenceReader
from backend.research.models import (
    RunPolicy,
    TaskProposal,
    WorkerResult,
    Finding,
    Lead,
    ReviewedEntity,
)
from backend.research.workers import ReplayWorker
from backend.research.scheduler import ResearchGoal, run_research
from backend.research.critic import CriticDecision, FindingReview
from backend.research.recovery import ToolFailure, choose_recovery, recover_cached
from backend.research.projection import project_case


@contextmanager
def offline_guard(source_db: Path, case_db: Path):
    """Fail closed at model, process, network and SQLite adapter boundaries."""
    connect = sqlite3.connect

    def forbidden(*args, **kwargs):
        raise RuntimeError(
            "ARC offline replay forbids model/provider/network execution"
        )

    def scoped_connect(database, *args, **kwargs):
        name = str(database)
        if name.startswith("file:"):
            path = Path(name[5:].split("?", 1)[0]).resolve()
            if (
                path != source_db.resolve()
                or "mode=ro" not in name
                or not kwargs.get("uri")
            ):
                raise RuntimeError(
                    "ARC offline source access must be explicitly read-only"
                )
        elif Path(name).resolve() != case_db.resolve():
            raise RuntimeError(
                "ARC offline replay forbids unrelated/private database access"
            )
        return connect(database, *args, **kwargs)

    with ExitStack() as stack:
        for name in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.socket.sendto",
            "subprocess.Popen",
            "claude_agent_sdk.ClaudeSDKClient.__init__",
            "neo4j.AsyncGraphDatabase.driver",
        ):
            stack.enter_context(patch(name, side_effect=forbidden))
        stack.enter_context(patch("sqlite3.connect", side_effect=scoped_connect))
        yield


def load_cached_records(path: Path, session_id: str):
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as db:
        rows = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM investigation_evidence WHERE session_id=?",
                (session_id,),
            )
        ]
    if not rows:
        raise ValueError("No cached evidence for this session")
    superseded = {
        r.get("supersedes_evidence_id") for r in rows if r.get("supersedes_evidence_id")
    }
    return [r for r in rows if r["evidence_id"] not in superseded], sorted(superseded)


def import_case(
    board,
    run,
    records,
    *,
    identity_url="https://www.linkedin.com/in/pawan-software-developer/",
):
    reader = EvidenceReader(board, run)
    for record in records:
        tool = record["source"]
        view = model_output(tool, record["data"])
        if persisted_output_path(view):
            continue
        inputs = record.get("inputs", {})
        url = (inputs.get("linkedin_url") or identity_url) if "linkedin" in tool else ""
        if not url and isinstance(inputs.get("url"), str):
            url = inputs["url"]
        if tool == "mcp__github__get_file_contents" and isinstance(view, str):
            match = re.search(
                r"repo://([^/]+/[^/]+)/sha/([^/]+)/contents/(\S+)\]", view
            )
            if match:
                url = f"https://github.com/{match[1]}/blob/{match[2]}/{match[3]}"
        kind = (
            "SELF_AUTHORED"
            if "linkedin" in tool or tool == "mcp__github__get_file_contents"
            else "UNCLASSIFIED"
        )
        board.add_source(
            run,
            record["evidence_id"],
            {"original": record["data"], "view": view},
            url=url,
            observed_at=record.get("observed_at", ""),
            source_kind=kind,
            origin_group="subject-self-authored" if kind == "SELF_AUTHORED" else tool,
        )
        reader.index_source(record["evidence_id"], "/view")
    return reader


async def replay(
    source_db: Path,
    session_id: str,
    output_dir: Path,
    *,
    event_sink=None,
    process_guard=True,
):
    if session_id != "543e4074-d81b-4ec5-993d-d23c36794c83":
        raise ValueError(
            "This acceptance fixture supports only the reviewed session 543e4074-d81b-4ec5-993d-d23c36794c83"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    case_db = output_dir / "case.sqlite3"
    if case_db.exists():
        raise ValueError(
            "Output case already exists. Choose a fresh output directory to preserve the previous replay."
        )
    # The standalone CLI guards its entire process. The API uses the fixed
    # deterministic adapters directly so global socket patches cannot interrupt
    # another request. There is no model/provider adapter in this function.
    with offline_guard(source_db, case_db) if process_guard else nullcontext():
        records, excluded = load_cached_records(source_db, session_id)
        with Blackboard(case_db, event_sink=event_sink) as board:
            run = "replay-" + session_id
            goal = "Review three cached technical claims about Pawan Kumar"
            board.create_run(run, goal)
            reader = import_case(board, run, records)
            with closing(
                sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
            ) as db:
                row = db.execute(
                    "SELECT state FROM investigations WHERE session_id=?", (session_id,)
                ).fetchone()
            decisions = json.loads(row[0]).get("human_decisions", []) if row else []
            with board.transaction():
                board._put(
                    run,
                    "operator_context",
                    "identity",
                    {
                        "decisions": decisions,
                        "provenance": "Original operator responses; not independent source proof",
                    },
                )
            board.emit(
                run,
                "research.identity_context",
                count=len(decisions),
                text="Original operator decisions retained separately from evidence support.",
            )
            board.emit(
                run,
                "research.replay",
                text="Deterministic reviewed replay, not live model reasoning. Source timestamps are original.",
                offline=True,
            )
            # This fixture is deliberately limited to the reviewed session, not a generic extractor.
            profile = next(
                r
                for r in records
                if r["source"] == "mcp__osint__scrape_linkedin_profile"
            )
            posts = next(
                r for r in records if r["source"] == "mcp__osint__scrape_linkedin_posts"
            )
            views = {
                r["evidence_id"]: board._get(run, "source", r["evidence_id"])["data"][
                    "view"
                ]
                for r in records
            }
            experience = views[profile["evidence_id"]]["profile"]["experience"]
            index = next(
                i
                for i, role in enumerate(experience)
                if role.get("companyName") == "AmoYou"
            )
            stela = reader.add_span(
                profile["evidence_id"],
                f"/view/profile/experience/{index}",
                encoded(experience[index]),
            )
            if "40%" not in stela.text or "Stela" not in stela.text:
                raise ValueError("Cached Stela fixture has changed")
            claim_specs = [
                (
                    "stela",
                    "Stela model routing reduced code-suggestion latency by 40%.",
                ),
                ("flightdeck", "Pawan built Flightdeck as a macOS developer cockpit."),
                ("oan", "Open Agent Network implements working agent payment escrow."),
            ]
            hypotheses = {
                key: board.add_hypothesis(run, "Pawan Kumar", statement, i)
                for i, (key, statement) in enumerate(claim_specs)
            }
            primary = {"stela": stela}
            links = {}
            readmes = {}
            urls = {}
            for key, repo in [
                ("flightdeck", "Flightdeck"),
                ("oan", "Open-Agent-Network"),
            ]:
                url = f"https://github.com/Jackpkn/{repo}"
                urls[key] = url
                pi = next(
                    i
                    for i, p in enumerate(views[posts["evidence_id"]])
                    if p.get("article", {}).get("url") == url
                )
                post = views[posts["evidence_id"]][pi]
                primary[key] = reader.add_span(
                    posts["evidence_id"], f"/view/{pi}/text", post["text"]
                )
                links[key] = reader.add_span(
                    posts["evidence_id"],
                    f"/view/{pi}/article",
                    encoded(post["article"]),
                )
                source = next(
                    r
                    for r in records
                    if r["source"] == "mcp__github__get_file_contents"
                    and f"repo://Jackpkn/{repo}/" in str(views[r["evidence_id"]])
                )
                text = views[source["evidence_id"]]
                end = text.find("\n## ")
                readmes[key] = reader.add_span(
                    source["evidence_id"], "/view", text[:end], 0, end
                )
            # Roles preserve semantic contexts. Other cached records stay available but are not proof.
            policy = RunPolicy(max_input_tokens=12000)
            seen_packets = []
            expected = {}

            def candidate(key, span, statement, relation="supports"):
                expected[statement] = [span.id]
                return Finding(
                    hypothesis_id=hypotheses[key].id,
                    statement=statement,
                    span_ids=[span.id],
                    relation=relation,
                )

            def resolve(task, packet):
                key = next(
                    k for k, h in hypotheses.items() if h.id == task.hypothesis_id
                )
                seen_packets.append(
                    {
                        "task_id": task.id,
                        "wave": task.wave,
                        "depth": task.depth,
                        "input_bytes": packet.token_upper_bound,
                        "included_span_ids": [s.id for s in packet.spans],
                        "excluded_span_ids": packet.excluded_span_ids,
                        "excluded_span_count": packet.excluded_span_count,
                        "manifest_id": packet.manifest_id,
                        "missing_span_ids": packet.missing_span_ids,
                    }
                )

                # Adapter consumes the same bounded packet contract as a model worker.
                def required(span):
                    if span.id not in {s.id for s in packet.spans}:
                        raise ValueError("Reviewed fixture span did not fit packet")

                if task.role == "identity":
                    required(primary[key])
                    statement = {
                        "stela": "The AmoYou role description claims 40% lower suggestion latency.",
                        "flightdeck": "The LinkedIn author says they built Flightdeck, a macOS app.",
                        "oan": "The LinkedIn author claims Open Agent Network has escrow contracts on Base Sepolia.",
                    }[key]
                    result = WorkerResult(
                        findings=[candidate(key, primary[key], statement)]
                    )
                    if key != "stela":
                        required(links[key])
                        result.leads = [
                            Lead(
                                hypothesis_id=task.hypothesis_id,
                                url=urls[key],
                                span_id=links[key].id,
                                reason="The selected profile post links this repository; inspect its claims, not assume ownership.",
                            )
                        ]
                        result.children = [
                            TaskProposal(
                                hypothesis_id=task.hypothesis_id,
                                goal=f"Inspect cached {key} README claims",
                                role="artifact",
                                span_ids=[readmes[key].id],
                            )
                        ]
                    return result
                if task.role == "artifact":
                    required(readmes[key])
                    failure = ToolFailure(
                        operation_id="simulated-read-" + key,
                        kind="INACCESSIBLE",
                        alternate_span_ids=[readmes[key].id],
                        simulated=True,
                    )
                    recovered = recover_cached(
                        board,
                        task,
                        failure,
                        choose_recovery(failure, task, board.snapshot(run), "offline"),
                    )
                    if not recovered:
                        return WorkerResult(
                            status="blocked", unresolved=["Cached README unavailable"]
                        )
                    return WorkerResult(
                        findings=[
                            candidate(
                                key,
                                readmes[key],
                                f"The cached {key} README describes its design; implementation, ownership and adoption remain unchecked.",
                            )
                        ]
                    )
                # Explicit missing-cache outcome, no provider request or synthetic corroboration.
                failure = ToolFailure(
                    operation_id="simulated-missing-" + key,
                    kind="EMPTY",
                    simulated=True,
                )
                recover_cached(
                    board,
                    task,
                    failure,
                    choose_recovery(failure, task, board.snapshot(run), "offline"),
                )
                return WorkerResult(
                    unresolved=[
                        "MISSING_CACHED_SOURCE: no independent benchmark, deployment audit or contribution history in this case."
                    ]
                )

            async def critic(report, local_reader):
                reviews = []
                for f in report.after.findings:
                    if f.reviewed:
                        continue
                    valid = expected.get(f.statement) == f.span_ids
                    for sid in f.span_ids:
                        local_reader.read_span(sid)
                    reviews.append(
                        FindingReview(
                            finding_id=f.id,
                            accepted=valid,
                            reason="Manually reviewed replay fixture: exact self-authored claim, not independent implementation proof."
                            if valid
                            else "Not in the reviewed fixture",
                        )
                    )
                proposals = []
                if report.wave == 1:
                    keys = ["stela", "flightdeck"]
                elif report.wave == 2:
                    keys = ["oan"]
                else:
                    keys = []
                for key in keys:
                    parents = [
                        t
                        for t in report.after.tasks
                        if t.hypothesis_id == hypotheses[key].id
                        and t.role != "corroboration"
                    ]
                    parent = max(parents, key=lambda t: t.depth)
                    proposals.append(
                        TaskProposal(
                            hypothesis_id=hypotheses[key].id,
                            goal=f"Check independent {key} proof in cache",
                            role="corroboration",
                            parent_id=parent.id,
                            span_ids=[primary[key].id],
                            wave=min(report.wave + 1, 3),
                        )
                    )
                entities = []
                for key in readmes:
                    findings = [
                        f
                        for f in report.after.findings
                        if f.span_ids == [readmes[key].id]
                    ]
                    if findings:
                        entities.append(
                            ReviewedEntity(
                                id="artifact_" + key,
                                title="Flightdeck"
                                if key == "flightdeck"
                                else "Open Agent Network",
                                kind="Artifact",
                                url=readmes[key].url,
                                finding_ids=[findings[0].id],
                                review_note="Repository README captured; author contribution and working deployment not independently established.",
                            )
                        )
                return CriticDecision(
                    reviews=reviews,
                    proposals=proposals,
                    entities=entities,
                    reason="All available support is self-authored. Missing independent tests, commits and deployment evidence remain explicit.",
                )

            initial = [
                TaskProposal(
                    hypothesis_id=hypotheses[key].id,
                    goal=f"Extract the cached {key} claim with qualifiers",
                    role="identity",
                    span_ids=[primary[key].id]
                    + ([links[key].id] if key in links else []),
                )
                for key in hypotheses
            ]
            outcome = await run_research(
                ResearchGoal(run_id=run, target=goal, initial_tasks=initial),
                board,
                ReplayWorker(resolve),
                policy,
                critic=critic,
            )
            draft = project_case(outcome)
            board.emit(
                run,
                "research.coverage",
                coverage=draft.coverage,
                missing_portrait_sections=draft.missing_portrait_sections,
            )
            if draft.graph:
                board.emit(
                    run,
                    "graph.research_preview",
                    graph=draft.graph.model_dump(),
                    committed=False,
                )
            events = board.events(run)
            report = {
                "measurement": "UTF-8 serialized packet bytes; conservative size proxy, not model token usage. System/tool schemas and future model overhead are not included.",
                "model_calls": 0,
                "actual_model_tokens": 0,
                "packet_byte_limit": policy.max_input_tokens,
                "raw_cached_data_bytes": sum(
                    len(encoded(r["data"]).encode()) for r in records
                ),
                "max_packet_bytes": max(
                    (p["input_bytes"] for p in seen_packets), default=0
                ),
                "packets": seen_packets,
            }
            accepted = {
                "cached_sources": len(records),
                "excluded_superseded": excluded,
                "waves": max((t.wave for t in outcome.tasks), default=0),
                "tasks": len(outcome.tasks),
                "max_depth": max((t.depth for t in outcome.tasks), default=0),
                "reviewed_findings": sum(f.reviewed for f in outcome.findings),
                "nodes": len(draft.graph.nodes) if draft.graph else 0,
                "edges": len(draft.graph.edges) if draft.graph else 0,
                "stop_reason": outcome.stop_reason,
                "model_calls": 0,
                "source_calls": 0,
                "neo4j_writes": 0,
                "private_vault_reads": 0,
                "peer_claim_writes": 0,
                "simulated_recovery": True,
                "coverage": draft.coverage,
                "missing_portrait_sections": draft.missing_portrait_sections,
                "limitations": draft.limitation,
            }
            assertions = {
                "task_bound": len(outcome.tasks) <= 8,
                "wave_bound": accepted["waves"] <= 3,
                "depth_bound": accepted["max_depth"] <= 2,
                "all_three_claims_unverified": len(outcome.hypotheses) == 3
                and all(h.support == "SELF_REPORTED_ONLY" for h in outcome.hypotheses),
                "bounded_packets": bool(seen_packets)
                and all(
                    p["input_bytes"] <= policy.max_input_tokens for p in seen_packets
                ),
                "five_reviewed_findings": accepted["reviewed_findings"] == 5,
                "cached_recovery_executed": any(
                    e["type"] == "tool.recovery" and e["state"] == "executed"
                    for e in events
                ),
                "cache_miss_recorded": any(
                    e["type"] == "tool.recovery"
                    and e["strategy"] == "MISSING_CACHED_SOURCE"
                    and e["state"] == "failed"
                    for e in events
                ),
            }
            accepted["checks"] = assertions
            bundle = {
                "version": 1,
                "offline": True,
                "summary": accepted,
                "events": events,
                "preview": draft.model_dump(),
                "token_report": report,
            }
            for name, value in [
                ("graph-preview.json", draft.model_dump()),
                ("token-size-report.json", report),
                ("summary.json", accepted),
                ("replay.json", bundle),
            ]:
                (output_dir / name).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n"
                )
            (output_dir / "event-trace.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
            )
            if not all(assertions.values()):
                raise ValueError("Offline acceptance failed; inspect summary.json")
            return accepted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(replay(database_path(), args.session_id, args.output_dir)),
            indent=2,
        )
    )
