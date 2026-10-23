"""Content checks after download: ffprobe for media, WebVTT shape for transcripts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_TIMING = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{1,2}:\d{2}\.\d{3})")
DURATION_TOLERANCE_S = 2.0


def _seconds(ts: str) -> float:
    parts = ts.split(":")
    s = float(parts[-1])
    m = int(parts[-2]) if len(parts) > 1 else 0
    h = int(parts[-3]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def vtt_check(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    cues = 0
    last_end = 0.0
    for line in text.splitlines():
        m = _TIMING.match(line)
        if m:
            cues += 1
            last_end = max(last_end, _seconds(m.group(2)))
    header = text.lstrip().startswith("WEBVTT")
    return {"ok": header and cues > 0, "webvtt_header": header, "cues": cues, "last_cue_end_s": round(last_end, 3)}


def text_check(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "lines": len(text.splitlines())}


def ffprobe(path: Path) -> dict[str, Any] | None:
    exe = shutil.which("ffprobe")
    if exe is None:
        return None
    r = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or "ffprobe failed").strip()[:300]}
    data = json.loads(r.stdout or "{}")
    streams = [
        {k: v for k, v in s.items() if k in ("codec_type", "codec_name", "width", "height")}
        for s in data.get("streams", [])
    ]
    dur = data.get("format", {}).get("duration")
    return {"ok": True, "duration_s": round(float(dur), 3) if dur else None, "streams": streams}


def check_file(path: Path, ftype: str, expected_duration_ms: int | None) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext == ".vtt":
        return vtt_check(path)
    if ext == ".txt":
        return text_check(path)
    if ftype in ("video", "audio") or ext in (".mp4", ".m4a"):
        probe = ffprobe(path)
        if probe is None:
            return {"ok": None, "note": "ffprobe not installed; content not checked"}
        if not probe.get("ok"):
            return probe
        kinds = {s.get("codec_type") for s in probe["streams"]}
        need = {"video", "audio"} if ftype == "video" else {"audio"}
        out = {**probe, "ok": need <= kinds}
        if not need <= kinds:
            out["error"] = f"missing stream types: {sorted(need - kinds)}"
        if expected_duration_ms and probe.get("duration_s") is not None:
            diff = abs(probe["duration_s"] - expected_duration_ms / 1000)
            out["duration_diff_s"] = round(diff, 3)
            if diff > DURATION_TOLERANCE_S:
                out["ok"] = False
                out["error"] = f"duration differs from Zoom's by {diff:.1f} s"
        return out
    return {"ok": None, "note": "no content check for this file type"}
