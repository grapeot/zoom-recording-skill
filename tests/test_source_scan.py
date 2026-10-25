"""Static checks that keep the safety rules from eroding."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "zoom_recording"

FORBIDDEN_CALLS = {
    "click", "dblclick", "tap", "dispatch_event", "press", "type", "fill", "check", "set_checked",
    "cookies", "add_cookies", "clear_cookies", "storage_state", "add_init_script", "set_extra_http_headers",
    "all_headers", "headers_array", "header_value", "set_input_files",
}
ALLOWED = {("guard.py", "click"), ("guard.py", "set_checked")}


def _calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node.func.attr, node.lineno


def test_no_forbidden_calls_outside_guard():
    hits = []
    for path in SRC.glob("*.py"):
        for name, line in _calls(path):
            if name in FORBIDDEN_CALLS and (path.name, name) not in ALLOWED:
                hits.append(f"{path.name}:{line} .{name}(")
    assert hits == []


def test_no_click_or_key_events_in_page_scripts():
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (".click()", "dispatchEvent", "document.cookie", "localStorage", "sessionStorage",
                       "NWS_TOKEN", "csrf"):
            assert needle not in text, f"{path.name} contains {needle!r}"


def test_request_headers_are_never_read():
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "headers":
                base = node.value
                # only response headers may be read: `resp.headers`
                assert isinstance(base, ast.Name) and base.id == "resp", f"{path.name}:{node.lineno}"


def test_no_personal_data_in_repository():
    root = SRC.parent.parent
    patterns = ("/Users/", "/home/", "@gmail", "@outlook", "ssrweb", "zoom.us/rec/play")
    for path in root.rglob("*"):
        if not path.is_file() or any(p in path.parts for p in (".git", ".venv", "__pycache__", ".pytest_cache")):
            continue
        if path.suffix not in (".py", ".md", ".json", ".toml", ".yml", ".example", ""):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in patterns:
            if path.name == "test_source_scan.py":
                continue
            assert pat not in text, f"{path.relative_to(root)} contains {pat!r}"
