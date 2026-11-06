"""Meetings: request permits, the meeting request rule, UI guards, planning and the CLI contract.

All identities are synthetic: meeting numbers are 99999999990-style, origins are https://zoom.us.
"""

import contextlib
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from zoom_recording import cli, guard, meetings
from zoom_recording.demo import Demo

ORIGIN = "https://zoom.us"
MID = "99999999990"
OTHER = "99999999991"
TZ_DISPLAY = "(GMT-7:00) Pacific Time (US and Canada)"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def spec(**kw):
    args = dict(topic="Synthetic test meeting", day="2026-11-03", start="10:00", duration=30,
                tz="America/Los_Angeles", description=None, now=NOW)
    args.update(kw)
    return meetings.parse_spec(**args)


def create_body(**changes):
    """A synthetic create body shaped like the schedule form's `POST /rest/meeting/save`."""
    body = {
        "scheduleFor": {"value": "SYNTHETIC_USER", "labeled": False},
        "topic": {"value": "Synthetic test meeting", "labeled": False},
        "agenda": {"value": None, "labeled": False},
        "startDate": {"value": "11/03/2026", "labeled": False},
        "startTime": {"value": "10:00"},
        "startTime2": {"value": "AM"},
        "duration": {"value": 30, "labeled": False},
        "timezone": {"value": "America/Los_Angeles", "labeled": False},
        "recurring": {"value": None, "labeled": False},
        "invitee": {"value": None, "labeled": False},
        "registration": {"value": None, "labeled": False},
        "meetingId": {"value": False, "labeled": False, "childParams": {"pmiNumber": {"value": 1}}},
        "passcode": {"value": True, "labeled": False},
        "waitingRoom": {"value": False, "labeled": False},
        "alternativeHost": {"value": [], "labeled": False},
        "muteUponEntry": {"value": True, "labeled": False},
        "encryption": {"value": None, "labeled": False},
        "audio": {"value": "both", "childParams": {"globalDialinCountries": {"value": ["US"]},
                                                   "channel": {"value": []}}},
        "frontParam": {"groupId": "", "targetUserId": None},
    }
    for key, value in changes.items():
        if value is KeyError:
            body.pop(key)
        else:
            body[key] = value
    return json.dumps(body)


def form_defaults():
    """The form's defaults for a new meeting (GET /rest/meeting/schedule), synthetic."""
    d = json.loads(create_body())
    d["topic"] = {"value": None}
    d["startDate"] = {"value": "10/31/2026"}
    d["startTime"] = {"value": "5:00"}
    d["startTime2"] = {"value": "PM"}
    d["duration"] = {"value": 60}
    d["encryption"] = {"value": False}
    return d


def create_permit(armed=True, **expected):
    exp = {**spec().permit_fields(), "passcode": True, "defaults": form_defaults(), **expected}
    return guard.CreateMeetingPermit(ORIGIN, exp, armed=armed)


SAVE = ORIGIN + guard.CREATE_MEETING_PATH
DELETE = ORIGIN + guard.DELETE_MEETING_PATH


def delete_body(**changes):
    fields = {"user_id": "", "id": MID, "meetingMasterEventId": "", "occurrence": "", "sendMail": "false",
              "mailBody": ""}
    fields.update(changes)
    return "&".join(f"{k}={v}" for k, v in fields.items() if v is not KeyError)


# ------------------------------------------------------------------ create permit

def test_create_permit_accepts_the_exact_new_meeting_once():
    p = create_permit()
    assert p.accept(SAVE, "POST", "xhr", create_body())
    assert not p.accept(SAVE, "POST", "xhr", create_body())
    assert p.audit[0]["path"] == "/rest/meeting/save" and p.audit[0]["creates"]["invitees"] == 0
    assert "SYNTHETIC_USER" not in json.dumps(p.audit)


def test_create_permit_accepts_the_zero_padded_hour_the_form_posts():
    p = create_permit(start_time="2:30", start_unit="PM")
    assert p.accept(SAVE, "POST", "xhr", create_body(startTime={"value": "02:30"}, startTime2={"value": "PM"}))


@pytest.mark.parametrize("changes", [
    {"topic": {"value": "Another topic"}},
    {"agenda": {"value": "unexpected description"}},
    {"startDate": {"value": "11/04/2026"}},
    {"startTime": {"value": "11:00"}},
    {"startTime": {"value": "010:00"}},
    {"startTime2": {"value": "PM"}},
    {"duration": {"value": 60}},
    {"duration": {"value": "30"}},
    {"timezone": {"value": "America/New_York"}},
    {"recurring": {"value": {"type": "DAILY"}}},
    {"invitee": {"value": [{"email": "ada@example.com"}]}},
    {"alternativeHost": {"value": ["ada@example.com"]}},
    {"registration": {"value": True}},
    {"waitingRoom": {"value": True}},
    {"passcode": {"value": False}},
    {"topic": KeyError},
    {"waitingRoom": KeyError},
    {"topic": "Synthetic test meeting"},  # not wrapped in {"value": ...}
    {"meetingId": {"value": False, "childParams": {"meetingNumber": {"value": MID}}}},  # an edit
    {"meetingNumber": MID},
    {"id": MID},
    {"meetingId": {"value": True, "childParams": {"pmiNumber": {"value": 1}}}},  # personal meeting id
    {"scheduleFor": {"value": "SOMEONE_ELSE"}},  # scheduling for another user
    {"muteUponEntry": {"value": False}},  # an untouched setting changed from the form's default
    {"encryption": {"value": True}},
    {"audio": {"value": "both", "childParams": {"globalDialinCountries": {"value": ["XX"]}}}},
    {"autoRec": {"value": "cloud"}},  # a field the form never had
    {"frontParam": {"groupId": "", "targetUserId": "SOMEONE"}},
])
def test_create_permit_fails_closed_on_any_field(changes):
    p = create_permit()
    assert not p.accept(SAVE, "POST", "xhr", create_body(**changes))
    assert not p.consumed and p.audit == [] and p.refused


