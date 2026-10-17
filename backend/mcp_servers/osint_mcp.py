"""ARC's public-source OSINT tools, served over Streamable HTTP."""

import asyncio
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.source_cache import SourceCache, CacheBlocked, verified_refresh_id
from backend.storage import database_path
import json
import logging
import os
import re
from typing import Literal
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
from apify_client import ApifyClientAsync
from ddgs import DDGS
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from backend.osint_tools import (
    inspect_domain_theharvester, search_username_maigret, search_username_sherlock,
)
from backend.domain_sources import inspect_domain_archive, inspect_domain_dns
from backend.you_search import search_web_you

mcp = FastMCP("ARC-Unified-OSINT-Server", mask_error_details=True)
github_requests = asyncio.Lock()
logger = logging.getLogger(__name__)

mcp.tool()(search_username_sherlock)
mcp.tool()(search_username_maigret)
mcp.tool()(inspect_domain_theharvester)
mcp.tool()(inspect_domain_dns)
mcp.tool()(inspect_domain_archive)
mcp.tool()(search_web_you)


@mcp.tool()
async def search_public_web(query: str, backend: Literal["auto", "brave", "yahoo", "duckduckgo", "google"] = "auto") -> dict:
    """Free public web search through DDGS; choose google for Google dork queries.

    Supports public professional queries such as site: and quoted names. Search
    engine support for operators varies. Results are unverified discovery leads.
    No paid API fallback. Engine blocks/rate limits are failures, not no matches.
    Library result-count and timeout defaults apply; no account quota is assumed.
    """
    if not query.strip() or len(query) > 1000:
        raise ToolError("Provide a nonblank public search query of at most 1,000 characters.")
    try:
        results = await asyncio.to_thread(lambda: DDGS().text(query.strip(), backend=backend))
    except Exception:
        raise ToolError("Free search failed or was blocked/rate-limited. No paid fallback or automatic retry was attempted.") from None
    return {"backend": backend, "results": results, "identity_verified": False,
            "limitation": "Search snippets and matching names are leads; inspect and disambiguate sources before using claims."}


@mcp.tool()
async def search_publications_crossref(query: str) -> dict:
    """Search free Crossref publication metadata by author, title or professional topic.

    Publisher-deposited author/ORCID/affiliation fields are attribution signals,
    not proof this is the intended person. Disambiguate before linking authors.
    No API key or paid provider is used. Provider result/timeout defaults apply.
    """
    if not query.strip() or len(query) > 1000:
        raise ToolError("Provide a nonblank publication query of at most 1,000 characters.")
    async with httpx.AsyncClient(headers={"User-Agent": "ARC-Engine (public professional research)"}) as client:
        try:
            response = await client.get("https://api.crossref.org/works", params={"query.bibliographic": query.strip()})
            response.raise_for_status()
            message = response.json()["message"]
            items = [{key: item[key] for key in (
                "DOI", "URL", "title", "author", "publisher", "container-title", "published", "type",
            ) if key in item} for item in message["items"]]
        except httpx.HTTPStatusError as exc:
            raise ToolError(f"Crossref HTTP {exc.response.status_code}. Check service availability/rate limits; do not automatically retry.") from None
        except (httpx.RequestError, ValueError, KeyError, TypeError):
            raise ToolError("Crossref request failed or returned invalid metadata. No automatic retry.") from None
    return {"source_url": str(response.url), "items": items, "identity_verified": False,
            "limitation": "Bibliographic search can mix namesakes; publisher-deposited metadata can be incomplete."}


def require_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ToolError(f"Configure {name} to use this tool.")
    return value


def linkedin_profile_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname not in {"linkedin.com", "www.linkedin.com"}
            or url.username or url.password or url.port not in {None, 443}
            or not re.fullmatch(r"/in/[^/]+/?", url.path)):
        raise ToolError("Provide an https://www.linkedin.com/in/handle profile URL.")
    return f"https://www.linkedin.com{url.path.rstrip('/')}"


async def run_apify(actor: str, run_input: dict, limit: int) -> list[dict]:
    try:
        charge_limit = Decimal(require_key("APIFY_MAX_TOTAL_CHARGE_USD"))
        if not charge_limit.is_finite() or charge_limit <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ToolError("APIFY_MAX_TOTAL_CHARGE_USD must be a positive finite amount.") from None
    if os.getenv("ARC_RESEARCH_OFFLINE") == "1":
        raise ToolError("Offline mode forbids Apify calls.")
    token = require_key("APIFY_API_TOKEN")
    try:
        refresh_id = verified_refresh_id(get_http_headers(), token)
    except ValueError:
        raise ToolError("Invalid application refresh identity.") from None
    operations = SourceCache(database_path())
    try:
        operation_id = operations.begin_operation("apify", actor, {"input": run_input, "limit": limit},
                                                   refresh_id)
    except CacheBlocked as exc:
        operations.close()
        raise ToolError(str(exc)) from None
    client = ApifyClientAsync(token)
    run_id = None
    try:
        # Both configured actors currently use pay-per-event pricing. Keep the
        # run ID before waiting so a failed wait never needs a fresh actor run.
        # The SDK sends a non-rewindable body only once (documented start API).
        # This disables retries for the paid POST, retaining SDK retries for reads.
        body = (chunk for chunk in (json.dumps(run_input).encode(),))
        started = await client.actor(actor).start(
            run_input=body, content_type="application/json",
            max_items=limit, max_total_charge_usd=charge_limit,
        )
        run_id = started.id
        operations.record_operation(operation_id, provider_run_id=run_id, status="RUNNING")
        logger.info(json.dumps({"event": "apify.run_started", "run_id": run_id, "actor": actor}))
        run = await client.run(run_id).wait_for_finish()
        if run is not None:
            operations.record_operation(operation_id, provider_run_id=run_id, status=str(run.status),
                                        usage_usd=float(run.usage_total_usd) if run.usage_total_usd is not None else None)
            logger.info(json.dumps({"event": "apify.run_finished", "run_id": run_id,
                "status": run.status, "usage_total_usd": run.usage_total_usd,
                "charged_event_counts": run.charged_event_counts}))
        if run is None or run.status != "SUCCEEDED":
            raise ToolError(f"Apify run {run_id} did not complete successfully; inspect it before retrying.")
        page = await client.dataset(run.default_dataset_id).list_items(limit=limit)
        items = page.items
        if (not items and limit == 1) or any(item.get("error") for item in items):
            raise ToolError("Apify returned no usable profile/post data.")
        return items
    except ToolError:
        raise
    except Exception:
        detail = f" Check existing run {run_id}." if run_id else " Start outcome may be unknown."
        raise ToolError(f"Apify request failed.{detail} Do not automatically rerun the actor.") from None
    finally:
        operations.close()
        await client.http_client.aclose()


