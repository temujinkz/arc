"""Private on-device journal. This module never imports the public graph writer."""

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.storage import database_path


def vault_path() -> Path:
    path = Path(os.getenv("ARC_PRIVATE_VAULT_PATH", str(Path(__file__).resolve().parents[1] / ".arc/private/vault.sqlite3"))).resolve()
    if path == database_path():
        raise ValueError("Private vault must be separate from research/cache storage.")
    return path


class OwnershipError(ValueError):
    pass


class Vault:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS claims (
                profile_id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0, approved_revision INTEGER,
                approved_context TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS journal (
                entry_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES claims(profile_id),
                kind TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbox (
                request_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES claims(profile_id),
                kind TEXT NOT NULL, question TEXT NOT NULL, reply TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def claimed(self, profile_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM claims WHERE profile_id=?", (profile_id,)).fetchone() is not None

    def authorize(self, profile_id: str, token: str | None):
        row = self.db.execute("SELECT * FROM claims WHERE profile_id=?", (profile_id,)).fetchone()
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        if row is None or not token or not secrets.compare_digest(row["owner_hash"], digest):
            raise OwnershipError("This journal is available only to the browser that claimed it on this device.")
        return row

    def claim(self, profile_id: str, token: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO claims(profile_id,owner_hash,created_at) VALUES (?,?,?)",
                (profile_id, hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat()))
        self.authorize(profile_id, token)

    def owner_view(self, profile_id: str, token: str) -> dict:
        claim = self.authorize(profile_id, token)
        entries = [dict(row) for row in self.db.execute("SELECT entry_id,kind,text,created_at FROM journal WHERE profile_id=? ORDER BY rowid DESC", (profile_id,))]
        return {"entries": entries, "revision": claim["revision"],
                "approved_revision": claim["approved_revision"],
                "approved_context": json.loads(claim["approved_context"]),
                "inbox": [dict(row) for row in self.db.execute("SELECT request_id,kind,question,reply,created_at FROM inbox WHERE profile_id=? ORDER BY rowid DESC", (profile_id,))]}

    def add_entry(self, profile_id: str, token: str, entry_id: str, kind: str, text: str):
        self.authorize(profile_id, token)
        with self.db:
            previous = self.db.execute("SELECT * FROM journal WHERE entry_id=?", (entry_id,)).fetchone()
            if previous:
                if (previous["profile_id"], previous["kind"], previous["text"]) != (profile_id, kind, text):
                    raise ValueError("Entry ID already used for different content.")
                return
            self.db.execute("INSERT INTO journal VALUES (?,?,?,?,?)", (entry_id, profile_id, kind, text, datetime.now(timezone.utc).isoformat()))
            self.db.execute("UPDATE claims SET revision=revision+1 WHERE profile_id=?", (profile_id,))

    def delete_entry(self, profile_id: str, token: str, entry_id: str):
        self.authorize(profile_id, token)
        with self.db:
            deleted = self.db.execute("DELETE FROM journal WHERE entry_id=? AND profile_id=?", (entry_id, profile_id))
            if deleted.rowcount:
                self.db.execute("UPDATE claims SET revision=revision+1 WHERE profile_id=?", (profile_id,))

    def approve(self, profile_id: str, token: str, revision: int, statements: list[str]):
        self.authorize(profile_id, token)
        with self.db:
            updated = self.db.execute("UPDATE claims SET approved_context=?, approved_revision=? WHERE profile_id=? AND revision=?",
                (json.dumps(statements, ensure_ascii=False), revision, profile_id, revision))
            if updated.rowcount != 1:
                raise ValueError("Journal changed. Review the current notes before approving representation.")

    def representation(self, profile_id: str) -> dict | None:
        # Deliberately never reads journal text. Public handlers use only this view.
        row = self.db.execute("SELECT revision,approved_revision,approved_context FROM claims WHERE profile_id=?", (profile_id,)).fetchone()
        if not row:
            return None
        ready = row["approved_revision"] is not None and row["approved_revision"] == row["revision"]
        return {"ready": ready, "revision": row["revision"], "statements": json.loads(row["approved_context"]) if ready else []}

    def reply(self, profile_id: str, request_id: str, kind: str, question: str, answer: dict | None = None):
        with self.db:
            previous = self.db.execute("SELECT * FROM inbox WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if (previous["profile_id"], previous["kind"], previous["question"]) != (profile_id, kind, question):
                    raise ValueError("Request ID already used for different content.")
                return json.loads(previous["reply"])
            if answer is not None:
                self.db.execute("INSERT INTO inbox VALUES (?,?,?,?,?,?)", (request_id, profile_id, kind, question,
                    json.dumps(answer), datetime.now(timezone.utc).isoformat()))
                return answer


@contextmanager
def open_vault():
    vault = Vault(vault_path())
    try:
        yield vault
    finally:
        vault.close()
