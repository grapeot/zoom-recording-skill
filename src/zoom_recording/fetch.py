"""Resumable ranged download of one file, with size and hash checks.

Zoom's signed links are only honored together with the browser session's own cookies, so the
bytes are fetched through the browser context's request API (`browser_getter`): the session
stays inside the browser and the tool never reads it. Each request asks for one range, which
keeps memory bounded and makes resuming trivial.

The link is a credential while it is valid: it is never printed, logged or stored, and every
error raised here carries only a status code or an exception type.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

CHUNK = 16 << 20
HASH_BLOCK = 1 << 20
_MD5 = re.compile(r"[0-9a-f]{32}")
_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")

# get(start, end) -> (status, lower-cased headers, body)
RangeGetter = Callable[[int, int], "tuple[int, dict[str, str], bytes]"]


class FetchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def browser_getter(request_context: Any, link: str, referer: str, timeout_ms: int = 120000) -> RangeGetter:
    """A RangeGetter that uses the browser context's request API (the browser's own session)."""

    def get(start: int, end: int) -> tuple[int, dict[str, str], bytes]:
        try:
            resp = request_context.get(
                link,
                headers={"Referer": referer, "Range": f"bytes={start}-{end}"},
                timeout=timeout_ms,
                max_redirects=5,
                fail_on_status_code=False,
            )
            status = resp.status
            headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"}
            body = resp.body() if status in (200, 206) else b""
            resp.dispose()
            return status, headers, body
        except Exception as e:  # noqa: BLE001 - the message may contain the link; keep only the type
            raise ConnectionError(type(e).__name__) from None

    return get


def file_hashes(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()  # md5 only to compare with the server's ETag
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(HASH_BLOCK), b""):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest(), md5.hexdigest()


def download(
    get: RangeGetter,
    dest: Path,
    expected_bytes: int | None,
    *,
    chunk: int = CHUNK,
    retries: int = 4,
    backoff: float = 2.0,
    progress: Callable[[int, int | None], None] | None = None,
) -> dict[str, Any]:
    """Download into `dest`, resuming from `dest.part`. Returns size, hashes and checks."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if part.exists() and expected_bytes is not None and part.stat().st_size > expected_bytes:
        part.unlink()
    resumed_from = part.stat().st_size if part.exists() else 0
    total = expected_bytes
    etag: str | None = None
    attempt = 0
    last_report = 0.0
    while True:
        offset = part.stat().st_size if part.exists() else 0
        if total is not None and offset >= total:
            break
        end = offset + chunk - 1
        if total is not None:
            end = min(end, total - 1)
        try:
            status, headers, body = get(offset, end)
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt < retries:
                attempt += 1
                time.sleep(min(backoff ** attempt, 30))
                continue
            raise FetchError("network", f"network error after {retries} retries: {e}") from None
        if status in (401, 403):
            raise FetchError("link_rejected", f"HTTP {status}: the download link was refused or expired")
        if status == 416 and total is not None and offset == total:
            break
        if status >= 500 or status == 429:
            if attempt < retries:
                attempt += 1
                time.sleep(min(backoff ** attempt, 30))
                continue
            raise FetchError("http_status", f"HTTP {status} after {retries} retries")
        etag = (headers.get("etag") or "").strip('"') or etag
        if status == 206:
            m = _RANGE.match(headers.get("content-range") or "")
            if not m or int(m.group(1)) != offset:
                raise FetchError("bad_range", "the server answered a different byte range than requested")
            if m.group(3) != "*":
                server_total = int(m.group(3))
                if expected_bytes is not None and server_total != expected_bytes:
                    raise FetchError("size_mismatch",
                                     f"server reports {server_total} bytes, Zoom listed {expected_bytes}")
                total = server_total
            if not body:
                raise FetchError("empty_range", "the server returned an empty range")
            with open(part, "ab") as fh:
                fh.write(body)
        elif status == 200:
            # Range ignored: the whole file came back. Accept it only when starting from zero.
            if offset != 0:
                part.unlink()
                continue
            if expected_bytes is not None and len(body) != expected_bytes:
                raise FetchError("size_mismatch", f"server sent {len(body)} bytes, Zoom listed {expected_bytes}")
            with open(part, "wb") as fh:
                fh.write(body)
            total = len(body)
        else:
            raise FetchError("http_status", f"unexpected HTTP {status}")
        attempt = 0
        if progress and time.monotonic() - last_report > 2:
            progress(part.stat().st_size, total)
            last_report = time.monotonic()

    size = part.stat().st_size if part.exists() else 0
    if total is not None and size != total:
        raise FetchError("size_mismatch", f"downloaded {size} bytes, expected {total}")
    sha, md5 = file_hashes(part)
    etag_md5 = etag if etag and _MD5.fullmatch(etag) else None
    if etag_md5 is not None and etag_md5 != md5:
        os.replace(part, part.with_name(part.name + ".bad"))
        raise FetchError("hash_mismatch", "md5 of the download does not match the server's ETag; moved aside as .bad")
    os.replace(part, dest)
    return {
        "bytes": size,
        "sha256": sha,
        "md5": md5,
        "etag_md5_match": None if etag_md5 is None else True,
        "resumed_from": resumed_from,
    }