@pytest.mark.parametrize("url,method,kind", [
    (SAVE + "?meetingNumber=" + MID, "POST", "xhr"),  # edit of an existing meeting
    (SAVE, "GET", "xhr"), (SAVE, "POST", "document"),
    (SAVE.replace("zoom.us", "zoom.us.evil.example"), "POST", "xhr"),
    (ORIGIN + "/rest/meeting/save2", "POST", "xhr"),
])
def test_create_permit_endpoint_is_exact(url, method, kind):
    assert not create_permit().accept(url, method, kind, create_body())


@pytest.mark.parametrize("body", [None, "", "not-json", "[]", '{"topic": {"value": "a"}, "topic": {"value": "b"}}'])
def test_create_permit_rejects_unparseable_or_duplicate_bodies(body):
    assert not create_permit().accept(SAVE, "POST", "xhr", body)


def test_create_permit_unarmed_or_without_form_state_refuses():
    assert not create_permit(armed=False).accept(SAVE, "POST", "xhr", create_body())
    p = guard.CreateMeetingPermit(ORIGIN, {**spec().permit_fields(), "defaults": form_defaults()}, armed=True)
    assert not p.accept(SAVE, "POST", "xhr", create_body())  # passcode state not read yet
    p = guard.CreateMeetingPermit(ORIGIN, {**spec().permit_fields(), "passcode": True}, armed=True)
    assert not p.accept(SAVE, "POST", "xhr", create_body())  # form defaults not captured


def test_unset_optional_sections_are_not_drift():
    body = json.loads(create_body())
    body["encryption"] = {"value": None}  # the form posts this untouched section as null
    body["audio"]["childParams"]["channel"] = {"value": []}
    assert guard._unchanged_from_defaults(body, form_defaults()) == []


def test_null_is_not_a_way_around_a_default():
    body = json.loads(create_body())
    body["muteUponEntry"] = {"value": None}  # default True; null could turn it off
    assert guard._unchanged_from_defaults(body, form_defaults()) == ["muteUponEntry"]
    body = json.loads(create_body())
    body["audio"]["childParams"]["globalDialinCountries"] = {"value": None}
    assert guard._unchanged_from_defaults(body, form_defaults()) == ["audio.globalDialinCountries"]


def test_list_all_counts_each_meeting_once(monkeypatch):
    item = (MID, "Synthetic test meeting", "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)")
    pages = {1: meetings.parse_upcoming(upcoming_body(item, item)), 2: meetings.parse_upcoming(upcoming_body(item))}
    for p in pages.values():
        p["total"] = 2  # the page says two meetings, but one is listed twice
    calls = []

    def load(page, overrides):
        n = int(overrides.get("page", "1"))
        calls.append(n)
        return pages.get(n, {"total": 2, "meetings": []})
    monkeypatch.setattr(meetings, "load_upcoming", load)
    out = meetings.list_all(None, 5)
    assert out["count"] == 1 and calls == [1, 2] and out["truncated"]


def test_create_permit_with_description():
    s = spec(description="One line")
    p = guard.CreateMeetingPermit(ORIGIN, {**s.permit_fields(), "passcode": True, "defaults": form_defaults()},
                                  armed=True)
    assert not p.accept(SAVE, "POST", "xhr", create_body())  # description missing from the body
    assert p.accept(SAVE, "POST", "xhr", create_body(agenda={"value": "One line"}))


# ------------------------------------------------------------------ delete permit

def test_delete_permit_accepts_one_exact_delete():
    p = guard.DeleteMeetingPermit(ORIGIN, MID, armed=True)
    assert p.accept(DELETE, "POST", "xhr", delete_body())
    assert not p.accept(DELETE, "POST", "xhr", delete_body())
    assert p.audit == [{"method": "POST", "path": "/meeting/delete",
                        "deletes": {"meeting": "requested id", "send_mail": False, "occurrence": None}}]


@pytest.mark.parametrize("body", [
    delete_body(id=OTHER), delete_body(sendMail="true"), delete_body(mailBody="bye"),
    delete_body(occurrence="1700000000000"), delete_body(user_id="SOMEONE"), delete_body(mailBody=KeyError),
    delete_body(meetingMasterEventId="SOMEEVENT"),
    delete_body() + "&extra=1", delete_body() + f"&id={MID}", "", None, "id=" + MID,
])
def test_delete_permit_fails_closed(body):
    p = guard.DeleteMeetingPermit(ORIGIN, MID, armed=True)
    assert not p.accept(DELETE, "POST", "xhr", body)
    assert not p.consumed


@pytest.mark.parametrize("url,method,kind,armed", [
    (DELETE, "POST", "xhr", False), (DELETE + "?x=1", "POST", "xhr", True), (DELETE, "GET", "xhr", True),
    (ORIGIN + "/meeting/recover/delete", "POST", "xhr", True),  # Recently Deleted, never
    ("https://evil.example/meeting/delete", "POST", "xhr", True),
])
def test_delete_permit_endpoint_is_exact(url, method, kind, armed):
    assert not guard.DeleteMeetingPermit(ORIGIN, MID, armed=armed).accept(url, method, kind, delete_body())


def test_delete_permit_lets_only_its_own_confirm_lookup_through():
    p = guard.DeleteMeetingPermit(ORIGIN, MID)
    assert p.allows_read(f"{ORIGIN}/meeting/{MID}/confirm?occurrence=undefined", "POST", "xhr", "")
    assert not p.allows_read(f"{ORIGIN}/meeting/{OTHER}/confirm", "POST", "xhr", "")
    assert not p.allows_read(f"{ORIGIN}/meeting/{MID}/confirm", "POST", "xhr", "sendMail=true")
    assert not p.allows_read(f"https://evil.example/meeting/{MID}/confirm", "POST", "xhr", "")
    assert not p.consumed and p.audit == []


# ------------------------------------------------------------------ the meeting request rule