@mcp.tool()
async def scrape_linkedin_profile(linkedin_url: str) -> dict:
    """Extract a public LinkedIn career timeline, education, skills and bio via Apify."""
    items = await run_apify(
        "harvestapi/linkedin-profile-scraper",
        {"urls": [linkedin_profile_url(linkedin_url)],
         "profileScraperMode": "Profile details no email ($4 per 1k)"}, 1,
    )
    return items[0]


@mcp.tool()
async def scrape_linkedin_posts(linkedin_url: str, max_posts: int = 10) -> list[dict]:
    """Extract 1–10 recent public posts. Use max_posts=1 for a minimal paid check."""
    if not 1 <= max_posts <= 10:
        raise ToolError("max_posts must be between 1 and 10 (ARC's per-call result bound).")
    return await run_apify(
        "supreme_coder/linkedin-post",
        {"urls": [linkedin_profile_url(linkedin_url)], "limitPerSource": max_posts,
         "deepScrape": False, "fetchDocumentDetails": False,
         "numComments": 0, "numLikes": 0}, max_posts,
    )


@mcp.tool()
async def inspect_github_developer_profile(github_handle: str) -> dict:
    """Read a public profile, twelve recently updated repos and thirty recent public events.

    Inspect up to five push-head commits for author email domains. Commit metadata
    is an attribution signal, not proof of identity; event sampling is not commit velocity.
    """
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_handle):
        raise ToolError("Provide a GitHub username, not a URL.")
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    errors: list[dict] = []
    async with httpx.AsyncClient(base_url="https://api.github.com", headers=headers,
                                 timeout=20) as client:
        async def get(path: str):
            try:
                # GitHub recommends serial requests to avoid secondary limits.
                async with github_requests:
                    response = await client.get(path)
                if response.status_code in {403, 429}:
                    reset = response.headers.get("x-ratelimit-reset", "not supplied")
                    retry = response.headers.get("retry-after", "not supplied")
                    raise ToolError(
                        f"GitHub HTTP {response.status_code}; stopped this inspection. "
                        f"Retry-After: {retry}; rate-limit reset (Unix seconds): {reset}. "
                        "Check permissions and rate limits before another request."
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                errors.append({"path": path, "status": exc.response.status_code})
            except (httpx.RequestError, ValueError):
                errors.append({"path": path, "error": "Request failed"})
            return None

        profile = await get(f"/users/{github_handle}")
        if profile is None:
            raise ToolError("GitHub profile request failed; check the username and rate limit.")
        repos = await get(f"/users/{github_handle}/repos?sort=updated&per_page=12")
        events = await get(f"/users/{github_handle}/events/public?per_page=30")
        pushes = [e for e in (events or []) if e.get("type") == "PushEvent"]
        heads = []
        for event in pushes:
            repo = event.get("repo", {}).get("name", "")
            sha = event.get("payload", {}).get("head", "")
            if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) and re.fullmatch(r"[a-fA-F0-9]{40}", sha):
                pair = (repo, sha)
                if pair not in heads:
                    heads.append(pair)
        commits = [await get(f"/repos/{repo}/commits/{sha}") for repo, sha in heads[:5]]
    signals = []
    for commit in commits:
        if not commit:
            continue
        author = commit.get("commit", {}).get("author") or {}
        email = author.get("email", "") or ""
        domain = email.rpartition("@")[2].lower()
        if domain and "noreply" not in email.lower():
            signals.append({"email_domain": domain, "commit_url": commit.get("html_url"),
                            "author_login": (commit.get("author") or {}).get("login"),
                            "identity_verified": False})
    return {
        "profile": {k: profile.get(k) for k in (
            "login", "name", "html_url", "bio", "blog", "company", "public_repos",
            "followers", "created_at",
        )},
        "recent_repositories": [{"name": r["name"], "url": r["html_url"],
            "description": r.get("description"), "language": r.get("language"),
            "stars": r["stargazers_count"], "fork": r["fork"]} for r in (repos or [])],
        "activity_sample": {"events_returned": len(events or []), "push_events": len(pushes),
                            "commit_heads_inspected": len(commits), "complete_history": False},
        "commit_author_signals": signals,
        "errors": errors,
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8001)
