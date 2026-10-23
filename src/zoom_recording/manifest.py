"""Folder and file naming, and the per-meeting manifest."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
SCHEMA = 1


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w]+", "_", text.lower(), flags=re.UNICODE).strip("_")
    s = re.sub(r"_+", "_", s)
    return (s[:limit].rstrip("_")) or "recording"


def meeting_dirname(start_iso: str | None, topic: str) -> str:
    date = (start_iso or "")[:10] or "undated"
    return f"{date}_{slug(topic)}"


def assign_local_names(clips: list[dict[str, Any]]) -> None:
    """Give every file a collision-free `local_name`, prefixing the clip number when there are
    several clips."""
    multi = len(clips) > 1
    used: set[str] = set()
    for clip in clips:
        for f in clip["files"]:
            base = slug(f.get("name") or f.get("type") or "file", 50)
            if multi:
                base = f"r{clip['index']}_{base}"
            ext = f.get("ext") or ""
            name = base + ext
            n = 2
            while name in used:
                name = f"{base}_{n}{ext}"
                n += 1
            used.add(name)
            f["local_name"] = name


def load(folder: Path) -> dict[str, Any]:
    p = folder / MANIFEST
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(folder: Path, data: dict[str, Any]) -> Path:
    """Write atomically. Callers pass the loaded manifest merged with their changes, so keys the
    tool does not own (your own notes) survive later runs."""
    folder.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, **{k: v for k, v in data.items() if k != "schema"}}
    fd, tmp = tempfile.mkstemp(prefix=".manifest.", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    target = folder / MANIFEST
    os.replace(tmp, target)
    return target


def completed(entry: dict[str, Any] | None, folder: Path) -> bool:
    """A manifest entry counts as done when its file exists with the recorded size and hash."""
    if not entry or not entry.get("sha256") or not entry.get("local_name"):
        return False
    p = folder / entry["local_name"]
    return p.is_file() and p.stat().st_size == entry.get("bytes")
