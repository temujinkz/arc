"""Free public DNS and archive indexes, without fetching target websites."""

from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastmcp.exceptions import ToolError

from backend.osint_tools import professional_domain, public_url


async def public_json(url: str, params: dict):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json(), str(response.url)
        except httpx.HTTPStatusError as exc:
            raise ToolError(f"Public metadata service returned HTTP {exc.response.status_code}; no automatic retry.") from None
        except (httpx.RequestError, ValueError):
            raise ToolError("Public metadata service failed or returned invalid JSON; no absence claim or automatic retry.") from None


async def inspect_domain_dns(domain: str, record_type: Literal["A", "AAAA", "MX", "NS", "CNAME", "TXT"] = "A") -> dict:
    """Read one record type from Google Public DNS for a sourced professional domain.

    No target website requests or paid API. DNS records do not prove a person's
    ownership/employment. Resolver answers may be cached; record TTL is supplied
    by DNS, not ARC's evidence retention policy.
    """
    domain = professional_domain(domain)
    data, url = await public_json("https://dns.google/resolve", {"name": domain, "type": record_type})
    if not isinstance(data, dict) or data.get("Status") not in {0, 3} or data.get("TC"):
        raise ToolError("DNS resolution failed or was truncated; do not treat it as missing evidence.")
    return {"domain": domain, "record_type": record_type, "source_url": url,
            "dns_status": data["Status"], "answers": data.get("Answer", []),
            "dnssec_validated": data.get("AD", False), "identity_verified": False,
            "limitation": "A resolver observation, not proof of live services, ownership or an individual's identity."}


async def inspect_domain_archive(domain: str, result_limit: int) -> dict:
    """Discover archived HTML pages for a sourced domain using Internet Archive CDX.

    Reads the public index only. Does not crawl snapshot contents. The caller
    supplies the requested result bound; historical captures do not prove current facts.
    """
    domain = professional_domain(domain)
    if isinstance(result_limit, bool) or result_limit <= 0:
        raise ToolError("Choose a positive result_limit for the archive index request.")
    params = {"url": f"{domain}/*", "output": "json", "filter": ["statuscode:200", "mimetype:text/html"],
              "fl": "timestamp,original,mimetype,statuscode,digest", "collapse": "urlkey", "limit": result_limit}
    data, url = await public_json("https://web.archive.org/cdx/search/cdx", params)
    if not isinstance(data, list) or (data and data[0] != ["timestamp", "original", "mimetype", "statuscode", "digest"]):
        raise ToolError("Archive returned an unsupported index response.")
    pages = []
    for row in data[1:]:
        if not isinstance(row, list) or len(row) != 5:
            raise ToolError("Archive returned an invalid index row.")
        timestamp, original, mime, status, digest = row
        if not isinstance(timestamp, str) or not timestamp.isdigit() or not public_url(original):
            raise ToolError("Archive returned an invalid timestamp or URL.")
        host = urlsplit(original).hostname.lower()
        if host != domain and not host.endswith("." + domain):
            raise ToolError("Archive returned a URL outside the requested domain.")
        pages.append({"captured_at": timestamp, "url": original, "mime_type": mime, "http_status": status,
                      "archive_digest": digest, "source_url": f"https://web.archive.org/web/{timestamp}/{original}"})
    return {"domain": domain, "source_url": url, "pages": pages, "requested_limit": result_limit,
            "identity_verified": False, "limitation": "A bounded archive index sample; historical captures and archive digests do not verify current identity or page content."}
