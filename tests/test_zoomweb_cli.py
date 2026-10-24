import contextlib
import json
from datetime import date
from urllib.parse import parse_qsl

import pytest

from conftest import signed_link
from zoom_recording import cli, zoomweb


def test_rewrite_list_form_changes_only_list_fields():
    original = ("from=10%2F23%2F2025&to=10%2F23%2F2026&search_value=&transcript_keyword=&search_type=mixed"
                "&p=1&search_status=0&assistant_host_id=")
    out = dict(parse_qsl(zoomweb.rewrite_list_form(original, {"from": "01/01/2020", "p": "3",
                                                                "search_value": "office hour"}),
                         keep_blank_values=True))
    assert out == {"from": "01/01/2020", "to": "10/23/2026", "search_value": "office hour",
                   "transcript_keyword": "", "search_type": "mixed", "p": "3", "search_status": "0",
                   "assistant_host_id": ""}


def test_rewrite_list_form_refuses_other_fields():
    with pytest.raises(ValueError):
        zoomweb.rewrite_list_form("p=1", {"assistant_host_id": "x"})


def test_page_date_and_origin():
    assert zoomweb.page_date(date(2024, 3, 5)) == "03/05/2024"

    class P:
        url = "https://us06web.zoom.us/recording/"
    assert zoomweb.origin_of(P()) == "https://us06web.zoom.us"
    P.url = "https://evil.example.com/recording"
    assert zoomweb.origin_of(P()) == "https://zoom.us"
    P.url = "about:blank"
    assert zoomweb.origin_of(P()) == "https://zoom.us"


def test_endpoint_patterns():
    assert zoomweb.LIST_RE.match("https://zoom.us/nws/recording/1.0/host-list")
    assert zoomweb.DETAIL_RE.match("https://zoom.us/nws/recording/1.0/detail?meeting_id=x")
    assert zoomweb.BATCH_RE.match("https://zoom.us/nws/recording/1.0/download-meeting/abc?startTime=1")
    assert zoomweb.BATCH_RE.match("https://zoom.us/nws/recording/1.0/guest/download-meeting/abc")
    assert not zoomweb.LIST_RE.match("https://evil.example.com/nws/recording/1.0/host-list")


# ------------------------------------------------------------------ CLI contract

def run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.mark.parametrize("argv", [[], ["bogus"], ["download"], ["list", "--from", "2024-13-01"],
                                  ["list", "--cdp", "http://10.1.2.3:9391"]])
def test_every_error_is_one_json_object(capsys, argv, monkeypatch):
    monkeypatch.setattr(cli.browser, "verify_own_browser",
                        lambda s: {"ok": False, "error": "no_browser", "message": "none"})
    code, obj = run(capsys, argv)
    assert code != 0 and obj["ok"] is False and obj["error"] in ("usage", "config", "no_browser")


def test_foreign_browser_is_refused(capsys, monkeypatch):
    monkeypatch.setattr(cli.browser, "verify_own_browser",
                        lambda s: {"ok": False, "error": "foreign_browser", "message": "refusing"})
    code, obj = run(capsys, ["status", "--cdp", "9391"])
    assert code == 1 and obj["error"] == "foreign_browser"


def test_list_rejects_reversed_range(capsys):
    code, obj = run(capsys, ["list", "--from", "2025-01-02", "--to", "2025-01-01"])
    assert obj["error"] == "usage"


class FakePage:
    url = "https://zoom.us/recording/"

    class context:  # noqa: N801
        request = object()


@pytest.fixture
def fake_attached(monkeypatch):
    blocked = []

    @contextlib.contextmanager
    def attached(args):
        yield FakePage(), blocked, []

    monkeypatch.setattr(cli, "attached", attached)
    return blocked


def test_list_pages_until_total(capsys, monkeypatch, fake_attached, list_body):
    calls = []

    def load_list(page, overrides, timeout_ms=0):
        calls.append(overrides)
        body = json.loads(json.dumps(list_body))
        body["result"]["total_records"] = 3
        if overrides["p"] == "2":
            body["result"]["recordings"] = body["result"]["recordings"][:1]
            body["result"]["recordings"][0]["meetingId"] = "TESTMEETINGCCCCCCCCCCC=="
        return body

    monkeypatch.setattr(cli.zoomweb, "load_list", load_list)
    code, obj = run(capsys, ["list", "--from", "2020-01-01", "--to", "2024-12-31", "--query", "workshop"])
    assert code == 0 and obj["count"] == 3 and obj["total"] == 3
    assert [c["p"] for c in calls] == ["1", "2"]
    assert calls[0]["from"] == "01/01/2020" and calls[0]["search_value"] == "workshop"


def test_needs_sign_in(capsys, monkeypatch, fake_attached):
    def load_list(page, overrides, timeout_ms=0):
        raise zoomweb.NeedsSignIn()
    monkeypatch.setattr(cli.zoomweb, "load_list", load_list)
    code, obj = run(capsys, ["status"])
    assert obj["error"] == "needs_sign_in" and "Sign in" in obj["action"]


