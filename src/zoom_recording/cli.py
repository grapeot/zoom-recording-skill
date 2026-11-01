"""`zoom-cli`: your own Zoom cloud recordings and meetings through a signed-in Chrome window.

`zoom-cli recordings ...` lists, inspects and downloads recordings; `zoom-cli meetings ...` lists,
schedules and deletes meetings. `zoom-rec` is the original name and stays a compatible alias: its
flat commands (`zoom-rec list`, `show`, `download`, `settings`) are the recordings commands.

Every command prints exactly one JSON object to stdout, including on error. Progress goes to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__, browser, fetch, guard, manifest, meetings, parse, sharing, verify, zoomweb
from .demo import Demo
from .paths import ConfigError, enclosing_git_repo, resolve_home
from .redact import scrub

SIGN_IN_ACTION = ("Sign in to Zoom in the Chrome window the tool drives (run `zoom-rec browser` if it is "
                  "not open), go to Recordings & Transcripts, then run the command again.")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


class _VersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        emit({"ok": True, "version": __version__})
        parser.exit(0)


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


@contextlib.contextmanager
def _attach(args: argparse.Namespace, permit: Any = None, *, meetings_scope: bool = False,
            slow_mo: int = 0) -> Iterator[tuple[Any, list[str], list[str]]]:
    """Verify the declared browser, attach, pick the work tab and guard the whole context."""
    from playwright.sync_api import sync_playwright

    session = browser.session_for(resolve_home(args.home), args.cdp, args.profile_dir)
    check = browser.verify_own_browser(session)
    if not check["ok"]:
        raise _Refusal(check)
    warnings = browser.profile_warnings(session)
    with sync_playwright() as p:
        _, context = browser.connect(p, session, slow_mo=slow_mo)
        page = browser.meeting_tab(context) if meetings_scope else browser.work_tab(context)
        blocked: list[str] = []
        remove = guard.install_request_guard(context, blocked, permit, meetings=meetings_scope)
        try:
            yield page, blocked, warnings
        finally:
            with contextlib.suppress(Exception):
                remove()


@contextlib.contextmanager
def attached(args: argparse.Namespace, permit: guard.PasscodePermit | None = None) -> Iterator[tuple[Any, list[str], list[str]]]:
    """Recording commands: verify the declared browser, attach, pick the work tab and guard it."""
    with _attach(args, permit) as got:
        yield got


@contextlib.contextmanager
def attached_meetings(args: argparse.Namespace, permit: Any = None,
                      slow_mo: int = 0) -> Iterator[tuple[Any, list[str], list[str]]]:
    """Meeting commands: like `attached`, with the stricter meeting request rule on the context."""
    with _attach(args, permit, meetings_scope=True, slow_mo=slow_mo) as got:
        yield got


class _Refusal(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("message", ""))
        self.payload = payload


def _extras(blocked: list[str], warnings: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if blocked:
        out["blocked_requests"] = blocked
    if warnings:
        out["warnings"] = warnings
    return out


# ---------------------------------------------------------------- commands

def cmd_browser(args: argparse.Namespace) -> dict[str, Any]:
    session = browser.session_for(resolve_home(args.home), args.cdp, args.profile_dir)
    return browser.launch(session)


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    with attached(args) as (page, blocked, warnings):
        listing = parse.parse_list(zoomweb.load_list(page, {}))
        return {"ok": True, "browser_verified": True, "signed_in": True,
                "recordings_in_default_range": listing["total"], **_extras(blocked, warnings)}


def cmd_list(args: argparse.Namespace) -> dict[str, Any]:
    to = args.to or date.today()
    frm = args.from_ or (to - timedelta(days=365))
    if frm > to:
        raise UsageError("--from is after --to")
    base = {"from": zoomweb.page_date(frm), "to": zoomweb.page_date(to), "search_value": args.query or ""}
    if args.max_pages < 1:
        raise UsageError("--max-pages must be at least 1")
    recs: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = None
    with attached(args) as (page, blocked, warnings):
        pg = 1
        while pg <= args.max_pages:
            listing = parse.parse_list(zoomweb.load_list(page, {**base, "p": str(pg)}))
            total = listing["total"]
            new = [r for r in listing["recordings"] if r["id"] not in seen]
            seen.update(r["id"] for r in new)
            recs.extend(new)
            log(f"page {pg}: {len(listing['recordings'])} recordings (total {total})")
            if not new or total is None or len(recs) >= total:
                break
            pg += 1
        out = {"ok": True, "from": frm.isoformat(), "to": to.isoformat(), "query": args.query or None,
               "total": total, "count": len(recs), "recordings": recs}
        if total is not None and len(recs) < total:
            out["truncated"] = f"stopped after {args.max_pages} pages; raise --max-pages or narrow the range"
        out.update(_extras(blocked, warnings))
        return out


def _public_detail(detail: dict[str, Any]) -> dict[str, Any]:
    return {**detail, "clips": [{**c, "files": [parse.public_file(f) for f in c["files"]]} for c in detail["clips"]]}


def cmd_show(args: argparse.Namespace) -> dict[str, Any]:
    with attached(args) as (page, blocked, warnings):
        detail = parse.parse_detail(zoomweb.load_detail(page, args.id))
        manifest.assign_local_names(detail["clips"])
        return {"ok": True, "meeting": _public_detail(detail), **_extras(blocked, warnings)}


def cmd_settings(args: argparse.Namespace) -> dict[str, Any]:
    if args.execute and not args.disable_passcode:
        raise UsageError("--execute requires --disable-passcode")
    permit = guard.PasscodePermit("", args.id)
    with attached(args, permit=permit) as (page, blocked, warnings):
        try:
            result = sharing.settings(page, args.id, disable=args.disable_passcode,
                                      execute=args.execute, permit=permit)
        except (guard.GuardError, zoomweb.PageError, parse.PayloadError, KeyboardInterrupt) as e:
            interrupted = isinstance(e, KeyboardInterrupt)
            error = "interrupted" if interrupted else "guard_refused" if isinstance(e, guard.GuardError) else "page"
            result = {"ok": False, "error": error,
                      "message": "stopped; inspect with settings ID before retrying" if interrupted else scrub(e),
                      "execution_attempted": permit.consumed,
                      "outcome_unknown": permit.consumed, "readback_verified": False,
                      "allowed_requests": permit.audit}
        return {**result, **_extras(blocked, warnings)}


def _meeting_folder(args: argparse.Namespace, detail: dict[str, Any]) -> Path:
    if args.out:
        return Path(args.out).expanduser()
    return Path(args.root).expanduser() / manifest.meeting_dirname(detail["start"], detail["topic"])


def _plan(detail: dict[str, Any], types: set[str] | None, clip: int | None, folder: Path,
          entries: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    items = []
    present_types = set()
    for c in detail["clips"]:
        if clip is not None and c["index"] != clip:
            continue
        for f in c["files"]:
            present_types.add(f["type"])
            if types is not None and f["type"] not in types:
                continue
            if not f["ready"]:
                state = "not_ready"
            elif manifest.completed(entries.get(f["file_id"]), folder):
                state = "done"
            else:
                state = "pending"
            items.append({"clip": c["index"], "file": f, "state": state})
    missing = sorted((types or set()) - present_types)
    return items, missing


def _item_out(item: dict[str, Any]) -> dict[str, Any]:
    f = item["file"]
    return {"clip": item["clip"], "type": f["type"], "name": f["name"], "local_name": f["local_name"],
            "bytes": f["bytes"], "state": item["state"], **({"result": item["result"]} if "result" in item else {})}


def cmd_download(args: argparse.Namespace) -> dict[str, Any]:
    types = parse.normalize_types(args.files)
    with attached(args) as (page, blocked, warnings):
        detail = parse.parse_detail(zoomweb.load_detail(page, args.id))
        if detail["download_disabled"]:
            return {"ok": False, "error": "download_disabled",
                    "message": "Zoom reports downloads are disabled for this recording", **_extras(blocked, warnings)}
        manifest.assign_local_names(detail["clips"])
        folder = _meeting_folder(args, detail)
        if args.clip is not None and args.clip not in [c["index"] for c in detail["clips"]]:
            raise UsageError(f"--clip {args.clip} does not exist; this recording has clips "
                             f"{[c['index'] for c in detail['clips']]}")
        man = manifest.load(folder)
        old_meeting = man.get("meeting") if isinstance(man.get("meeting"), dict) else {}
        if old_meeting.get("id") not in (None, detail["id"]):
            return {"ok": False, "error": "folder_in_use",
                    "message": "the output folder holds a manifest for a different meeting", "folder": str(folder),
                    **_extras(blocked, warnings)}
        old_files = man.get("files") if isinstance(man.get("files"), dict) else {}
        entries: dict[str, Any] = dict(old_files)
        items, missing = _plan(detail, types, args.clip, folder, entries)
        meeting = {**old_meeting, **{k: detail[k] for k in ("id", "topic", "start", "timezone", "duration_s")}}
        repo = enclosing_git_repo(folder)
        if repo is not None:
            warnings = [*warnings, f"the meeting folder is inside a git repository ({repo.name}); recordings and "
                                   "transcripts should stay out of version control"]
        head = {"meeting": meeting, "folder": str(folder)}
        if missing:
            head["missing_types"] = missing
        if not args.execute:
            return {"ok": True, "dry_run": True, **head, "files": [_item_out(i) for i in items],
                    "next": "rerun with --execute to download the pending files", **_extras(blocked, warnings)}

        referer = zoomweb.origin_of(page) + "/"
        pending_clips = sorted({i["clip"] for i in items if i["state"] == "pending"})
        for clip_index in pending_clips:
            todo = [i for i in items if i["clip"] == clip_index and i["state"] == "pending"]
            for attempt in (1, 2):
                if attempt == 2:
                    retry = [i for i in todo if i.get("result", {}).get("error") == "link_rejected"]
                    if not retry:
                        break
                    log("a download link was refused; fetching fresh links")
                    zoomweb.load_detail(page, args.id)
                    todo = retry
                position = [c["index"] for c in detail["clips"]].index(clip_index) + 1
                links = zoomweb.capture_links(page, position, len(detail["clips"]), log=log)
                clip_files = next(c["files"] for c in detail["clips"] if c["index"] == clip_index)
                mapping, unmatched = parse.match_links(links, clip_files)
                if unmatched:
                    log(f"{unmatched} download link(s) matched no listed file and were ignored")
                for item in todo:
                    f = item["file"]
                    link = mapping.get(f["file_id"])
                    if link is None:
                        item["state"], item["result"] = "failed", {"error": "no_link",
                                                                   "message": "no download link matched this file"}
                        continue
                    dest = folder / f["local_name"]
                    log(f"downloading {f['local_name']} ({f['bytes']} bytes)")
                    try:
                        res = fetch.download(
                            fetch.browser_getter(page.context.request, link, referer), dest, f["bytes"],
                            progress=lambda done, total, n=f["local_name"]: log(f"  {n}: {done}/{total}"),
                        )
                    except fetch.FetchError as e:
                        item["state"], item["result"] = "failed", {"error": e.code, "message": scrub(e)}
                        continue
                    finally:
                        link = None  # noqa: F841 - drop the reference as soon as possible
                    check = verify.check_file(dest, f["type"], f["duration_ms"])
                    entries[f["file_id"]] = {
                        **(entries.get(f["file_id"]) or {}),
                        "clip": clip_index, "type": f["type"], "name": f["name"], "local_name": f["local_name"],
                        "bytes": res["bytes"], "duration_ms": f["duration_ms"], "sha256": res["sha256"],
                        "md5": res["md5"], "etag_md5_match": res["etag_md5_match"], "check": check,
                        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    manifest.save(folder, {**man, "meeting": meeting, "files": entries})
                    item["state"] = "downloaded" if check.get("ok") is not False else "check_failed"
                    item["result"] = {"bytes": res["bytes"], "sha256": res["sha256"],
                                      "resumed_from": res["resumed_from"], "check": check}
            links = mapping = None  # noqa: F841
        if entries:
            manifest.save(folder, {**man, "meeting": meeting, "files": entries})
        ok = all(i["state"] in ("done", "downloaded") for i in items)
        if any(i["state"] == "not_ready" for i in items):
            head["not_ready"] = "some requested files are still processing in Zoom; rerun later"
        return {"ok": ok, "dry_run": False, **head, "files": [_item_out(i) for i in items],
                "manifest": str(folder / manifest.MANIFEST), **_extras(blocked, warnings)}


# ---------------------------------------------------------------- meetings

def _elapsed(start: float) -> float:
    return round(time.monotonic() - start, 2)


def _write_failure(e: BaseException, permit: Any, verb: str) -> dict[str, Any]:
    """A failed or interrupted meeting write, with whether the one permitted request went out."""
    if isinstance(e, KeyboardInterrupt):
        error, message = "interrupted", f"stopped; run `zoom-cli meetings list` before retrying the {verb}"
    elif isinstance(e, guard.SelectorError):
        error, message = "selector", scrub(e)
    elif isinstance(e, guard.GuardError):
        error, message = "guard_refused", scrub(e)
    else:
        error, message = "page", scrub(e)
    out: dict[str, Any] = {"ok": False, "error": error, "message": message,
                           "execution_attempted": permit.consumed, "outcome_unknown": permit.consumed,
                           "allowed_requests": permit.audit}
    if isinstance(e, guard.SelectorError):
        out["selector"] = e.detail
    if getattr(permit, "refused", None):
        out["permit_refused"] = permit.refused
    return out


def cmd_meetings_list(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_pages < 1:
        raise UsageError("--max-pages must be at least 1")
    window = meetings.window_for(args.to) if args.to else {}
    with attached_meetings(args) as (page, blocked, warnings):
        listing = meetings.list_all(page, args.max_pages, window, log=log)
        return {"ok": True, **listing, **_extras(blocked, warnings)}


def cmd_meetings_schedule(args: argparse.Namespace) -> dict[str, Any]:
    start = time.monotonic()
    try:
        spec = meetings.parse_spec(args.topic, args.date, args.start, args.duration, args.tz, args.description)
    except meetings.SpecError as e:
        raise UsageError(str(e)) from None
    demo = Demo(args.demo)
    permit = guard.CreateMeetingPermit("", {})
    with attached_meetings(args, permit, slow_mo=demo.slow_mo) as (page, blocked, warnings):
        if not args.execute:
            plan = meetings.schedule_plan(page, spec, demo)
            return {"ok": not plan["duplicates"], "dry_run": True, "executed": False, "meeting": spec.public(),
                    **({"error": "duplicate_meeting", "duplicates": plan["duplicates"]} if plan["duplicates"] else {}),
                    "will_request": {"method": "POST", "path": guard.CREATE_MEETING_PATH,
                                     "fields": {**spec.permit_fields(), "waiting_room": False,
                                                "passcode": "unchanged from the form", "invitees": 0}},
                    "next": "rerun with --execute to create it and read it back", "elapsed_s": _elapsed(start),
                    **_extras(blocked, warnings)}
        try:
            result = meetings.schedule_execute(page, spec, permit, demo, blocked)
        except (guard.GuardError, zoomweb.PageError, KeyboardInterrupt) as e:
            result = _write_failure(e, permit, "schedule")
        return {"dry_run": False, "meeting": {**spec.public(), **({"id": result.pop("meeting_id")}
                                                                  if "meeting_id" in result else {})},
                **result, "allowed_requests": permit.audit, "elapsed_s": _elapsed(start),
                **_extras(blocked, warnings)}


def cmd_meetings_delete(args: argparse.Namespace) -> dict[str, Any]:
    start = time.monotonic()
    if not meetings.MEETING_ID.fullmatch(args.id):
        raise UsageError("ID must be the meeting number (digits only, no spaces)")
    expect = " ".join((args.expect_topic or "").split())
    if not expect:
        raise UsageError("--expect-topic is required and must not be empty")
    demo = Demo(args.demo)
    permit = guard.DeleteMeetingPermit("", args.id)
    with attached_meetings(args, permit, slow_mo=demo.slow_mo) as (page, blocked, warnings):
        try:
            result = meetings.delete_meeting(page, args.id, expect, execute=args.execute, permit=permit,
                                             demo=demo, blocked=blocked)
        except (guard.GuardError, zoomweb.PageError, KeyboardInterrupt) as e:
            result = _write_failure(e, permit, "delete")
        return {**result, "allowed_requests": permit.audit, "elapsed_s": _elapsed(start),
                **_extras(blocked, warnings)}


# ---------------------------------------------------------------- parser

def _common() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--cdp", help="DevTools endpoint of the browser to drive, e.g. http://127.0.0.1:9391 "
                                      "(env ZOOM_REC_CDP; default port 9391)")
    common.add_argument("--profile-dir", help="profile directory the browser was started with "
                                              "(env ZOOM_REC_PROFILE_DIR; default <home>/browser-profile)")
    common.add_argument("--home", help="the tool's home directory (env ZOOM_REC_HOME; default ~/.zoom-recording)")
    return common


def _add_browser_commands(sub: Any, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser("browser", parents=[common], help="open a visible Chrome with the tool's own profile")
    p.set_defaults(func=cmd_browser)

    p = sub.add_parser("status", parents=[common], help="verify the browser and the Zoom sign-in")
    p.set_defaults(func=cmd_status)


def _add_recording_commands(sub: Any, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser("list", parents=[common], help="list cloud recordings in a date range")
    p.add_argument("--from", dest="from_", type=_iso_date, help="YYYY-MM-DD (default: one year before --to)")
    p.add_argument("--to", type=_iso_date, help="YYYY-MM-DD (default: today)")
    p.add_argument("--query", help="search text, matched by Zoom against topics")
    p.add_argument("--max-pages", type=int, default=50, help="stop after this many result pages (15 per page)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", parents=[common], help="show one recording's clips and files")
    p.add_argument("id", help="recording id from `list`")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("settings", parents=[common], help="inspect sharing; preview passcode removal unless --execute")
    p.add_argument("id", help="recording id from `list`")
    p.add_argument("--disable-passcode", action="store_true", help="plan disabling this recording's passcode only")
    p.add_argument("--execute", action="store_true", help="save only the planned passcode removal and read back")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("download", parents=[common], help="download files of one recording (plan unless --execute)")
    p.add_argument("id", help="recording id from `list`")
    p.add_argument("--files", default="all",
                   help="comma-separated types: video (mp4), audio (m4a), transcript, chat, cc, or all (default)")
    p.add_argument("--clip", type=int, help="only this clip number (Recording N on the detail page)")
    p.add_argument("--out", help="folder for this meeting (default: <root>/<date>_<topic>)")
    p.add_argument("--root", default="zoom-recordings", help="parent folder used when --out is not given")
    p.add_argument("--execute", action="store_true", help="actually download; without it only a plan is printed")
    p.set_defaults(func=cmd_download)


def _add_meeting_commands(sub: Any, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser("list", parents=[common], help="list upcoming meetings (read-only)")
    p.add_argument("--to", type=_iso_date, help="YYYY-MM-DD; widen the list past Zoom's default three months")
    p.add_argument("--max-pages", type=int, default=10, help="stop after this many result pages (15 per page)")
    p.set_defaults(func=cmd_meetings_list)

    p = sub.add_parser("schedule", parents=[common],
                       help="schedule one meeting without invitees, Waiting Room off (plan unless --execute)")
    p.add_argument("--topic", required=True, help="meeting topic")
    p.add_argument("--date", required=True, help="YYYY-MM-DD in --tz")
    p.add_argument("--start", required=True, help="start in --tz: HH:MM (24-hour) or H:MM AM/PM; quarter hours")
    p.add_argument("--duration", required=True, type=int, help="minutes; a multiple of 15")
    p.add_argument("--tz", required=True, help="IANA time zone, e.g. America/Los_Angeles")
    p.add_argument("--description", help="optional one-line description")
    p.add_argument("--execute", action="store_true", help="create the meeting and read it back")
    p.add_argument("--demo", action="store_true", help="slow down and outline each element on the page")
    p.set_defaults(func=cmd_meetings_schedule)

    p = sub.add_parser("delete", parents=[common],
                       help="delete one meeting when number and topic both match (plan unless --execute)")
    p.add_argument("id", help="meeting number from `meetings list` (digits)")
    p.add_argument("--expect-topic", required=True, help="the meeting's exact topic; refuse when it differs")
    p.add_argument("--execute", action="store_true", help="delete it (recoverable for 7 days) and read back")
    p.add_argument("--demo", action="store_true", help="slow down and outline each element on the page")
    p.set_defaults(func=cmd_meetings_delete)


def build_parser(prog: str = "zoom-rec") -> argparse.ArgumentParser:
    """`zoom-cli` has command groups; `zoom-rec` additionally keeps its original flat recording commands."""
    common = _common()
    ap = _Parser(prog=prog, description=__doc__.splitlines()[0])
    ap.add_argument("--version", action=_VersionAction, nargs=0, help="print the version as JSON")
    sub = ap.add_subparsers(dest="command", parser_class=_Parser)
    sub.required = True
    _add_browser_commands(sub, common)
    if prog == "zoom-rec":
        _add_recording_commands(sub, common)
    rec = sub.add_parser("recordings", help="cloud recordings: list, show, download, settings")
    rec_sub = rec.add_subparsers(dest="recordings_command", parser_class=_Parser)
    rec_sub.required = True
    _add_recording_commands(rec_sub, common)
    mtg = sub.add_parser("meetings", help="meetings: list, schedule, delete")
    mtg_sub = mtg.add_subparsers(dest="meetings_command", parser_class=_Parser)
    mtg_sub.required = True
    _add_meeting_commands(mtg_sub, common)
    return ap


def main(argv: list[str] | None = None, prog: str = "zoom-rec") -> int:
    parser = build_parser(prog)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        emit({"ok": False, "error": "usage", "message": str(e)})
        return 2
    try:
        result = args.func(args)
    except UsageError as e:
        result = {"ok": False, "error": "usage", "message": str(e)}
    except _Refusal as e:
        result = {"ok": False, **e.payload}
    except zoomweb.NeedsSignIn:
        result = {"ok": False, "error": "needs_sign_in", "action": SIGN_IN_ACTION}
    except ConfigError as e:
        result = {"ok": False, "error": "config", "message": scrub(e)}
    except guard.SelectorError as e:
        result = {"ok": False, "error": "selector", "message": scrub(e), "selector": e.detail}
    except guard.GuardError as e:
        result = {"ok": False, "error": "guard_refused", "message": scrub(e)}
    except (zoomweb.PageError, parse.PayloadError) as e:
        result = {"ok": False, "error": "page", "message": scrub(e)}
    except KeyboardInterrupt:
        result = {"ok": False, "error": "interrupted",
                  "message": "stopped; rerun the same command to resume (partial files are kept)"}
    except Exception as e:  # noqa: BLE001 - one JSON object, always
        result = {"ok": False, "error": "unexpected", "type": type(e).__name__, "message": scrub(e)[:500]}
    emit(result)
    return 0 if result.get("ok") else 1


def main_cli(argv: list[str] | None = None) -> int:
    """Entry point of `zoom-cli`."""
    return main(argv, prog="zoom-cli")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
