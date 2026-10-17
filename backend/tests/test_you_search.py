"""You.com contract checks using ARC's real MCP and cache; no network/model calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client
from fastmcp.server import create_proxy

from backend.agent import cache_namespace, upstream_registry
from backend.evidence_ledger import source_observations
from backend.mcp_servers.osint_mcp import mcp
from backend.mcp_servers.cached_proxy import SourceCacheMiddleware
from backend.source_cache import SourceCache, request_key, tool_namespace
from backend.scratchpad import InvestigationScratchpad
from backend.tool_outputs import source_cache_info

TOOL = 'search_web_you'
ENV = {'YDC_API_KEY': 'offline-fixture-secret', 'ARC_RESEARCH_OFFLINE': '0'}
RESPONSE = {'results': {'web': [{'url': 'https://example.com/project', 'title': 'Project',
    'snippets': ['A public project excerpt'], 'page_age': '2026-10-01'}], 'news': []},
    'metadata': {'search_uuid': 'fixture-search', 'query': 'project', 'latency': 0.2}}


class YouSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_mcp_cache_and_scratchpad_keep_sources_without_second_request(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), 'https://ydc-index.io/v1/search')
            self.assertEqual(request.method, 'POST')
            self.assertIn(request.headers['X-API-Key'], {ENV['YDC_API_KEY'], 'rotated-fixture-secret'})
            self.assertEqual(json.loads(request.content), {'query': 'project', 'strict_operators': True})
            return httpx.Response(200, json=RESPONSE)
        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV), patch(
            'httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
        ):
            cache = SourceCache(Path(directory) / 'cache.sqlite3')
            proxy = create_proxy(mcp)
            proxy.add_middleware(SourceCacheMiddleware(cache, 'osint:you-fixture', {TOOL}))
            async with Client(proxy) as client:
                self.assertIn(TOOL, [t.name for t in await client.list_tools()])
                first = await client.call_tool(TOOL, {'query': 'project'})
                second = await client.call_tool(TOOL, {'query': ' project ', 'count': None, 'strict_operators': True})
                self.assertEqual(len(requests), 1)
                self.assertEqual(first.data['results'], RESPONSE['results'])
                payload = {'content': [b.model_dump(mode='json') for b in second.content],
                           'structuredContent': second.structured_content}
                info = source_cache_info(payload)
                self.assertTrue(info['hit'])
                pad = InvestigationScratchpad('offline-you')
                await pad.record_tool_result('tool', 'mcp__osint__'+TOOL, payload)
                self.assertEqual(pad.raw_evidence[0]['observed_at'], info['observed_at'])
                self.assertNotIn(ENV['YDC_API_KEY'], json.dumps(pad.raw_evidence))
                with patch.dict(os.environ, {'YDC_API_KEY': 'rotated-fixture-secret'}):
                    await client.call_tool(TOOL, {'query': 'project'})
                await client.call_tool(TOOL, {'query': 'project'})
                self.assertEqual(len(requests), 2, 'Key rotation isolates You results without destroying the old cache')
            cache.close()

    async def test_provider_errors_are_not_results_or_automatically_retried(self):
        real_client = httpx.AsyncClient
        for status in (401, 402, 403, 422, 429, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={'detail': ENV['YDC_API_KEY']})
            with self.subTest(status=status), patch.dict(os.environ, ENV), patch(
                'httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
            ):
                async with Client(mcp) as client:
                    with self.assertRaises(Exception) as exc:
                        await client.call_tool(TOOL, {'query': f'error {status}'})
                    self.assertNotIn(ENV['YDC_API_KEY'], str(exc.exception))
                    self.assertEqual(len(calls), 1)

    async def test_offline_missing_key_and_invalid_parameters_never_call_provider(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('Network is forbidden')
        cases = [({'YDC_API_KEY': '', 'ARC_RESEARCH_OFFLINE': '0'}, {'query': 'project'}),
                 ({**ENV, 'ARC_RESEARCH_OFFLINE': '1'}, {'query': 'project'}),
                 (ENV, {'query': '   '}), (ENV, {'query': 'p', 'count': 101}),
                 (ENV, {'query': 'p', 'count': True}),
                 (ENV, {'query': 'p', 'freshness': '2026-02-30to2026-03-01'}),
                 (ENV, {'query': 'p', 'include_domains': ['example.com'], 'exclude_domains': ['other.com']})]
        with patch('httpx.AsyncClient', side_effect=forbidden) as network:
            async with Client(mcp) as client:
                for env, args in cases:
                    with self.subTest(args=args), patch.dict(os.environ, env):
                        with self.assertRaises(Exception):
                            await client.call_tool(TOOL, args)
                        network.assert_not_called()

    async def test_registration_credential_scope_and_request_options(self):
        with patch.dict(os.environ, {'YDC_API_KEY': ''}):
            self.assertNotIn('mcp__osint__'+TOOL, upstream_registry()[1])
            unconfigured = cache_namespace('osint', {})
            old_you = tool_namespace(unconfigured, TOOL)
        with patch.dict(os.environ, ENV):
            self.assertIn('mcp__osint__'+TOOL, upstream_registry()[1])
            self.assertEqual(unconfigured, cache_namespace('osint', {}), 'Adding You must preserve existing paid-source cache entries')
            self.assertNotEqual(old_you, tool_namespace(unconfigured, TOOL))
            self.assertEqual(unconfigured, tool_namespace(unconfigured, 'search_identity_exa'))
        self.assertNotEqual(request_key('n', TOOL, {'query': 'p', 'count': 1}),
                            request_key('n', TOOL, {'query': 'p', 'count': 2}))
        observations = source_observations('mcp__osint__'+TOOL, RESPONSE)
        self.assertTrue(any(o['value'] == 'A public project excerpt' for o in observations))

    async def test_headers_pause_later_requests_without_sleeping_or_retrying(self):
        from backend import you_search
        real_client = httpx.AsyncClient
        for status, headers in [(200, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1100'}),
                                (429, {'Retry-After': '100'})]:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json=RESPONSE, headers=headers)
            with self.subTest(status=status), patch.dict(os.environ, ENV), patch.dict(you_search._not_before, {}, clear=True), patch(
                'backend.you_search.time.time', return_value=1000
            ), patch('httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
                async with Client(mcp) as client:
                    if status == 200:
                        await client.call_tool(TOOL, {'query': 'first'})
                    else:
                        with self.assertRaises(Exception):
                            await client.call_tool(TOOL, {'query': 'first'})
                    with self.assertRaises(Exception):
                        await client.call_tool(TOOL, {'query': 'different query'})
                    self.assertEqual(len(calls), 1)

    async def test_timeout_and_malformed_success_block_cache_retry(self):
        real_client = httpx.AsyncClient
        for failure in ('timeout', 'invalid'):
            calls = []
            def handler(request):
                calls.append(request)
                if failure == 'timeout':
                    raise httpx.ReadTimeout('fixture timeout', request=request)
                return httpx.Response(200, json={'results': {'web': 'not a results list'}})
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV), patch(
                'httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
            ):
                cache = SourceCache(Path(directory) / 'cache.sqlite3')
                proxy = create_proxy(mcp)
                proxy.add_middleware(SourceCacheMiddleware(cache, 'failed-you', {TOOL}))
                async with Client(proxy) as client:
                    for _ in range(2):
                        with self.assertRaises(Exception):
                            await client.call_tool(TOOL, {'query': failure})
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(cache.db.execute('SELECT status FROM source_cache').fetchone()[0], 'uncertain')
                cache.close()

    async def test_options_and_real_empty_results_remain_distinct_from_errors(self):
        real_client = httpx.AsyncClient
        args = {'query': 'site:example.com project', 'count': 3, 'freshness': '2026-01-01to2026-10-17',
                'include_domains': ['example.com'], 'strict_operators': True}
        def handler(request):
            self.assertEqual(json.loads(request.content), args)
            self.assertNotIn('extraction', json.loads(request.content))
            return httpx.Response(200, json={'results': {'web': [], 'news': []}, 'metadata': {'search_uuid': 'empty'}})
        with patch.dict(os.environ, ENV), patch('httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            async with Client(mcp) as client:
                result = await client.call_tool(TOOL, args)
                self.assertEqual(result.data['results'], {'web': [], 'news': []})
                self.assertFalse(result.data['identity_verified'])


    async def test_exa_is_not_callable_even_if_legacy_key_exists(self):
        with patch.dict(os.environ, {**ENV, 'EXA_API_KEY': 'legacy-fixture'}):
            self.assertNotIn('mcp__osint__search_identity_exa', upstream_registry()[1])
            async with Client(mcp) as client:
                self.assertNotIn('search_identity_exa', [t.name for t in await client.list_tools()])
                with self.assertRaises(Exception):
                    await client.call_tool('search_identity_exa', {'query': 'removed'})