@pytest.mark.parametrize("url,method,kind,blocked", [
    (SAVE, "POST", "xhr", True),
    (DELETE, "POST", "xhr", True),
    (f"{ORIGIN}/meeting/{MID}/confirm", "POST", "xhr", True),
    (ORIGIN + "/rest/meeting/list", "POST", "xhr", False),
    (ORIGIN + "/rest/meeting/view", "POST", "xhr", False),
    (ORIGIN + "/rest/meeting/schedule?meetingMasterEventId=", "GET", "xhr", False),
    (ORIGIN + "/rest/meeting/timezone", "GET", "xhr", False),
    (f"{ORIGIN}/meeting/{MID}/edit", "GET", "document", False),
    (ORIGIN + "/rest/meeting/anything-new", "POST", "fetch", True),
    (ORIGIN + "/profile/setting/update", "POST", "xhr", True),  # account-global settings
    (ORIGIN + "/rest/user/setting", "PATCH", "xhr", True),
    (ORIGIN + "/meeting/recently_deleted/recover", "POST", "xhr", True),
    (ORIGIN + "/meeting/trash/clear", "GET", "xhr", True),
    (ORIGIN + "/rest/invite/send", "POST", "xhr", True),
    (ORIGIN + "/rest/recording/1.0/update-share", "POST", "xhr", True),
    (ORIGIN + "/nws/recording/1.0/download-meeting/abc", "POST", "xhr", True),  # recordings are out of scope
    (ORIGIN + "/rest/anything", "POST", "xhr", True),
    (ORIGIN + "/user/plan/change", "POST", "xhr", True),
    (ORIGIN + "/rest/coach_mark/v1/web_read", "POST", "xhr", False),
    (ORIGIN + "/billing/wording/resolve", "POST", "xhr", False),
    (ORIGIN + "/billing/plan/upgrade", "POST", "xhr", True),
    (ORIGIN + "/csrf_js", "POST", "xhr", False),
    (ORIGIN + "/cdn-cgi/challenge-platform/h/g/jsd/oneshot/abc/save", "POST", "xhr", False),
    ("https://st1.zoom.us/static/delete_dialog.js", "GET", "script", False),
    ("https://example.com/rest/meeting/save", "POST", "xhr", False),
])
def test_is_meeting_write(url, method, kind, blocked):
    assert guard.is_meeting_write(url, method, kind) is blocked


def guarded(permit=None, status=200):
    routes = []
    target = SimpleNamespace(route=lambda m, h: routes.append((m, h)), unroute=lambda m, h: None)
    blocked = []
    guard.install_request_guard(target, blocked, permit, meetings=True)
    matcher, handler = routes[0]

    def send(url, body=None, method="POST", kind="xhr"):
        outcome = []
        req = SimpleNamespace(url=url, method=method, resource_type=kind, post_data=body)
        assert matcher.search(url)

        def fetch(max_redirects=None):
            assert max_redirects == 0  # the permitted write is sent without following redirects
            return SimpleNamespace(status=status)
        handler(SimpleNamespace(request=req, abort=lambda: outcome.append("abort"),
                                fallback=lambda: outcome.append("allow"), fetch=fetch,
                                fulfill=lambda response=None: outcome.append("allow")))
        return outcome[0]
    return send, blocked


def test_a_redirected_permitted_write_is_aborted():
    send, blocked = guarded(create_permit(), status=307)
    assert send(SAVE, create_body()) == "abort"
    assert blocked == ["POST https://zoom.us/rest/meeting/save (redirect refused)"]


def test_meeting_guard_blocks_writes_without_a_permit():
    send, blocked = guarded()
    assert send(SAVE, create_body()) == "abort"
    assert send(DELETE, delete_body()) == "abort"
    assert send(ORIGIN + "/rest/meeting/list", "listType=upcoming") == "allow"
    assert blocked == ["POST https://zoom.us/rest/meeting/save", "POST https://zoom.us/meeting/delete"]


def test_meeting_guard_lets_one_armed_create_through():
    p = create_permit()
    send, _ = guarded(p)
    assert send(DELETE, delete_body()) == "abort"  # a create permit never opens delete
    assert send(SAVE, create_body(invitee={"value": ["ada@example.com"]})) == "abort"
    assert send(SAVE, create_body()) == "allow"
    assert send(SAVE, create_body()) == "abort"


def test_meeting_guard_lets_one_armed_delete_and_its_lookup_through():
    p = guard.DeleteMeetingPermit(ORIGIN, MID)
    send, _ = guarded(p)
    assert send(f"{ORIGIN}/meeting/{MID}/confirm?occurrence=undefined", "") == "allow"
    assert send(DELETE, delete_body()) == "abort"  # not armed yet
    p.armed = True
    assert send(SAVE, create_body()) == "abort"
    assert send(DELETE, delete_body(id=OTHER)) == "abort"
    assert send(DELETE, delete_body()) == "allow"
    assert send(DELETE, delete_body()) == "abort"


def test_recording_guard_is_unchanged_without_meetings_scope():
    routes = []
    target = SimpleNamespace(route=lambda m, h: routes.append((m, h)), unroute=lambda m, h: None)
    guard.install_request_guard(target, [])
    assert routes[0][0] is guard.GUARD_URL


# ------------------------------------------------------------------ UI guards

class Loc:
    """A fake locator for one element (or `count` elements) with a DOM description."""

    def __init__(self, info=None, count=1, visible=True, checked=None, on_click=None, value=None):
        self.info = {"tag": "BUTTON", "role": "", "label": "", "text": "", "href": "", "type": "", "id": "",
                     "disabled": False, "dialog": None, **(info or {})}
        self._count, self.visible, self.clicks, self.fills = count, visible, 0, []
        self.checked, self.on_click, self.value = checked, on_click, value

    def count(self):
        return self._count

    def is_visible(self):
        return self.visible

    def evaluate(self, script, *args):
        if "zoom-checkbox__wrap" in script:
            return {"type": "checkbox", "checked": self.checked, "disabled": False,
                    "wrap_label": self.info.get("wrap_label"), "inputs_in_wrap": 1}
        return dict(self.info)

    def click(self, timeout=None):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def fill(self, value, timeout=None):
        self.fills.append(value)
        self.value = value

    def input_value(self):
        return self.value


