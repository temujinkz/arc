"""Offline privacy-boundary checks. No provider calls or real profile mutations."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from backend import profile_routes, public_profiles, vault_routes
from backend.private_vault import open_vault

PROFILE = {"profile_id": "person", "name": "Example Builder", "graph": {"nodes": [
    {"label": "Person", "title": "Example Builder", "summary": "Public person"},
    {"label": "Artifact", "title": "Project", "summary": "Public repository", "url": "https://example.org/project", "evidence_state": "observed"}
]}}
SECRET = "PRIVATE-CANARY-82f3: confidential acquisition plans"


class PrivateWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"ARC_PRIVATE_VAULT_PATH": str(Path(self.temp.name) / "private/vault.sqlite3")})
        self.env.start()
        self.profile = patch.object(public_profiles, "get_profile", AsyncMock(return_value=PROFILE.copy()))
        self.owner_profile = patch.object(vault_routes, "get_profile", AsyncMock(return_value=PROFILE.copy()))
        self.profile.start(); self.owner_profile.start()
        self.app = FastAPI()
        self.app.include_router(vault_routes.router); self.app.include_router(profile_routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 12000)), base_url="http://localhost:8000", headers={"origin": "http://localhost:3000"})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.profile.stop(); self.owner_profile.stop(); self.env.stop(); self.temp.cleanup()

    async def setup_owner(self):
        self.assertEqual((await self.client.post("/api/vault/person/claim")).status_code, 200)
        self.assertEqual((await self.client.post("/api/vault/person/entries", json={"entry_id": str(uuid4()), "kind": "note", "text": SECRET})).status_code, 201)
        view = (await self.client.get("/api/vault/person")).json()
        self.assertIn(SECRET, json.dumps(view))
        self.assertEqual((await self.client.post("/api/vault/person/representation", json={"revision": view["revision"], "statements": ["Open to public developer-tool collaborations."]})).status_code, 200)
        return view

    async def test_owner_origin_and_cookie_boundary(self):
        self.assertEqual((await self.client.get("/api/vault/person")).status_code, 401)
        response = await self.client.post("/api/vault/person/claim", headers={"origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403)
        await self.setup_owner()
        self.assertEqual((await self.client.get("/api/vault/person", headers={"origin": "https://attacker.example"})).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get("/api/vault/person")).status_code, 401)
        self.assertEqual((await self.client.post("/api/vault/person/claim")).status_code, 409)
        public = await self.client.get("/api/profiles/person")
        self.assertNotIn(SECRET, public.text)
        self.assertTrue(public.json()["claimed"])

    async def test_stale_approval_is_rejected(self):
        await self.setup_owner()
        await self.client.post("/api/vault/person/entries", json={"entry_id": str(uuid4()), "kind": "goal", "text": "Changed private goal"})
        stale = await self.client.post("/api/vault/person/representation", json={"revision": 1, "statements": ["Open to collaborations."]})
        self.assertEqual(stale.status_code, 409)

    async def test_the_model_routes_are_gone(self):
        await self.setup_owner()
        self.assertEqual((await self.client.post("/api/vault/person/prepare")).status_code, 404)
        self.assertEqual((await self.client.post("/api/profiles/person/agent", json={"request_id": str(uuid4()), "kind": "question", "question": "Hello there"})).status_code, 404)

    async def test_peer_claim_is_attributed_and_separate(self):
        value = {"claim_id": str(uuid4()), "author_name": "A collaborator", "author_url": "https://example.org/person", "relationship": "Worked together", "statement": "We worked on a public release together."}
        with patch.object(public_profiles, "add_peer_claim", AsyncMock(return_value=value)) as save:
            response = await self.client.post("/api/profiles/person/peers", json=value)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["attribution"], "self_attributed")
            self.assertNotIn(SECRET, repr(save.call_args))
        with patch.object(public_profiles, "query", AsyncMock(side_effect=[[], [value]])) as query:
            await public_profiles.add_peer_claim("person", value)
            self.assertIn("PeerClaim", query.call_args.args[0])
            self.assertNotIn("AIJudgment", query.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
