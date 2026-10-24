"""Driving the Zoom recordings web page: list, detail, and the batch of download links.

The page makes its own requests with its own headers. The tool only
- rewrites the form fields (`from`, `to`, `p`, `search_value`) of the page's list request,
- reads the JSON responses of the list and detail requests,
- intercepts the batch-download response so Chrome downloads nothing, and keeps the links in memory.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from . import guard, parse

LIST_RE = re.compile(r"^https://[^/]*zoom\.us/nws/recording/1\.0/host-list(\?.*)?$")
DETAIL_RE = re.compile(r"^https://[^/]*zoom\.us/nws/recording/1\.0/detail\?")
BATCH_RE = re.compile(r"^https://[^/]*zoom\.us/nws/recording/1\.0/(guest/)?download-meeting/")
SIGNIN_HINTS = ("/signin", "/login", "/saml", "/sso")
LIST_FIELDS = ("from", "to", "p", "search_value")
CONFIRM_DIALOG_TEXT = "Download recording files"


class NeedsSignIn(Exception):
    pass


class PageError(Exception):
    pass


def page_date(d: date) -> str:
    """The recordings page sends dates as MM/DD/YYYY."""
    return d.strftime("%m/%d/%Y")


def rewrite_list_form(post_data: str | None, overrides: dict[str, str]) -> str:
    """Pure: replace only the list fields in the page's own form body."""
    unknown = set(overrides) - set(LIST_FIELDS)
    if unknown:
        raise ValueError(f"not a list field: {sorted(unknown)}")
    pairs = parse_qsl(post_data or "", keep_blank_values=True)
    seen = set()
    out = []
    for k, v in pairs:
        if k in overrides:
            out.append((k, overrides[k]))
            seen.add(k)
        else:
            out.append((k, v))
    for k, v in overrides.items():
        if k not in seen:
            out.append((k, v))
    return urlencode(out)


def origin_of(page: Any) -> str:
    parts = urlsplit(page.url or "")
    host = parts.hostname or ""
    if parts.scheme == "https" and (host == "zoom.us" or host.endswith(".zoom.us")):
        return f"https://{host}"
    return "https://zoom.us"


def _signed_out(page: Any) -> bool:
    parts = urlsplit(page.url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme.startswith("http") and not (host == "zoom.us" or host.endswith(".zoom.us")):
        return True  # sent to an identity provider
    return any(h in parts.path.lower() for h in SIGNIN_HINTS)


def load_list(page: Any, overrides: dict[str, str], timeout_ms: int = 45000) -> dict[str, Any]:
    """Load the recordings page with the list request's fields rewritten; return the raw JSON."""

    def handler(route: Any) -> None:
        req = route.request
        if req.method == "POST":
            route.continue_(post_data=rewrite_list_form(req.post_data, overrides))
        else:
            route.continue_()

    page.route(LIST_RE, handler)
    try:
        try:
            with page.expect_response(lambda r: bool(LIST_RE.match(r.url)) and r.request.method == "POST",
                                      timeout=timeout_ms) as info:
                page.goto(origin_of(page) + "/recording", wait_until="domcontentloaded", timeout=timeout_ms)
            resp = info.value
        except Exception as e:  # noqa: BLE001 - classify below
            if _signed_out(page):
                raise NeedsSignIn() from None
            raise PageError(f"the recordings list did not load: {type(e).__name__}") from None
        if _signed_out(page):
            raise NeedsSignIn()
        if resp.status != 200:
            raise PageError(f"{parse.LIST_ENDPOINT}: HTTP {resp.status}")
        return resp.json()
    finally:
        page.unroute(LIST_RE, handler)


def load_detail(page: Any, meeting_id: str, timeout_ms: int = 45000) -> dict[str, Any]:
    url = f"{origin_of(page)}/recording/detail?meeting_id={quote(meeting_id, safe='')}"
    try:
        with page.expect_response(lambda r: bool(DETAIL_RE.match(r.url)) and r.request.method == "GET",
                                  timeout=timeout_ms) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        resp = info.value
    except Exception as e:  # noqa: BLE001
        if _signed_out(page):
            raise NeedsSignIn() from None
        raise PageError(f"the recording detail did not load: {type(e).__name__}") from None
    if _signed_out(page):
        raise NeedsSignIn()
    if resp.status != 200:
        raise PageError(f"{parse.DETAIL_ENDPOINT}: HTTP {resp.status}")
    return resp.json()


def capture_links(page: Any, clip_position: int, clip_count: int, timeout_ms: int = 30000,
                  log: Callable[[str], None] = lambda s: None) -> list[str]:
    """Press the Download button of the clip at `clip_position` (1-based, in page order) and the
    confirmation, if shown; return the signed links.

    Clip-level Download buttons are found by label shape and their visible text "Download"; there
    must be exactly `clip_count` of them, otherwise the page is not what we expect and nothing is
    clicked. The batch response is fulfilled to the page with an empty list, so Chrome starts no
    download. Must be called on a loaded detail page.
    """
    captured: dict[str, Any] = {}

    def handler(route: Any) -> None:
        try:
            resp = route.fetch()
            body = resp.json()
            captured["body"] = body
            result = body.get("result") if isinstance(body, dict) else None
            if not (isinstance(result, dict) and isinstance(result.get("batchDownloadFileList"), list)):
                route.abort()  # unknown shape: never hand links to the page
                return
            route.fulfill(response=resp, json={**body, "result": {**result, "batchDownloadFileList": []}})
        except Exception as e:  # noqa: BLE001 - never let the page download on a failure
            captured["error"] = type(e).__name__
            route.abort()

    page.route(BATCH_RE, handler)
    try:
        page.bring_to_front()
        buttons = page.locator('button[aria-label^="Download "]:visible').filter(
            has_text=re.compile(r"^\s*Download\s*$"))
        waited = 0
        while buttons.count() != clip_count and waited < timeout_ms:
            page.wait_for_timeout(250)
            waited += 250
        if buttons.count() != clip_count:
            raise PageError(f"expected {clip_count} clip Download button(s) on the detail page, "
                            f"found {buttons.count()}; nothing was clicked")
        label = guard.safe_click(buttons.nth(clip_position - 1), guard.DOWNLOAD_CLIP,
                                 text_allow=guard.DOWNLOAD_TEXT)
        log(f"pressed {label!r}")
        waited = 0
        confirmed = False
        while "body" not in captured and "error" not in captured and waited < timeout_ms:
            if not confirmed:
                dialog = page.locator("[role=dialog]:visible").filter(has_text=CONFIRM_DIALOG_TEXT)
                if dialog.count() == 1:
                    confirm = dialog.locator("button:visible").filter(
                        has_text=re.compile(r"^\s*Download\s*$"))
                    guard.safe_click(confirm, guard.CONFIRM_DOWNLOAD)
                    confirmed = True
                    log("confirmed the download dialog")
            page.wait_for_timeout(250)
            waited += 250
        if "error" in captured:
            raise PageError(f"{parse.BATCH_ENDPOINT}: interception failed ({captured['error']})")
        if "body" not in captured:
            raise PageError(f"{parse.BATCH_ENDPOINT}: no response within {timeout_ms // 1000} s")
        return parse.parse_batch(captured["body"])
    finally:
        page.unroute(BATCH_RE, handler)
