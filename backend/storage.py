"""Local transactional history. No provider or agent work occurs during recovery."""

import json
import os
import sqlite3
from pathlib import Path


def database_path() -> Path:
    return Path(os.getenv("ARC_DATABASE_PATH", str(Path(__file__).resolve().parents[1] / ".arc/arc.sqlite3"))).resolve()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


class RunStore:
    def __init__(self, path: Path):
        self.db = connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS investigations (
                session_id TEXT PRIMARY KEY, target TEXT NOT NULL,
                state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_events (
                session_id TEXT NOT NULL REFERENCES investigations(session_id),
                sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (session_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS investigation_evidence (
                session_id TEXT NOT NULL REFERENCES investigations(session_id),
                evidence_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (session_id, evidence_id)
            );
        """)

    def save(self, run) -> None:
        pad = run.scratchpad
        state = {key: getattr(run, key) for key in (
            "done", "budget", "chat_session_id", "chat_cost", "chat_blocked",
            "chat_requests", "refresh_sources",
            "cached_source_session_id", "research_case_path", "research_run_id",
            "collection_mode", "source_requests",
            "chat_evidence_ids",
        )}
        state["human_decisions"] = pad.human_decisions
        created = run.events[0].get("timestamp", 0) if run.events else 0
        updated = run.events[-1].get("timestamp", created) if run.events else created
        with self.db:
            self.db.execute("""INSERT INTO investigations VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET state=excluded.state,
                updated_at=excluded.updated_at""",
                (pad.session_id, run.target, json.dumps(state), created, updated))
            self.db.executemany("INSERT INTO investigation_events VALUES (?, ?, ?)", [
                (pad.session_id, index + 1, json.dumps(event, ensure_ascii=False))
                for index, event in enumerate(run.events[run.saved_events:], run.saved_events)
            ])
            self.db.executemany("INSERT INTO investigation_evidence VALUES (?, ?, ?)", [
                (pad.session_id, e["evidence_id"], json.dumps(e, ensure_ascii=False))
                for e in pad.raw_evidence[run.saved_evidence:]
            ])
        run.saved_events = len(run.events)
        run.saved_evidence = len(pad.raw_evidence)

    def load(self, session_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM investigations WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": session_id, "target": row["target"], **json.loads(row["state"]),
                "events": [json.loads(e[0]) for e in self.db.execute(
                    "SELECT payload FROM investigation_events WHERE session_id=? ORDER BY sequence", (session_id,))],
                "evidence": [json.loads(e[0]) for e in self.db.execute(
                    "SELECT payload FROM investigation_evidence WHERE session_id=? ORDER BY rowid", (session_id,))]}

    def history(self) -> list[dict]:
        return [{"session_id": r["session_id"], "target": r["target"],
                 "active": not json.loads(r["state"])["done"],
                 "created_at": r["created_at"], "updated_at": r["updated_at"]}
                for r in self.db.execute("SELECT * FROM investigations ORDER BY updated_at DESC, rowid DESC")]

    def close(self):
        self.db.close()
