import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from zoom_recording import cli, guard, sharing, zoomweb

ORIGIN = "https://zoom.us"
URL = ORIGIN + guard.SAVE_SETTINGS_PATH
MID = "synthetic-recording"


def permit():
    return guard.PasscodePermit(ORIGIN, MID, armed=True)


@pytest.mark.parametrize("body", [json.dumps({"meetingId": MID, "enablePasscode": False}),
                                  "meetingId=synthetic-recording&enablePasscode=false"])
def test_exact_removal_is_one_shot(body):
    p = permit()
    assert p.accept(URL, "POST", "xhr", body)
    assert not p.accept(URL, "POST", "xhr", body)
    assert p.audit == [{"method": "POST", "path": guard.SAVE_SETTINGS_PATH,
                        "changes": {"enablePasscode": False}}]


@pytest.mark.parametrize("body", [
    '{"meetingId":"synthetic-recording","enablePasscode":true}',
    '{"meetingId":"other","enablePasscode":false}',
    '{"meetingId":"synthetic-recording","enablePasscode":0}',
    '{"meetingId":"synthetic-recording","enablePasscode":false,"allowViewersDownload":false}',
    '{"meetingId":"synthetic-recording","enablePasscode":false,"passwd":"secret"}',
    '{"meetingId":"synthetic-recording","meetingId":"synthetic-recording","enablePasscode":false}',
    'meetingId=synthetic-recording&enablePasscode=false&enablePasscode=true',
    'not-json', '[]', '{}', None,
])
def test_payload_fail_closed(body):
    p = permit()
    assert not p.accept(URL, "POST", "xhr", body)
    assert not p.consumed and p.audit == []


@pytest.mark.parametrize("url,method,kind", [
    (URL + "?extra=1", "POST", "xhr"), (URL, "GET", "xhr"), (URL, "PUT", "xhr"),
    (URL, "POST", "document"), (URL.replace("zoom.us", "evil.example"), "POST", "xhr"),
    (URL.replace("save-share-setting", "save-share-data"), "POST", "xhr"),
    (URL.replace("save-share-setting", "delete-meeting"), "POST", "xhr"),
])
def test_endpoint_method_and_resource_are_exact(url, method, kind):
    assert not permit().accept(url, method, kind, json.dumps({"meetingId": MID, "enablePasscode": False}))


def test_unarmed_and_default_remain_read_only():
    p = guard.PasscodePermit(ORIGIN, MID)
    assert not p.accept(URL, "POST", "xhr", f'meetingId={MID}&enablePasscode=false')
    assert guard.is_mutation(URL, "POST")
    with pytest.raises(guard.GuardError):
        guard.check_label("", "Share", guard.SHARING_CLICKS["open"])


@pytest.mark.parametrize("label,text", [("Delete", "Delete"), ("Share", "Delete"),
                                        ("Share and delete", "Share"), ("Copy link", "Copy link")])
def test_explicit_sharing_click_never_allows_other_controls(label, text):
    clicked = []
    locator = SimpleNamespace(count=lambda: 1, get_attribute=lambda n: label,
                              inner_text=lambda: text, click=lambda **k: clicked.append(True))
    with pytest.raises(guard.GuardError):
        guard.safe_click(locator, guard.SHARING_CLICKS["open"], sharing_action="open")
    assert clicked == []


@pytest.mark.parametrize("label,visible,enabled,kind,allowed", [
    ("Passcode", True, True, "checkbox", True),
    ("Viewers can download", True, True, "checkbox", False),
    ("Passcode", False, True, "checkbox", False),
    ("Passcode", True, False, "checkbox", False),
    ("Passcode", True, True, "password", False),
])
def test_checkbox_guard_only_unchecks_passcode(label, visible, enabled, kind, allowed):
    changes = []
    checked = [True]
    def click(**kwargs):
        changes.append(False)
        checked[0] = False
    wrapper = SimpleNamespace(count=lambda: 1, evaluate=lambda script: "SPAN",
                              get_attribute=lambda n: "recording-share-panel-checkbox__wrap is-checked",
                              locator=lambda s: SimpleNamespace(count=lambda: 1), inner_text=lambda: label,
                              click=click)
    locator = SimpleNamespace(count=lambda: 1, is_visible=lambda: visible, is_enabled=lambda: enabled,
                              get_attribute=lambda n: kind, evaluate=lambda s: label,
                              is_checked=lambda: checked[0], locator=lambda s: wrapper)
    if allowed:
        guard.disable_passcode(locator)
        assert changes == [False]
    else:
        with pytest.raises(guard.GuardError):
            guard.disable_passcode(locator)
        assert changes == []


