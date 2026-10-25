"""Browser-first sharing inspection and explicit, narrowly guarded passcode removal."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from . import guard, parse, zoomweb

SHARE_PATH = "/nws/recording/1.0/share-data"
# This checkbox is conditional UI under Passcode, not an independent permission change.
PASSCODE_DEPENDENT_LABELS = {
    "Allow invitees to access this recording without a passcode",
    "Allow meeting invitees to access this recording without a passcode",
}


def share_url(result: dict[str, Any]) -> str:
    """Use the same canonical share URL construction as Zoom's sharing panel, without pwd."""
    base = result.get("baseUrl")
    token = result.get("encryptMeetId")
    if not isinstance(base, str) or not isinstance(token, str) or not token:
        raise zoomweb.PageError("sharing response lacks canonical URL fields")
    parts = urlsplit(base)
    if (parts.scheme != "https" or not parts.hostname or
            not guard.ZOOM_HOST.search(parts.hostname) or parts.username or parts.password or
            parts.port not in (None, 443) or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise zoomweb.PageError("sharing response has an invalid Zoom origin")
    return base.rstrip("/") + "/rec/share/" + quote(token, safe="")


def _click(locator: Any, action: str) -> None:
    guard.safe_click(locator, guard.SHARING_CLICKS[action], sharing_action=action)


def inspect(page: Any, meeting_id: str) -> tuple[dict[str, Any], Any]:
    detail = parse.parse_detail(zoomweb.load_detail(page, meeting_id))
    if detail["id"] != meeting_id:
        raise zoomweb.PageError("recording identity mismatch; no settings changed")
    try:
        with page.expect_response(lambda r: urlsplit(r.url).path == SHARE_PATH and
                                  r.request.method == "GET", timeout=30000) as info:
            _click(page.get_by_role("button", name="Share", exact=True), "open")
        resp = info.value
        if resp.status != 200:
            raise zoomweb.PageError("sharing inspection failed")
        result = parse._result(resp.json(), SHARE_PATH)
        if result.get("meetingId") != meeting_id:
            raise zoomweb.PageError("sharing recording identity mismatch")
        url = share_url(result)
        dialog = page.get_by_role("dialog", name="Share", exact=True)
        more = dialog.get_by_role("button", name="More settings", exact=True)
        more.wait_for(state="visible", timeout=10000)
        public_link_access = dialog.get_by_text("Anyone with the link", exact=True).count() == 1
        _click(more, "expand")
        checkbox = dialog.get_by_role("checkbox", name="Passcode", exact=True)
        checkbox.wait_for(state="visible", timeout=10000)
        # Only labels, checked and disabled state: never password input values or credentials.
        controls = dialog.get_by_role("checkbox").evaluate_all("""els => els.map(e => ({
            label: (e.getAttribute('aria-labelledby') || '').split(/\\s+/)
              .map(id => document.getElementById(id)?.textContent || '').join(' ').trim(),
            checked: e.checked, disabled: e.disabled
        }))""")
        if not controls or any(not c["label"] for c in controls) or len({c["label"] for c in controls}) != len(controls):
            raise zoomweb.PageError("ambiguous sharing controls; no settings changed")
        state = {"passcode_enabled": checkbox.is_checked(), "passcode_locked": not checkbox.is_enabled(),
                 "share_url": url, "public_link_access": public_link_access, "controls": controls,
                 "access": {k: result.get(k) for k in ("currentShareScopeId", "shareScopeType", "publicRecording",
                             "privateRecording", "enableExpiryDate", "expiryDate", "onDemand",
                             "waterMarkerFlag", "allowViewersDownload", "allowViewerSeeChat", "allowViewerSeeTranscript",
                             "allowInviteesBypassPasscode")}}
        meeting = {k: detail[k] for k in ("id", "topic", "start", "timezone", "duration_s")}
        return {"meeting": meeting, "settings": state}, dialog
    except (guard.GuardError, zoomweb.PageError):
        raise
    except Exception as e:
        raise zoomweb.PageError(f"sharing inspection failed ({type(e).__name__})") from None


def unchanged_controls(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Compare controls by label, allowing only the documented passcode child to disappear."""
    old = {c["label"]: c for c in before["controls"] if c["label"] != "Passcode"}
    new = {c["label"]: c for c in after["controls"] if c["label"] != "Passcode"}
    removed = old.keys() - new.keys()
    if removed:
        old_flag = before["access"].get("allowInviteesBypassPasscode")
        new_flag = after["access"].get("allowInviteesBypassPasscode")
        if (not before["passcode_enabled"] or after["passcode_enabled"]
                or not removed.issubset(PASSCODE_DEPENDENT_LABELS)
                or not isinstance(old_flag, bool) or not isinstance(new_flag, bool) or old_flag != new_flag):
            return False
        for label in removed:
            del old[label]
    return old == new


def settings(page: Any, meeting_id: str, *, disable: bool, execute: bool,
             permit: guard.PasscodePermit) -> dict[str, Any]:
    if permit.meeting_id != meeting_id or (execute and not disable):
        raise guard.GuardError("execution requires a matching recording and explicit passcode removal")
    before, dialog = inspect(page, meeting_id)
    # Detail navigation can redirect an initial blank/global tab to a regional Zoom host.
    permit.origin = zoomweb.origin_of(page)
    state = before["settings"]
    change = disable and state["passcode_enabled"]
    out = {"ok": True, "dry_run": not execute, "executed": False, **before,
           "changes": {"passcode_enabled": {"from": True, "to": False}} if change else {},
           "allowed_requests": permit.audit}
    if not execute:
        return out
    if not change:
        out["readback_verified"] = True
        out["no_op"] = "passcode already disabled"
        return out
    if state["passcode_locked"]:
        raise guard.GuardError("passcode is locked by account policy; no settings changed")
    checkbox = dialog.get_by_role("checkbox", name="Passcode", exact=True)
    guard.disable_passcode(checkbox)
    if checkbox.is_checked():
        raise guard.GuardError("Passcode checkbox did not clear")
    save_button = dialog.get_by_role("button", name="Save", exact=True)
    permit.armed = True
    try:
        with page.expect_response(lambda r: r.url == permit.origin + guard.SAVE_SETTINGS_PATH
                                  and r.request.method == "POST", timeout=30000) as info:
            _click(save_button, "save")
        resp = info.value
        body = resp.json()
        if resp.status != 200 or not isinstance(body, dict) or body.get("status") is not True:
            raise zoomweb.PageError("passcode save failed; inspect settings before retrying")
        if not permit.consumed:
            raise guard.GuardError("save did not pass the scoped request guard")
    except (guard.GuardError, zoomweb.PageError):
        raise
    except Exception as e:
        raise zoomweb.PageError(f"save outcome uncertain ({type(e).__name__}); inspect settings before retrying") from None
    finally:
        permit.armed = False
    # The response can precede the panel's asynchronous state update. Wait for the
    # current settings view to close before navigation/readback can race its refresh.
    try:
        save_button.wait_for(state="hidden", timeout=30000)
        page.get_by_role("button", name="More settings", exact=True).wait_for(state="visible", timeout=30000)
    except Exception as e:
        raise zoomweb.PageError(f"save UI did not settle ({type(e).__name__}); inspect settings before retrying") from None
    after, _ = inspect(page, meeting_id)
    verified = (not after["settings"]["passcode_enabled"] and unchanged_controls(state, after["settings"])
                and state["access"] == after["settings"]["access"]
                and state["public_link_access"] == after["settings"]["public_link_access"])
    return {**out, "ok": verified, "executed": True, "before": state,
            "settings": after["settings"], "readback_verified": verified,
            **({"error": "readback_mismatch"} if not verified else {})}
