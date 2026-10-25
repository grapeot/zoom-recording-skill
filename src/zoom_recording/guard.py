"""The only place that clicks; read-only by default with a scoped passcode removal permit.

`safe_click` clicks an element only when its accessible label fully matches an allowlisted
pattern and neither the label nor the visible text mentions a destructive or sharing action.
`install_request_guard` aborts non-GET requests (and any request at all to delete or trash
endpoints) whose path looks like a mutation, for every page the tool drives.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from .redact import path_only

DENY_WORDS = re.compile(
    r"delete|trash|remove|recover|restore|share|link|copy|move|rename|edit|passcode|password"
    r"|setting|upload|clips|transcribe|archive|transfer|unlock|lock",
    re.IGNORECASE,
)

# Accessible labels the tool may click, and nothing else. A clip's Download button is labelled
# "Download " + the clip's name ("Recording 1", "Recording", or a name the owner gave it) and its
# visible text is exactly "Download"; per-file icons have the same label shape but no text.
DOWNLOAD_CLIP = re.compile(r"Download \S.{0,99}")
DOWNLOAD_TEXT = re.compile(r"Download")
CONFIRM_DOWNLOAD = re.compile(r"Download")

# Explicit UI-only sharing operations. These do not grant network write permission.
SHARING_CLICKS = {
    "open": re.compile(r"Share"),
    "expand": re.compile(r"More settings"),
    "save": re.compile(r"Save"),
}

ZOOM_HOST = re.compile(r"(^|\.)zoom\.us$", re.IGNORECASE)
MUTATION_PATH = re.compile(
    r"delete|trash|recover|restore|update|rename|move|share|passcode|password|setting|upload"
    r"|archive|transfer|unlock|lock|create|add-|/add|remove|edit",
    re.IGNORECASE,
)
ALWAYS_BLOCK_PATH = re.compile(r"delete|trash|recover|restore", re.IGNORECASE)
GUARD_URL = re.compile(r"^https?://([^/?#]*\.)?zoom\.us(:\d+)?/[^?#]*(" + MUTATION_PATH.pattern + r")",
                       re.IGNORECASE)


class GuardError(Exception):
    pass


def check_label(label: str, text: str, allow: re.Pattern[str], text_allow: re.Pattern[str] | None = None) -> None:
    """Pure check behind `safe_click`; raises GuardError when the click is not allowed."""
    label = " ".join((label or "").split())
    text = " ".join((text or "").split())
    name = label or text
    if not allow.fullmatch(name):
        raise GuardError(f"refusing to click {name!r}: label is not on the allowlist")
    if text_allow is not None and not text_allow.fullmatch(text):
        raise GuardError(f"refusing to click {name!r}: visible text {text!r} is not on the allowlist")
    for part in (label, text):
        hit = DENY_WORDS.search(part)
        if hit:
            raise GuardError(f"refusing to click {name!r}: it mentions {hit.group(0)!r}")


def safe_click(locator: Any, allow: re.Pattern[str], timeout_ms: int = 10000,
               text_allow: re.Pattern[str] | None = None, *, sharing_action: str | None = None) -> str:
    """Click `locator` after `check_label`. Returns the label that was clicked."""
    if locator.count() != 1:
        raise GuardError(f"expected exactly one element to click, found {locator.count()}")
    label = locator.get_attribute("aria-label") or ""
    text = locator.inner_text() or ""
    if sharing_action is None:
        check_label(label, text, allow, text_allow)
    else:
        pattern = SHARING_CLICKS.get(sharing_action)
        if pattern is None or allow is not pattern:
            raise GuardError("unknown sharing click operation")
        name = " ".join((label or text).split())
        if not pattern.fullmatch(name) or (text.strip() and not pattern.fullmatch(" ".join(text.split()))):
            raise GuardError("sharing control does not match the exact operation")
    locator.click(timeout=timeout_ms)
    return " ".join((label or text).split())


def disable_passcode(locator: Any) -> None:
    """Uncheck Passcode through its verified wrapper; Zoom's decoration covers the input."""
    if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
        raise GuardError("expected one visible enabled Passcode checkbox")
    if locator.get_attribute("type") != "checkbox":
        raise GuardError("Passcode control is not a checkbox")
    label = locator.evaluate("e => (e.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim()")
    if label != "Passcode":
        raise GuardError("checkbox label is not exactly Passcode")
    if not locator.is_checked():
        return
    wrapper = locator.locator("..")
    if (wrapper.count() != 1 or wrapper.evaluate("e => e.tagName") != "SPAN"
            or "recording-share-panel-checkbox__wrap" not in (wrapper.get_attribute("class") or "").split()
            or wrapper.locator('input[type="checkbox"]').count() != 1
            or " ".join(wrapper.inner_text().split()) != "Passcode"):
        raise GuardError("unexpected Passcode checkbox wrapper; nothing clicked")
    wrapper.click(timeout=10000)
    if locator.is_checked():
        raise GuardError("Passcode wrapper click did not clear the checkbox")