def button(name, **info):
    return Loc({"text": name, **info})


@pytest.mark.parametrize("count", [0, 2])
def test_meeting_click_needs_exactly_one(count):
    loc = button("Schedule a Meeting", count=count) if False else Loc({"text": "Schedule a Meeting"}, count=count)
    with pytest.raises(guard.SelectorError) as e:
        guard.meeting_click(loc, "schedule_entry", "Schedule a Meeting")
    assert e.value.detail == {"op": "schedule_entry", "expected": "Schedule a Meeting", "found": count}
    assert loc.clicks == 0


@pytest.mark.parametrize("op,expected,info,visible", [
    ("schedule_entry", "Schedule a Meeting", {"tag": "A", "role": "button", "text": "Schedule a Meeting",
                                              "href": "/meeting/delete"}, True),
    ("schedule_entry", "Schedule a Meeting", {"tag": "A", "role": "", "text": "Schedule a Meeting",
                                              "href": "/meeting/schedule"}, True),  # a link, not the button
    ("open_combobox", "Choose date", {"tag": "SPAN", "role": "combobox", "label": "End by date"}, True),
    ("open_combobox", "Select meeting template", {"tag": "INPUT", "role": "combobox",
                                                 "label": "Select meeting template"}, True),
    ("pick_option", "10:00", {"tag": "DIV", "role": "option", "label": "10:30"}, True),
    ("pick_option", "Delete", {"tag": "DIV", "role": "option", "label": "Delete"}, True),
    ("pick_date", "Friday,October 30,2026 not selected",
     {"tag": "DIV", "role": "button", "label": "Friday,October 30,2026 not selected", "disabled": True}, True),
    ("add_description", "Add Description", {"text": "Add Description"}, False),
    ("open_combobox", "Edit global Dial-in Countries/Regions", {"role": "combobox",
                                                               "label": "Edit global Dial-in Countries/Regions"}, True),
])
def test_meeting_click_refuses_wrong_controls(op, expected, info, visible):
    loc = Loc(info, visible=visible)
    with pytest.raises(guard.GuardError):
        guard.meeting_click(loc, op, expected)
    assert loc.clicks == 0


def test_meeting_click_allows_the_expected_controls():
    entry = Loc({"tag": "A", "role": "button", "text": "Schedule a Meeting", "href": "/meeting/schedule?from=upcoming"})
    guard.meeting_click(entry, "schedule_entry", "Schedule a Meeting")
    option = Loc({"tag": "LI", "role": "option", "text": "AM"})
    guard.meeting_click(option, "pick_option", "AM")
    tz = Loc({"tag": "DIV", "role": "option", "label": TZ_DISPLAY})
    guard.meeting_click(tz, "pick_option", TZ_DISPLAY)
    cell = Loc({"tag": "DIV", "role": "button", "label": "Today, Saturday,October 31,2026 selected"})
    guard.meeting_click(cell, "pick_date", "Today, Saturday,October 31,2026 selected")
    assert (entry.clicks, option.clicks, tz.clicks, cell.clicks) == (1, 1, 1, 1)


def test_save_needs_an_armed_create_permit():
    save = button("Save")
    for permit in (None, create_permit(armed=False), guard.DeleteMeetingPermit(ORIGIN, MID, armed=True)):
        with pytest.raises(guard.GuardError):
            guard.meeting_click(save, "save_meeting", "Save", permit)
    consumed = create_permit()
    consumed.consumed = True
    with pytest.raises(guard.GuardError):
        guard.meeting_click(save, "save_meeting", "Save", consumed)
    assert save.clicks == 0
    guard.meeting_click(save, "save_meeting", "Save", create_permit())
    assert save.clicks == 1


DIALOG = "Delete Meeting You can recover this meeting within 7 days from Recently Deleted. Delete Cancel"


def test_delete_clicks_need_the_delete_permit_and_the_right_dialog():
    page_delete = button("Delete")
    with pytest.raises(guard.GuardError):
        guard.meeting_click(page_delete, "open_delete", "Delete", create_permit())
    with pytest.raises(guard.GuardError):
        guard.meeting_click(button("Delete", dialog=DIALOG), "open_delete", "Delete",
                            guard.DeleteMeetingPermit(ORIGIN, MID))
    guard.meeting_click(page_delete, "open_delete", "Delete", guard.DeleteMeetingPermit(ORIGIN, MID))
    assert page_delete.clicks == 1

    unarmed = guard.DeleteMeetingPermit(ORIGIN, MID)
    confirm = button("Delete", dialog=DIALOG)
    with pytest.raises(guard.GuardError):
        guard.meeting_click(confirm, "confirm_delete", "Delete", unarmed)
    permanent = button("Delete", dialog="Permanently delete this meeting? This cannot be undone. Delete Cancel")
    with pytest.raises(guard.GuardError, match="recoverable"):
        guard.meeting_click(permanent, "confirm_delete", "Delete", guard.DeleteMeetingPermit(ORIGIN, MID, armed=True))
    with pytest.raises(guard.GuardError):
        guard.meeting_click(button("Delete"), "confirm_delete", "Delete", guard.DeleteMeetingPermit(ORIGIN, MID, armed=True))
    assert confirm.clicks == permanent.clicks == 0
    guard.meeting_click(confirm, "confirm_delete", "Delete", guard.DeleteMeetingPermit(ORIGIN, MID, armed=True))
    assert confirm.clicks == 1


def test_no_other_click_inside_a_delete_dialog():
    with pytest.raises(guard.GuardError):
        guard.meeting_click(Loc({"role": "option", "text": "AM", "dialog": DIALOG}), "pick_option", "AM")


def test_unknown_operations_are_refused():
    loc = button("Copy Invitation")
    with pytest.raises(guard.SelectorError):
        guard.meeting_click(loc, "copy_invitation", "Copy Invitation")
    with pytest.raises(guard.GuardError):
        guard.meeting_fill(Loc({"tag": "INPUT", "label": ""}), "invitees", "ada@example.com")
    assert loc.clicks == 0


