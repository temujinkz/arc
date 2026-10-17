"""Explicit cache-only architect repair. Never mounts source MCP tools."""

import argparse
import asyncio
import fcntl
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from neo4j import AsyncGraphDatabase

from backend.agent import cache_namespace, upstream_registry
from backend.graph_architect import execute_graph_architect_pass
from backend.investigations import Investigation
from backend.source_cache import SourceCache, request_key
from backend.storage import RunStore, database_path
from backend.tool_outputs import normalize_output, persisted_output_path


async def rebuild(session_id: str):
    path = database_path()
    with path.with_suffix(".api.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store, cache = RunStore(path), SourceCache(path)
        try:
            snapshot = store.load(session_id)
            if not snapshot or not snapshot["done"]:
                raise ValueError("A completed, inactive investigation is required.")
            costs = [e.get("cost_usd") for e in snapshot["events"] if e["type"] == "agent.usage"]
            if not costs or any(c is None or not math.isfinite(c) or c < 0 for c in costs):
                raise ValueError("Known recorded usage is required.")
            remaining = snapshot["budget"] - sum(costs)
            if not math.isfinite(remaining) or remaining <= 0:
                raise ValueError("No remaining authorized budget.")
            backup = path.parent / "verification" / f"dossier-rebuild-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            backup.mkdir(parents=True, exist_ok=False)
            with sqlite3.connect(backup / "history.sqlite3") as destination:
                store.db.backup(destination)
            original_event_count = len(snapshot["events"])
            run = Investigation.restore(snapshot, store)
            servers, _ = upstream_registry()
            original = next(e for e in run.scratchpad.raw_evidence if e["source"] == "mcp__osint__scrape_linkedin_profile")
            key = request_key(cache_namespace("osint", servers["osint"]), "scrape_linkedin_profile", original["inputs"])
            row = cache.db.execute("SELECT * FROM source_cache WHERE cache_key=? AND status='ready'", (key,)).fetchone()
            if row is None:
                raise ValueError("Recovered profile is not cached. No scrape will be attempted.")
            data = normalize_output(json.loads(row["payload"]))
            if not isinstance(data, dict) or not data.get("experience"):
                raise ValueError("Cached profile is incomplete.")
            if persisted_output_path(normalize_output(original["data"])) and not any(
                e.get("supersedes_evidence_id") == original["evidence_id"] for e in run.scratchpad.raw_evidence
            ):
                recovered = {**original, "evidence_id": str(uuid4()), "tool_use_id": f"recovered_{uuid4().hex}",
                             "data": data, "observed_at": row["observed_at"],
                             "supersedes_evidence_id": original["evidence_id"],
                             "source_cache": {"hit": True, "observed_at": row["observed_at"], "key": key}}
                run.scratchpad.raw_evidence.append(recovered)
            # Restore the operator identity decision explicitly recorded in the
            # project conversation, not an inference from matching account names.
            if session_id == "543e4074-d81b-4ec5-993d-d23c36794c83" and not run.scratchpad.human_decisions:
                run.scratchpad.human_decisions.append({
                    "request_id": "restored-operator-identity-confirmation",
                    "question": "Which public person and GitHub account did the operator select?",
                    "answer": {"text": "The operator selected https://www.linkedin.com/in/pawan-software-developer/ and confirmed https://github.com/Jackpkn belongs to the same person. This is operator confirmation, not independent identity verification."},
                    "provenance": "Restored from the user's original project conversation."})
            async with AsyncGraphDatabase.driver(os.environ["NEO4J_URI"], auth=(
                os.getenv("NEO4J_USERNAME") or os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])) as driver:
                async with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
                    old = await session.run("MATCH (r:ARCInvestigation {session_id:$sid}) RETURN properties(r) AS run", sid=session_id)
                    (backup / "public-run-before.json").write_text(json.dumps(await old.data(), default=str, ensure_ascii=False))
                    old_nodes = await session.run("MATCH (n:ARCNode {session_id:$sid}) RETURN properties(n) AS node, labels(n) AS labels", sid=session_id)
                    (backup / "public-nodes-before.json").write_text(json.dumps(await old_nodes.data(), default=str, ensure_ascii=False))
                    old_edges = await session.run("MATCH (a:ARCNode {session_id:$sid})-[r]->(b:ARCNode {session_id:$sid}) RETURN a.node_id AS source, b.node_id AS target, type(r) AS relationship, properties(r) AS properties", sid=session_id)
                    (backup / "public-edges-before.json").write_text(json.dumps(await old_edges.data(), default=str, ensure_ascii=False))
            run.done = False
            run.scratchpad.emit({"type": "agent.status", "stage": "cached_rebuild", "text": "Rebuilding the dossier from recovered cached evidence. No sources were scraped."})
            try:
                await execute_graph_architect_pass(run.scratchpad, remaining, target=run.target)
                run.scratchpad.emit({"type": "investigation.complete"})
            except BaseException:
                run.chat_blocked = True
                run.scratchpad.emit({"type": "investigation.failed", "phase": "cached_rebuild", "error": "Cached architect rebuild did not complete; no automatic retry."})
                raise
            finally:
                run.finish()
            graph = next(e["graph"] for e in reversed(run.events) if e["type"] == "graph.final_commit")
            new_cost = sum(e["cost_usd"] for e in run.events[original_event_count:] if e["type"] == "agent.usage" and e.get("cost_usd") is not None)
            receipt = {"session_id": session_id, "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                       "new_reported_cost_usd": new_cost, "source_calls": 0, "backup": str(backup)}
            (backup / "receipt.json").write_text(json.dumps(receipt))
            return receipt
        finally:
            cache.close()
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--execute-authorized-architect", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(rebuild(args.session_id))))
