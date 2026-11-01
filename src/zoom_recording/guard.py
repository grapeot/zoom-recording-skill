"""The only place that clicks or types; read-only by default, with one-shot write permits.

`safe_click` clicks an element only when its accessible label fully matches an allowlisted
pattern and neither the label nor the visible text mentions a destructive or sharing action.
`install_request_guard` aborts non-GET requests (and any request at all to delete or trash
endpoints) whose path looks like a mutation, for every page the tool drives; with `meetings=True`
it also aborts every non-GET request under /meeting and /rest/meeting other than the page's reads.

Writes pass only through a one-shot permit armed around one explicit UI action and bound to one
exact request: `PasscodePermit` (disable a recording's sharing passcode), `CreateMeetingPermit`
(create the requested meeting), `DeleteMeetingPermit` (delete the requested meeting). Meeting
clicks and fills go through `meeting_click` / `meeting_fill`, which require exactly one control with
the operation's role and exact name, and fail closed with `SelectorError` otherwise.
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


class _OneShotPermit:
    """Shared rules for every write permit: armed only around one explicit UI action, consumed by
    the first matching request, bound to the page's own HTTPS Zoom origin, and closed on anything
    unexpected. Subclasses add the exact endpoint and body rule of their one operation."""

    origin: str
    armed: bool
    consumed: bool

    def _request_ok(self, url: str, method: str, resource_type: str, path: str) -> bool:
        if not self.armed or self.consumed or method != "POST" or resource_type not in {"xhr", "fetch"}:
            return False
        if url != self.origin + path:
            return False
        origin = urlsplit(self.origin)
        return not (origin.scheme != "https" or not guard_host(origin.hostname) or origin.path or origin.query
                    or origin.fragment or origin.username or origin.password or origin.port not in (None, 443))


@dataclass
class PasscodePermit(_OneShotPermit):
    """One request, one recording, one field. Only armed around an explicit UI Save."""
    origin: str
    meeting_id: str
    armed: bool = False
    consumed: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)

    def accept(self, url: str, method: str, resource_type: str, body: str | None) -> bool:
        if not self._request_ok(url, method, resource_type, SAVE_SETTINGS_PATH):
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


# ------------------------------------------------------------------ meetings: request permits

CREATE_MEETING_PATH = "/rest/meeting/save"
DELETE_MEETING_PATH = "/meeting/delete"
CONFIRM_DELETE_PATH = re.compile(r"/meeting/(\d{9,12})/confirm")
DELETE_FORM_KEYS = ("user_id", "id", "meetingMasterEventId", "occurrence", "sendMail", "mailBody")
# Top-level keys that mark an existing meeting (an edit) rather than a new one.
EXISTING_MEETING_KEYS = {"id", "number", "meetingNumber", "uuid", "dynamicHostKey", "occurrence"}


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _wrapped(body: dict[str, Any], key: str) -> Any:
    """The schedule form posts every field as {"value": ...}; a missing wrapper fails closed."""
    entry = body.get(key)
    if not isinstance(entry, dict) or "value" not in entry:
        raise ValueError(f"field {key} missing")
    return entry["value"]


# Fields a schedule command sets on purpose; each has its own exact check below.
SET_BY_COMMAND = frozenset({"topic", "agenda", "startDate", "startTime", "startTime2", "duration", "timezone",
                            "waitingRoom", "passcode", "invitee", "recurring", "frontParam"})


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == {} or value == ""


# Sections the real form posts as null when untouched although its defaults carry a value
# (observed 2026-10-31 by comparing an aborted save with the form's defaults). Only these may be null
# over a value; every other field must equal its default exactly.
NULL_WHEN_UNTOUCHED = frozenset({"breakoutRoom", "encryption", "focusMode", "internalMeeting", "meetingSummary",
                                 "meetingSummaryTemplate", "moderateUnmute", "requestUnmuteAll", "waitingRoom2"})


def _not_beyond_default(sent: Any, default: Any, nullable: bool = False) -> bool:
    """A field the command did not set must equal the form's default; an unset default may be posted
    empty; only the observed `NULL_WHEN_UNTOUCHED` sections may be posted as null over a value."""
    return sent == default or (_empty(sent) and _empty(default)) or (nullable and sent is None)


def _unchanged_from_defaults(body: dict[str, Any], defaults: dict[str, Any]) -> list[str]:
    """Names of fields (or field.child) that differ from the form's own defaults beyond being unset."""
    drift = []
    for key, entry in body.items():
        if key == "frontParam":
            continue  # checked on its own
        if key not in defaults:
            drift.append(key)
            continue
        default = defaults.get(key) if isinstance(defaults.get(key), dict) else {}
        if not isinstance(entry, dict):
            drift.append(key)
            continue
        if key not in SET_BY_COMMAND and not _not_beyond_default(entry.get("value"), default.get("value"),
                                                                 key in NULL_WHEN_UNTOUCHED):
            drift.append(key)
        children = entry.get("childParams") or {}
        default_children = default.get("childParams") or {}
        if not isinstance(children, dict):
            drift.append(key + ".childParams")
            continue
        for child, child_entry in children.items():
            sent = child_entry.get("value") if isinstance(child_entry, dict) else child_entry
            base = default_children.get(child)
            base = base.get("value") if isinstance(base, dict) else base
            if (child not in default_children and not _empty(sent)) or not _not_beyond_default(sent, base):
                drift.append(f"{key}.{child}")
    return drift