def test_meeting_fill_checks_field_and_readback():
    topic = Loc({"tag": "INPUT", "label": "Topic", "id": "topic"})
    guard.meeting_fill(topic, "topic", "Synthetic test meeting")
    assert topic.fills == ["Synthetic test meeting"]
    invitee = Loc({"tag": "INPUT", "label": "Enter user names or email addresses"})
    with pytest.raises(guard.SelectorError):
        guard.meeting_fill(invitee, "topic", "x")
    assert invitee.fills == []
    with pytest.raises(guard.GuardError):
        guard.meeting_fill(Loc({"tag": "INPUT", "label": "Select start time"}), "start_time", "10:10 PM")

    class Sticky(Loc):
        def input_value(self):
            return "something else"
    with pytest.raises(guard.GuardError, match="does not hold"):
        guard.meeting_fill(Sticky({"tag": "INPUT", "label": "Topic"}), "topic", "Synthetic test meeting")
    with pytest.raises(guard.SelectorError):
        guard.meeting_fill(Loc({"tag": "INPUT", "label": "Topic"}, count=2), "topic", "x")


def test_waiting_room_is_the_only_checkbox_and_unchecks_through_its_wrapper():
    passcode = Loc({"wrap_label": "Passcode"}, checked=True)
    with pytest.raises(guard.SelectorError):
        guard.uncheck_waiting_room(passcode)
    off = Loc({"wrap_label": "Waiting Room"}, checked=False)
    assert guard.uncheck_waiting_room(off) is False
    on = Loc({"wrap_label": "Waiting Room"}, checked=True)
    wrapper = SimpleNamespace(count=lambda: 1, get_attribute=lambda n: "zoom-checkbox__wrap is-checked",
                              click=lambda timeout=None: setattr(on, "checked", False))
    on.locator = lambda selector: wrapper
    assert guard.uncheck_waiting_room(on) is True and on.checked is False


# ------------------------------------------------------------------ the request and the listing

def test_parse_spec_normalizes_times_and_computes_utc():
    s = spec(start="14:30", duration=90)
    assert (s.start_time, s.unit, s.hours, s.minutes, s.start_date) == ("2:30", "PM", 1, 30, "11/03/2026")
    assert s.cell_label == "Tuesday,November 3,2026"
    pub = s.public()
    # 2026-11-03 is after US daylight saving time ends: Pacific is UTC-8.
    assert pub["start_utc"] == "2026-11-03T22:30:00Z" and pub["end_utc"] == "2026-11-04T00:00:00Z"
    assert spec(start="12:00").unit == "PM" and spec(start="0:15").start_time == "12:15"
    assert spec(start="9:45 am").start_time == "9:45"


@pytest.mark.parametrize("kw", [
    {"start": "10:10"}, {"start": "25:00"}, {"start": "13:00 PM"}, {"start": "ten"}, {"duration": 20},
    {"duration": 0}, {"duration": 1455}, {"tz": "Pacific Time"}, {"day": "2026-13-01"}, {"day": "2026-10-30"},
    {"topic": "  "}, {"description": "two\nlines"},
])
def test_parse_spec_rejects(kw):
    with pytest.raises(meetings.SpecError):
        spec(**kw)


def upcoming_body(*items):
    return {"status": True, "result": {"totalRecords": len(items), "meetings": [
        {"time": "Tue, Nov 3", "list": [{"number": number, "topic": topic, "schDate": when,
                                          "schTimeF": "10:00 AM - 10:30 AM", "duration": 30, "type": 2,
                                          "joinChatLink": "https://zoom.example/launch/jc/" + number,
                                          "meetingMasterEventId": "SYNTHETIC"} for number, topic, when in items]}]}}


def test_parse_upcoming_keeps_only_schedule_fields():
    out = meetings.parse_upcoming(upcoming_body((MID, "Synthetic test meeting",
                                                 "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)")))
    assert out["meetings"] == [{"id": MID, "topic": "Synthetic test meeting",
                                "when": "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)",
                                "time_range": "10:00 AM - 10:30 AM", "duration_min": 30, "recurring": False}]
    assert "jc" not in json.dumps(out) and "SYNTHETIC" not in json.dumps(out)
    with pytest.raises(Exception):
        meetings.parse_upcoming({"status": False})


def test_duplicates_match_topic_and_start():
    listing = meetings.parse_upcoming(upcoming_body(
        (MID, "Synthetic test meeting", "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)"),
        (OTHER, "Synthetic test meeting", "Tue, Nov 3 11:00 AM Pacific Time (US and Canada)")))
    assert [m["id"] for m in meetings.duplicates(listing["meetings"], spec())] == [MID]


def test_list_form_rewrite_and_window():
    body = "listType=upcoming&page=1&pageSize=15&dateDuration=2026-10-31%2C2027-01-31"
    out = meetings.rewrite_list_form(body, {"page": "2"})
    assert "page=2" in out and "listType=upcoming" in out and "pageSize=15" in out
    with pytest.raises(ValueError):
        meetings.rewrite_list_form(body, {"listType": "previous"})
    from datetime import date
    assert meetings.window_for(date(2026, 11, 3), today=date(2026, 10, 31)) == {}
    assert meetings.window_for(date(2027, 6, 1), today=date(2026, 10, 31)) == {"dateDuration": "2026-10-31,2027-06-02"}


# ------------------------------------------------------------------ schedule orchestration with fakes

class FakePage:
    url = ORIGIN + "/meeting/schedule?from=upcoming"

    def __init__(self, save):
        self.save, self.listeners = save, []

    def get_by_role(self, role, name=None, exact=None):
        assert (role, name) == ("button", "Save")
        return self.save

    def on(self, event, fn):
        self.listeners.append(fn)

    def remove_listener(self, event, fn):
        self.listeners.remove(fn)

    def wait_for_timeout(self, ms):
        pass


