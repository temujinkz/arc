"""People directory through Monid and Apollo.

Searching is free and returns a role and an employer. A reveal is a paid, per-person action the user
confirms: it returns the full profile and, when Apollo has one, a work email. A phone number is a
separate, dearer and slower reveal, because Apollo delivers it minutes later.

Contact details live only in a private local file. This module never imports the graph writer, so
nothing it returns can reach Neo4j or the public profile.
"""

import asyncio
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

BASE = "https://api.monid.ai"
EMAIL_COST = Decimal("0.026")   # profile and work email in one call
PHONE_COST = Decimal("0.208")   # added when Apollo has a mobile number
DEFAULT_CAP = Decimal("0.50")   # Monid keys have no spend limit of their own, so the app keeps one
STALE_DAYS = 30
REVEAL_WAIT = 15.0              # seconds to wait for an answer before handing back a pending phone request


class DirectoryError(Exception):
    """A message that is safe to show the user."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def directory_path() -> Path:
    default = Path(__file__).resolve().parents[1] / ".arc/private/directory.sqlite3"
    return Path(os.getenv("ARC_DIRECTORY_PATH", str(default))).resolve()


def spend_cap() -> Decimal:
    try:
        value = Decimal(os.getenv("MONID_MAX_SPEND_USD", str(DEFAULT_CAP)))
        if not value.is_finite() or value < 0:
            raise InvalidOperation
        return value
    except InvalidOperation:
        raise DirectoryError("MONID_MAX_SPEND_USD must be a non-negative amount.", 503) from None


def api_key() -> str:
    key = os.getenv("MONID_API_KEY", "").strip()
    if not key:
        raise DirectoryError("Add MONID_API_KEY to the backend .env to use the people directory.", 503)
    return key


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False)


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def person_key(name: str, company: str | None) -> str:
    return f"{norm(name)}|{norm(company)}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS people (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT NOT NULL,
                found INTEGER NOT NULL, preview TEXT, profile TEXT, looked_up_at TEXT NOT NULL, revealed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS contacts (
                key TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                revealed_at TEXT NOT NULL, PRIMARY KEY (key, kind, value)
            );
            CREATE TABLE IF NOT EXISTS spend (run_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, usd TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS phone_runs (run_id TEXT PRIMARY KEY, key TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def spent(self) -> Decimal:
        return sum((Decimal(r["usd"]) for r in self.db.execute("SELECT usd FROM spend")), Decimal(0))

    def add_spend(self, run_id: str, endpoint: str, usd: Decimal):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO spend VALUES (?,?,?,?)", (run_id, endpoint, str(usd), now()))

    def row(self, key: str):
        return self.db.execute("SELECT * FROM people WHERE key=?", (key,)).fetchone()

    def save_preview(self, key: str, name: str, company: str, found: bool, preview: dict | None):
        with self.db:
            self.db.execute(
                "INSERT INTO people(key,name,company,found,preview,looked_up_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET found=excluded.found, preview=excluded.preview, looked_up_at=excluded.looked_up_at",
                (key, name, company, int(found), json.dumps(preview) if preview else None, now()))

    def save_profile(self, key: str, name: str, company: str, profile: dict):
        with self.db:
            self.db.execute(
                "INSERT INTO people(key,name,company,found,profile,looked_up_at,revealed_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET found=1, profile=excluded.profile, revealed_at=excluded.revealed_at",
                (key, name, company, 1, json.dumps(profile), now(), now()))

    def save_contacts(self, key: str, contacts: list[dict]):
        with self.db:
            for c in contacts:
                self.db.execute("INSERT OR IGNORE INTO contacts VALUES (?,?,?,?,?)", (key, c["kind"], c["value"], c.get("detail", ""), now()))

    def contacts(self, key: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT kind,value,detail FROM contacts WHERE key=? ORDER BY kind,value", (key,)).fetchall()

    def start_phone(self, run_id: str, key: str):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO phone_runs(run_id,key,started_at) VALUES (?,?,?)", (run_id, key, now()))

    def finish_phone(self, run_id: str):
        with self.db:
            self.db.execute("UPDATE phone_runs SET done=1 WHERE run_id=?", (run_id,))

    def phone_run(self, run_id: str):
        return self.db.execute("SELECT * FROM phone_runs WHERE run_id=?", (run_id,)).fetchone()

    def pending_phone(self, key: str):
        return self.db.execute("SELECT run_id FROM phone_runs WHERE key=? AND done=0 ORDER BY started_at DESC", (key,)).fetchone()


def open_store() -> Store:
    return Store(directory_path())


# ---------- Monid ----------

async def monid_run(client: httpx.AsyncClient, endpoint: str, params: dict, wait: float = 20.0) -> dict:
    """Runs an Apollo endpoint through Monid. Returns the run, which may still be RUNNING after `wait` seconds."""
    headers = {"Authorization": f"Bearer {api_key()}"}
    try:
        response = await client.post(f"{BASE}/v1/run", headers=headers, json={"provider": "apollo", "endpoint": endpoint, "input": {"queryParams": params}})
    except httpx.HTTPError:
        raise DirectoryError("Could not reach Monid.") from None
    check(response)
    run = response.json()
    deadline = time.monotonic() + wait
    while run.get("status") in ("RUNNING", "QUEUED") and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        run = await monid_poll(client, run["runId"])
    return run


async def monid_poll(client: httpx.AsyncClient, run_id: str) -> dict:
    try:
        response = await client.get(f"{BASE}/v1/runs/{run_id}", headers={"Authorization": f"Bearer {api_key()}"})
    except httpx.HTTPError:
        raise DirectoryError("Could not reach Monid.") from None
    check(response)
    return response.json()


def check(response: httpx.Response):
    if response.status_code == 402 or response.status_code == 503:
        raise DirectoryError("Your Monid balance is empty or inactive. Top up at app.monid.ai/wallet.", 402)
    if response.status_code in (401, 403):
        raise DirectoryError("Monid rejected the API key.", 502)
    if response.status_code >= 400:
        raise DirectoryError(f"Monid answered {response.status_code}.", 502)


def run_cost(run: dict, fallback: Decimal) -> Decimal:
    try:
        return Decimal(str((run.get("cost") or {}).get("value", fallback)))
    except InvalidOperation:
        return fallback


# ---------- reading Apollo's answers ----------

def last_name_matches(masked: str | None, last: str | None) -> bool:
    """Apollo's free search masks surnames as 'Ta***v'. A name matches when the visible letters line up."""
    if not masked or not last:
        return not masked
    pattern = "".join(".*" if ch == "*" else re.escape(ch.lower()) for ch in re.sub(r"\*+", "*", masked))
    return re.fullmatch(pattern, last.lower()) is not None


