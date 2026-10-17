"""Import a saved local investigation and source snapshots without network calls."""

import argparse
import fcntl
import json
from pathlib import Path

from backend.agent import upstream_registry, cache_namespace
from backend.investigations import Investigation
from backend.source_cache import SourceCache, cacheable
from backend.storage import RunStore, database_path


def seed_source(cache, servers, finding, *, data=None):
    parts = finding["source"].split("__", 2)
    if len(parts) != 3 or parts[1] not in {"osint", "firecrawl", "github"} or parts[1] not in servers:
        return False
    value = finding["data"] if data is None else data
    payload = {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}], "isError": False}
    if not isinstance(value, str):
        payload["structuredContent"] = value if isinstance(value, dict) else {"result": value}
    if not cacheable(payload):
        return False
    key, entry = cache.claim(cache_namespace(parts[1], servers[parts[1]]), parts[2], finding["inputs"])
    if not entry.get("hit"):
        cache.complete(key, entry["owner"], payload, finding["observed_at"])
    return True


def import_snapshot(path: Path, budget: float, recovered_profile: Path | None = None):
    snapshot = json.loads(path.read_text())
    if not snapshot["events"] or snapshot["events"][-1]["type"] != "investigation.complete":
        raise ValueError("Legacy import accepts completed investigations only; do not restart unfinished work.")
    dbpath = database_path()
    dbpath.parent.mkdir(parents=True, exist_ok=True)
    with dbpath.with_suffix(".api.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store, cache = RunStore(dbpath), SourceCache(dbpath)
        try:
            existing = store.load(snapshot["session_id"])
            if existing:
                if existing["events"] != snapshot["events"] or existing["evidence"] != snapshot["evidence"]:
                    raise ValueError("Stored history differs; refusing to overwrite it.")
            else:
                run = Investigation.restore({**snapshot, "done": True, "budget": budget}, store)
                run.saved_events = run.saved_evidence = 0
                run.persist()
            servers, _ = upstream_registry()
            seeded = sum(seed_source(cache, servers, e) for e in snapshot["evidence"])
            if recovered_profile:
                profile = next(e for e in snapshot["evidence"] if e["source"] == "mcp__osint__scrape_linkedin_profile")
                seeded += seed_source(cache, servers, profile, data=json.loads(recovered_profile.read_text()))
            return {"session_id": snapshot["session_id"], "events": len(snapshot["events"]),
                    "evidence_records": len(snapshot["evidence"]), "sources_seeded": seeded}
        finally:
            cache.close()
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--recovered-profile", type=Path)
    args = parser.parse_args()
    if not 0 < args.budget < float("inf"):
        parser.error("budget must be positive and finite")
    print(json.dumps(import_snapshot(args.snapshot, args.budget, args.recovered_profile)))