def _same_clock(sent: Any, expected: str) -> bool:
    """The form shows "2:30" but posts "02:30"; accept exactly those two spellings of H:MM."""
    if not isinstance(sent, str) or not re.fullmatch(r"(1[0-2]|[1-9]):[0-5]\d", expected):
        return False
    return sent in (expected, expected.zfill(5))


@dataclass
class CreateMeetingPermit(_OneShotPermit):
    """Lets exactly one `POST /rest/meeting/save` through: a new, non-recurring meeting whose topic,
    date, start, duration and time zone equal the requested ones, with no Zoom invitees, no
    alternative hosts, no registration, Waiting Room off and Passcode unchanged from the form. Every
    other field must equal the form's own defaults (the page's `GET /rest/meeting/schedule` response)
    or be left unset, the meeting id must be generated (not the personal meeting id), and the meeting
    must be scheduled for the signed-in user the form defaulted to.

    Edits (a query string, a meeting number in the body) and every other field mismatch are refused.
    `expected` keys: topic, agenda, start_date (MM/DD/YYYY), start_time (H:MM), start_unit (AM|PM),
    duration (minutes), timezone (IANA id); `passcode` (bool) and `defaults` (the form defaults'
    `meeting` object, held in memory only) are filled in from the live form before arming.
    """
    origin: str
    expected: dict[str, Any]
    armed: bool = False
    consumed: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def accept(self, url: str, method: str, resource_type: str, body: str | None) -> bool:
        if not self._request_ok(url, method, resource_type, CREATE_MEETING_PATH):
            return False
        exp = self.expected
        defaults = exp.get("defaults")
        if not isinstance(exp.get("passcode"), bool) or not isinstance(defaults, dict) or not defaults:
            return False
        try:
            data = json.loads(body or "", object_pairs_hook=_json_no_duplicates)
            mid = data.get("meetingId") if isinstance(data, dict) else None
            if (not isinstance(data, dict) or EXISTING_MEETING_KEYS & set(data) or not isinstance(mid, dict)
                    or "meetingNumber" in (mid.get("childParams") or {})):
                self.refused.append("body: not a new meeting")
                return False
            agenda = _wrapped(data, "agenda")
            checks = {
                "topic": _wrapped(data, "topic") == exp["topic"],
                "agenda": (agenda in (None, "") and not exp.get("agenda"))
                or (bool(exp.get("agenda")) and agenda == exp["agenda"]),
                "startDate": _wrapped(data, "startDate") == exp["start_date"],
                "startTime": _same_clock(_wrapped(data, "startTime"), exp["start_time"]),
                "startTime2": _wrapped(data, "startTime2") == exp["start_unit"],
                "duration": type(_wrapped(data, "duration")) is int and _wrapped(data, "duration") == exp["duration"],
                "timezone": _wrapped(data, "timezone") == exp["timezone"],
                "recurring": _wrapped(data, "recurring") is None,
                "invitee": _wrapped(data, "invitee") in (None, []),
                "alternativeHost": _wrapped(data, "alternativeHost") in (None, []),
                "registration": _wrapped(data, "registration") in (None, False),
                "waitingRoom": _wrapped(data, "waitingRoom") is False,
                "passcode": _wrapped(data, "passcode") is exp["passcode"],
                "meetingId": _wrapped(data, "meetingId") is False,
                "scheduleFor": isinstance(_wrapped(data, "scheduleFor"), str)
                and _wrapped(data, "scheduleFor") == _wrapped(defaults, "scheduleFor"),
                # Front-end routing hints, observed as {"groupId": "", "targetUserId": null}.
                "frontParam": "frontParam" not in data or (
                    isinstance(data["frontParam"], dict) and set(data["frontParam"]) <= {"groupId", "targetUserId"}
                    and all(v in (None, "") for v in data["frontParam"].values())),
            }
            for name in _unchanged_from_defaults(data, defaults):
                checks[name] = False
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            self.refused.append(f"body: {type(e).__name__}")
            return False
        if not all(checks.values()):
            # Field names only, never values: enough to see which rule closed.
            self.refused.append("fields: " + ",".join(k for k, ok in checks.items() if not ok))
            return False
        self.consumed = True
        self.audit.append({"method": "POST", "path": CREATE_MEETING_PATH, "creates": {
            "topic": exp["topic"], "start_date": exp["start_date"], "start_time": exp["start_time"],
            "start_unit": exp["start_unit"], "duration": exp["duration"], "timezone": exp["timezone"],
            "waiting_room": False, "passcode": exp["passcode"], "invitees": 0}})
        return True


