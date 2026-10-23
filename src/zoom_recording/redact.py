"""Scrub URLs and token-shaped strings from anything that may reach output.

Zoom hands out signed download links (query strings with a policy and a signature). They are
bearer credentials for as long as they are valid, so every error string passes through `scrub`
before it is printed.
"""

from __future__ import annotations

import re

_URL_QUERY = re.compile(r"(https?://[^\s?#\"'<>]+)[?#][^\s\"'<>]*", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-~%+=]{48,}")


def scrub(text: object) -> str:
    s = str(text)
    s = _URL_QUERY.sub(lambda m: f"{m.group(1)}?<redacted>", s)
    s = _LONG_TOKEN.sub("<redacted>", s)
    return s


def path_only(url: str) -> str:
    """Scheme, host and path of a URL, without query or fragment."""
    m = re.match(r"(https?://[^\s?#]+)", url)
    return scrub(m.group(1)) if m else "<url>"