def first_matches(found: str | None, wanted: str) -> bool:
    """'Janaki Ram' (Apollo) matches 'Janaki' (the slide), and the other way round."""
    a, b = norm(found), norm(wanted)
    return bool(a) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def name_parts(name: str) -> tuple[str, list[str]]:
    """First name, and the words that could be the surname. A trailing initial ('Michelle Julia N.') is not a surname."""
    words = name.replace(".", " ").split()
    real = [w for w in words if len(w) > 1] or words
    return real[0], real[1:] or [""]


def search_terms(name: str, company: str | None) -> list[str]:
    """Apollo matches keywords strictly, so 'Janaki Ram Goteti' finds nobody but 'Janaki Goteti' does."""
    first, rest = name_parts(name)
    terms = [" ".join([first, *rest]).strip(), f"{first} {rest[-1]}".strip()]
    if company:
        terms.append(f"{first} {rest[-1]} {company}".strip())
    return list(dict.fromkeys(t for t in terms if t))


def preview_from_search(output: dict, name: str, company: str | None) -> dict | None:
    first, rest = name_parts(name)
    wanted = norm(company)
    for person in (output or {}).get("people") or []:
        masked = person.get("last_name_obfuscated")
        if not first_matches(person.get("first_name"), first):
            continue
        if rest != [""] and not any(last_name_matches(masked, word) for word in rest):
            continue
        org = (person.get("organization") or {}).get("name") or ""
        # A person at a different employer is a namesake, not a match.
        if wanted and wanted not in norm(org) and norm(org) not in wanted:
            continue
        return {
            "apollo_id": person.get("id"),
            "title": person.get("title"),
            "company": org or None,
            "has_email": bool(person.get("has_email")),
            "has_phone": str(person.get("has_direct_phone") or "").lower().startswith(("yes", "maybe")),
        }
    return None


