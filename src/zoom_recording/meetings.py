"""Driving the Zoom meeting pages: read the upcoming list, schedule one meeting, delete one meeting.

The page makes its own requests with its own headers. The tool reads JSON responses (the upcoming
list, a meeting's view, the time-zone table), clicks and types only through `guard`, and lets
exactly one write through per command with a one-shot permit:

- `schedule`: `POST /rest/meeting/save` for a new meeting with the requested fields, at the moment
  Save is pressed;
- `delete`: `POST /meeting/delete` for the requested meeting number, at the moment the dialog's
  Delete is pressed.

Every other write is aborted by the context-wide request guard. Waiting for state (an option to
appear, a value to read back) replaces fixed sleeps.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import guard, zoomweb
from .demo import Demo

LIST_PATH = "/rest/meeting/list"
VIEW_PATH = "/rest/meeting/view"
TIMEZONE_PATH = "/rest/meeting/timezone"
DEFAULTS_PATH = "/rest/meeting/schedule"
LIST_FIELDS = ("page", "dateDuration")
LIST_RE = re.compile(r"^https://([^/?#]*\.)?zoom\.us/rest/meeting/list(\?.*)?$")
MEETING_URL = re.compile(r"/meeting/(\d{9,12})(?:[/?#]|$)")
MEETING_ID = re.compile(r"\d{9,12}")
GMT_PREFIX = re.compile(r"^\(GMT[+-]\d{1,2}:\d{2}\)\s*")
DEFAULT_WINDOW_DAYS = 89
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
CONTROLS = ("Choose date", "Select start time", "Select start time unit", "select duration hours",
            "select duration minutes", "Select time zone")
VALUES_JS = """els => els.map(e => [e.getAttribute('aria-label'),
  (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') ? e.value : (e.innerText || '').replace(/\\s+/g, ' ').trim()])"""
CHECKBOXES_JS = """els => els.map(e => {
  const wrap = e.closest('.zoom-checkbox__wrap');
  return [wrap ? (wrap.innerText || '').replace(/\\s+/g, ' ').trim() : '', e.checked === true];
})"""


class SpecError(ValueError):
    """The requested meeting cannot be scheduled as given (a usage error)."""


# ------------------------------------------------------------------ the requested meeting

@dataclass(frozen=True)
class Spec:
    topic: str
    day: date
    hour12: int
    minute: int
    unit: str
    duration: int
    tz: str
    description: str | None = None

    @property
    def start_date(self) -> str:
        return self.day.strftime("%m/%d/%Y")

    @property
    def start_time(self) -> str:
        return f"{self.hour12}:{self.minute:02d}"

    @property
    def hours(self) -> int:
        return self.duration // 60

    @property
    def minutes(self) -> int:
        return self.duration % 60

    @property
    def cell_label(self) -> str:
        """The date cell's label without its selection suffix, e.g. "Tuesday,November 3,2026"."""
        return f"{WEEKDAYS[self.day.weekday()]},{MONTHS[self.day.month - 1]} {self.day.day},{self.day.year}"

    def local_start(self) -> datetime:
        hour24 = self.hour12 % 12 + (12 if self.unit == "PM" else 0)
        return datetime(self.day.year, self.day.month, self.day.day, hour24, self.minute, tzinfo=ZoneInfo(self.tz))

    def public(self) -> dict[str, Any]:
        start = self.local_start()
        return {"topic": self.topic, "date": self.day.isoformat(), "start": f"{self.start_time} {self.unit}",
                "duration_min": self.duration, "timezone": self.tz,
                "start_utc": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_utc": (start + timedelta(minutes=self.duration)).astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                **({"description": self.description} if self.description else {})}

    def permit_fields(self) -> dict[str, Any]:
        return {"topic": self.topic, "agenda": self.description or None, "start_date": self.start_date,
                "start_time": self.start_time, "start_unit": self.unit, "duration": self.duration,
                "timezone": self.tz}


def parse_spec(topic: str, day: str, start: str, duration: int, tz: str, description: str | None = None,
               now: datetime | None = None) -> Spec:
    """Validate the command-line request. Zoom's pickers offer quarter hours only."""
    topic = " ".join((topic or "").split())
    if not topic or len(topic) > 200:
        raise SpecError("--topic must be 1 to 200 characters")
    if description is not None and (not description.strip() or "\n" in description or len(description) > 2000):
        raise SpecError("--description must be one line of at most 2000 characters")
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise SpecError(f"--date expects YYYY-MM-DD, got {day!r}") from None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*", start or "")
    if not m:
        raise SpecError(f"--start expects HH:MM (24-hour) or H:MM AM/PM, got {start!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if m.group(3):
        unit = m.group(3).upper()
        if not 1 <= hour <= 12:
            raise SpecError("--start with AM/PM needs an hour from 1 to 12")
        hour12 = hour
    else:
        if not 0 <= hour <= 23:
            raise SpecError("--start hour must be 0-23")
        unit = "PM" if hour >= 12 else "AM"
        hour12 = hour % 12 or 12
    if minute % 15 or minute > 45:
        raise SpecError("--start minutes must be 00, 15, 30 or 45 (the options Zoom's picker offers)")
    if duration <= 0 or duration % 15 or duration > 24 * 60:
        raise SpecError("--duration must be a positive multiple of 15 minutes, at most 1440")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise SpecError(f"--tz expects an IANA time zone such as America/Los_Angeles, got {tz!r}") from None
    spec = Spec(topic, d, hour12, minute, unit, duration, tz, description.strip() if description else None)
    if spec.local_start() <= (now or datetime.now(timezone.utc)):
        raise SpecError("the requested start is not in the future")
    return spec


# ------------------------------------------------------------------ small helpers

def wait_until(page: Any, check: Callable[[], Any], what: str, timeout_ms: int = 10000) -> Any:
    """Poll `check` until it returns something truthy; the page sets the pace, not a fixed sleep."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise zoomweb.PageError(f"{what} did not happen within {timeout_ms // 1000} s")
        page.wait_for_timeout(50)


def tz_name(display: str) -> str:
    """"(GMT-7:00) Pacific Time (US and Canada)" -> "Pacific Time (US and Canada)"."""
    return GMT_PREFIX.sub("", " ".join((display or "").split()))


def rewrite_list_form(post_data: str | None, overrides: dict[str, str]) -> str:
    """Pure: replace only the paging/window fields of the page's own upcoming-list form."""
    unknown = set(overrides) - set(LIST_FIELDS)
    if unknown:
        raise ValueError(f"not a list field: {sorted(unknown)}")
    pairs = parse_qsl(post_data or "", keep_blank_values=True)
    out = [(k, overrides.get(k, v)) for k, v in pairs]
    out += [(k, v) for k, v in overrides.items() if k not in {k for k, _ in pairs}]
    return urlencode(out)


def window_for(day: date | None, today: date | None = None) -> dict[str, str]:
    """A dateDuration override when `day` is beyond the page's default three-month window."""
    today = today or date.today()
    if day is None or day <= today + timedelta(days=DEFAULT_WINDOW_DAYS):
        return {}
    return {"dateDuration": f"{today.isoformat()},{(day + timedelta(days=1)).isoformat()}"}


# ------------------------------------------------------------------ reading

def parse_upcoming(body: Any) -> dict[str, Any]:
    """Pure: the page's upcoming-list JSON into meeting records (no join links, no passcodes)."""
    if not isinstance(body, dict) or body.get("status") is not True or not isinstance(body.get("result"), dict):
        raise zoomweb.PageError(f"{LIST_PATH}: unexpected response")
    result = body["result"]
    groups = result.get("meetings")
    if not isinstance(groups, list):
        raise zoomweb.PageError(f"{LIST_PATH}: no meeting list in the response")
    meetings = []
    for group in groups:
        items = group.get("list") if isinstance(group, dict) else None
        if not isinstance(items, list):
            raise zoomweb.PageError(f"{LIST_PATH}: unexpected group shape")
        for item in items:
            number = str(item.get("number", "")) if isinstance(item, dict) else ""
            if not MEETING_ID.fullmatch(number) or not isinstance(item.get("topic"), str):
                raise zoomweb.PageError(f"{LIST_PATH}: unexpected meeting shape")
            meetings.append({"id": number, "topic": item["topic"], "when": item.get("schDate"),
                             "time_range": item.get("schTimeF"), "duration_min": item.get("duration"),
                             "recurring": item.get("type") not in (None, 2)})
    total = result.get("totalRecords")
    return {"total": total if isinstance(total, int) else None, "meetings": meetings}


def load_upcoming(page: Any, overrides: dict[str, str] | None = None, timeout_ms: int = 45000) -> dict[str, Any]:
    """Open the upcoming list and return the page's own list response (parsed)."""
    overrides = overrides or {}

    def handler(route: Any) -> None:
        req = route.request
        if req.method == "POST":
            route.continue_(post_data=rewrite_list_form(req.post_data, overrides))
        else:
            route.continue_()

    if overrides:
        page.route(LIST_RE, handler)
    try:
        try:
            with page.expect_response(
                    lambda r: urlsplit(r.url).path == LIST_PATH and r.request.method == "POST"
                    and "listType=upcoming" in (r.request.post_data or ""), timeout=timeout_ms) as info:
                if urlsplit(page.url or "").path == "/meeting" and zoomweb.is_zoom_page(page):
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    # No fragment: a fragment-only change would not reload the list.
                    page.goto(zoomweb.origin_of(page) + "/meeting", wait_until="domcontentloaded", timeout=timeout_ms)
            resp = info.value
        except Exception as e:  # noqa: BLE001 - classify below
            if zoomweb._signed_out(page):
                raise zoomweb.NeedsSignIn() from None
            raise zoomweb.PageError(f"the upcoming meetings list did not load: {type(e).__name__}") from None
        if zoomweb._signed_out(page):
            raise zoomweb.NeedsSignIn()
        if resp.status != 200:
            raise zoomweb.PageError(f"{LIST_PATH}: HTTP {resp.status}")
        return parse_upcoming(resp.json())
    finally:
        if overrides:
            page.unroute(LIST_RE, handler)


def list_all(page: Any, max_pages: int, window: dict[str, str] | None = None,
             log: Callable[[str], None] = lambda s: None) -> dict[str, Any]:
    meetings: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    total = None
    for n in range(1, max_pages + 1):
        overrides = dict(window or {})
        if n > 1:
            overrides["page"] = str(n)
        listing = load_upcoming(page, overrides)
        total = listing["total"]
        new = []
        for m in listing["meetings"]:
            if (m["id"], m["when"]) not in seen:  # one entry per meeting occurrence, even within a page
                seen.add((m["id"], m["when"]))
                new.append(m)
        meetings.extend(new)
        log(f"upcoming page {n}: {len(listing['meetings'])} meetings (total {total})")
        if not new or total is None or len(meetings) >= total:
            break
    out: dict[str, Any] = {"total": total, "count": len(meetings), "meetings": meetings}
    if total is not None and len(meetings) < total:
        out["truncated"] = f"stopped after {max_pages} pages; raise --max-pages"
    return out


MAX_CHECK_PAGES = 20


def upcoming_complete(page: Any, window: dict[str, str] | None = None) -> dict[str, Any]:
    """Every page of the upcoming list; a duplicate check or a "gone" check needs all of it."""
    listing = list_all(page, MAX_CHECK_PAGES, window)
    if listing.get("truncated") or listing["total"] is None:
        raise zoomweb.PageError("the upcoming list could not be read completely; nothing was decided from it")
    return listing


def _unwrap(meeting: dict[str, Any], key: str) -> Any:
    entry = meeting.get(key)
    return entry.get("value") if isinstance(entry, dict) else entry


def view_number(meeting: dict[str, Any]) -> str | None:
    """The meeting number inside a view response: `meetingId.value`, an integer (observed 2026-10-31)."""
    value = _unwrap(meeting, "meetingId")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return str(value) if MEETING_ID.fullmatch(str(value)) else None


def read_meeting(page: Any, meeting_id: str, timeout_ms: int = 45000) -> dict[str, Any]:
    """Open the meeting's information page and read the page's own view response.

    Only schedule fields are kept; the passcode and join links in that response are dropped.
    """
    url = f"{zoomweb.origin_of(page)}/meeting/{meeting_id}"
    try:
        with page.expect_response(lambda r: urlsplit(r.url).path == VIEW_PATH and r.request.method == "POST",
                                  timeout=timeout_ms) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        resp = info.value
    except Exception as e:  # noqa: BLE001
        if zoomweb._signed_out(page):
            raise zoomweb.NeedsSignIn() from None
        raise zoomweb.PageError(f"the meeting page did not load: {type(e).__name__}") from None
    if zoomweb._signed_out(page):
        raise zoomweb.NeedsSignIn()
    body = resp.json() if resp.status == 200 else None
    result = body.get("result") if isinstance(body, dict) and body.get("status") is True else None
    meeting = result.get("meeting") if isinstance(result, dict) else None
    if not isinstance(meeting, dict) or not isinstance(_unwrap(meeting, "topic"), str):
        raise zoomweb.PageError("no such meeting, or the meeting view did not load")
    if view_number(meeting) != meeting_id or zoomweb.origin_of(page) + f"/meeting/{meeting_id}" not in (page.url or ""):
        raise zoomweb.PageError("the meeting page is not the requested meeting number")
    return {"id": meeting_id, "topic": _unwrap(meeting, "topic"), "start_date": _unwrap(meeting, "startDate"),
            "start_time": _unwrap(meeting, "startTime"), "start_unit": _unwrap(meeting, "startTime2"),
            "duration_min": _unwrap(meeting, "duration"), "timezone": _unwrap(meeting, "timezone"),
            "recurring": _unwrap(meeting, "recurring") not in (None, False, "", {})}


def schedule_form(page: Any) -> Any:
    form = page.locator("form").filter(has=page.locator("#topic"))
    found = form.count()
    if found != 1:
        raise guard.SelectorError("schedule_form", "form with Topic", found)
    return form


def combobox(form: Any, label: str) -> Any:
    return form.locator(f'[role="combobox"][aria-label="{label}"]')


def read_controls(form: Any) -> dict[str, Any]:
    """Topic, the six schedule controls (inputs before interaction, text spans after) and the two
    security checkboxes by row text. Each must appear exactly once."""
    values: dict[str, list[str]] = {}
    for label, value in form.locator('[role="combobox"][aria-label]').evaluate_all(VALUES_JS):
        values.setdefault(label, []).append(value)
    boxes: dict[str, list[bool]] = {}
    for label, checked in form.locator('input[type="checkbox"]').evaluate_all(CHECKBOXES_JS):
        boxes.setdefault(label, []).append(checked)
    out: dict[str, Any] = {"topic": form.locator("#topic").input_value()}
    for label in CONTROLS:
        found = values.get(label, [])
        if len(found) != 1:
            raise guard.SelectorError("read_control", label, len(found))
        out[label] = found[0]
    for label in ("Passcode", "Waiting Room"):
        found = boxes.get(label, [])
        if len(found) != 1:
            raise guard.SelectorError("read_checkbox", label, len(found))
        out[label] = found[0]
    return out


def compare(spec: Spec, controls: dict[str, Any], tz_display: str | None, passcode: bool | None) -> dict[str, Any]:
    """Field-by-field comparison of the form (or edit page) with the request."""
    rows = {
        "topic": (spec.topic, controls["topic"]),
        "date": (spec.start_date, controls["Choose date"]),
        "start_time": (spec.start_time, controls["Select start time"]),
        "start_unit": (spec.unit, controls["Select start time unit"]),
        "duration_hours": (str(spec.hours), controls["select duration hours"]),
        "duration_minutes": (str(spec.minutes), controls["select duration minutes"]),
        "time_zone": (tz_name(tz_display or ""), tz_name(controls["Select time zone"])),
        "waiting_room": (False, controls["Waiting Room"]),
    }
    if passcode is not None:
        rows["passcode"] = (passcode, controls["Passcode"])
    return {k: {"expected": e, "actual": a, "ok": e == a and (k != "time_zone" or bool(e))}
            for k, (e, a) in rows.items()}


def _show_controls(demo: Demo, page: Any, form: Any, controls: dict[str, Any]) -> None:
    if not demo.enabled:
        return
    demo.show(form.locator("#topic"), f'textbox · "Topic" · {controls["topic"]}', page)
    for label in CONTROLS:
        demo.show(combobox(form, label), f'combobox · "{label}" · {controls[label]}', page)
    for label in ("Passcode", "Waiting Room"):
        wrap = form.locator(".zoom-checkbox__wrap").filter(has_text=re.compile(rf"^\s*{label}\s*$"))
        demo.show(wrap, f'checkbox · row "{label}" · {"checked" if controls[label] else "unchecked"}', page)


# ------------------------------------------------------------------ schedule

def schedule_plan(page: Any, spec: Spec, demo: Demo) -> dict[str, Any]:
    """Read-only: check the upcoming list for the same meeting and that the entry control exists."""
    listing = upcoming_complete(page, window_for(spec.day))
    clash = duplicates(listing["meetings"], spec)
    entry = page.get_by_role("button", name="Schedule a Meeting", exact=True)
    guard.require_one(entry, "schedule_entry", "Schedule a Meeting")
    demo.show(entry, 'button · "Schedule a Meeting" · would open the form (plan only, not clicked)', page)
    demo.clear(page)
    return {"duplicates": clash}


def duplicates(meetings: list[dict[str, Any]], spec: Spec) -> list[dict[str, Any]]:
    """Upcoming meetings with the same topic whose listed start matches the requested one."""
    d = spec.day
    prefix = f"{WEEKDAYS[d.weekday()][:3]}, {MONTHS[d.month - 1][:3]} {d.day} {spec.start_time} {spec.unit}"
    return [m for m in meetings if m["topic"] == spec.topic and str(m.get("when") or "").startswith(prefix)]


def _open_form(page: Any, demo: Demo, timeout_ms: int) -> tuple[Any, dict[str, str], dict[str, Any]]:
    """Open the form; return it, the page's time-zone table and the form's own defaults.

    The defaults (`GET /rest/meeting/schedule`, a new meeting's `meeting` object) bound the create
    permit: every field the command does not set must keep its default. They stay in memory."""
    entry = page.get_by_role("button", name="Schedule a Meeting", exact=True)
    demo.show(entry, 'button · "Schedule a Meeting"', page)
    try:
        with page.expect_response(lambda r: urlsplit(r.url).path == DEFAULTS_PATH and r.request.method == "GET"
                                  and "meetingNumber" not in urlsplit(r.url).query, timeout=timeout_ms) as defaults_info:
            with page.expect_response(lambda r: urlsplit(r.url).path == TIMEZONE_PATH and r.request.method == "GET",
                                      timeout=timeout_ms) as info:
                guard.meeting_click(entry, "schedule_entry", "Schedule a Meeting")
        body = info.value.json()
        defaults_body = defaults_info.value.json()
    except guard.GuardError:
        raise
    except Exception as e:  # noqa: BLE001
        raise zoomweb.PageError(f"the schedule form did not load: {type(e).__name__}") from None
    table = body.get("result") if isinstance(body, dict) else None
    if not isinstance(table, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in table.items()):
        raise zoomweb.PageError(f"{TIMEZONE_PATH}: unexpected response")
    result = defaults_body.get("result") if isinstance(defaults_body, dict) and defaults_body.get("status") is True else None
    defaults = result.get("meeting") if isinstance(result, dict) else None
    if not isinstance(defaults, dict) or not isinstance(_unwrap(defaults, "scheduleFor"), str):
        raise zoomweb.PageError(f"{DEFAULTS_PATH}: unexpected response")
    page.locator("#topic").wait_for(state="visible", timeout=timeout_ms)
    return schedule_form(page), table, defaults


def _pick(page: Any, form: Any, label: str, option: str, demo: Demo, read: Callable[[], str | None],
          typed: str | None = None, fill_op: str | None = None) -> None:
    box = combobox(form, label)
    demo.show(box, f'combobox · "{label}" · {read()} → {option}', page)
    guard.meeting_click(box, "open_combobox", label)
    if typed is not None and fill_op is not None:
        guard.meeting_fill(box, fill_op, typed)
    choice = page.get_by_role("option", name=option, exact=True)
    wait_until(page, lambda: choice.count() >= 1 and choice.first.is_visible(), f'option "{option}" of "{label}" to appear')
    demo.show(choice, f'option · "{option}"', page)
    guard.meeting_click(choice, "pick_option", option)


def _set_date(page: Any, form: Any, spec: Spec, demo: Demo, read: Callable[[], str | None]) -> None:
    box = combobox(form, "Choose date")
    demo.show(box, f'combobox · "Choose date" · {read()} → {spec.start_date}', page)
    guard.meeting_click(box, "open_combobox", "Choose date")
    grid = page.get_by_role("grid")
    wait_until(page, lambda: grid.count() >= 1, "the date picker to open")
    cell = page.get_by_role("button", name=re.compile(rf"^(Today, )?{re.escape(spec.cell_label)} (not )?selected$"))
    target = (spec.day.year, spec.day.month)
    for _ in range(25):
        if cell.count():
            break
        shown = [datetime.strptime(g, "%B %Y") for g in grid.evaluate_all("els => els.map(e => e.getAttribute('aria-label') || '')")
                 if re.fullmatch(r"[A-Z][a-z]+ \d{4}", g or "")]
        if len(shown) != 1 or (shown[0].year, shown[0].month) >= target:
            raise guard.SelectorError("pick_date", spec.cell_label, 0, "date cell not in the picker")
        nxt = page.get_by_role("button", name="Next month", exact=True)
        guard.meeting_click(nxt, "next_month", "Next month")
        before = shown[0]
        wait_until(page, lambda: [g for g in grid.evaluate_all("els => els.map(e => e.getAttribute('aria-label') || '')")
                                  if g and g != before.strftime("%B %Y")], "the date picker to change month")
    found = cell.count()
    if found != 1:
        raise guard.SelectorError("pick_date", spec.cell_label, found)
    label = cell.get_attribute("aria-label") or ""
    demo.show(cell, f'date cell · "{label}"', page)
    guard.meeting_click(cell, "pick_date", label)


def try_controls(form: Any) -> dict[str, Any] | None:
    """`read_controls`, or None while a control is being swapped (an input becomes a text span)."""
    try:
        return read_controls(form)
    except guard.SelectorError:
        return None


def settle(page: Any, form: Any, ready: Callable[[dict[str, Any]], bool], timeout_ms: int) -> dict[str, Any]:
    """Read the controls once `ready` holds; after the timeout return the last complete read so the
    caller reports a mismatch instead of a timeout."""
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while True:
        current = try_controls(form)
        last = current or last
        if current and ready(current):
            return current
        if time.monotonic() >= deadline:
            if last is None:
                return read_controls(form)  # raises the structured selector error
            return last
        page.wait_for_timeout(50)


def fill_form(page: Any, form: Any, spec: Spec, tz_display: str, demo: Demo) -> dict[str, Any]:
    """Fill topic, description, date, start, unit, duration and (when it differs) time zone."""

    def value(label: str) -> Callable[[], str | None]:
        return lambda: (try_controls(form) or {}).get(label)

    topic = form.locator("#topic")
    demo.show(topic, f'textbox · "Topic" · → {spec.topic}', page)
    guard.meeting_fill(topic, "topic", spec.topic)
    if spec.description:
        add = form.get_by_role("button", name="Add Description", exact=True)
        demo.show(add, 'button · "Add Description"', page)
        guard.meeting_click(add, "add_description", "Add Description")
        area = form.locator('textarea[aria-label="Add Description"]')
        wait_until(page, lambda: area.count() == 1, "the description field to appear")
        guard.meeting_fill(area, "description", spec.description)
    _set_date(page, form, spec, demo, value("Choose date"))
    wait_until(page, lambda: value("Choose date")() == spec.start_date, "the date to read back")
    _pick(page, form, "Select start time", spec.start_time, demo, value("Select start time"),
          typed=spec.start_time, fill_op="start_time")
    wait_until(page, lambda: value("Select start time")() == spec.start_time, "the start time to read back")
    _pick(page, form, "Select start time unit", spec.unit, demo, value("Select start time unit"))
    wait_until(page, lambda: value("Select start time unit")() == spec.unit, "AM/PM to read back")
    # Typed minutes are reset on blur; only a picked option sticks. Hours first, then minutes.
    _pick(page, form, "select duration hours", str(spec.hours), demo, value("select duration hours"))
    wait_until(page, lambda: value("select duration hours")() == str(spec.hours), "duration hours to read back")
    _pick(page, form, "select duration minutes", str(spec.minutes), demo, value("select duration minutes"))
    wait_until(page, lambda: value("select duration minutes")() == str(spec.minutes), "duration minutes to read back")
    if tz_name(value("Select time zone")() or "") != tz_name(tz_display):
        _pick(page, form, "Select time zone", tz_display, demo, value("Select time zone"),
              typed=tz_name(tz_display), fill_op="time_zone_filter")
        wait_until(page, lambda: tz_name(value("Select time zone")() or "") == tz_name(tz_display), "the time zone to read back")
    else:
        demo.show(combobox(form, "Select time zone"), f'combobox · "Select time zone" · {value("Select time zone")()} (already set)', page)
    return read_controls(form)


def schedule_execute(page: Any, spec: Spec, permit: guard.CreateMeetingPermit, demo: Demo,
                     blocked: list[str], timeout_ms: int = 30000) -> dict[str, Any]:
    """Create the meeting, then reopen its edit page and compare every requested field."""
    t0 = time.monotonic()
    marks: dict[str, float] = {}

    def mark(name: str) -> None:
        marks[name] = round(time.monotonic() - t0, 2)

    listing = upcoming_complete(page, window_for(spec.day))
    mark("upcoming_checked")
    clash = duplicates(listing["meetings"], spec)
    if clash:
        return {"ok": False, "error": "duplicate_meeting", "executed": False,
                "message": "an upcoming meeting with this topic and start already exists; nothing was created",
                "duplicates": clash}
    form, tz_table, defaults = _open_form(page, demo, timeout_ms)
    mark("form_open")
    tz_display = tz_table.get(spec.tz)
    if not tz_display:
        raise zoomweb.PageError(f"Zoom's time zone list has no {spec.tz}; nothing was created")
    before = settle(page, form, lambda c: bool(c["Select time zone"]) and bool(c["Choose date"]), timeout_ms)
    passcode_before = before["Passcode"]
    filled = fill_form(page, form, spec, tz_display, demo)
    if filled["Waiting Room"]:
        wrap = form.locator(".zoom-checkbox__wrap").filter(has_text=re.compile(r"^\s*Waiting Room\s*$"))
        demo.show(wrap, 'checkbox · row "Waiting Room" · checked → unchecked', page)
        guard.uncheck_waiting_room(wrap.locator('input[type="checkbox"]'))
        filled = read_controls(form)
    pre = compare(spec, filled, tz_display, passcode_before)
    mark("form_filled")
    _show_controls(demo, page, form, filled)
    if not all(r["ok"] for r in pre.values()):
        return {"ok": False, "error": "form_mismatch", "executed": False,
                "message": "the form did not take the requested values; nothing was saved", "form": pre}
    permit.origin = zoomweb.origin_of(page)
    permit.expected = {**spec.permit_fields(), "passcode": passcode_before, "defaults": defaults}
    save = page.get_by_role("button", name="Save", exact=True)
    demo.show(save, 'button · "Save" · create permit armed for this one meeting', page)
    status: dict[str, int] = {}

    def on_response(r: Any) -> None:
        if urlsplit(r.url).path == guard.CREATE_MEETING_PATH and r.request.method == "POST":
            status["code"] = r.status

    page.on("response", on_response)
    permit.armed = True
    try:
        guard.meeting_click(save, "save_meeting", "Save", permit)
        wait_until(page, lambda: MEETING_URL.search(urlsplit(page.url).path)
                   or (not permit.consumed and any(guard.CREATE_MEETING_PATH in b for b in blocked))
                   or status.get("code", 200) != 200, "the saved meeting page", timeout_ms)
    finally:
        permit.armed = False
        page.remove_listener("response", on_response)
    if not permit.consumed:
        raise guard.GuardError("the save request did not match the create permit and was blocked; nothing was created")
    found = MEETING_URL.search(urlsplit(page.url).path)
    if status.get("code", 200) != 200 or not found:
        raise zoomweb.PageError("save outcome uncertain; run `zoom-cli meetings list` before retrying")
    meeting_id = found.group(1)
    mark("saved")
    demo.say("saved; reading the meeting back from its edit page")
    readback = readback_edit(page, meeting_id, spec, tz_display, passcode_before, demo, timeout_ms)
    listed = any(m["id"] == meeting_id and m["topic"] == spec.topic
                 for m in upcoming_complete(page, window_for(spec.day))["meetings"])
    mark("read_back")
    verified = all(r["ok"] for r in readback.values()) and listed
    return {"ok": verified, "executed": True, "meeting_id": meeting_id, "readback": readback, "timings_s": marks,
            "listed_in_upcoming": listed, "readback_verified": verified,
            **({"error": "readback_mismatch"} if not verified else {})}


def readback_edit(page: Any, meeting_id: str, spec: Spec, tz_display: str, passcode: bool, demo: Demo,
                  timeout_ms: int = 30000) -> dict[str, Any]:
    """Open /meeting/{id}/edit fresh and compare; never press Save there."""
    page.goto(f"{zoomweb.origin_of(page)}/meeting/{meeting_id}/edit", wait_until="domcontentloaded", timeout=timeout_ms)
    page.locator("#topic").wait_for(state="visible", timeout=timeout_ms)
    form = schedule_form(page)
    controls = settle(page, form, lambda c: c["topic"] == spec.topic and c["Choose date"] == spec.start_date, timeout_ms)
    rows = compare(spec, controls, tz_display, passcode)
    if spec.description:
        area = form.locator('textarea[aria-label="Add Description"]')
        actual = area.input_value() if area.count() == 1 else None
        rows["description"] = {"expected": spec.description, "actual": actual, "ok": actual == spec.description}
    _show_controls(demo, page, form, controls)
    demo.clear(page)
    return rows


# ------------------------------------------------------------------ delete

def delete_meeting(page: Any, meeting_id: str, expect_topic: str, *, execute: bool,
                   permit: guard.DeleteMeetingPermit, demo: Demo, blocked: list[str],
                   timeout_ms: int = 30000) -> dict[str, Any]:
    """Delete one whole meeting after its number and topic both match. Plan unless `execute`."""
    info = read_meeting(page, meeting_id, timeout_ms)
    public = {k: info[k] for k in ("id", "topic", "start_date", "start_time", "start_unit", "duration_min", "timezone")}
    if info["topic"] != expect_topic:
        return {"ok": False, "error": "topic_mismatch", "executed": False, "meeting": public,
                "message": "the meeting's topic does not equal --expect-topic; nothing was deleted"}
    if info["recurring"]:
        return {"ok": False, "error": "recurring_meeting", "executed": False, "meeting": public,
                "message": "recurring meetings are not supported; nothing was deleted"}
    heading = page.get_by_text(expect_topic, exact=True)
    wait_until(page, lambda: heading.count() >= 1, "the topic on the meeting page", timeout_ms)
    demo.show(heading.first, f'text · Topic · "{expect_topic}" = --expect-topic', page)
    button = page.get_by_role("button", name="Delete", exact=True)
    wait_until(page, lambda: button.count() >= 1, "the Delete button", timeout_ms)
    guard.require_one(button, "open_delete", "Delete")
    if not execute:
        demo.show(button, 'button · "Delete" · would open the delete dialog (plan only, not clicked)', page)
        demo.clear(page)
        return {"ok": True, "dry_run": True, "executed": False, "meeting": public,
                "will_request": {"method": "POST", "path": guard.DELETE_MEETING_PATH, "send_mail": False},
                "next": "rerun with --execute to delete; Zoom keeps it in Recently Deleted for 7 days"}
    permit.origin = zoomweb.origin_of(page)
    demo.show(button, 'button · "Delete"', page)
    guard.meeting_click(button, "open_delete", "Delete", permit)
    dialog = page.get_by_role("dialog").filter(has_text=guard.DELETE_DIALOG_TITLE)
    wait_until(page, lambda: dialog.count() == 1, "the Delete Meeting dialog", timeout_ms)
    if dialog.locator('input[type="checkbox"]:checked, input[type="radio"]:checked').count():
        raise guard.GuardError("the delete dialog has a pre-selected option (for example notifying people); nothing was deleted")
    confirm = dialog.get_by_role("button", name="Delete", exact=True)
    demo.show(confirm, 'dialog button · "Delete" · delete permit armed for this one meeting', page)
    status: dict[str, int] = {}

    def on_response(r: Any) -> None:
        if urlsplit(r.url).path == guard.DELETE_MEETING_PATH and r.request.method == "POST":
            status["code"] = r.status

    page.on("response", on_response)
    permit.armed = True
    try:
        guard.meeting_click(confirm, "confirm_delete", "Delete", permit)
        wait_until(page, lambda: (permit.consumed and status.get("code") is not None
                                  and not MEETING_URL.search(urlsplit(page.url).path))
                   or (not permit.consumed and any(guard.DELETE_MEETING_PATH in b for b in blocked))
                   or status.get("code", 200) != 200, "the delete to finish", timeout_ms)
    finally:
        permit.armed = False
        page.remove_listener("response", on_response)
    if not permit.consumed:
        raise guard.GuardError("the delete request did not match the delete permit and was blocked; nothing was deleted")
    if status.get("code") != 200:
        raise zoomweb.PageError("delete outcome uncertain; run `zoom-cli meetings list` before retrying")
    # The page goes back to the upcoming list by itself; let that navigation finish first so the
    # readback does not race it.
    wait_until(page, lambda: urlsplit(page.url).path == "/meeting", "the page to return to the meeting list", timeout_ms)
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    demo.say("deleted; reading the upcoming list back")
    day = None
    if isinstance(info.get("start_date"), str):
        try:
            day = datetime.strptime(info["start_date"], "%m/%d/%Y").date()
        except ValueError:
            day = None
    remaining = [m for m in upcoming_complete(page, window_for(day))["meetings"] if m["id"] == meeting_id]
    gone = not remaining
    return {"ok": gone, "dry_run": False, "executed": True, "meeting": public, "absent_from_upcoming": gone,
            "readback_verified": gone, **({"error": "readback_mismatch"} if not gone else {})}
