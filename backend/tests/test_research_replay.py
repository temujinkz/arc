import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from backend.replay_research import offline_guard, load_cached_records


class ReplayTests(unittest.TestCase):
    def test_network_and_model_calls_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.sqlite3"
            with offline_guard(path, path):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    socket.create_connection(("example.com", 443))
                from claude_agent_sdk import ClaudeSDKClient

                with self.assertRaisesRegex(RuntimeError, "offline"):
                    ClaudeSDKClient()
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    sqlite3.connect("/tmp/private-vault.sqlite3")

    def test_superseded_evidence_is_excluded_and_source_db_unmodified(self):
        with tempfile.TemporaryDirectory() as directory:
            import json

            path = Path(directory) / "sources.sqlite3"
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE investigation_evidence(session_id TEXT,evidence_id TEXT,payload TEXT)"
            )
            for e in [
                {"evidence_id": "old", "source": "test", "data": "file notice"},
                {
                    "evidence_id": "new",
                    "source": "test",
                    "data": {"value": "original"},
                    "supersedes_evidence_id": "old",
                },
            ]:
                db.execute(
                    "INSERT INTO investigation_evidence VALUES(?,?,?)",
                    ("r", e["evidence_id"], json.dumps(e)),
                )
            db.commit()
            db.close()
            before = path.read_bytes()
            records, excluded = load_cached_records(path, "r")
            self.assertEqual([e["evidence_id"] for e in records], ["new"])
            self.assertEqual(excluded, ["old"])
            self.assertEqual(path.read_bytes(), before)
