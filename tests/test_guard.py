import re

import pytest

from zoom_recording import guard


@pytest.mark.parametrize("label,text", [
    ("Download Recording 1", "Download"),
    ("Download Recording", " Download "),
    ("Download Week 3 talk", "Download"),
])
def test_allowed_clip_download(label, text):
    guard.check_label(label, text, guard.DOWNLOAD_CLIP, guard.DOWNLOAD_TEXT)


@pytest.mark.parametrize("label,text", [
    ("Delete Recording 1", "Delete"),
    ("Copy shareable link Recording 1", "Copy shareable link"),
    ("Download Shared screen with speaker view", ""),
    ("Download Recording 1", "Delete"),
    ("", "Download Recording 1 and move to trash"),
    ("Download Recording 1 ", "Share"),
    ("Download Recording 1", ""),
    ("Download Shared files", "Download"),
    ("Download", "Download"),
])
def test_refused_labels(label, text):
    with pytest.raises(guard.GuardError):
        guard.check_label(label, text, guard.DOWNLOAD_CLIP, guard.DOWNLOAD_TEXT)


def test_confirm_button_needs_exact_text():
    guard.check_label("", " Download ", guard.CONFIRM_DOWNLOAD)
    with pytest.raises(guard.GuardError):
        guard.check_label("", "Download and share", guard.CONFIRM_DOWNLOAD)


class FakeLocator:
    def __init__(self, label="", text="", count=1):
        self.label, self.text, self._count, self.clicked = label, text, count, False

    def count(self):
        return self._count

    def get_attribute(self, name):
        assert name == "aria-label"
        return self.label

    def inner_text(self):
        return self.text

    def click(self, timeout=None):
        self.clicked = True


def test_safe_click_clicks_only_allowed():
    ok = FakeLocator("Download Recording 2", "Download")
    assert guard.safe_click(ok, guard.DOWNLOAD_CLIP, text_allow=guard.DOWNLOAD_TEXT) == "Download Recording 2"
    assert ok.clicked
    bad = FakeLocator("Delete Recording 2", "Delete")
    with pytest.raises(guard.GuardError):
        guard.safe_click(bad, guard.DOWNLOAD_CLIP)
    assert not bad.clicked


def test_safe_click_needs_exactly_one_element():
    with pytest.raises(guard.GuardError, match="exactly one"):
        guard.safe_click(FakeLocator("Download Recording 1", count=2), guard.DOWNLOAD_CLIP)


@pytest.mark.parametrize("url,method,rtype,blocked", [
    ("https://zoom.us/nws/recording/1.0/host-list", "POST", "xhr", False),
    ("https://zoom.us/nws/recording/1.0/detail?meeting_id=x", "GET", "xhr", False),
    ("https://zoom.us/nws/recording/1.0/download-meeting/x?startTime=1", "POST", "xhr", False),
    ("https://zoom.us/nws/recording/1.0/delete-meeting", "POST", "xhr", True),
    ("https://zoom.us/nws/recording/1.0/trash/list", "GET", "xhr", True),
    ("https://us06web.zoom.us/nws/recording/1.0/update-share-status", "POST", "fetch", True),
    ("https://zoom.us/recording/update_meet_passwd", "POST", "document", True),
    ("https://zoom.us/nws/recording/1.0/get-recordfile-shareinfo", "GET", "xhr", False),
    ("https://st1.zoom.us/static/js/trash.min.js", "GET", "script", False),
    ("https://st1.zoom.us/static/css/meeting_delete_dialog.css", "GET", "stylesheet", False),
    ("https://example.com/delete", "POST", "xhr", False),
])
def test_is_mutation(url, method, rtype, blocked):
    assert guard.is_mutation(url, method, rtype) is blocked


def test_guard_url_regex_covers_every_blocked_case():
    for url in ("https://zoom.us/nws/recording/1.0/delete-meeting",
                "https://us06web.zoom.us/nws/recording/1.0/update-share-status"):
        assert guard.GUARD_URL.search(url)
    assert not guard.GUARD_URL.search("https://zoom.us/nws/recording/1.0/host-list")
    assert not guard.GUARD_URL.search("https://zoom.us/recording?next=/delete")


class FakeRequest:
    def __init__(self, url, method="POST", resource_type="xhr"):
        self.url, self.method, self.resource_type = url, method, resource_type


class FakeRoute:
    def __init__(self, request):
        self.request, self.outcome = request, None

    def abort(self):
        self.outcome = "abort"

    def fallback(self):
        self.outcome = "fallback"


class FakePage:
    def __init__(self):
        self.routes = []

    def route(self, matcher, handler):
        self.routes.append((matcher, handler))

    def unroute(self, matcher, handler):
        self.routes.remove((matcher, handler))


def test_install_request_guard_aborts_and_records():
    page, blocked = FakePage(), []
    remove = guard.install_request_guard(page, blocked)
    matcher, handler = page.routes[0]
    assert isinstance(matcher, re.Pattern)
    r1 = FakeRoute(FakeRequest("https://zoom.us/nws/recording/1.0/delete-meeting?id=SECRETSECRET"))
    handler(r1)
    r2 = FakeRoute(FakeRequest("https://st1.zoom.us/js/trash.js", "GET", "script"))
    handler(r2)
    assert (r1.outcome, r2.outcome) == ("abort", "fallback")
    assert blocked == ["POST https://zoom.us/nws/recording/1.0/delete-meeting"]
    remove()
    assert page.routes == []
