"""`zoom-rec`: list, inspect and download your own Zoom cloud recordings.

Every command prints exactly one JSON object to stdout, including on error. Progress goes to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__, browser, fetch, guard, manifest, parse, sharing, verify, zoomweb
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
def attached(args: argparse.Namespace, permit: guard.PasscodePermit | None = None) -> Iterator[tuple[Any, list[str], list[str]]]:
    """Verify the declared browser, attach, pick the work tab and guard it."""
    from playwright.sync_api import sync_playwright

    session = browser.session_for(resolve_home(args.home), args.cdp, args.profile_dir)
    check = browser.verify_own_browser(session)
    if not check["ok"]:
        raise _Refusal(check)
    warnings = browser.profile_warnings(session)
    with sync_playwright() as p:
        _, context = browser.connect(p, session)
        page = browser.work_tab(context)
        blocked: list[str] = []
        remove = guard.install_request_guard(context, blocked, permit)
        try:
            yield page, blocked, warnings
        finally:
            with contextlib.suppress(Exception):
                remove()


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


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--cdp", help="DevTools endpoint of the browser to drive, e.g. http://127.0.0.1:9391 "
                                      "(env ZOOM_REC_CDP; default port 9391)")
    common.add_argument("--profile-dir", help="profile directory the browser was started with "
                                              "(env ZOOM_REC_PROFILE_DIR; default <home>/browser-profile)")
    common.add_argument("--home", help="the tool's home directory (env ZOOM_REC_HOME; default ~/.zoom-recording)")

    ap = _Parser(prog="zoom-rec", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action=_VersionAction, nargs=0, help="print the version as JSON")
    sub = ap.add_subparsers(dest="command", parser_class=_Parser)
    sub.required = True

    p = sub.add_parser("browser", parents=[common], help="open a visible Chrome with the tool's own profile")
    p.set_defaults(func=cmd_browser)

    p = sub.add_parser("status", parents=[common], help="verify the browser and the Zoom sign-in")
    p.set_defaults(func=cmd_status)

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
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
