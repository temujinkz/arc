"""One explicitly authorized cache-only model evaluation; never a live investigation."""

import argparse
import asyncio
from contextlib import ExitStack, closing, contextmanager
import json
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

import httpx
from dotenv import dotenv_values

from backend.replay_research import import_case, load_cached_records
from backend.storage import database_path
from backend.research.blackboard import Blackboard
from backend.research.models import RunPolicy, TaskProposal
from backend.research.scheduler import ResearchGoal, run_research
from backend.research.projection import (
    PortraitEvidencePacket,
    project_case,
    synthesize_portrait,
)
from backend.research.eval_model import (
    MODEL,
    MAX_CALLS,
    RESERVATION_USD,
    EvaluationLedger,
    EvaluationModel,
    EvaluationStopped,
    ModelOnlyTransport,
    save_json,
)
from backend.research.eval_adapters import (
    ExtractedClaims,
    validate_extracted_coverage,
    CachedModelWorker,
    CachedModelCritic,
    CachedPortrait,
)

SESSION = "543e4074-d81b-4ec5-993d-d23c36794c83"


@contextmanager
def evaluation_guard(source_db, case_db):
    """Permit only Anthropic TLS connections, read-only history and the temp case."""
    original_connect, original_dns = socket.socket.connect, socket.getaddrinfo
    original_sqlite = sqlite3.connect
    addresses = set()

    def forbidden(*args, **kwargs):
        raise EvaluationStopped("Source providers, subprocesses and Neo4j are disabled")

    def dns(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name != "api.anthropic.com" and name not in addresses:
            raise EvaluationStopped("DNS outside model-only allowlist")
        if port not in (443, "443", "https"):
            raise EvaluationStopped("Non-TLS model destination")
        result = original_dns(host, port, *args, **kwargs)
        addresses.update(item[4][0] for item in result)
        return result

    def connect(sock, address):
        if (
            not isinstance(address, tuple)
            or address[0] not in addresses
            or address[1] != 443
        ):
            raise EvaluationStopped("Socket destination outside model-only allowlist")
        return original_connect(sock, address)

    def scoped_sqlite(database, *args, **kwargs):
        name = str(database)
        allowed_read = name == f"file:{source_db.resolve()}?mode=ro" and kwargs.get(
            "uri"
        )
        if not allowed_read and Path(name).resolve() != case_db.resolve():
            raise EvaluationStopped(
                "Private/unrelated or writable production database forbidden"
            )
        return original_sqlite(database, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch("socket.getaddrinfo", dns))
        stack.enter_context(patch("socket.socket.connect", connect))
        stack.enter_context(patch("sqlite3.connect", scoped_sqlite))
        for name in (
            "socket.socket.connect_ex",
            "socket.socket.sendto",
            "subprocess.Popen",
            "neo4j.AsyncGraphDatabase.driver",
            "neo4j.GraphDatabase.driver",
        ):
            stack.enter_context(patch(name, side_effect=forbidden))
        yield


def seed_packet(board, run, records, reader):
    """Select project-bearing source passages; the model, not this selector, extracts claims."""
    profile = next(
        r for r in records if r["source"] == "mcp__osint__scrape_linkedin_profile"
    )
    posts = next(
        r for r in records if r["source"] == "mcp__osint__scrape_linkedin_posts"
    )
    sources = {
        r["evidence_id"]: board._get(run, "source", r["evidence_id"])["data"]["view"]
        for r in records
    }
    primary = []
    experience = sources[profile["evidence_id"]]["profile"]["experience"]
    for i, role in enumerate(experience):
        if "Stela" in json.dumps(role):
            from backend.research.blackboard import encoded

            primary.append(
                reader.add_span(
                    profile["evidence_id"],
                    f"/view/profile/experience/{i}",
                    encoded(role),
                )
            )
            break
    for i, post in enumerate(sources[posts["evidence_id"]]):
        content = post.get("text", "")
        if (
            "Flightdeck" in content
            or "Open Agent Network" in content
            or "Open-Agent-Network" in content
        ):
            primary.append(
                reader.add_span(posts["evidence_id"], f"/view/{i}/text", content)
            )
    primary = primary[:3]
    if len(primary) != 3:
        raise ValueError("Reviewed cache layout changed; no paid extraction")
    all_ids = {s.id for s in board.snapshot(run).spans}
    payload = {
        "subject": "Pawan Kumar, operator-selected LinkedIn pawan-software-developer",
        "scope": "Stela, Flightdeck and Open Agent Network; cached public statements only",
        "passages": [s.model_dump() for s in primary],
        "excluded_span_count": len(all_ids - {s.id for s in primary}),
        "selection_limitation": "Project-specific evaluation selected source passages, not a complete unbiased profile search. Full exclusion manifest remains in selection-manifest.json.",
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 12000:
        raise ValueError("Claim extraction packet exceeds 12000 bytes")
    return payload, sorted(all_ids - {s.id for s in primary})


def cached_baselines(source_db, session):
    with closing(
        sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
    ) as db:
        rows = db.execute(
            "SELECT payload FROM investigation_events WHERE session_id=? ORDER BY sequence",
            (session,),
        ).fetchall()
        state = db.execute(
            "SELECT state FROM investigations WHERE session_id=?", (session,)
        ).fetchone()
    graphs = [
        event["graph"]
        for (raw,) in rows
        if (event := json.loads(raw)).get("type") == "graph.final_commit"
    ]
    return {
        "original_one_pass": graphs[0] if graphs else None,
        "latest_cached_repaired": graphs[-1] if graphs else None,
    }, json.loads(state[0]).get("human_decisions", []) if state else []


async def evaluate(session, output_dir, *, authorized=False):
    if session != SESSION:
        raise ValueError(
            "This controlled evaluation supports only the approved cached session"
        )
    output_dir = output_dir.resolve()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    source_db, case_db = database_path(), output_dir / "case.sqlite3"
    # Read only the model credential; no scraper credentials are forwarded to the adapter.
    api_key = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(
        Path(__file__).resolve().parents[1] / ".env"
    ).get("ANTHROPIC_API_KEY")
    if authorized and not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured")
    with evaluation_guard(source_db, case_db), Blackboard(case_db) as board:
        run = "model-eval-" + session
        goal = "Evaluate three technical claims about the operator-selected Pawan Kumar using cached evidence only"
        board.create_run(run, goal)
        records, excluded = load_cached_records(source_db, session)
        reader = import_case(board, run, records)
        baseline, decisions = cached_baselines(source_db, session)
        save_json(output_dir / "baseline-dossiers.json", baseline)
        with board.transaction():
            board._put(
                run,
                "operator_context",
                "identity",
                {
                    "decisions": decisions,
                    "provenance": "Operator context, not independent verification",
                },
            )
        seed, manifest = seed_packet(board, run, records, reader)
        save_json(
            output_dir / "selection-manifest.json",
            {
                "excluded_span_ids": manifest,
                "included_span_ids": [s["id"] for s in seed["passages"]],
                "superseded_sources": excluded,
            },
        )
        save_json(output_dir / "extraction-packet.json", seed)
        preparation = {
            "model": MODEL,
            "session_id": session,
            "cached_sources": len(records),
            "max_calls": MAX_CALLS,
            "max_reserved_usd": MAX_CALLS * RESERVATION_USD,
            "source_calls": 0,
            "neo4j_writes": 0,
            "private_vault_reads": 0,
            "input_rates_verified": "2026-10-15",
            "adapter": "single-response Messages API behind ResearchWorker; application scheduler owns recursion",
        }
        save_json(output_dir / "preparation.json", preparation)
        if not authorized:
            return {**preparation, "status": "prepared_only", "model_calls": 0}
        ledger = EvaluationLedger(output_dir)
        transport = ModelOnlyTransport(
            httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(600.0, connect=10.0),
        ) as client:
            model = EvaluationModel(client, ledger)
            worker, critic = CachedModelWorker(model, board), CachedModelCritic(model)
            error = None
            portrait = None
            try:
                extracted, _ = await model.generate(
                    "extract_claims",
                    "Extract exactly three specific, testable technical claims, one per project, from these "
                    "cached passages. Preserve author attribution and important qualifications. Select consequential "
                    "claims that could be corroborated with implementation, benchmarks or contribution evidence; "
                    "do not reduce every claim to 'a post exists'. No outside knowledge. Cite exact supplied "
                    "span_ids and state what remains unverified. This is extraction, not verification.",
                    seed,
                    ExtractedClaims,
                )
                seed_ids = {s["id"] for s in seed["passages"]}
                if any(not set(c.span_ids) <= seed_ids for c in extracted.claims):
                    raise ValueError("Extraction cited unknown source passage")
                validate_extracted_coverage(extracted,[{"span_ids":[s["id"]]} for s in seed["passages"]])
                save_json(
                    output_dir / "extracted-hypotheses.json", extracted.model_dump()
                )
                initial = []
                for priority, claim in enumerate(extracted.claims):
                    h = board.add_hypothesis(
                        run, "Pawan Kumar", claim.statement, priority
                    )
                    initial.append(
                        TaskProposal(
                            hypothesis_id=h.id,
                            goal="Assess this specific claim from cached evidence, preserving attribution and limitations",
                            role="identity",
                            span_ids=claim.span_ids,
                        )
                    )
                policy = RunPolicy(
                    max_input_tokens=12000,
                    max_concurrent_workers=1,
                    execution_mode="authorized_model",
                )
                board.emit(
                    run,
                    "research.evaluation",
                    offline=False,
                    sources_offline=True,
                    max_cost_usd=0.10,
                    model=MODEL,
                )
                outcome = await run_research(
                    ResearchGoal(
                        run_id=run,
                        target=goal,
                        initial_tasks=initial,
                        remaining_budget=0.10 - ledger.total_cost,
                    ),
                    board,
                    worker,
                    policy,
                    critic=critic,
                )
                accepted = [f for f in outcome.findings if f.reviewed]
                selected = []
                selected_ids = set()
                for f in accepted:
                    new_ids = set(f.span_ids) | selected_ids
                    candidate = PortraitEvidencePacket(
                        findings=selected + [f],
                        spans=[reader.read_span(s) for s in sorted(new_ids)],
                    )
                    if (
                        len(candidate.model_dump_json().encode())
                        <= candidate.max_input_bytes
                    ):
                        selected.append(f)
                        selected_ids = new_ids
                save_json(
                    output_dir / "portrait-selection.json",
                    {
                        "included_findings": [f.id for f in selected],
                        "excluded_findings": [
                            f.id for f in accepted if f not in selected
                        ],
                    },
                )
                if (
                    selected
                    and not ledger.halted
                    and not any(c["status"] == "pending" for c in ledger.calls)
                ):
                    packet = PortraitEvidencePacket(
                        findings=selected,
                        spans=[reader.read_span(s) for s in sorted(selected_ids)],
                    )
                    portrait = await synthesize_portrait(packet, CachedPortrait(model))
                    save_json(output_dir / "portrait.json", portrait.model_dump())
            except Exception as exc:
                error = f"{type(exc).__name__}: evaluation stopped; inspect saved request/response artifacts. No retry."
                save_json(output_dir / "error.json", {"error": error})
            outcome = board.snapshot(run)
            draft = project_case(outcome)
            draft.limitation = "One cache-only live model evaluation. Semantic quality requires manual review; no independent ground truth, live sources or Neo4j commit."
            if portrait and draft.graph:
                draft.graph.portrait = portrait.sections
                draft.missing_portrait_sections = portrait.missing_sections
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
            summary = {
                **preparation,
                "status": "partial" if error else "completed",
                "error": error,
                "stop_reason": outcome.stop_reason,
                "model_calls": len(ledger.calls),
                "total_cost_usd": ledger.total_cost,
                "unknown_usage": any(c["status"] == "pending" for c in ledger.calls),
                "waves": max((t.wave for t in outcome.tasks), default=0),
                "tasks": len(outcome.tasks),
                "reviewed_findings": sum(f.reviewed for f in outcome.findings),
                "nodes": len(draft.graph.nodes) if draft.graph else 0,
                "edges": len(draft.graph.edges) if draft.graph else 0,
                "coverage": draft.coverage,
                "missing_portrait_sections": draft.missing_portrait_sections,
            }
            tokens = {
                "measurement": "Worker packet bytes and provider-reported request tokens are separate measurements",
                "packets": [
                    {
                        "task_id": p["task_id"],
                        "input_bytes": p["token_upper_bound"],
                        "included_span_ids": [s["id"] for s in p["spans"]],
                        "excluded_span_ids": p["excluded_span_ids"],
                        "excluded_span_count": p["excluded_span_count"],
                        "manifest_id": p["manifest_id"],
                        "missing_span_ids": p["missing_span_ids"],
                    }
                    for p in worker.packets
                ],
                "model_calls": len(ledger.calls),
                "usage": ledger.calls,
            }
            save_json(output_dir / "case-snapshot.json", outcome.model_dump())
            save_json(output_dir / "graph-preview.json", draft.model_dump())
            save_json(output_dir / "summary.json", summary)
            save_json(output_dir / "token-size-report.json", tokens)
            (output_dir / "event-trace.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
            )
            save_json(
                output_dir / "evaluation.json",
                {
                    "summary": summary,
                    "events": events,
                    "preview": draft.model_dump(),
                    "token_report": tokens,
                },
            )
            return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--authorize-model", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                evaluate(
                    args.session_id, args.output_dir, authorized=args.authorize_model
                )
            ),
            indent=2,
        )
    )
