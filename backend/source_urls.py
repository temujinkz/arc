"""Canonical source URLs shared by validation and request-idempotency keys."""

import re
from urllib.parse import urlsplit


def canonical_linkedin_profile_url(value: str) -> str:
    """Accept public profile URLs, including LinkedIn's two-letter country hosts.

    LinkedIn documents country-prefixed profile URLs here:
    https://www.linkedin.com/help/recruiter/answer/a522735
    Canonicalization changes routing only; it does not establish identity.
    """
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https"
                or not re.fullmatch(r"(?:(?:www|[a-z]{2})\.)?linkedin\.com", host)
                or url.username or url.password or url.port not in {None, 443}
                or not re.fullmatch(r"/in/[^/]+/?", url.path)):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Provide an HTTPS LinkedIn public profile URL with an /in/handle path.") from None
    return f"https://www.linkedin.com{url.path.rstrip('/')}"