@pytest.mark.parametrize("bad_part", ["tag", "class", "count", "label", "still_checked"])
def test_checkbox_wrapper_fails_closed(bad_part):
    clicks = []
    checked = [True]
    def click(**kwargs):
        clicks.append("wrapper")
        if bad_part != "still_checked":
            checked[0] = False
    wrapper = SimpleNamespace(count=lambda: 1,
        evaluate=lambda s: "DIV" if bad_part == "tag" else "SPAN",
        get_attribute=lambda n: "wrong-wrapper" if bad_part == "class" else "recording-share-panel-checkbox__wrap",
        locator=lambda s: SimpleNamespace(count=lambda: 2 if bad_part == "count" else 1),
        inner_text=lambda: "Viewers can download" if bad_part == "label" else "Passcode", click=click)
    locator = SimpleNamespace(count=lambda: 1, is_visible=lambda: True, is_enabled=lambda: True,
        get_attribute=lambda n: "checkbox", evaluate=lambda s: "Passcode",
        is_checked=lambda: checked[0], locator=lambda s: wrapper)
    with pytest.raises(guard.GuardError):
        guard.disable_passcode(locator)
    assert clicks == (["wrapper"] if bad_part == "still_checked" else [])


def test_checkbox_already_off_never_clicks_wrapper():
    locator = SimpleNamespace(count=lambda: 1, is_visible=lambda: True, is_enabled=lambda: True,
        get_attribute=lambda n: "checkbox", evaluate=lambda s: "Passcode", is_checked=lambda: False)
    # No wrapper accessor: reaching one would fail instead of toggling an already-off input.
    guard.disable_passcode(locator)


def test_guard_permit_does_not_open_other_endpoints_or_fields():
    routes = []
    target = SimpleNamespace(route=lambda m, h: routes.append((m, h)), unroute=lambda m, h: None)
    p, blocked = permit(), []
    guard.install_request_guard(target, blocked, p)
    matcher, handler = routes[0]
    def run(url, body):
        outcome = []
        req = SimpleNamespace(url=url, method="POST", resource_type="xhr", post_data=body)
        assert matcher.search(url)
        handler(SimpleNamespace(request=req, abort=lambda: outcome.append("abort"), fallback=lambda: outcome.append("allow")))
        return outcome
    body = f'meetingId={MID}&enablePasscode=false'
    assert run(ORIGIN + "/nws/recording/1.0/unknown-operation", body) == ["abort"]
    assert run(URL, body + "&isOnDemand=true") == ["abort"]
    assert run(URL, body) == ["allow"]
    assert run(URL, body) == ["abort"]


@pytest.mark.parametrize("base", ["https://zoom.us.evil.example", "http://zoom.us", "https://zoom.us/path",
                                   "https://user@zoom.us", "https://zoom.us?token=secret"])
def test_share_url_rejects_noncanonical_origin(base):
    with pytest.raises(zoomweb.PageError):
        sharing.share_url({"baseUrl": base, "encryptMeetId": "fake"})


def test_share_url_never_embeds_password_or_download_fields():
    assert sharing.share_url({"baseUrl": ORIGIN, "encryptMeetId": "fake/token",
                              "hPasscode": "secret", "recordMeetRawPassword": "secret",
                              "downloadUrl": "secret"}) == ORIGIN + "/rec/share/fake%2Ftoken"


def state(enabled=True):
    return {"meeting": {"id": MID}, "settings": {"passcode_enabled": enabled, "passcode_locked": False,
            "share_url": ORIGIN + "/rec/share/fake", "public_link_access": True, "access": {"publicRecording": True},
            "controls": [{"label": "Passcode", "checked": enabled, "disabled": False},
                         {"label": "Viewers can download", "checked": True, "disabled": False}]}}


def setup_ui(monkeypatch, before, after=None, save_success=True):
    calls = []
    checkbox = SimpleNamespace(is_checked=lambda: False)
    dialog = SimpleNamespace(get_by_role=lambda *a, **k: checkbox)
    values = iter([before, after] if after else [before])
    monkeypatch.setattr(sharing, "inspect", lambda *a: (copy.deepcopy(next(values)), dialog))
    monkeypatch.setattr(guard, "disable_passcode", lambda *a: calls.append("disable"))
    p = permit()
    p.armed = False
    def click(*args):
        assert p.armed
        assert p.accept(URL, "POST", "xhr", f'meetingId={MID}&enablePasscode=false')
        calls.append("save")
    monkeypatch.setattr(sharing, "_click", click)
    @contextmanager
    def expect(*a, **k):
        yield SimpleNamespace(value=SimpleNamespace(status=200, json=lambda: {"status": save_success}))
    page = SimpleNamespace(expect_response=expect, url=ORIGIN + "/recording/detail")
    return page, p, calls