def profile_from_person(person: dict) -> dict:
    org = person.get("organization") or {}
    place = ", ".join(x for x in (person.get("city"), person.get("state"), person.get("country")) if x)
    return {
        "apollo_id": person.get("id"),
        "name": person.get("name"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "seniority": person.get("seniority"),
        "departments": person.get("departments") or [],
        "location": place or None,
        "linkedin_url": person.get("linkedin_url"),
        "github_url": person.get("github_url"),
        "twitter_url": person.get("twitter_url"),
        "history": [
            {"company": h.get("organization_name"), "title": h.get("title"), "start": h.get("start_date"), "end": h.get("end_date"), "current": bool(h.get("current"))}
            for h in (person.get("employment_history") or [])[:8]
        ],
        "company": {
            "name": org.get("name"), "website": org.get("website_url"), "industry": org.get("industry"),
            "employees": org.get("estimated_num_employees"), "founded": org.get("founded_year"),
            "stage": org.get("latest_funding_stage"), "linkedin_url": org.get("linkedin_url"),
        } if org else None,
    }


def contacts_from_person(person: dict) -> list[dict]:
    out: list[dict] = []
    if person.get("email") and person.get("email_status") != "unavailable":
        out.append({"kind": "email", "value": person["email"], "detail": person.get("email_status") or ""})
    for item in person.get("personal_emails") or []:
        value = item if isinstance(item, str) else item.get("email")
        if value:
            out.append({"kind": "personal_email", "value": value})
    for phone in person.get("phone_numbers") or []:
        value = phone.get("sanitized_number") or phone.get("raw_number")
        if value:
            out.append({"kind": "phone", "value": value, "detail": phone.get("type_cd") or phone.get("type") or ""})
    return out


# ---------- the three actions ----------

def view(store: Store, key: str) -> dict:
    row = store.row(key)
    if row is None:
        return {"key": key, "looked_up": False, "found": False, "preview": None, "profile": None, "contact": None}
    contacts = store.contacts(key)
    pending = store.pending_phone(key)
    revealed = row["revealed_at"] is not None
    return {
        "key": key,
        "looked_up": True,
        "found": bool(row["found"]),
        "preview": json.loads(row["preview"]) if row["preview"] else None,
        "profile": json.loads(row["profile"]) if row["profile"] else None,
        "contact": {
            "emails": [{"value": c["value"], "status": c["detail"]} for c in contacts if c["kind"] == "email"],
            "personal_emails": [c["value"] for c in contacts if c["kind"] == "personal_email"],
            "phones": [{"value": c["value"], "type": c["detail"]} for c in contacts if c["kind"] == "phone"],
            "phone_pending": pending["run_id"] if pending else None,
        } if (revealed or pending) else None,
    }


def fresh(row) -> bool:
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row["looked_up_at"])
        # A miss is only remembered for an hour, so a better search can try again soon.
        return age.total_seconds() < 3600 if not row["found"] else age.days < STALE_DAYS
    except ValueError:
        return False


