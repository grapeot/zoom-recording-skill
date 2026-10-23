"""Pure parsing of the recordings web app's JSON payloads into small records.

Only fields the commands need are kept. Host, account and attendee details, thumbnails and
share settings are dropped on purpose.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

LIST_ENDPOINT = "/nws/recording/1.0/host-list"
DETAIL_ENDPOINT = "/nws/recording/1.0/detail"
BATCH_ENDPOINT = "/nws/recording/1.0/download-meeting"

# Zoom file icon types -> the short names used on the command line.
TYPE_NAMES = {
    "video": "video",
    "audio": "audio",
    "audio-transcript": "transcript",
    "chatfile": "chat",
    "cc": "cc",
}
TYPE_ALIASES = {
    "mp4": "video", "video": "video",
    "m4a": "audio", "audio": "audio",
    "transcript": "transcript", "vtt": "transcript",
    "chat": "chat", "txt": "chat",
    "cc": "cc", "captions": "cc",
}
READY = 100


class PayloadError(Exception):
    """A payload did not have the expected shape, or the web app reported an error."""


def _result(body: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise PayloadError(f"{endpoint}: response is not a JSON object")
    if body.get("status") is not True:
        raise PayloadError(f"{endpoint}: status={body.get('status')!r} errorCode={body.get('errorCode')!r} "
                           f"errorMessage={body.get('errorMessage')!r}")
    res = body.get("result")
    if not isinstance(res, dict):
        raise PayloadError(f"{endpoint}: missing result object")
    return res


def iso_time(ms: int | None, tz: str | None) -> str | None:
    if not ms:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    if tz and ZoneInfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo(tz))
        except Exception:  # noqa: BLE001 - unknown zone name: keep UTC
            pass
    return dt.isoformat(timespec="seconds")


def parse_list(body: Any) -> dict[str, Any]:
    res = _result(body, LIST_ENDPOINT)
    items = res.get("recordings")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise PayloadError(f"{LIST_ENDPOINT}: recordings is not a list")
    recs = []
    for it in items:
        try:
            recs.append({
                "id": it["meetingId"],
                "topic": it.get("topic") or "",
                "start": iso_time(it.get("meetingStartTime"), it.get("meetingTimezone")),
                "timezone": it.get("meetingTimezone"),
                "duration_s": round((it.get("meetingDurationInMs") or 0) / 1000) or (it.get("meetingDuration") or 0) * 60,
                "file_count": it.get("recordingCount"),
                "total_bytes": it.get("totalSize"),
            })
        except (KeyError, TypeError) as e:
            raise PayloadError(f"{LIST_ENDPOINT}: recording entry without {e}") from None
    return {
        "recordings": recs,
        "total": res.get("total_records"),
        "page": res.get("page"),
        "page_size": res.get("page_size"),
    }


def _file_record(f: dict[str, Any], transcript_status: dict[str, Any]) -> dict[str, Any]:
    icon = f.get("iconType") or "unknown"
    server_name = f.get("fileName") or ""
    ext = os.path.splitext(server_name)[1].lower()
    rec = {
        "file_id": f.get("id"),
        "type": TYPE_NAMES.get(icon, icon),
        "name": f.get("displayFileName") or icon,
        "ext": ext,
        "bytes": f.get("fileSize"),
        "duration_ms": f.get("videoDuration") or None,
        "ready": f.get("recordingStatus") == READY,
        "_server_name": server_name,  # internal: used to match download links, never printed
    }
    if icon == "audio-transcript" and transcript_status:
        st = transcript_status.get(f.get("id"))
        if st is None and len(transcript_status) == 1:
            st = next(iter(transcript_status.values()))
        if st is not None:
            rec["ready"] = rec["ready"] and st == "OK"
    return rec


def _hms(value: Any) -> int:
    try:
        parts = [int(x) for x in str(value).split(":")]
    except ValueError:
        return 0
    total = 0
    for x in parts:
        total = total * 60 + x
    return total


def _meeting_seconds(meet: dict[str, Any], clips: list[dict[str, Any]]) -> int:
    """Older meetings lack meetingDurationInMs; fall back to minutes, then to the clip lengths."""
    if meet.get("meetingDurationInMs"):
        return round(meet["meetingDurationInMs"] / 1000)
    if meet.get("meetingDuration"):
        return int(meet["meetingDuration"]) * 60
    return sum(_hms(c.get("duration")) for c in clips)


def parse_detail(body: Any) -> dict[str, Any]:
    res = _result(body, DETAIL_ENDPOINT)
    meet = res.get("meet")
    files_map = res.get("clipFilesResultMap")
    if not isinstance(meet, dict) or not isinstance(files_map, dict):
        raise PayloadError(f"{DETAIL_ENDPOINT}: missing meet or clipFilesResultMap")
    info_map = res.get("clipInfoResultMap") or {}
    tstatus = res.get("transcriptStatusMap") or {}
    clips = []
    for pos, key in enumerate(sorted(files_map, key=lambda k: int(k) if str(k).isdigit() else 0), start=1):
        info = info_map.get(key) or {}
        files = [_file_record(f, tstatus) for f in files_map[key] or []]
        clips.append({
            "index": info.get("currentClipIndex") or pos,
            "start_ms": int(key) if str(key).isdigit() else info.get("clipStartTimeMS"),
            "duration": info.get("clipDuration"),
            "files": files,
        })
    return {
        "id": meet.get("meetingId"),
        "topic": meet.get("topic") or "",
        "start": iso_time(meet.get("meetingStartTime"), meet.get("meetingTimezone")),
        "timezone": meet.get("meetingTimezone"),
        "duration_s": _meeting_seconds(meet, clips),
        "download_disabled": bool(res.get("disableDownload")),
        "clips": clips,
    }


def public_file(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def parse_batch(body: Any) -> list[str]:
    res = _result(body, BATCH_ENDPOINT)
    links = res.get("batchDownloadFileList")
    if not isinstance(links, list) or not all(isinstance(u, str) for u in links):
        raise PayloadError(f"{BATCH_ENDPOINT}: batchDownloadFileList missing or not a list of strings")
    return links


def match_links(links: list[str], files: list[dict[str, Any]]) -> tuple[dict[str, str], int]:
    """Map file_id -> signed link by the file name at the end of the link's path.

    Returns the mapping and the number of links that matched no file (or more than one).
    Never guesses: an ambiguous or unknown link is left out.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if f.get("_server_name"):
            by_name.setdefault(f["_server_name"], []).append(f)
    mapping: dict[str, str] = {}
    unmatched = 0
    for link in links:
        last = unquote(urlsplit(link).path.rsplit("/", 1)[-1])
        cands = by_name.get(last, [])
        if len(cands) == 1 and cands[0]["file_id"] not in mapping:
            mapping[cands[0]["file_id"]] = link
        else:
            unmatched += 1
    return mapping, unmatched


def normalize_types(spec: str | None) -> set[str] | None:
    """`--files` value -> set of type names; None means every type."""
    if not spec or spec.strip().lower() == "all":
        return None
    out = set()
    for raw in spec.split(","):
        t = raw.strip().lower()
        if not t:
            continue
        out.add(TYPE_ALIASES.get(t, t))
    return out
