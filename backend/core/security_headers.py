"""SCRUM-62: security headers added to every API response.

These headers tell the browser to be strict with DietRx responses:

    X-Content-Type-Options: nosniff     never guess a different content type
    X-Frame-Options: DENY               the API can't be shown inside a frame
    Referrer-Policy: no-referrer        don't leak URLs to other sites
    Cache-Control: no-store             (for /api and /auth only) health data
                                        and tokens are never cached on disk

The rules are a plain function so they can be tested without a running server.
"""

from __future__ import annotations

from typing import Dict, MutableMapping

BASE_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

PRIVATE_PREFIXES = ("/api/", "/auth/")


def apply_security_headers(path: str, headers: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """Add the security headers for a response to ``path``. Existing values are kept."""
    for name, value in BASE_HEADERS.items():
        if name not in headers:
            headers[name] = value
    if path.startswith(PRIVATE_PREFIXES) and "Cache-Control" not in headers:
        headers["Cache-Control"] = "no-store"
    return headers