def test_preview_never_clicks_or_arms(monkeypatch):
    page, p, calls = setup_ui(monkeypatch, state())
    result = sharing.settings(page, MID, disable=True, execute=False, permit=p)
    assert result["dry_run"] and result["changes"] and not result["executed"]
    assert not p.armed and calls == []


def test_already_off_is_idempotent(monkeypatch):
    page, p, calls = setup_ui(monkeypatch, state(False))
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert result["ok"] and result["readback_verified"] and not result["executed"]
    assert calls == [] and not p.armed


def test_write_and_readback_accept_new_share_url(monkeypatch):
    after = state(False)
    after["settings"]["share_url"] = ORIGIN + "/rec/share/new-fake-token"
    page, p, calls = setup_ui(monkeypatch, state(), after)
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert result["ok"] and result["executed"] and result["readback_verified"]
    assert calls == ["disable", "save"] and not p.armed


@pytest.mark.parametrize("changed", ["passcode", "access", "checkbox", "public_link_access"])
def test_readback_mismatch_is_not_success(monkeypatch, changed):
    after = state(False)
    if changed == "passcode":
        after = state(True)
    elif changed == "access":
        after["settings"]["access"]["publicRecording"] = False
    elif changed == "public_link_access":
        after["settings"]["public_link_access"] = False
    else:
        after["settings"]["controls"][1]["checked"] = False
    page, p, _ = setup_ui(monkeypatch, state(), after)
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert not result["ok"] and not result["readback_verified"] and result["executed"]
    assert result["error"] == "readback_mismatch" and not p.armed


@pytest.mark.parametrize("label", sorted(sharing.PASSCODE_DEPENDENT_LABELS))
def test_conditional_passcode_child_can_disappear_without_permission_change(monkeypatch, label):
    before, after = state(), state(False)
    before["settings"]["controls"].append({"label": label, "checked": True, "disabled": False})
    for snapshot in (before, after):
        snapshot["settings"]["access"]["allowInviteesBypassPasscode"] = True
    page, p, _ = setup_ui(monkeypatch, before, after)
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert result["ok"] and result["readback_verified"]


@pytest.mark.parametrize("failure", ["unknown_child", "changed_flag", "missing_flag", "non_boolean_flag", "lost_download"])
def test_disappearing_control_is_not_blanket_exempt(monkeypatch, failure):
    before, after = state(), state(False)
    child = {"label": "Allow invitees to access this recording without a passcode", "checked": True, "disabled": False}
    before["settings"]["controls"].append(child)
    for snapshot in (before, after):
        snapshot["settings"]["access"]["allowInviteesBypassPasscode"] = True
    if failure == "unknown_child":
        child["label"] = "Allow strangers to change settings"
    elif failure == "changed_flag":
        after["settings"]["access"]["allowInviteesBypassPasscode"] = False
    elif failure == "missing_flag":
        after["settings"]["access"].pop("allowInviteesBypassPasscode")
    elif failure == "non_boolean_flag":
        after["settings"]["access"]["allowInviteesBypassPasscode"] = 1
    else:
        after["settings"]["controls"] = after["settings"]["controls"][:1]
    page, p, _ = setup_ui(monkeypatch, before, after)
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert not result["ok"] and result["error"] == "readback_mismatch"


def test_failed_save_disarms_permit(monkeypatch):
    page, p, _ = setup_ui(monkeypatch, state(), save_success=False)
    with pytest.raises(zoomweb.PageError, match="save failed"):
        sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert not p.armed


def test_locked_passcode_and_wrong_recording_fail_before_click(monkeypatch):
    before = state()
    before["settings"]["passcode_locked"] = True
    page, p, calls = setup_ui(monkeypatch, before)
    with pytest.raises(guard.GuardError, match="locked"):
        sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert calls == []
    with pytest.raises(guard.GuardError, match="matching"):
        sharing.settings(page, "other", disable=True, execute=True, permit=p)


