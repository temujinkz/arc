"""Explicit free-source lookups through the production MCP/cache path."""

import asyncio
from typing import Literal

from fastmcp import Client
from fastmcp.server import create_proxy
from pydantic import BaseModel, ConfigDict, Field

from backend.agent import cache_namespace, upstream_registry
from backend.evidence_ledger import content_digest
from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
from backend.osint_tools import executable, professional_domain, username_and_sites
from backend.source_cache import SourceCache, canonical_inputs
from backend.storage import database_path


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)


class WebQuery(Query):
    backend: Literal["auto", "brave", "yahoo", "duckduckgo", "google"] = "auto"


class Username(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1)
    sites: list[str] = Field(min_length=1)


class Domain(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain: str = Field(min_length=1, max_length=253, pattern=r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\.?$")


class DomainLimit(Domain):
    result_limit: int = Field(gt=0, strict=True)


class DNS(Domain):
    record_type: Literal["A", "AAAA", "MX", "NS", "CNAME", "TXT"] = "A"


FREE_TOOLS = {
    "search_public_web": ("Web discovery", "query", WebQuery, None),
    "search_username_sherlock": ("Sherlock", "username", Username, "sherlock"),
    "search_username_maigret": ("Maigret", "username", Username, "maigret"),
    "inspect_domain_theharvester": ("Certificate records", "domain_limit", DomainLimit, "theharvester"),
    "search_publications_crossref": ("Publications", "query", Query, None),
    "inspect_domain_dns": ("Public DNS", "dns", DNS, None),
    "inspect_domain_archive": ("Archived website pages", "domain_limit", DomainLimit, None),
}


def catalog() -> list[dict]:
    return [{"tool": tool, "name": name, "input_kind": kind,
             "available": executable(binary) is not None if binary else True, "paid": False}
            for tool, (name, kind, _schema, binary) in FREE_TOOLS.items()]


def validate_arguments(tool: str, arguments: dict) -> dict:
    if tool not in FREE_TOOLS:
        raise ValueError("This workbench accepts the listed free source tools only.")
    schema = FREE_TOOLS[tool][2]
    value = schema.model_validate(arguments).model_dump()
    if "username" in value:
        value["username"], value["sites"] = username_and_sites(value["username"], value["sites"])
    if "domain" in value:
        value["domain"] = professional_domain(value["domain"])
    return canonical_inputs(tool, value)


async def execute_source(tool: str, arguments: dict, refresh_id: str = "") -> dict:
    upstream, _ = upstream_registry()
    cache = SourceCache(database_path())
    proxy = create_proxy({"mcpServers": {"osint": upstream["osint"]}}, name="ARC-explicit-source")
    proxy.add_middleware(SourceCacheMiddleware(cache, cache_namespace("osint", upstream["osint"]), {tool}, refresh_id))
    try:
        async with Client(proxy) as client:
            result = await client.call_tool(tool, arguments)
            return {"content": [item.model_dump(mode="json") for item in result.content],
                    "structuredContent": result.structured_content, "isError": result.is_error}
    finally:
        cache.close()


async def collect_source(run, request_id: str, tool: str, arguments: dict, fresh: bool):
    pad = run.scratchpad
    tool_id = f"source-{request_id}"
    name = f"mcp__osint__{tool}"
    before = len(pad.raw_evidence)
    try:
        await pad.record_tool_start(tool_id, name, arguments, subagent="operator")
        response = await execute_source(tool, arguments, request_id if fresh else "")
        if response.get("isError"):
            raise RuntimeError("Source returned an error.")
        await pad.record_tool_result(tool_id, name, response, inputs=arguments)
        run.source_requests[request_id]["state"] = "complete"
        pad.emit({"type": "source.complete", "request_id": request_id,
                  "evidence_added": len(pad.raw_evidence) - before,
                  "text": "Source lookup finished. The saved assessment has not been regenerated."})
    except asyncio.CancelledError:
        await pad.record_tool_failure(tool_id, name)
        run.source_requests[request_id]["state"] = "interrupted"
        pad.emit({"type": "source.cancelled", "request_id": request_id,
                  "error": "Source lookup stopped. Its provider outcome may be unknown; no automatic retry."})
        raise
    except Exception:
        await pad.record_tool_failure(tool_id, name)
        run.source_requests[request_id]["state"] = "failed"
        pad.emit({"type": "source.failed", "request_id": request_id,
                  "error": "Source lookup failed, was blocked, or could not be captured. Inspect its status before explicitly requesting fresh sources."})
    finally:
        run.finish()


def request_signature(tool: str, arguments: dict, fresh: bool) -> str:
    return content_digest([tool, arguments, fresh])
