"""Visible Chrome: launch with the tool's own profile, verify ownership, attach over CDP.

Chrome runs with a persistent profile and a DevTools port bound to 127.0.0.1. The tool attaches
with Playwright's connect_over_cdp and only disconnects; it never closes the browser. Nothing here
reads, prints or exports cookies or storage, and nothing types into a sign-in page.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .paths import ConfigError, enclosing_git_repo

DEFAULT_PORT = 9391
RECORDINGS_URL = "https://zoom.us/recording"
SINGLETON_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass
class Session:
    port: int
    profile_dir: Path

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def parse_cdp(value: str | None) -> int | None:
    """Accept `PORT`, `HOST:PORT` or `http://HOST:PORT` on a loopback host; return the port."""
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return int(v)
    if "://" not in v:
        v = "http://" + v
    parts = urlsplit(v)
    if parts.hostname not in LOOPBACK:
        raise ConfigError(f"--cdp must point at a loopback address, got host {parts.hostname!r}")
    if not parts.port:
        raise ConfigError("--cdp needs a port, for example http://127.0.0.1:9391")
    return parts.port


def session_for(home: Path, cdp: str | None = None, profile_dir: str | None = None) -> Session:
    port = parse_cdp(cdp or os.environ.get("ZOOM_REC_CDP")) or DEFAULT_PORT
    prof = profile_dir or os.environ.get("ZOOM_REC_PROFILE_DIR") or str(home / "browser-profile")
    return Session(port=port, profile_dir=Path(prof).expanduser())


def profile_warnings(session: Session) -> list[str]:
    repo = enclosing_git_repo(session.profile_dir)
    if repo is None:
        return []
    return [f"the browser profile is inside a git repository ({repo.name}); it holds a signed-in "
            "session, keep it out of version control"]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def profile_in_use(profile: Path) -> bool:
    return any(os.path.lexists(profile / n) for n in SINGLETON_FILES)


def find_chrome() -> str | None:
    explicit = os.environ.get("ZOOM_REC_CHROME")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch(session: Session, url: str = RECORDINGS_URL) -> dict[str, Any]:
    repo = enclosing_git_repo(session.profile_dir)
    if repo is not None:
        raise ConfigError(f"browser profile is inside a git repository ({repo}); choose a profile "
                          "directory outside any repository with --home or --profile-dir")
    if port_in_use(session.port):
        return {"ok": False, "error": "port_in_use",
                "message": f"port {session.port} is already in use; run `zoom-rec status`, or pick "
                           "another port with --cdp"}
    if profile_in_use(session.profile_dir):
        return {"ok": False, "error": "profile_in_use",
                "message": "the profile is locked by a running Chrome", "profile_dir": str(session.profile_dir)}
    chrome = find_chrome()
    if not chrome:
        return {"ok": False, "error": "chrome_not_found", "message": "set ZOOM_REC_CHROME to the Chrome executable"}
    created = not session.profile_dir.exists()
    session.profile_dir.mkdir(parents=True, exist_ok=True)
    if created:
        os.chmod(session.profile_dir, 0o700)
    log_path = session.profile_dir / "zoom-rec-browser.log"
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={session.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={session.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1400,1000",
                url,
            ],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,  # the browser outlives the shell that started it
        )
    os.chmod(log_path, 0o600)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {"ok": False, "error": "chrome_exited", "message": "Chrome exited right after starting; see the log",
                    "log": str(log_path)}
        try:
            with urllib.request.urlopen(f"{session.endpoint}/json/version", timeout=2) as r:
                json.loads(r.read())
            return {"ok": True, "pid": proc.pid, "cdp_endpoint": session.endpoint,
                    "profile_dir": str(session.profile_dir), "url": url,
                    "next": "Sign in to Zoom in the new Chrome window, then run `zoom-rec status`."}
        except Exception:  # noqa: BLE001 - wait for the port
            time.sleep(0.3)
    return {"ok": False, "error": "cdp_timeout", "message": "the DevTools endpoint did not come up within 20 s",
            "log": str(log_path)}


def browser_matches(cmdlines: list[str], port: int, profile_dir: Path) -> bool:
    """Pure: is there a process started with both this debugging port and this profile?"""
    want_port = f"--remote-debugging-port={port}"
    dirs = {str(profile_dir), str(profile_dir.expanduser().resolve())}
    pats = [re.compile(re.escape(f"--user-data-dir={d}") + r"/?(\s|$)") for d in dirs]
    return any(want_port in line.split() and any(pt.search(line) for pt in pats) for line in cmdlines)


def listening_pids(port: int) -> list[int] | None:
    """PIDs listening on the port, or None when lsof is unavailable."""
    exe = shutil.which("lsof")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [int(x) for x in out.split() if x.isdigit()]


def _cmdlines(pids: list[int] | None) -> list[str]:
    if pids:
        lines: list[str] = []
        for pid in pids:
            r = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
            lines += r.stdout.splitlines()
        return lines
    flags = ["-axww"] if sys.platform == "darwin" else ["-eww"]
    return subprocess.run(["ps", *flags, "-o", "args="], capture_output=True, text=True, timeout=10).stdout.splitlines()


def verify_own_browser(session: Session) -> dict[str, Any]:
    """Refuse to drive a Chrome that was not started with the declared profile on this port."""
    if not port_in_use(session.port):
        return {"ok": False, "error": "no_browser",
                "message": f"no browser is listening on 127.0.0.1:{session.port}; run `zoom-rec browser`"}
    try:
        lines = _cmdlines(listening_pids(session.port))
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": "cannot_verify", "message": f"cannot list processes: {type(e).__name__}"}
    if browser_matches(lines, session.port, session.profile_dir):
        return {"ok": True}
    return {"ok": False, "error": "foreign_browser",
            "message": f"the browser on port {session.port} was not started with the declared profile "
                       "directory; refusing to drive it (declare it with --profile-dir)"}


def connect(p: Any, session: Session) -> tuple[Any, Any]:
    """Attach to the running Chrome. Callers only let Playwright exit; the browser stays open."""
    browser = p.chromium.connect_over_cdp(session.endpoint, timeout=15000)
    if not browser.contexts:
        raise RuntimeError("browser has no default context")
    return browser, browser.contexts[0]


def is_zoom_web(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "zoom.us" or host.endswith(".zoom.us")


def work_tab(context: Any) -> Any:
    """Reuse a tab already on a Zoom recordings page, else open one."""
    for page in context.pages:
        parts = urlsplit(page.url)
        if is_zoom_web(page.url) and parts.path.startswith("/recording"):
            return page
    return context.new_page()
