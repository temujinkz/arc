"""You.com Web Search REST adapter. Provider contract checked 2026-10-17."""

import asyncio
import hashlib
import math
import os
import re
import time
from datetime import date, timezone
from email.utils import parsedate_to_datetime
from typing import Annotated

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENDPOINT = 'https://ydc-index.io/v1/search'
_requests = asyncio.Lock()
_not_before: dict[str, float] = {}


class SearchHit(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    url: str
    title: str | None = None
    description: str | None = None
    snippets: list[str] | None = None


class SearchResults(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    web: list[SearchHit] | None = None
    news: list[SearchHit] | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    results: SearchResults
    metadata: dict | None = None


def _rate_metadata(headers: httpx.Headers, scope: str) -> dict[str, float]:
    """Only accept numeric/provider-defined timing; never return arbitrary headers."""
    values = {}
    for header, field in [('X-RateLimit-Limit', 'limit'), ('X-RateLimit-Remaining', 'remaining'),
                          ('X-RateLimit-Reset', 'reset_at')]:
        try:
            value = float(headers[header])
            if math.isfinite(value) and value >= 0:
                values[field] = value
        except (KeyError, ValueError):
            pass
    retry = headers.get('Retry-After')
    if retry:
        try:
            seconds = float(retry)
            until = time.time() + seconds if math.isfinite(seconds) and seconds >= 0 else 0
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry)
                until = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
            except (ValueError, TypeError, OverflowError):
                until = 0
        if until > time.time():
            values['retry_at'] = until
    until = max(values.get('retry_at', 0), values.get('reset_at', 0) if values.get('remaining') == 0 else 0)
    if until > time.time():
        _not_before[scope] = until
    return values


async def search_web_you(
    query: str,
    count: Annotated[int | None, Field(ge=1, le=100, strict=True)] = None,
    freshness: str | None = None,
    include_domains: Annotated[list[str] | None, Field(max_length=500)] = None,
    exclude_domains: Annotated[list[str] | None, Field(max_length=5000)] = None,
    strict_operators: bool = True,
) -> dict:
    """Search public web/news through You.com (paid; no per-call confirmation needed).

    Returns original URLs, snippets and search metadata, not verified identities.
    count is per section: web and news; omitted uses provider default 10. Freshness
    accepts day/week/month/year or YYYY-MM-DDtoYYYY-MM-DD. Include/exclude domains
    are mutually exclusive. Strict operators prevent silently relaxing site/name
    constraints. Snippets only: no full-page extraction, pagination or paid retry.
    A page_age is provider metadata, not ARC's observation time or a verified date.
    """
    if os.getenv('ARC_RESEARCH_OFFLINE') == '1':
        raise ToolError('Offline mode forbids You.com calls.')
    key = os.getenv('YDC_API_KEY', '').strip()
    if not key:
        raise ToolError('Configure YDC_API_KEY to use You.com Web Search.')
    if not query.strip() or len(query) > 1000:
        raise ToolError('Provide a nonblank public search query of at most 1,000 characters.')
    if count is not None and (type(count) is not int or not 1 <= count <= 100):
        raise ToolError('You.com count must be an integer from 1 to 100 per result section.')
    if include_domains and exclude_domains:
        raise ToolError('You.com include_domains and exclude_domains cannot be combined.')
    if freshness is not None and freshness not in {'day', 'week', 'month', 'year'}:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}', freshness):
                raise ValueError
            start, end = (date.fromisoformat(v) for v in freshness.split('to'))
            if start > end:
                raise ValueError
        except ValueError:
            raise ToolError('Use day/week/month/year or a valid ascending YYYY-MM-DDtoYYYY-MM-DD date range.') from None
    body = {'query': query.strip(), 'strict_operators': strict_operators}
    for name, value in [('count', count), ('freshness', freshness),
                        ('include_domains', include_domains), ('exclude_domains', exclude_domains)]:
        if value is not None and value != []:
            body[name] = value
    scope = hashlib.sha256(key.encode()).hexdigest()
    async with _requests:
        until = _not_before.get(scope, 0)
        if until > time.time():
            raise ToolError(f'You.com requests paused until Unix time {math.ceil(until)} by provider rate-limit headers. No request sent.')
        # httpx's existing default timeout applies (also the You Python SDK default).
        # No transport retries, redirects or environment proxy credential forwarding.
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            try:
                response = await client.post(ENDPOINT, headers={'X-API-Key': key}, json=body)
            except httpx.RequestError:
                raise ToolError('You.com request failed or timed out; billing outcome is unknown. Do not automatically retry.') from None
        rate = _rate_metadata(response.headers, scope)
        status = response.status_code
        errors = {401: 'API key rejected', 402: 'credits exhausted', 403: 'Web Search scope not permitted',
                  422: 'search parameters rejected', 429: 'rate limit reached'}
        if status != 200:
            reason = errors.get(status, 'provider request failed')
            timing = f' Provider retry time: {math.ceil(rate["retry_at"])} (Unix).' if 'retry_at' in rate else ''
            raise ToolError(f'You.com HTTP {status}: {reason}.{timing} No automatic retry; inspect the account/request before retrying.')
        try:
            data = response.json()
            parsed = SearchResponse.model_validate(data)
            if parsed.results.web is None and parsed.results.news is None:
                raise ValueError('Missing result sections')
        except (ValueError, ValidationError):
            raise ToolError('You.com returned an invalid search response; do not treat this as no matches or automatically retry.') from None
    # Keep returned source fields verbatim for exact passage pointers and citations.
    return {**data, 'provider': 'you.com', 'rate_limit': rate, 'identity_verified': False,
            'limitation': 'Search snippets are discovery leads, not independent verification. Inspect original sources; page_age is unverified provider date metadata.'}
