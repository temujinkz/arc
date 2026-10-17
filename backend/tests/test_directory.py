"""Offline checks for the people directory. A fake Monid answers every call, and nothing is ever charged."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from backend import directory, directory_routes

SEARCH_HIT = {"total_entries": 1, "people": [{
    "id": "abc123", "first_name": "Ada", "last_name_obfuscated": "Ok***r", "title": "Staff Engineer",
    "has_email": True, "has_direct_phone": "Maybe: please request direct dial via people/bulk_match",
    "organization": {"name": "Example Systems"},
}]}
PERSON = {
    "id": "abc123", "name": "Ada Okafor", "first_name": "Ada", "last_name": "Okafor", "title": "Staff Engineer",
    "headline": "Builds compilers", "seniority": "senior", "departments": ["engineering"], "city": "Lagos", "country": "Nigeria",
    "linkedin_url": "http://www.linkedin.com/in/ada", "email": "ada@example.test", "email_status": "verified",
    "employment_history": [{"organization_name": "Example Systems", "title": "Staff Engineer", "start_date": "2022-01-01", "end_date": None, "current": True}],
    "organization": {"name": "Example Systems", "website_url": "http://example.test", "industry": "software", "estimated_num_employees": 120},
}


class FakeMonid:
    """Stands in for api.monid.ai and remembers what it was asked."""

    def __init__(self, search=SEARCH_HIT, person=PERSON, phone_polls=1):
        self.calls, self.search, self.person, self.phone_polls = [], search, person, phone_polls

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.method == "POST" and request.url.path == "/v1/run":
            body = json.loads(request.content)
            endpoint, params = body["endpoint"], body["input"]["queryParams"]
            if endpoint == "/mixed_people/api_search":
                return httpx.Response(200, json={"runId": "s1", "status": "COMPLETED", "output": self.search, "cost": {"value": 0}})
            if params.get("reveal_phone_number"):
                return httpx.Response(202, json={"runId": "phone-run", "status": "RUNNING"})
            return httpx.Response(200, json={"runId": f"m{len(self.calls)}", "status": "COMPLETED", "output": {"person": self.person}, "cost": {"value": 0.026}})
        if request.method == "GET" and request.url.path == "/v1/runs/phone-run":
            self.phone_polls -= 1
            if self.phone_polls >= 0:
                return httpx.Response(200, json={"runId": "phone-run", "status": "RUNNING"})
            person = {**self.person, "phone_numbers": [{"sanitized_number": "+15550100", "type_cd": "mobile"}]}
            return httpx.Response(200, json={"runId": "phone-run", "status": "COMPLETED", "output": {"person": person}, "cost": {"value": 0.234}})
        return httpx.Response(404, json={"message": "unexpected"})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class DirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MONID_API_KEY": "test-key", "ARC_DIRECTORY_PATH": str(Path(self.temp.name) / "private/directory.sqlite3"), "MONID_MAX_SPEND_USD": "0.50"})
        self.env.start()
        self.fake = FakeMonid()
        self.http = patch.object(directory, "http_client", lambda: self.fake.client())
        self.http.start()
        self.app = FastAPI()
        self.app.include_router(directory_routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 12000)), base_url="http://localhost:8010")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.http.stop()
        self.env.stop()
        self.temp.cleanup()

    async def lookup(self, name="Ada Okafor", company="Example Systems"):
        return await self.client.post("/api/directory/lookup", json={"name": name, "company": company})

    async def test_free_lookup_matches_the_employer_and_returns_no_contact_details(self):
        body = (await self.lookup()).json()
        self.assertTrue(body["found"])
        self.assertEqual(body["preview"]["title"], "Staff Engineer")
        self.assertIsNone(body["contact"])
        self.assertNotIn("ada@example.test", json.dumps(body))
        self.assertEqual([c[2]["endpoint"] for c in self.fake.calls if c[1] == "/v1/run"], ["/mixed_people/api_search"])

    async def test_a_namesake_at_another_employer_is_not_a_match(self):
        body = (await self.lookup(company="A Different Company")).json()
        self.assertFalse(body["found"])
        self.assertIsNone(body["preview"])

    async def test_lookup_is_cached(self):
        await self.lookup()
        before = len(self.fake.calls)
        self.assertTrue((await self.lookup()).json()["found"])
        self.assertEqual(len(self.fake.calls), before)

    def test_names_with_a_two_word_first_name_or_a_surname_initial_still_match(self):
        output = {"people": [{"id": "1", "first_name": "Janaki Ram", "last_name_obfuscated": "Go***i", "title": "Engineer", "organization": {"name": "Crusoe"}},
                             {"id": "2", "first_name": "Michelle", "last_name_obfuscated": "Ju***a", "title": "Founder", "organization": {"name": "Blue Labs"}}]}
        self.assertEqual(directory.preview_from_search(output, "Janaki Ram Goteti", "Crusoe")["apollo_id"], "1")
        self.assertEqual(directory.preview_from_search(output, "Michelle Julia N.", "Blue")["apollo_id"], "2")
        # A different surname is still rejected.
        self.assertIsNone(directory.preview_from_search({"people": [{"id": "3", "first_name": "Michelle", "last_name_obfuscated": "Sm***h", "organization": {"name": "Blue"}}]}, "Michelle Julia N.", "Blue"))

    def test_search_phrasings_drop_middle_names_and_initials(self):
        self.assertEqual(directory.search_terms("Janaki Ram Goteti", "Crusoe"), ["Janaki Ram Goteti", "Janaki Goteti", "Janaki Goteti Crusoe"])
        self.assertEqual(directory.search_terms("Michelle Julia N.", "Blue"), ["Michelle Julia", "Michelle Julia Blue"])

    async def test_a_second_search_with_the_employer_finds_people_the_name_alone_buries(self):
        buried = {"total_entries": 0, "people": []}
        outputs = iter([buried, SEARCH_HIT])
        self.fake.search = None
        original = self.fake.handler
        def handler(request):
            if request.method == "POST" and request.url.path == "/v1/run" and json.loads(request.content)["endpoint"] == "/mixed_people/api_search":
                self.fake.calls.append((request.method, request.url.path, json.loads(request.content)))
                return httpx.Response(200, json={"runId": "s", "status": "COMPLETED", "output": next(outputs), "cost": {"value": 0}})
            return original(request)
        self.fake.handler = handler
        body = (await self.lookup()).json()
        self.assertTrue(body["found"])
        searches = [c[2]["input"]["queryParams"]["q_keywords"] for c in self.fake.calls if c[1] == "/v1/run"]
        self.assertEqual(searches, ["Ada Okafor", "Ada Okafor Example Systems"][:len(searches)])
        self.assertEqual(searches[0], "Ada Okafor")

    def test_masked_surnames_match_only_when_the_visible_letters_line_up(self):
        self.assertTrue(directory.last_name_matches("Ta***v", "Talkenov"))
        self.assertFalse(directory.last_name_matches("Ta***v", "Tanner"))
        self.assertTrue(directory.last_name_matches(None, "Anything"))

    async def test_reveal_needs_the_user_to_confirm_the_price(self):
        response = await self.client.post("/api/directory/reveal", json={"name": "Ada Okafor", "company": "Example Systems"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.fake.calls, [])

    async def test_reveal_returns_profile_and_email_charges_once_and_is_never_repeated(self):
        await self.lookup()
        first = (await self.client.post("/api/directory/reveal", json={"name": "Ada Okafor", "company": "Example Systems", "confirm": True})).json()
        self.assertEqual(first["contact"]["emails"][0]["value"], "ada@example.test")
        self.assertEqual(first["profile"]["location"], "Lagos, Nigeria")
        self.assertEqual(first["profile"]["history"][0]["company"], "Example Systems")
        matches = [c for c in self.fake.calls if c[1] == "/v1/run" and c[2]["endpoint"] == "/people/match"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][2]["input"]["queryParams"]["id"], "abc123")
        self.assertFalse(matches[0][2]["input"]["queryParams"]["reveal_phone_number"])
        again = await self.client.post("/api/directory/reveal", json={"name": "Ada Okafor", "company": "Example Systems", "confirm": True})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/v1/run" and c[2]["endpoint"] == "/people/match"]), 1)
        self.assertEqual((await self.client.get("/api/directory/status")).json()["spent_usd"], 0.026)

    async def test_the_spending_limit_stops_a_reveal_before_any_call(self):
        with patch.dict(os.environ, {"MONID_MAX_SPEND_USD": "0.03"}):
            ok = await self.client.post("/api/directory/reveal", json={"name": "Ada Okafor", "company": "Example Systems", "confirm": True})
            self.assertEqual(ok.status_code, 200)
            calls = len(self.fake.calls)
            blocked = await self.client.post("/api/directory/reveal", json={"name": "Grace Hopper", "company": "Navy", "confirm": True})
            self.assertEqual(blocked.status_code, 402)
            self.assertIn("spending limit", blocked.json()["detail"])
            self.assertEqual(len(self.fake.calls), calls)

    async def test_a_phone_number_is_pending_first_and_arrives_later(self):
        body = {"name": "Ada Okafor", "company": "Example Systems", "confirm": True, "phone": True}
        with patch.object(directory, "REVEAL_WAIT", 0.0):
            pending = (await self.client.post("/api/directory/reveal", json=body)).json()
        self.assertEqual(pending["contact"]["phone_pending"], "phone-run")
        self.assertEqual(pending["contact"]["phones"], [])
        still = (await self.client.get("/api/directory/phone/phone-run")).json()
        self.assertEqual(still["contact"]["phones"], [])
        done = (await self.client.get("/api/directory/phone/phone-run")).json()
        self.assertEqual(done["contact"]["phones"][0]["value"], "+15550100")
        self.assertIsNone(done["contact"]["phone_pending"])
        self.assertAlmostEqual((await self.client.get("/api/directory/status")).json()["spent_usd"], 0.234)

    async def test_without_a_key_the_user_is_told_what_to_add(self):
        with patch.dict(os.environ, {"MONID_API_KEY": ""}):
            response = await self.lookup()
        self.assertEqual(response.status_code, 503)
        self.assertIn("MONID_API_KEY", response.json()["detail"])

    async def test_another_machine_or_another_site_is_refused(self):
        remote = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("203.0.113.9", 4000)), base_url="http://localhost:8010")
        self.assertEqual((await remote.get("/api/directory/status")).status_code, 403)
        await remote.aclose()
        foreign = await self.client.get("/api/directory/status", headers={"origin": "https://evil.example"})
        self.assertEqual(foreign.status_code, 403)
        local = await self.client.get("/api/directory/status", headers={"origin": "http://localhost:5173"})
        self.assertEqual(local.status_code, 200)

    def test_contact_details_cannot_reach_the_graph(self):
        source = Path(directory.__file__).read_text() + Path(directory_routes.__file__).read_text()
        for forbidden in ("graph_store", "public_profiles", "neo4j", "commit_graph"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
