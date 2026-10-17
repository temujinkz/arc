"""Production FastMCP gateway: retain official upstream tools, cache their reads."""

import asyncio
from contextlib import asynccontextmanager

import os
import sys
from pathlib import Path

# SDK launches this file with an absolute path while its cwd is .arc.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mcp.types as mt
from fastmcp import Client
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import FastMCPProxy
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult, InputRequiredToolResult
from fastmcp.exceptions import ToolError

from backend.agent import upstream_registry, cache_namespace
from backend.source_cache import CACHE_NOTE, CacheBlocked, SourceCache, cacheable, refresh_headers, tool_namespace
from backend.storage import database_path
from backend.tool_outputs import normalize_output
import json


async def wait_for_robin(client: Client) -> None:
    # Robin documents up to 60 seconds for Tor startup. Health checks do not
    # search, scrape or invoke a model; never retry the source operation itself.
    try:
        async with asyncio.timeout(60):
            while True:
                result = await client.call_tool("robin_health", {"check_engines": False})
                text = "\n".join(block.text for block in result.content if isinstance(block, mt.TextContent))
                if text.startswith("status: ok\n") and "tor: up" in text.splitlines():
                    return
                if "tor_bootstrapping" not in text:
                    raise ToolError("Robin readiness check failed; no source request was sent.")
                await asyncio.sleep(2)
    except TimeoutError:
        raise ToolError("Robin's Tor startup timed out; no source request was sent.") from None


class SourceCacheMiddleware(Middleware):
    def __init__(self, cache: SourceCache, namespace: str, allowed: set[str], refresh_id: str = "", *, offline: bool = False, robin_client: Client | None = None):
        self.robin_client = robin_client
        self.offline = offline
        self.cache, self.namespace, self.allowed, self.refresh_id = cache, namespace, allowed, refresh_id

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if name not in self.allowed:
            raise ToolError("This source tool is not enabled in ARC.")
        namespace = tool_namespace(self.namespace, name)
        # Only ordinary single-response source reads are supported by this cache.
        if getattr(context.message, "task", None):
            raise ToolError("Background tool tasks are not enabled for cached source reads.")
        try:
            if self.offline:
                from backend.source_cache import request_key
                key = request_key(namespace, name, context.message.arguments or {})
                entry = self.cache.lookup(namespace, name, context.message.arguments or {})
                if entry is None:
                    raise ToolError("MISSING_CACHED_SOURCE: offline mode forbids upstream calls.")
            else:
                key, entry = self.cache.claim(namespace, name, context.message.arguments or {}, self.refresh_id)
        except CacheBlocked as exc:
            raise ToolError(str(exc)) from None
        if entry.get("hit"):
            payload, observed_at, hit = entry["payload"], entry["observed_at"], True
        else:
            owner = entry["owner"]
            try:
                if self.robin_client is not None:
                    await wait_for_robin(self.robin_client)
                result = await call_next(context)
                if not isinstance(result, ToolResult) or isinstance(result, InputRequiredToolResult):
                    raise ToolError("Source did not return a completed result; do not repeat the provider call.")
                payload = mt.CallToolResult(content=result.content, structuredContent=result.structured_content,
                                            isError=result.is_error).model_dump(mode="json", by_alias=True, exclude_none=True)
                if self.namespace.startswith("robin:"):
                    data = normalize_output(payload)
                    if isinstance(data, str) and data.startswith("status: needs_domain\n"):
                        raise ToolError("Robin needs the user's research domain/preset before searching. Ask for that choice; no search evidence was collected.")
                    if (not isinstance(data, str)
                            or not data.startswith(("status: ok\n", "status: no_results\n"))
                            or name == "robin_scrape" and data.startswith("status: ok\n0 of ")):
                        raise ToolError("Robin returned a setup, outage or failure response; no evidence was accepted. Inspect it before requesting fresh sources.")
                if not cacheable(payload):
                    self.cache.fail(key, owner)
                    return result
                observed_at = self.cache.complete(key, owner, payload)
                hit = False
            except BaseException:
                self.cache.fail(key, owner)
                raise
        result = mt.CallToolResult.model_validate(payload)
        metadata = {"hit": hit, "observed_at": observed_at, "key": key}
        # Preserve upstream structured output/schema. Text annotation also reaches
        # SDK clients that omit MCP _meta from PostToolUse callbacks.
        return ToolResult(content=[*result.content, mt.TextContent(type="text", text=CACHE_NOTE + json.dumps(metadata))],
                          structured_content=result.structured_content,
                          meta={"arc_source_cache": metadata}, is_error=False)


def build_proxy(provider: str):
    servers, allowed = upstream_registry()
    if provider not in {"osint", "firecrawl", "github", "robin"} or provider not in servers:
        raise ValueError("Source provider is not configured.")
    namespace = os.getenv("ARC_CACHE_NAMESPACE") or cache_namespace(provider, servers[provider])
    if provider == "osint" and os.getenv("ARC_CACHE_REFRESH_ID") and os.getenv("APIFY_API_TOKEN"):
        servers[provider] = {**servers[provider], "headers": {
            **servers[provider].get("headers", {}),
            **refresh_headers(os.environ["ARC_CACHE_REFRESH_ID"], os.environ.get("APIFY_API_TOKEN", "")),
        }}
    cache = SourceCache(database_path())
    robin_client = None
    if provider == "robin":
        # A new stdio client per request starts a new Tor container. Keep this
        # one inside the proxy process's lifespan (one SDK server, not global).
        robin_client = Client({"mcpServers": {provider: servers[provider]}}, init_timeout=60, timeout=600)

        @asynccontextmanager
        async def lifespan(_server):
            try:
                async with robin_client:
                    yield
            finally:
                cache.close()

        proxy = FastMCPProxy(client_factory=lambda: robin_client, name="ARC-robin-cache", lifespan=lifespan)
    else:
        # Firecrawl currently advertises handshake-era MCP only. Otherwise a
        # modern client of this proxy forces an unsupported era upstream.
        options = {"mode": "legacy", "provider_error_strategy": "raise"} if provider == "firecrawl" else {}
        proxy = create_proxy({"mcpServers": {provider: servers[provider]}}, name=f"ARC-{provider}-cache", **options)
    proxy.add_middleware(SourceCacheMiddleware(cache, namespace,
        {tool.split("__", 2)[2] for tool in allowed if tool.startswith(f"mcp__{provider}__")},
        os.getenv("ARC_CACHE_REFRESH_ID", ""), offline=os.getenv("ARC_RESEARCH_OFFLINE") == "1", robin_client=robin_client))
    return proxy


if __name__ == "__main__":
    build_proxy(os.environ["ARC_CACHE_PROVIDER"]).run(transport="stdio")