def test_cli_execute_without_requested_change_is_usage_error(capsys):
    assert cli.main(["settings", MID, "--execute"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "usage"


@pytest.mark.parametrize("failure", [zoomweb.PageError, KeyboardInterrupt])
def test_cli_retains_attempt_audit_on_uncertain_save(monkeypatch, capsys, failure):
    @contextmanager
    def attached(args, permit):
        yield SimpleNamespace(url=ORIGIN), [], []
    def uncertain(page, meeting_id, *, permit, **kwargs):
        permit.origin = ORIGIN
        permit.armed = True
        assert permit.accept(URL, "POST", "xhr", f'meetingId={MID}&enablePasscode=false')
        permit.armed = False
        raise failure("save outcome uncertain; inspect settings before retrying")
    monkeypatch.setattr(cli, "attached", attached)
    monkeypatch.setattr(sharing, "settings", uncertain)
    assert cli.main(["settings", MID, "--disable-passcode", "--execute"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["outcome_unknown"] and result["execution_attempted"]
    assert result["allowed_requests"] and not result["readback_verified"]


class SharingPage:
    """Fake browser, real inspect/click/checkbox chain and simulated server state."""
    def __init__(self, detail_body, *, enabled=True):
        self.url = "about:blank"
        self.detail = copy.deepcopy(detail_body)
        self.detail["result"]["meet"]["meetingId"] = MID
        self.server_enabled = self.ui_enabled = enabled
        self.actions = []
        self.controls_override = None
        self.share_id = MID
        self.permit = None
        self.waiter = None

    def load_detail(self, page, meeting_id):
        self.url = "https://us06web.zoom.us/recording/detail"
        self.ui_enabled = self.server_enabled
        return self.detail

    def respond(self, save=False):
        path = guard.SAVE_SETTINGS_PATH if save else sharing.SHARE_PATH
        body = {"status": True, "result": {"meetingId": self.share_id,
                "baseUrl": "https://us06web.zoom.us", "encryptMeetId": "fake-token",
                "publicRecording": True}}
        response = SimpleNamespace(url="https://us06web.zoom.us" + path, status=200,
                                   request=SimpleNamespace(method="POST" if save else "GET"), json=lambda: body)
        predicate, info = self.waiter
        assert predicate(response)
        info.value = response

    @contextmanager
    def expect_response(self, predicate, **kwargs):
        info = SimpleNamespace()
        self.waiter = predicate, info
        yield info
        self.waiter = None

    def get_by_role(self, role, name=None, **kwargs):
        if role == "dialog":
            return self
        if role == "checkbox" and name is None:
            return SimpleNamespace(evaluate_all=lambda script: self.controls_override if self.controls_override is not None else [
                {"label": "Passcode", "checked": self.ui_enabled, "disabled": False},
                {"label": "Viewers can download", "checked": True, "disabled": False}])
        return SharingControl(self, name)

    def get_by_text(self, text, **kwargs):
        return SimpleNamespace(count=lambda: 1)


class SharingControl:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def count(self):
        return 1

    def get_attribute(self, name):
        return "checkbox" if name == "type" else self.name

    def inner_text(self):
        return self.name

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def is_checked(self):
        return self.page.ui_enabled

    def evaluate(self, script):
        return self.name

    def wait_for(self, **kwargs):
        pass

    def locator(self, selector):
        assert selector == ".." and self.name == "Passcode"
        def uncheck(**kwargs):
            self.page.actions.append("uncheck")
            self.page.ui_enabled = False
        return SimpleNamespace(count=lambda: 1, evaluate=lambda s: "SPAN",
            get_attribute=lambda n: "recording-share-panel-checkbox__wrap",
            locator=lambda s: SimpleNamespace(count=lambda: 1), inner_text=lambda: "Passcode", click=uncheck)

    def click(self, **kwargs):
        self.page.actions.append(self.name)
        if self.name == "Share":
            self.page.respond()
        elif self.name == "Save":
            p = self.page.permit
            assert p.accept("https://us06web.zoom.us" + guard.SAVE_SETTINGS_PATH, "POST", "xhr",
                            f"meetingId={MID}&enablePasscode=false")
            self.page.server_enabled = self.page.ui_enabled
            self.page.respond(save=True)


def test_real_inspect_and_guard_chain_after_regional_redirect(monkeypatch, detail_body):
    page = SharingPage(detail_body)
    monkeypatch.setattr(zoomweb, "load_detail", page.load_detail)
    p = guard.PasscodePermit(ORIGIN, MID)
    page.permit = p
    result = sharing.settings(page, MID, disable=True, execute=True, permit=p)
    assert result["ok"] and result["executed"] and result["readback_verified"]
    assert p.origin == "https://us06web.zoom.us" and not p.armed
    assert page.actions == ["Share", "More settings", "uncheck", "Save", "Share", "More settings"]


@pytest.mark.parametrize("failure", ["detail_id", "share_id", "empty_controls", "empty_label", "duplicate_label"])
def test_real_inspect_rejects_ambiguous_identity_and_controls(monkeypatch, detail_body, failure):
    page = SharingPage(detail_body)
    monkeypatch.setattr(zoomweb, "load_detail", page.load_detail)
    if failure == "detail_id":
        page.detail["result"]["meet"]["meetingId"] = "other"
    elif failure == "share_id":
        page.share_id = "other"
    elif failure == "empty_controls":
        page.controls_override = []
    elif failure == "empty_label":
        page.controls_override = [{"label": "", "checked": True, "disabled": False}]
    else:
        page.controls_override = [{"label": "Passcode", "checked": True, "disabled": False}] * 2
    with pytest.raises(zoomweb.PageError):
        sharing.inspect(page, MID)
    assert "Save" not in page.actions and "uncheck" not in page.actions