@dataclass
class DeleteMeetingPermit(_OneShotPermit):
    """Lets exactly one `POST /meeting/delete` through for one meeting number: the whole meeting
    (no occurrence), no cancellation email, no mail body. Zoom keeps it in Recently Deleted.

    The page's own pre-dialog lookup `POST /meeting/{id}/confirm` (empty body, returns the registrant
    count) is a read for this same meeting and is let through without consuming the permit.
    """
    origin: str
    meeting_id: str
    armed: bool = False
    consumed: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)

    def allows_read(self, url: str, method: str, resource_type: str, body: str | None) -> bool:
        parts = urlsplit(url)
        match = CONFIRM_DELETE_PATH.fullmatch(parts.path)
        return bool(method == "POST" and resource_type in {"xhr", "fetch"} and match
                    and match.group(1) == self.meeting_id and not (body or "").strip()
                    and parts.query in ("", "occurrence=undefined", "occurrence=")
                    and f"{parts.scheme}://{parts.netloc}" == self.origin)

    def accept(self, url: str, method: str, resource_type: str, body: str | None) -> bool:
        if not self._request_ok(url, method, resource_type, DELETE_MEETING_PATH):
            return False
        try:
            pairs = parse_qsl(body or "", keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return False
        keys = [k for k, _ in pairs]
        if sorted(keys) != sorted(DELETE_FORM_KEYS):
            return False
        data = dict(pairs)
        if (data["id"] != self.meeting_id or data["sendMail"] != "false" or data["mailBody"] != ""
                or data["occurrence"] != "" or data["user_id"] != "" or data["meetingMasterEventId"] != ""):
            return False
        self.consumed = True
        self.audit.append({"method": "POST", "path": DELETE_MEETING_PATH,
                           "deletes": {"meeting": "requested id", "send_mail": False, "occurrence": None}})
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


# Meeting pages: every non-GET request under /meeting or /rest/meeting is a write unless it is one
# of the page's own observed reads. Elsewhere, write-sounding paths are refused too. The schedule
# form's save endpoint (`/rest/meeting/save`) does not match MUTATION_PATH, which is why commands
# that drive meeting pages use this stricter rule.
# Every non-GET under these prefixes is a write unless it is one of the listed reads. The page's own
# token, telemetry and notice requests live outside them and pass; they cannot change the account.
MEETING_SCOPE = re.compile(r"^/(rest|nws|meetings?|recordings?|profile|account|user|settings?|webinar|"
                           r"pac|scheduler|calendar|integration|zoomapp|team|users?|billing(?!/wording/))(/|$)",
                           re.IGNORECASE)
MEETING_READ_POSTS = frozenset({"/rest/meeting/list", "/rest/meeting/view", "/rest/coach_mark/v1/web_read"})
MEETING_WRITE_WORDS = re.compile(
    r"save|schedule|invite|invitation|mail|send|cancel|template|regist|alternative|recover|restore"
    r"|trash|delete|setting|profile|edit|update|create|remove", re.IGNORECASE)
ZOOM_ANY_URL = re.compile(r"^https?://([^/?#]*\.)?zoom\.us(:\d+)?/", re.IGNORECASE)


def is_meeting_write(url: str, method: str, resource_type: str = "xhr") -> bool:
    """Pure: should a request be aborted while a command drives meeting pages?"""
    if resource_type in STATIC_TYPES:
        return False
    parts = urlsplit(url)
    if not ZOOM_HOST.search(parts.hostname or ""):
        return False
    if is_mutation(url, method, resource_type):
        return True
    if method.upper() == "GET":
        return False
    path = parts.path
    if MEETING_SCOPE.search(path):
        return path not in MEETING_READ_POSTS
    if path.startswith("/cdn-cgi/"):  # bot-challenge plumbing with random path tokens
        return False
    return bool(MEETING_WRITE_WORDS.search(path))


def _send_without_redirect(route: Any, blocked: list[str], url: str) -> None:
    """Send the one permitted meeting write ourselves, refusing to follow a redirect: the browser's
    route handler only sees the first URL, so a redirected write would skip the guard."""
    try:
        resp = route.fetch(max_redirects=0)
    except Exception:  # noqa: BLE001 - never fall back to an unguarded send
        blocked.append(f"POST {path_only(url)} (send failed)")
        route.abort()
        return
    if 300 <= resp.status < 400:
        blocked.append(f"POST {path_only(url)} (redirect refused)")
        route.abort()
        return
    route.fulfill(response=resp)


def install_request_guard(target: Any, blocked: list[str], permit: Any = None, *,
                          meetings: bool = False) -> Callable[[], None]:
    """Abort mutation-looking requests; blocked paths are appended to `blocked`.

    `target` is the browser context (covers every tab, popups and, in Chromium, service-worker
    requests) or a single page. `meetings=True` applies `is_meeting_write` for commands that drive
    meeting pages. `permit` (a one-shot permit) may let its single request through.

    Returns a function that removes the guard.
    """

    # A regex (not a Python predicate) so the browser filters requests without a round trip;
    # the handler then applies the exact rule, including the method.
    matcher = ZOOM_ANY_URL if (permit or meetings) else GUARD_URL

    def handler(route: Any) -> None:
        req = route.request
        recording_write = (permit is not None and urlsplit(req.url).path.startswith("/nws/recording/")
                           and req.method != "GET" and req.resource_type not in STATIC_TYPES)
        meeting_write = meetings and is_meeting_write(req.url, req.method, req.resource_type)
        if is_mutation(req.url, req.method, req.resource_type) or recording_write or meeting_write:
            if permit is not None and permit.accept(req.url, req.method, req.resource_type, req.post_data):
                if meetings:
                    _send_without_redirect(route, blocked, req.url)
                else:
                    route.fallback()
                return
            allows_read = getattr(permit, "allows_read", None)
            if allows_read is not None and allows_read(req.url, req.method, req.resource_type, req.post_data):
                route.fallback()
                return
            blocked.append(f"{req.method} {path_only(req.url)}")
            route.abort()
        else:
            route.fallback()

    target.route(matcher, handler)
    return lambda: target.unroute(matcher, handler)


# ------------------------------------------------------------------ meetings: UI guards
#
# Every meeting-page click or fill names an operation. The operation fixes the element's role and
# the exact accessible name (aria-label, or visible text when there is no aria-label) it may have;
# the caller's locator must resolve to exactly one visible, enabled element in the current form or
# dialog. Anything else raises SelectorError with the operation, the expected name and the count,
# and nothing is clicked. Writes need their permit: Save only while a CreateMeetingPermit is armed,
# the dialog's Delete only while a DeleteMeetingPermit is armed.

class SelectorError(GuardError):
    """A meeting control was missing, ambiguous, or not the expected one; nothing was clicked."""

    def __init__(self, op: str, expected: str, found: int | None = None, reason: str = ""):
        self.detail = {"op": op, "expected": expected}
        if found is not None:
            self.detail["found"] = found
        if reason:
            self.detail["reason"] = reason
        msg = f"{op}: expected exactly one {expected!r}"
        msg += f", found {found}" if found is not None else ""
        super().__init__(msg + (f" ({reason})" if reason else "") + "; nothing was clicked")


COMBOBOXES = frozenset({"Choose date", "Select start time", "Select start time unit", "select duration hours",
                        "select duration minutes", "Select time zone"})
TIME_OPTION = re.compile(r"(1[0-2]|[1-9]):[0-5]\d")
UNIT_OPTION = re.compile(r"AM|PM")
NUMBER_OPTION = re.compile(r"\d{1,2}")
TZ_OPTION = re.compile(r"\(GMT[+-]\d{1,2}:\d{2}\) [^\n]{1,80}")
DATE_CELL = re.compile(r"(Today, )?(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),"
                       r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                       r" \d{1,2},\d{4} (not )?selected")
DELETE_DIALOG_TITLE = "Delete Meeting"
DELETE_DIALOG_RECOVERY = "You can recover this meeting within 7 days from Recently Deleted."

# op -> (role, allowed names: a set of exact strings or one fullmatch pattern)
MEETING_CLICKS: dict[str, tuple[str, Any]] = {
    "schedule_entry": ("button", {"Schedule a Meeting"}),
    "open_combobox": ("combobox", COMBOBOXES),
    "next_month": ("button", {"Next month"}),
    "pick_date": ("button", DATE_CELL),
    "pick_option": ("option", re.compile("|".join(p.pattern for p in (TIME_OPTION, UNIT_OPTION, NUMBER_OPTION,
                                                                     TZ_OPTION)))),
    "add_description": ("button", {"Add Description"}),
    "save_meeting": ("button", {"Save"}),
    "open_delete": ("button", {"Delete"}),
    "confirm_delete": ("button", {"Delete"}),
}
# op -> (tag, aria-label). The invitee field is deliberately absent: it cannot be filled.
MEETING_FILLS: dict[str, tuple[str, str]] = {
    "topic": ("INPUT", "Topic"),
    "description": ("TEXTAREA", "Add Description"),
    "start_time": ("INPUT", "Select start time"),
    "time_zone_filter": ("INPUT", "Select time zone"),
}
IMPLICIT_ROLES = {"BUTTON": "button", "TEXTAREA": "textbox", "SELECT": "combobox"}

DESCRIBE_JS = """e => ({
  tag: e.tagName, role: e.getAttribute('role') || '', label: e.getAttribute('aria-label') || '',
  text: (e.innerText || '').replace(/\\s+/g, ' ').trim(), href: e.getAttribute('href') || '',
  type: e.getAttribute('type') || '', id: e.id || '',
  disabled: e.getAttribute('aria-disabled') === 'true' || e.disabled === true,
  dialog: e.closest('[role=dialog]') ? (e.closest('[role=dialog]').innerText || '').replace(/\\s+/g, ' ').trim() : null
})"""


def describe(locator: Any) -> dict[str, Any]:
    """Role, names and state of the single element behind `locator` (no values of password inputs)."""
    return locator.evaluate(DESCRIBE_JS)


def _role(info: dict[str, Any]) -> str:
    if info.get("role"):
        return info["role"]
    if info.get("tag") == "A" and info.get("href"):
        return "link"
    return IMPLICIT_ROLES.get(info.get("tag", ""), "")


def _name_ok(name: str, allowed: Any) -> bool:
    if isinstance(allowed, re.Pattern):
        return bool(allowed.fullmatch(name))
    return name in allowed


def require_one(locator: Any, op: str, expected: str) -> dict[str, Any]:
    """Verify, without clicking, that `locator` is exactly one visible, enabled control for `op`
    named `expected`. Returns its description. Used for plans and before every click."""
    if op not in MEETING_CLICKS:
        raise SelectorError(op, expected, reason="unknown operation")
    role, allowed = MEETING_CLICKS[op]
    found = locator.count()
    if found != 1:
        raise SelectorError(op, expected, found)
    info = describe(locator)
    name = " ".join((info.get("label") or info.get("text") or "").split())
    if _role(info) != role:
        raise SelectorError(op, expected, 1, f"role is {_role(info)!r}, not {role!r}")
    if name != expected or not _name_ok(name, allowed):
        raise SelectorError(op, expected, 1, f"name is {name!r}")
    if info.get("disabled") or not locator.is_visible():
        raise SelectorError(op, expected, 1, "control is hidden or disabled")
    return info


def meeting_click(locator: Any, op: str, expected: str, permit: Any = None, timeout_ms: int = 10000) -> str:
    """Click one meeting control after `require_one` and the operation's own rule."""
    info = require_one(locator, op, expected)
    dialog = info.get("dialog")
    if op == "schedule_entry" and urlsplit(info.get("href") or "").path != "/meeting/schedule":
        raise SelectorError(op, expected, 1, "does not lead to the schedule form")
    if op == "save_meeting" and not (isinstance(permit, CreateMeetingPermit) and permit.armed and not permit.consumed):
        raise GuardError("refusing to press Save: no armed create-meeting permit")
    if op == "open_delete":
        if not isinstance(permit, DeleteMeetingPermit) or dialog is not None:
            raise GuardError("refusing to press Delete: not the meeting page's own Delete for a delete command")
    if op == "confirm_delete":
        if not (isinstance(permit, DeleteMeetingPermit) and permit.armed and not permit.consumed):
            raise GuardError("refusing to confirm Delete: no armed delete-meeting permit")
        if dialog is None or DELETE_DIALOG_TITLE not in dialog or DELETE_DIALOG_RECOVERY not in dialog:
            raise GuardError("refusing to confirm Delete: the dialog is not the recoverable Delete Meeting dialog")
    if op not in ("open_delete", "confirm_delete") and dialog is not None and DELETE_DIALOG_TITLE in dialog:
        raise GuardError("refusing to click inside a Delete Meeting dialog")
    locator.click(timeout=timeout_ms)
    return expected


def meeting_fill(locator: Any, op: str, value: str, timeout_ms: int = 10000) -> None:
    """Type `value` into one allowlisted meeting field and confirm the field holds it."""
    if op not in MEETING_FILLS:
        raise GuardError(f"refusing to type into {op!r}: not an allowlisted field")
    tag, label = MEETING_FILLS[op]
    found = locator.count()
    if found != 1:
        raise SelectorError(op, label, found)
    info = describe(locator)
    if info.get("tag") != tag or info.get("label") != label:
        raise SelectorError(op, label, 1, f"field is {info.get('tag')} {info.get('label')!r}")
    if info.get("disabled") or not locator.is_visible() or info.get("type") == "password":
        raise SelectorError(op, label, 1, "field is hidden, disabled or a password field")
    if "\n" in value or not 0 < len(value) <= (2000 if op == "description" else 200):
        raise GuardError(f"refusing to type into {label!r}: value length or shape")
    if op == "start_time" and not TIME_OPTION.fullmatch(value):
        raise GuardError("start time must look like H:MM")
    locator.fill(value, timeout=timeout_ms)
    if op in ("topic", "description") and locator.input_value() != value:
        raise GuardError(f"{label!r} does not hold the typed value")


CHECKBOX_LABEL_JS = """e => {
  const wrap = e.closest('.zoom-checkbox__wrap');
  return {type: e.getAttribute('type') || '', checked: e.checked === true,
          disabled: e.disabled === true || e.getAttribute('aria-disabled') === 'true',
          wrap_label: wrap ? (wrap.innerText || '').replace(/\\s+/g, ' ').trim() : null,
          inputs_in_wrap: wrap ? wrap.querySelectorAll('input[type=checkbox]').length : 0};
}"""


def uncheck_waiting_room(locator: Any) -> bool:
    """Clear the one checkbox whose row text is exactly "Waiting Room". Returns True if it clicked.

    The only checkbox a meeting command may change. Passcode and every other checkbox are read-only.
    """
    found = locator.count()
    if found != 1:
        raise SelectorError("uncheck_waiting_room", "Waiting Room", found)
    info = locator.evaluate(CHECKBOX_LABEL_JS)
    if info.get("type") != "checkbox" or info.get("wrap_label") != "Waiting Room" or info.get("inputs_in_wrap") != 1:
        raise SelectorError("uncheck_waiting_room", "Waiting Room", 1, "not the Waiting Room checkbox")
    if info.get("disabled"):
        raise GuardError("Waiting Room is locked by account policy; nothing was clicked")
    if not info.get("checked"):
        return False
    wrapper = locator.locator("xpath=..")
    if wrapper.count() != 1 or "zoom-checkbox__wrap" not in (wrapper.get_attribute("class") or "").split():
        raise SelectorError("uncheck_waiting_room", "Waiting Room", wrapper.count(), "unexpected wrapper")
    wrapper.click(timeout=10000)
    if locator.evaluate(CHECKBOX_LABEL_JS).get("checked"):
        raise GuardError("Waiting Room did not clear")
    return True