FORM = {"topic": "Synthetic test meeting", "Choose date": "11/03/2026", "Select start time": "10:00",
        "Select start time unit": "AM", "select duration hours": "0", "select duration minutes": "30",
        "Select time zone": TZ_DISPLAY, "Passcode": True, "Waiting Room": False}


def schedule_with(monkeypatch, body, upcoming=()):
    """Run schedule_execute with the page helpers faked; pressing Save sends `body` through the guard."""
    blocked = []
    permit = guard.CreateMeetingPermit("", {})
    send_holder = {}
    save = Loc({"text": "Save"})
    page = FakePage(save)

    def press():
        allowed = permit.accept(page.url.split("/meeting")[0] + guard.CREATE_MEETING_PATH, "POST", "xhr", body)
        if allowed:
            page.url = f"{ORIGIN}/meeting/{MID}?meetingMasterEventId=SYNTHETIC"
            for fn in page.listeners:
                fn(SimpleNamespace(url=ORIGIN + guard.CREATE_MEETING_PATH, request=SimpleNamespace(method="POST"),
                                   status=200))
        else:
            blocked.append("POST https://zoom.us/rest/meeting/save")
    save.on_click = press
    monkeypatch.setattr(meetings, "load_upcoming", lambda *a, **k: {"total": 0, "meetings": list(upcoming)})
    monkeypatch.setattr(meetings, "_open_form", lambda *a: ("form", {"America/Los_Angeles": TZ_DISPLAY},
                                                            form_defaults()))
    monkeypatch.setattr(meetings, "settle", lambda *a: dict(FORM))
    monkeypatch.setattr(meetings, "fill_form", lambda *a: dict(FORM))
    reads = []
    monkeypatch.setattr(meetings, "readback_edit", lambda page, mid, s, tz, pc, demo, t=0: reads.append(mid) or
                        meetings.compare(s, FORM, tz, pc))
    send_holder["permit"] = permit
    return page, permit, blocked, save, reads


def test_schedule_execute_creates_once_and_reads_back(monkeypatch):
    page, permit, blocked, save, reads = schedule_with(monkeypatch, create_body())
    monkeypatch.setattr(meetings, "load_upcoming", lambda *a, **k: meetings.parse_upcoming(upcoming_body(
        (MID, "Synthetic test meeting", "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)"))) if reads else
        {"total": 0, "meetings": []})
    out = meetings.schedule_execute(page, spec(), permit, Demo(False), blocked)
    assert out["ok"] and out["readback_verified"] and out["meeting_id"] == MID and out["listed_in_upcoming"]
    assert save.clicks == 1 and permit.consumed and not permit.armed and blocked == [] and reads == [MID]


def test_schedule_execute_with_a_wrong_body_creates_nothing(monkeypatch):
    page, permit, blocked, save, reads = schedule_with(monkeypatch, create_body(invitee={"value": ["ada@example.com"]}))
    with pytest.raises(guard.GuardError, match="nothing was created"):
        meetings.schedule_execute(page, spec(), permit, Demo(False), blocked)
    assert save.clicks == 1 and not permit.consumed and not permit.armed and reads == []


def test_schedule_execute_refuses_duplicates_before_opening_the_form(monkeypatch):
    clash = meetings.parse_upcoming(upcoming_body(
        (MID, "Synthetic test meeting", "Tue, Nov 3 10:00 AM Pacific Time (US and Canada)")))["meetings"]
    page, permit, blocked, save, reads = schedule_with(monkeypatch, create_body(), upcoming=clash)
    monkeypatch.setattr(meetings, "_open_form", lambda *a: pytest.fail("must not open the form"))
    out = meetings.schedule_execute(page, spec(), permit, Demo(False), blocked)
    assert out["error"] == "duplicate_meeting" and save.clicks == 0


def test_schedule_execute_stops_when_the_form_did_not_take_a_value(monkeypatch):
    page, permit, blocked, save, reads = schedule_with(monkeypatch, create_body())
    monkeypatch.setattr(meetings, "fill_form", lambda *a: {**FORM, "select duration minutes": "0"})
    out = meetings.schedule_execute(page, spec(), permit, Demo(False), blocked)
    assert out["error"] == "form_mismatch" and save.clicks == 0 and not permit.armed


def test_readback_compare_flags_drift():
    rows = meetings.compare(spec(), {**FORM, "Waiting Room": True, "Passcode": False}, TZ_DISPLAY, True)
    assert not rows["waiting_room"]["ok"] and not rows["passcode"]["ok"] and rows["time_zone"]["ok"]
    assert not meetings.compare(spec(), FORM, None, True)["time_zone"]["ok"]


class DropdownPage:
    """The duration-hours option list is still closing (visible for `closing_polls` more 50 ms polls,
    or forever with None) when the minutes dropdown is next. Both lists offer "0"."""

    def __init__(self, closing_polls, monkeypatch):
        self.closing, self.clock, self.opened, self.events = closing_polls, 0.0, False, []
        monkeypatch.setattr(meetings, "time", SimpleNamespace(monotonic=lambda: self.clock))
        self.box = Loc({"tag": "INPUT", "role": "combobox", "label": "select duration minutes"},
                       on_click=self._open)
        self.option = Loc({"tag": "DIV", "role": "option", "label": "0"},
                          on_click=lambda: self.events.append("pick"))
        self.option.count = lambda: (self.closing != 0) + self.opened
        self.option.first = self.option

    def _open(self):
        self.events.append(("open", self.closing))
        self.opened = True

    def get_by_role(self, role, name=None, exact=None):
        if role == "listbox":
            return SimpleNamespace(count=lambda: (self.closing != 0) + self.opened)
        assert (role, name, exact) == ("option", "0", True)
        return self.option

    def wait_for_timeout(self, ms):
        self.clock += ms / 1000
        if self.closing:
            self.closing -= 1


def pick_minutes(page):
    form = SimpleNamespace(locator=lambda sel: page.box)
    meetings._pick(page, form, "select duration minutes", "0", Demo(False), lambda: "30")


