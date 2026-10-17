"""Regression for live search results using LinkedIn country subdomains. Offline only."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy

from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
from backend.mcp_servers.osint_mcp import linkedin_profile_url, mcp
from backend.source_cache import SourceCache, canonical_inputs, request_key
from backend.source_urls import canonical_linkedin_profile_url


class SourceURLTests(unittest.IsolatedAsyncioTestCase):
    async def test_country_url_and_standard_url_share_one_provider_call_and_original_cache_time(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory) / "sources.sqlite3")
            proxy = create_proxy(mcp)
            proxy.add_middleware(SourceCacheMiddleware(cache, "offline", {"scrape_linkedin_profile"}))
            actor = AsyncMock(return_value=[{"firstName": "Synthetic", "linkedinUrl": "https://www.linkedin.com/in/example"}])
            with patch("backend.mcp_servers.osint_mcp.run_apify", actor):
                async with Client(proxy) as client:
                    first = await client.call_tool("scrape_linkedin_profile", {"linkedin_url": "https://in.linkedin.com/in/example/"})
                    second = await client.call_tool("scrape_linkedin_profile", {"linkedin_url": "https://www.linkedin.com/in/example"})
            self.assertEqual(actor.await_count, 1)
            self.assertEqual(actor.call_args.args[1]["urls"], ["https://www.linkedin.com/in/example"])
            self.assertEqual(first.structured_content, second.structured_content)
            self.assertFalse(first.meta["arc_source_cache"]["hit"])
            self.assertTrue(second.meta["arc_source_cache"]["hit"])
            self.assertEqual(first.meta["arc_source_cache"]["observed_at"], second.meta["arc_source_cache"]["observed_at"])
            cache.close()

    def test_country_alias_cannot_bypass_pending_or_uncertain_canonical_request(self):
        from backend.source_cache import CacheBlocked
        for failed in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                cache = SourceCache(Path(directory) / "sources.sqlite3")
                key, entry = cache.claim("offline", "scrape_linkedin_profile", {"linkedin_url": "https://www.linkedin.com/in/example"})
                if failed:
                    cache.fail(key, entry["owner"])
                with self.assertRaises(CacheBlocked):
                    cache.claim("offline", "scrape_linkedin_profile", {"linkedin_url": "https://in.linkedin.com/in/example"})
                cache.close()

    def test_supported_country_hosts_and_existing_profile_keys(self):
        canonical = "https://www.linkedin.com/in/example"
        for host in ("linkedin.com", "www.linkedin.com", "in.linkedin.com", "uk.linkedin.com", "ca.linkedin.com"):
            raw = f"https://{host}/in/example/?tracking=example#profile"
            self.assertEqual(linkedin_profile_url(raw), canonical)
            for tool in ("scrape_linkedin_profile", "scrape_linkedin_posts"):
                self.assertEqual(request_key("same-account", tool, {"linkedin_url": raw}), request_key("same-account", tool, {"linkedin_url": canonical}))

    def test_invalid_and_lookalike_urls_do_not_alias_a_valid_cached_profile(self):
        canonical = "https://www.linkedin.com/in/example"
        invalid = ["https://linkedin.com.attacker.example/in/example", "https://attacker.linkedin.com/in/example",
                   "https://foo.in.linkedin.com/in/example", "https://in.linkedin.com:8443/in/example",
                   "https://user:secret@in.linkedin.com/in/example", "http://in.linkedin.com/in/example",
                   "https://in.linkedin.com/company/example", "https://in.linkedin.com/in/example/posts",
                   "https://in.linkedin.com:notaport/in/example", "https://127.0.0.1/in/example"]
        for raw in invalid:
            with self.assertRaises(ValueError):
                canonical_linkedin_profile_url(raw)
            with self.assertRaises(ToolError):
                linkedin_profile_url(raw)
            self.assertEqual(canonical_inputs("scrape_linkedin_profile", {"linkedin_url": raw})["linkedin_url"], raw)
            self.assertNotEqual(request_key("same-account", "scrape_linkedin_profile", {"linkedin_url": raw}), request_key("same-account", "scrape_linkedin_profile", {"linkedin_url": canonical}))