SAVE_SETTINGS_PATH = "/nws/recording/1.0/save-share-setting"


@dataclass
class PasscodePermit:
    """One request, one recording, one field. Only armed around an explicit UI Save."""
    origin: str
    meeting_id: str
    armed: bool = False
    consumed: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)

    def accept(self, url: str, method: str, resource_type: str, body: str | None) -> bool:
        if not self.armed or self.consumed or method != "POST" or resource_type not in {"xhr", "fetch"}:
            return False
        if url != self.origin + SAVE_SETTINGS_PATH:
            return False
        origin = urlsplit(self.origin)
        if (origin.scheme != "https" or not guard_host(origin.hostname) or origin.path or origin.query
                or origin.fragment or origin.username or origin.password or origin.port not in (None, 443)):
            return False
        try:
            if (body or "").lstrip().startswith("{"):
                pairs = json.loads(body, object_pairs_hook=list)
            else:
                pairs = parse_qsl(body or "", keep_blank_values=True, strict_parsing=True)
            if not isinstance(pairs, list) or len(pairs) != 2:
                return False
            data = dict(pairs)
            if set(data) != {"meetingId", "enablePasscode"} or data["meetingId"] != self.meeting_id:
                return False
            value = data["enablePasscode"]
            if value is not False and value != "false":
                return False
        except (ValueError, TypeError):
            return False
        self.consumed = True
        self.audit.append({"method": "POST", "path": SAVE_SETTINGS_PATH,
                           "changes": {"enablePasscode": False}})
        return True


def guard_host(host: str | None) -> bool:
    return bool(host and ZOOM_HOST.search(host))


STATIC_TYPES = {"script", "stylesheet", "image", "font", "media", "manifest", "texttrack"}


def is_mutation(url: str, method: str, resource_type: str = "xhr") -> bool:
    """Pure: should a request with this URL, method and resource type be aborted?

    Static assets are never blocked: the app ships code for its delete and share dialogs, and
    loading that code changes nothing.
    """
    if resource_type in STATIC_TYPES:
        return False
    parts = urlsplit(url)
    if not ZOOM_HOST.search(parts.hostname or ""):
        return False
    path = parts.path
    if ALWAYS_BLOCK_PATH.search(path):
        return True
    return method.upper() != "GET" and bool(MUTATION_PATH.search(path))


def install_request_guard(target: Any, blocked: list[str],
                          permit: PasscodePermit | None = None) -> Callable[[], None]:
    """Abort mutation-looking requests; blocked paths are appended to `blocked`.

    `target` is the browser context (covers every tab, popups and, in Chromium, service-worker
    requests) or a single page.

    Returns a function that removes the guard.
    """

    # A regex (not a Python predicate) so the browser filters requests without a round trip;
    # the handler then applies the exact rule, including the method.
    matcher = re.compile(r"^https?://([^/?#]*\.)?zoom\.us(:\d+)?/", re.IGNORECASE) if permit else GUARD_URL

    def handler(route: Any) -> None:
        req = route.request
        recording_write = (permit is not None and urlsplit(req.url).path.startswith("/nws/recording/")
                           and req.method != "GET" and req.resource_type not in STATIC_TYPES)
        if is_mutation(req.url, req.method, req.resource_type) or recording_write:
            if permit is not None and permit.accept(req.url, req.method, req.resource_type, req.post_data):
                route.fallback()
                return
            blocked.append(f"{req.method} {path_only(req.url)}")
            route.abort()
        else:
            route.fallback()

    target.route(matcher, handler)
    return lambda: target.unroute(matcher, handler)
