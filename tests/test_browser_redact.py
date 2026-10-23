from pathlib import Path

import pytest

from zoom_recording import browser, redact
from zoom_recording.paths import ConfigError, enclosing_git_repo

PROFILE = Path("/path/to/profile")


@pytest.mark.parametrize("line,ok", [
    ("Chrome --user-data-dir=/path/to/profile --remote-debugging-port=9391 https://zoom.us/recording", True),
    ("Chrome --remote-debugging-port=9391 --user-data-dir=/path/to/profile/", True),
    ("Chrome --remote-debugging-port=9391 --user-data-dir=/path/to/profile-other", False),
    ("Chrome --remote-debugging-port=9392 --user-data-dir=/path/to/profile", False),
    ("Chrome --user-data-dir=/path/to/profile", False),
])
def test_browser_matches(line, ok):
    assert browser.browser_matches([line], 9391, PROFILE) is ok


@pytest.mark.parametrize("value,port", [
    ("9391", 9391), ("127.0.0.1:9400", 9400), ("http://localhost:9500", 9500), (None, None),
])
def test_parse_cdp(value, port):
    assert browser.parse_cdp(value) == port


@pytest.mark.parametrize("value", ["http://10.0.0.5:9391", "example.com:9391", "http://127.0.0.1"])
def test_parse_cdp_refuses_remote_or_portless(value):
    with pytest.raises(ConfigError):
        browser.parse_cdp(value)


def test_session_defaults_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ZOOM_REC_CDP", raising=False)
    monkeypatch.delenv("ZOOM_REC_PROFILE_DIR", raising=False)
    s = browser.session_for(tmp_path)
    assert s.port == browser.DEFAULT_PORT and s.profile_dir == tmp_path / "browser-profile"
    monkeypatch.setenv("ZOOM_REC_CDP", "127.0.0.1:9444")
    monkeypatch.setenv("ZOOM_REC_PROFILE_DIR", str(tmp_path / "p"))
    s = browser.session_for(tmp_path)
    assert s.port == 9444 and s.profile_dir == tmp_path / "p"
    s = browser.session_for(tmp_path, cdp="9555", profile_dir=str(tmp_path / "q"))
    assert s.port == 9555 and s.profile_dir == tmp_path / "q"


def test_launch_refuses_profile_inside_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    s = browser.Session(port=1, profile_dir=tmp_path / "profile")
    with pytest.raises(ConfigError, match="git repository"):
        browser.launch(s)
    assert enclosing_git_repo(tmp_path / "profile") == tmp_path.resolve()
    assert browser.profile_warnings(s)


def test_work_tab_reuses_recordings_tab():
    class P:
        def __init__(self, url):
            self.url = url

    class C:
        def __init__(self, pages):
            self.pages = pages

        def new_page(self):
            return "new"

    rec = P("https://zoom.us/recording/detail?meeting_id=x")
    assert browser.work_tab(C([P("https://example.com/recording"), rec])) is rec
    assert browser.work_tab(C([P("https://zoom.us/profile")])) == "new"


def test_scrub_removes_query_strings_and_tokens():
    s = redact.scrub("failed GET https://files.example.com/a/b.mp4?Policy=xyz&Signature=abc for you")
    assert s == "failed GET https://files.example.com/a/b.mp4?<redacted> for you"
    token = "A" * 60
    assert redact.scrub(f"token {token} end") == "token <redacted> end"
    assert redact.path_only("https://zoom.us/a/b?x=1#y") == "https://zoom.us/a/b"