def test_show_hides_internal_fields(capsys, monkeypatch, fake_attached, detail_body):
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    code, obj = run(capsys, ["show", "TESTMEETINGAAAAAAAAAAA=="])
    assert code == 0
    assert "_server_name" not in json.dumps(obj) and "GMT_TEST" not in json.dumps(obj)
    assert obj["meeting"]["clips"][0]["files"][0]["local_name"] == "r1_shared_screen_with_speaker_view.mp4"


def test_download_plan_clicks_nothing(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    monkeypatch.setattr(cli.zoomweb, "capture_links", lambda *a, **k: pytest.fail("plan must not click"))
    code, obj = run(capsys, ["download", "TESTMEETINGAAAAAAAAAAA==", "--files", "mp4,transcript,cc",
                             "--root", str(tmp_path)])
    assert code == 0 and obj["dry_run"] is True
    assert [f["type"] for f in obj["files"]] == ["video", "transcript", "video"]
    assert obj["missing_types"] == ["cc"]
    assert obj["folder"].endswith("2024-06-01_example_workshop_week_1")


def test_download_execute_writes_manifest_and_skips_next_time(capsys, monkeypatch, fake_attached,
                                                               detail_body, tmp_path):
    contents = {"GMT_TEST_1_chat.txt": b"x" * 50, "GMT_TEST_1_transcript.vtt":
                (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n").ljust(300, b"\n")}
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    clicks = []

    def capture_links(page, clip_position, clip_count, **kw):
        clicks.append((clip_position, clip_count))
        return [signed_link(n) for n in contents]

    def browser_getter(request_context, link, referer, timeout_ms=0):
        name = link.split("?")[0].rsplit("/", 1)[-1]
        data = contents[name]

        def get(start, end):
            chunk = data[start:end + 1]
            return 206, {"content-range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, chunk
        return get

    monkeypatch.setattr(cli.zoomweb, "capture_links", capture_links)
    monkeypatch.setattr(cli.fetch, "browser_getter", browser_getter)
    argv = ["download", "TESTMEETINGAAAAAAAAAAA==", "--files", "chat,transcript", "--out", str(tmp_path / "m"),
            "--execute"]
    code, obj = run(capsys, argv)
    assert code == 0, obj
    assert [f["state"] for f in obj["files"]] == ["downloaded", "downloaded"]
    text = (tmp_path / "m" / "manifest.json").read_text()
    man = json.loads(text)
    assert man["meeting"]["topic"] == "Example Workshop: Week 1"
    assert "Signature" not in text and "files.example.com" not in text
    assert "Signature" not in json.dumps(obj)
    assert clicks == [(1, 2)]
    code, obj = run(capsys, argv)
    assert [f["state"] for f in obj["files"]] == ["done", "done"] and clicks == [(1, 2)]


def test_download_refuses_folder_of_another_meeting(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"meeting": {"id": "OTHER"}}))
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    code, obj = run(capsys, ["download", "TESTMEETINGAAAAAAAAAAA==", "--out", str(tmp_path)])
    assert obj["error"] == "folder_in_use"


def test_version_is_json(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out)["version"]


def test_unexpected_errors_are_scrubbed(capsys, monkeypatch, fake_attached):
    def boom(page, mid, timeout_ms=0):
        raise RuntimeError("failed " + signed_link("x.mp4"))
    monkeypatch.setattr(cli.zoomweb, "load_detail", boom)
    code, obj = run(capsys, ["show", "X"])
    assert obj["error"] == "unexpected" and "Signature" not in obj["message"]


def _execute_setup(monkeypatch, detail_body, contents, reject_first=False):
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    clicks, gets = [], {"n": 0}

    def capture_links(page, clip_position, clip_count, **kw):
        clicks.append((clip_position, clip_count))
        return [signed_link(n) for n in contents]

    def browser_getter(request_context, link, referer, timeout_ms=0):
        data = contents[link.split("?")[0].rsplit("/", 1)[-1]]

        def get(start, end):
            gets["n"] += 1
            if reject_first and gets["n"] == 1:
                return 403, {}, b""
            chunk = data[start:end + 1]
            return 206, {"content-range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, chunk
        return get

    monkeypatch.setattr(cli.zoomweb, "capture_links", capture_links)
    monkeypatch.setattr(cli.fetch, "browser_getter", browser_getter)
    return clicks


def test_expired_link_gets_fresh_links_once(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    clicks = _execute_setup(monkeypatch, detail_body, {"GMT_TEST_1_chat.txt": b"y" * 50}, reject_first=True)
    code, obj = run(capsys, ["download", "M", "--files", "chat", "--out", str(tmp_path), "--execute"])
    assert code == 0 and obj["files"][0]["state"] == "downloaded"
    assert clicks == [(1, 2), (1, 2)]


def test_second_clip_uses_its_position_and_files(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    clicks = _execute_setup(monkeypatch, detail_body, {"GMT_TEST_2_video.mp4": b"v" * 2000})
    monkeypatch.setattr(cli.verify, "check_file", lambda *a: {"ok": True})
    code, obj = run(capsys, ["download", "M", "--clip", "2", "--files", "video", "--out", str(tmp_path),
                             "--execute"])
    assert code == 0 and clicks == [(2, 2)]
    assert [f["local_name"] for f in obj["files"]] == ["r2_shared_screen_with_speaker_view.mp4"]


def test_unknown_clip_is_a_usage_error(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    monkeypatch.setattr(cli.zoomweb, "load_detail", lambda page, mid, timeout_ms=0: detail_body)
    code, obj = run(capsys, ["download", "M", "--clip", "9", "--out", str(tmp_path)])
    assert obj["error"] == "usage"


def test_not_ready_files_make_ok_false(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    clicks = _execute_setup(monkeypatch, detail_body, {})
    code, obj = run(capsys, ["download", "M", "--clip", "2", "--files", "poll", "--out", str(tmp_path),
                             "--execute"])
    assert obj["ok"] is False and obj["files"][0]["state"] == "not_ready" and clicks == []


def test_user_keys_in_manifest_survive(capsys, monkeypatch, fake_attached, detail_body, tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"meeting": {"id": "TESTMEETINGAAAAAAAAAAA==", "note": "keep"}, "publish": {"where": "x"},
         "files": {"file-chat-1": {"mine": 1}}}))
    _execute_setup(monkeypatch, detail_body, {"GMT_TEST_1_chat.txt": b"y" * 50})
    run(capsys, ["download", "M", "--files", "chat", "--out", str(tmp_path), "--execute"])
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["meeting"]["note"] == "keep" and man["publish"] == {"where": "x"}
    assert man["files"]["file-chat-1"]["mine"] == 1 and man["files"]["file-chat-1"]["sha256"]


# ------------------------------------------------------------------ capture_links with fakes

class _Btn:
    def __init__(self, label, text):
        self.label, self.text, self.clicked = label, text, False

    def count(self):
        return 1

    def get_attribute(self, name):
        return self.label

    def inner_text(self):
        return self.text

    def click(self, timeout=None):
        self.clicked = True
        self.on_click()


class _Buttons:
    def __init__(self, items):
        self.items = items

    def filter(self, has_text=None):
        return _Buttons([b for b in self.items if has_text.fullmatch(b.text) or has_text.search(b.text)])

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]


class _Route:
    def __init__(self, body):
        self.body, self.fulfilled, self.aborted = body, None, False

    def fetch(self):
        body = self.body

        class R:
            def json(self_inner):
                return body
        return R()

    def fulfill(self, response=None, json=None):
        self.fulfilled = json

    def abort(self):
        self.aborted = True


class _Page:
    def __init__(self, buttons, body):
        self.buttons, self.body, self.handler, self.route_obj = buttons, body, None, None
        for b in buttons:
            b.on_click = self._fire

    def _fire(self):
        self.route_obj = _Route(self.body)
        self.handler(self.route_obj)

    def route(self, pattern, handler):
        self.handler = handler

    def unroute(self, pattern, handler):
        self.handler = None

    def bring_to_front(self):
        pass

    def locator(self, selector):
        if selector.startswith("button["):
            return _Buttons(self.buttons)
        return _Buttons([])  # no confirmation dialog

    def wait_for_timeout(self, ms):
        pass


def test_capture_links_empties_the_list_given_to_the_page():
    body = {"status": True, "result": {"batchDownloadFileList": [signed_link("a.mp4")], "other": 1}}
    page = _Page([_Btn("Download Recording 1", "Download")], body)
    links = zoomweb.capture_links(page, 1, 1)
    assert links == [signed_link("a.mp4")]
    assert page.route_obj.fulfilled["result"] == {"batchDownloadFileList": [], "other": 1}
    assert page.handler is None


def test_capture_links_aborts_unknown_shapes():
    page = _Page([_Btn("Download Recording 1", "Download")], {"status": True, "result": {"files": []}})
    with pytest.raises(Exception):
        zoomweb.capture_links(page, 1, 1)
    assert page.route_obj.aborted and page.route_obj.fulfilled is None


def test_capture_links_refuses_when_button_count_differs():
    page = _Page([_Btn("Download Recording 1", "Download")], {})
    with pytest.raises(zoomweb.PageError, match="nothing was clicked"):
        zoomweb.capture_links(page, 1, 2, timeout_ms=500)
    assert not page.buttons[0].clicked


def test_capture_links_picks_the_clip_by_position():
    b1, b2 = _Btn("Download Recording 1", "Download"), _Btn("Download Recording 2", "Download")
    body = {"status": True, "result": {"batchDownloadFileList": []}}
    page = _Page([b1, b2], body)
    zoomweb.capture_links(page, 2, 2)
    assert b2.clicked and not b1.clicked
