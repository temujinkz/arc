"""Durable exact-request cache with cross-process claims before billable calls."""

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from backend.storage import connect
from backend.tool_outputs import is_error, normalize_output, persisted_output_path

CACHE_VERSION = 1
CACHE_NOTE = "ARC_SOURCE_CACHE "


class CacheBlocked(RuntimeError):
    pass


def canonical_inputs(tool: str, inputs: dict) -> dict:
    value = dict(inputs)
    if tool in {"scrape_linkedin_profile", "scrape_linkedin_posts"}:
        raw = value.get("linkedin_url", "")
        url = urlsplit(raw)
        # Normalize only accepted public profile URLs; never broaden the tool's input validation.
        if (url.scheme == "https" and url.hostname in {"linkedin.com", "www.linkedin.com"}
                and not url.username and not url.password and url.port in {None, 443}
                and re.fullmatch(r"/in/[^/]+/?", url.path)):
            value["linkedin_url"] = f"https://www.linkedin.com{url.path.rstrip('/')}"
        if tool == "scrape_linkedin_posts":
            value.setdefault("max_posts", 10)
    elif tool in {"search_identity_exa", "search_public_web", "search_publications_crossref"} and isinstance(value.get("query"), str):
        value["query"] = value["query"].strip()
        if tool == "search_public_web":
            value.setdefault("backend", "auto")
    elif tool in {"search_username_sherlock", "search_username_maigret"} and isinstance(value.get("sites"), list):
        if all(isinstance(site, str) for site in value["sites"]):
            value["sites"] = sorted(set(value["sites"]))
    elif tool in {"inspect_domain_theharvester", "inspect_domain_dns", "inspect_domain_archive"} and isinstance(value.get("domain"), str):
        value["domain"] = value["domain"].strip().lower().rstrip(".")
        if tool == "inspect_domain_dns":
            value.setdefault("record_type", "A")
    elif tool == "inspect_github_developer_profile" and isinstance(value.get("github_handle"), str):
        value["github_handle"] = value["github_handle"].lower()
    return value


def request_key(namespace: str, tool: str, inputs: dict) -> str:
    payload = json.dumps([CACHE_VERSION, namespace, tool, canonical_inputs(tool, inputs)],
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def cacheable(payload: dict) -> bool:
    data = normalize_output(payload)
    if is_error(payload) or is_error(data) or persisted_output_path(data):
        return False
    if isinstance(data, dict) and data.get("errors"):
        return False
    if isinstance(data, list) and any(is_error(item) for item in data):
        return False
    return True


class SourceCache:
    def __init__(self, path):
        self.db = connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS source_cache (
            cache_key TEXT PRIMARY KEY, namespace TEXT NOT NULL, tool TEXT NOT NULL,
            inputs TEXT NOT NULL, status TEXT NOT NULL, owner TEXT NOT NULL,
            refresh_id TEXT NOT NULL, observed_at TEXT, payload TEXT
        )""")
        self.db.commit()

    def claim(self, namespace: str, tool: str, inputs: dict, refresh_id: str = "") -> tuple[str, dict | None]:
        key = request_key(namespace, tool, inputs)
        owner = str(uuid4())
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM source_cache WHERE cache_key=?", (key,)).fetchone()
            if row:
                if row["status"] == "pending":
                    raise CacheBlocked("An identical source request is in progress or was interrupted. No second provider call was started.")
                wants_refresh = bool(refresh_id and refresh_id != row["refresh_id"])
                if row["status"] == "ready" and not wants_refresh:
                    self.db.commit()
                    return key, {"payload": json.loads(row["payload"]), "observed_at": row["observed_at"], "hit": True}
                if row["status"] == "uncertain" and not wants_refresh:
                    raise CacheBlocked("The previous source request failed or its outcome is unknown. Inspect that run before explicitly requesting fresh sources.")
            self.db.execute("""INSERT INTO source_cache VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, NULL)
                ON CONFLICT(cache_key) DO UPDATE SET status='pending', owner=excluded.owner,
                refresh_id=excluded.refresh_id""",
                (key, namespace, tool, json.dumps(canonical_inputs(tool, inputs), sort_keys=True), owner, refresh_id))
            self.db.commit()
            return key, {"owner": owner}
        except BaseException:
            self.db.rollback()
            raise

    def complete(self, key: str, owner: str, payload: dict, observed_at: str | None = None) -> str:
        observed_at = observed_at or datetime.now(timezone.utc).isoformat()
        with self.db:
            result = self.db.execute("""UPDATE source_cache SET status='ready', payload=?, observed_at=?
                WHERE cache_key=? AND owner=? AND status='pending'""",
                (json.dumps(payload, ensure_ascii=False), observed_at, key, owner))
            if result.rowcount != 1:
                raise CacheBlocked("Source cache ownership changed; no automatic retry is permitted.")
        return observed_at

    def fail(self, key: str, owner: str):
        with self.db:
            self.db.execute("UPDATE source_cache SET status='uncertain' WHERE cache_key=? AND owner=?", (key, owner))

    def close(self):
        self.db.close()
