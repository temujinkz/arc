"""Production FastMCP gateway: retain official upstream tools, cache their reads."""

import os
import sys
from pathlib import Path

# SDK launches this file with an absolute path while its cwd is .arc.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mcp.types as mt
from fastmcp.server import create_proxy
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult, InputRequiredToolResult
from fastmcp.exceptions import ToolError

from backend.agent import upstream_registry, cache_namespace
from backend.source_cache import CACHE_NOTE, CacheBlocked, SourceCache, cacheable
from backend.storage import database_path
from backend.tool_outputs import normalize_output
import json


class SourceCacheMiddleware(Middleware):
    def __init__(self, cache: SourceCache, namespace: str, allowed: set[str], refresh_id: str = ""):
        self.cache, self.namespace, self.allowed, self.refresh_id = cache, namespace, allowed, refresh_id

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if name not in self.allowed:
            raise ToolError("This source tool is not enabled in ARC.")
        # Only ordinary single-response source reads are supported by this cache.
        if getattr(context.message, "task", None):
            raise ToolError("Background tool tasks are not enabled for cached source reads.")
        try:
            key, entry = self.cache.claim(self.namespace, name, context.message.arguments or {}, self.refresh_id)
        except CacheBlocked as exc:
            raise ToolError(str(exc)) from None
        if entry.get("hit"):
            payload, observed_at, hit = entry["payload"], entry["observed_at"], True
        else:
            owner = entry["owner"]
            try:
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
    cache = SourceCache(database_path())
    proxy = create_proxy({"mcpServers": {provider: servers[provider]}}, name=f"ARC-{provider}-cache")
    proxy.add_middleware(SourceCacheMiddleware(cache, cache_namespace(provider, servers[provider]),
        {tool.split("__", 2)[2] for tool in allowed if tool.startswith(f"mcp__{provider}__")},
        os.getenv("ARC_CACHE_REFRESH_ID", "")))
    return proxy


if __name__ == "__main__":
    build_proxy(os.environ["ARC_CACHE_PROVIDER"]).run(transport="stdio")