async def lookup(store: Store, client: httpx.AsyncClient, name: str, company: str | None, refresh: bool = False) -> dict:
    """Free. Finds out whether Apollo knows this person at this employer, without any contact details."""
    key = person_key(name, company)
    row = store.row(key)
    if row is not None and fresh(row) and not refresh:
        return view(store, key)
    preview = None
    # Several phrasings, cheapest first, because Apollo's keyword search is strict. All of them are free.
    for keywords in search_terms(name, company):
        run = await monid_run(client, "/mixed_people/api_search", {"q_keywords": keywords, "per_page": 25})
        if run.get("status") != "COMPLETED":
            raise DirectoryError("Apollo did not answer in time. Try again.")
        preview = preview_from_search(run.get("output") or {}, name, company)
        if preview:
            break
    store.save_preview(key, name, company or "", preview is not None, preview)
    return view(store, key)


def estimate(phone: bool) -> Decimal:
    return EMAIL_COST + (PHONE_COST if phone else Decimal(0))


async def reveal(store: Store, client: httpx.AsyncClient, name: str, company: str | None, *,
                 linkedin_url: str | None = None, phone: bool = False) -> dict:
    """Paid. The caller must have shown the cost and been told to go ahead. Never repeated for someone already revealed."""
    key = person_key(name, company)
    row = store.row(key)
    already = row is not None and row["revealed_at"] is not None
    has_phone = bool(store.pending_phone(key)) or any(c["kind"] == "phone" for c in store.contacts(key))
    if already and (not phone or has_phone):
        return view(store, key)
    cost = estimate(phone)
    if store.spent() + cost > spend_cap():
        raise DirectoryError(f"This would pass the ${spend_cap()} spending limit (spent ${store.spent()}). Raise MONID_MAX_SPEND_USD to continue.", 402)

    params: dict = {"reveal_phone_number": bool(phone)}
    preview = json.loads(row["preview"]) if row is not None and row["preview"] else None
    if preview and preview.get("apollo_id"):
        params["id"] = preview["apollo_id"]
    elif linkedin_url:
        params["linkedin_url"] = linkedin_url
    else:
        params["name"] = name
        if company:
            params["organization_name"] = company
    run = await monid_run(client, "/people/match", params, wait=REVEAL_WAIT)
    status = run.get("status")
    if status == "RUNNING" or status == "QUEUED":
        # The profile part is ready quickly, the phone arrives later. Keep the run and let the caller poll it.
        if row is None:
            store.save_preview(key, name, company or "", False, None)
        store.start_phone(run["runId"], key)
        return view(store, key)
    if status != "COMPLETED":
        raise DirectoryError("Apollo could not enrich this person.")
    save_enrichment(store, key, name, company or "", run, phone)
    return view(store, key)


def save_enrichment(store: Store, key: str, name: str, company: str, run: dict, phone: bool):
    person = ((run.get("output") or {}).get("person")) or {}
    if not person:
        raise DirectoryError("Apollo found no one matching this person.")
    store.save_profile(key, name, company, profile_from_person(person))
    store.save_contacts(key, contacts_from_person(person))
    store.add_spend(run["runId"], "/people/match", run_cost(run, estimate(phone)))


async def poll_phone(store: Store, client: httpx.AsyncClient, run_id: str) -> dict:
    """Free. Checks a phone reveal that Apollo is still working on, and stores it when it lands."""
    record = store.phone_run(run_id)
    if record is None:
        raise DirectoryError("Unknown phone request.", 404)
    key = record["key"]
    if record["done"]:
        return view(store, key)
    run = await monid_poll(client, run_id)
    if run.get("status") in ("RUNNING", "QUEUED"):
        return view(store, key)
    row = store.row(key)
    if run.get("status") == "COMPLETED":
        save_enrichment(store, key, row["name"] if row else "", row["company"] if row else "", run, True)
    store.finish_phone(run_id)
    return view(store, key)