def test_pick_waits_for_the_previous_dropdown_to_close(monkeypatch):
    page = DropdownPage(closing_polls=6, monkeypatch=monkeypatch)  # about 300 ms
    pick_minutes(page)
    # opened only once the hours list was gone, so exactly one "0" was on the page when picking
    assert page.events == [("open", 0), "pick"]
    assert page.box.clicks == 1 and page.option.clicks == 1 and page.clock == pytest.approx(0.3)


def test_pick_fails_closed_when_the_previous_dropdown_never_closes(capsys, monkeypatch, fake_meetings):
    page = DropdownPage(closing_polls=None, monkeypatch=monkeypatch)
    monkeypatch.setattr(meetings, "schedule_execute", lambda *a: pick_minutes(page))
    code, obj = run(capsys, SCHEDULE + ["--execute"])
    assert code == 1 and obj["ok"] is False and obj["error"] == "page"
    assert "previous dropdown to close did not happen within 10 s" in obj["message"]
    assert obj["execution_attempted"] is False and obj["outcome_unknown"] is False and obj["allowed_requests"] == []
    assert page.events == [] and page.box.clicks == 0 and page.option.clicks == 0
    assert page.clock >= 10


# ------------------------------------------------------------------ delete with fakes

class DeletePage:
    url = f"{ORIGIN}/meeting/{MID}"

    def __init__(self):
        self.clicked = []

    def get_by_text(self, text, exact=None):
        return SimpleNamespace(count=lambda: 1, first=None)

    def get_by_role(self, role, name=None, exact=None):
        page = self

        class B(Loc):
            def click(self, timeout=None):
                page.clicked.append(name)
        return B({"text": name})

    def wait_for_timeout(self, ms):
        pass


def fake_view(monkeypatch, topic="Synthetic test meeting", recurring=False):
    monkeypatch.setattr(meetings, "read_meeting", lambda page, mid, t=0: {
        "id": mid, "topic": topic, "start_date": "11/03/2026", "start_time": "10:00", "start_unit": "AM",
        "duration_min": 30, "timezone": "America/Los_Angeles", "recurring": recurring})


def test_delete_refuses_a_topic_mismatch_without_clicking(monkeypatch):
    fake_view(monkeypatch, topic="Someone else's meeting")
    page = DeletePage()
    permit = guard.DeleteMeetingPermit("", MID)
    out = meetings.delete_meeting(page, MID, "Synthetic test meeting", execute=True, permit=permit,
                                  demo=Demo(False), blocked=[])
    assert out["error"] == "topic_mismatch" and page.clicked == [] and not permit.consumed


def test_delete_refuses_recurring_meetings(monkeypatch):
    fake_view(monkeypatch, recurring=True)
    page = DeletePage()
    out = meetings.delete_meeting(page, MID, "Synthetic test meeting", execute=True,
                                  permit=guard.DeleteMeetingPermit("", MID), demo=Demo(False), blocked=[])
    assert out["error"] == "recurring_meeting" and page.clicked == []


def test_delete_plan_clicks_nothing(monkeypatch):
    fake_view(monkeypatch)
    page = DeletePage()
    permit = guard.DeleteMeetingPermit("", MID)
    out = meetings.delete_meeting(page, MID, "Synthetic test meeting", execute=False, permit=permit,
                                  demo=Demo(False), blocked=[])
    assert out["dry_run"] and out["ok"] and page.clicked == [] and not permit.armed


# ------------------------------------------------------------------ CLI contract

def run(capsys, argv, entry=cli.main_cli):
    code = entry(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def fake_meetings(monkeypatch):
    holder = {}

    @contextlib.contextmanager
    def attached_meetings(args, permit=None, slow_mo=0):
        holder["permit"], holder["slow_mo"] = permit, slow_mo
        yield SimpleNamespace(url=ORIGIN + "/meeting"), [], []

    monkeypatch.setattr(cli, "attached_meetings", attached_meetings)
    return holder


SCHEDULE = ["meetings", "schedule", "--topic", "Synthetic test meeting", "--date", "2099-11-03", "--start", "10:00",
            "--duration", "30", "--tz", "America/Los_Angeles"]


def test_schedule_plan_never_executes(capsys, monkeypatch, fake_meetings):
    monkeypatch.setattr(meetings, "schedule_execute", lambda *a, **k: pytest.fail("plan must not execute"))
    monkeypatch.setattr(meetings, "schedule_plan", lambda page, s, demo: {"duplicates": []})
    code, obj = run(capsys, SCHEDULE)
    assert code == 0 and obj["dry_run"] is True and obj["executed"] is False
    assert obj["will_request"]["path"] == "/rest/meeting/save" and obj["will_request"]["fields"]["invitees"] == 0
    assert not fake_meetings["permit"].armed and fake_meetings["slow_mo"] == 0


def test_schedule_plan_reports_duplicates(capsys, monkeypatch, fake_meetings):
    monkeypatch.setattr(meetings, "schedule_plan", lambda page, s, demo: {"duplicates": [{"id": MID}]})
    code, obj = run(capsys, SCHEDULE)
    assert code == 1 and obj["error"] == "duplicate_meeting"


def test_schedule_plan_entry_check_clicks_nothing(monkeypatch):
    entry = Loc({"tag": "A", "role": "button", "text": "Schedule a Meeting", "href": "/meeting/schedule?from=upcoming"})
    page = SimpleNamespace(get_by_role=lambda *a, **k: entry)
    monkeypatch.setattr(meetings, "load_upcoming", lambda *a, **k: {"total": 0, "meetings": []})
    assert meetings.schedule_plan(page, spec(), Demo(False)) == {"duplicates": []}
    assert entry.clicks == 0
    missing = Loc({"text": "Schedule a Meeting"}, count=0)
    page = SimpleNamespace(get_by_role=lambda *a, **k: missing)
    with pytest.raises(guard.SelectorError):
        meetings.schedule_plan(page, spec(), Demo(False))


def test_demo_flag_slows_down(capsys, monkeypatch, fake_meetings):
    monkeypatch.setattr(meetings, "schedule_plan", lambda page, s, demo: {"duplicates": []})
    run(capsys, SCHEDULE + ["--demo"])
    assert fake_meetings["slow_mo"] > 0


def test_uncertain_schedule_reports_outcome_unknown(capsys, monkeypatch, fake_meetings):
    from zoom_recording import zoomweb

    def execute(page, s, permit, demo, blocked):
        permit.consumed = True
        raise zoomweb.PageError("save outcome uncertain")
    monkeypatch.setattr(meetings, "schedule_execute", execute)
    code, obj = run(capsys, SCHEDULE + ["--execute"])
    assert code == 1 and obj["outcome_unknown"] is True and obj["execution_attempted"] is True


def test_selector_failures_are_structured(capsys, monkeypatch, fake_meetings):
    def execute(page, s, permit, demo, blocked):
        raise guard.SelectorError("pick_option", "AM", 2)
    monkeypatch.setattr(meetings, "schedule_execute", execute)
    code, obj = run(capsys, SCHEDULE + ["--execute"])
    assert obj["error"] == "selector" and obj["selector"] == {"op": "pick_option", "expected": "AM", "found": 2}
    assert obj["outcome_unknown"] is False


@pytest.mark.parametrize("argv", [
    SCHEDULE[:-2],  # no --tz
    [*SCHEDULE[:7], "10:10", *SCHEDULE[8:]],
    ["meetings", "delete", "817 0000 0000", "--expect-topic", "x"],
    ["meetings", "delete", MID],  # no --expect-topic
    ["meetings", "delete", MID, "--expect-topic", "  "],
    ["meetings", "invite", MID],
    ["meetings"],
    ["list"],  # zoom-cli has no flat recording commands
])
def test_meeting_usage_errors_are_one_json_object(capsys, argv, fake_meetings):
    code, obj = run(capsys, argv)
    assert code == 2 or obj["error"] == "usage"
    assert obj["ok"] is False


def test_delete_command_passes_a_bound_permit(capsys, monkeypatch, fake_meetings):
    seen = {}

    def delete(page, mid, topic, *, execute, permit, demo, blocked):
        seen.update(mid=mid, topic=topic, execute=execute, permit_id=permit.meeting_id)
        return {"ok": True, "dry_run": True, "executed": False}
    monkeypatch.setattr(meetings, "delete_meeting", delete)
    code, obj = run(capsys, ["meetings", "delete", MID, "--expect-topic", " Synthetic  test meeting "])
    assert code == 0 and seen == {"mid": MID, "topic": "Synthetic test meeting", "execute": False, "permit_id": MID}
    assert obj["allowed_requests"] == [] and "elapsed_s" in obj


# ------------------------------------------------------------------ zoom-rec stays an alias

@pytest.fixture
def fake_recordings(monkeypatch, list_body):
    @contextlib.contextmanager
    def attached(args):
        yield SimpleNamespace(url="https://zoom.us/recording/"), [], []
    monkeypatch.setattr(cli, "attached", attached)
    monkeypatch.setattr(cli.zoomweb, "load_list", lambda page, overrides, timeout_ms=0: copy.deepcopy(list_body))


def test_zoom_rec_and_zoom_cli_recordings_agree(capsys, fake_recordings):
    argv = ["--from", "2024-01-01", "--to", "2024-12-31"]
    _, legacy = run(capsys, ["list", *argv], entry=cli.main)
    _, grouped = run(capsys, ["recordings", "list", *argv], entry=cli.main_cli)
    _, alias_grouped = run(capsys, ["recordings", "list", *argv], entry=cli.main)
    assert legacy == grouped == alias_grouped and legacy["ok"] is True


def test_zoom_rec_also_has_the_meeting_group(capsys, monkeypatch, fake_meetings):
    monkeypatch.setattr(meetings, "list_all", lambda page, n, window, log=None: {"total": 0, "count": 0, "meetings": []})
    code, obj = run(capsys, ["meetings", "list"], entry=cli.main)
    assert code == 0 and obj["meetings"] == []


def test_zoom_cli_version_is_json(capsys):
    with pytest.raises(SystemExit):
        cli.main_cli(["--version"])
    assert json.loads(capsys.readouterr().out)["version"]


# ------------------------------------------------------------------ identity and completeness checks

def view_page(number, topic="Synthetic test meeting"):
    body = {"status": True, "result": {"meeting": {
        "topic": {"value": topic}, "startDate": {"value": "11/03/2026"}, "recurring": {"value": None},
        "meetingId": {"value": number, "childParams": None},
        "passcode": {"value": "SECRETPASS"}}}}

    class Page:
        url = "about:blank"

        @contextlib.contextmanager
        def expect_response(self, predicate, timeout=None):
            yield SimpleNamespace(value=SimpleNamespace(status=200, json=lambda: body))

        def goto(self, url, **kw):
            self.url = url
    return Page()


def test_read_meeting_requires_the_view_to_be_the_requested_number():
    info = meetings.read_meeting(view_page(int(MID)), MID)
    assert info["id"] == MID and info["topic"] == "Synthetic test meeting" and not info["recurring"]
    assert "SECRETPASS" not in json.dumps(info)
    with pytest.raises(meetings.zoomweb.PageError, match="not the requested"):
        meetings.read_meeting(view_page(OTHER), MID)
    for bad in (None, True, "not-a-number"):
        with pytest.raises(meetings.zoomweb.PageError):
            meetings.read_meeting(view_page(bad), MID)


def test_duplicate_and_gone_checks_need_the_whole_list(monkeypatch):
    monkeypatch.setattr(meetings, "list_all", lambda page, n, window=None, log=None: {
        "total": 40, "count": 30, "meetings": [], "truncated": "stopped"})
    with pytest.raises(meetings.zoomweb.PageError, match="completely"):
        meetings.upcoming_complete(None)
    monkeypatch.setattr(meetings, "list_all", lambda page, n, window=None, log=None: {
        "total": None, "count": 0, "meetings": []})
    with pytest.raises(meetings.zoomweb.PageError):
        meetings.upcoming_complete(None)
