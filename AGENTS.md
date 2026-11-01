# AGENTS.md

Notes and invariants for coding agents working in `https://github.com/grapeot/zoom-recording-skill`.

## Commands

Check for a `.venv` at the repository root first. Activate it if present; otherwise create it with uv. Install dependencies with `uv pip install`.

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

Tests are fully offline. They never contact Zoom and never start a browser. Downloads are exercised against an in-memory ranged-GET fake; page driving against fake page, locator and route objects.

## Repository invariants

1. **Default branch is `master`.** Every change goes through a feature branch and a PR into `master`.
2. **Read-only toward Zoom by default; each write command registers exactly one permitted request.** Writes pass only through a one-shot permit in `guard.py`, armed around one explicit UI action, bound to the page's own HTTPS Zoom origin, consumed by the first match, and closed on anything unexpected:
   - Recordings (unchanged): only `recordings settings ID --disable-passcode --execute` (alias `zoom-rec settings ...`). `guard.PasscodePermit` accepts one `POST /nws/recording/1.0/save-share-setting` with exact fields `meetingId` matching `ID` and `enablePasscode: false`, armed solely around `Save`. No other settings, audience, expiry, permissions, invitations, deletion, renaming, moving or uploads.
   - `meetings schedule --execute`: `guard.CreateMeetingPermit` accepts one `POST /rest/meeting/save` without a query string or meeting number (a new meeting, not an edit) whose topic, description, date, start, AM/PM, duration and time zone equal the request, with no invitees, no alternative hosts, no registration, not recurring, a generated meeting id, scheduled for the signed-in user, Waiting Room off and Passcode equal to the form's state; every other field must equal the form's own defaults (`GET /rest/meeting/schedule`), with `null` allowed only for the sections the form was observed to post as null. Armed only while Save is pressed.
   - `meetings delete ID --expect-topic T --execute`: `guard.DeleteMeetingPermit` accepts one `POST /meeting/delete` with exactly the observed form fields, `id` equal to `ID`, `sendMail=false`, and empty `user_id`, `meetingMasterEventId`, `mailBody` and `occurrence`, after the meeting page's own view response showed that number and topic. Armed only while the recoverable "Delete Meeting" dialog's Delete is pressed. It also lets the page's own `POST /meeting/{ID}/confirm` lookup (empty body) through as a read.
   Keep the context-wide `guard.install_request_guard` installed for every attached command; meeting commands add `meetings=True`, which aborts every non-GET under the account-changing prefixes (`/rest`, `/nws`, `/meeting`, `/recording`, `/profile`, `/account`, `/user`, `/settings`, `/billing` and others) except the list, view and coach-mark reads, plus write-sounding paths anywhere. The one permitted meeting write is sent with redirects refused, because the route handler only sees a request's first URL. Never permitted: Zoom invitees, account-global settings, purging Recently Deleted, deleting or changing recordings or their sharing audience from a meetings command.
3. **Every UI interaction goes through `guard.py`.** General clicks retain their deny-word checks. Sharing clicks require exact `Share`, `More settings`, or `Save` operations; `guard.disable_passcode` only unchecks one visible, enabled checkbox labelled `Passcode`. Meeting clicks go through `guard.meeting_click` with a named operation: exactly one visible, enabled element with that role and exact name in the current form or dialog, otherwise `SelectorError` and nothing is clicked; Save needs an armed create permit, the dialog's Delete an armed delete permit. Typing goes through `guard.meeting_fill` (Topic, description, start-time and time-zone filters only; never the invitee field). `guard.uncheck_waiting_room` is the only meeting checkbox change. Do not add `.click(`, `.fill(`, `.set_checked(`, `dispatch_event`, `keyboard.press` or `el.click()` outside the guard functions; `tests/test_source_scan.py` enforces this per function.
4. **Never automate sign-in, never touch credentials.** No code reads, prints, exports or injects cookies, storage state, authorization headers or CSRF tokens. The human signs in in the visible window. Requests the page makes keep their own headers; the tool only rewrites the paging/range form fields of the recording-list and upcoming-meeting-list requests and reads JSON responses.
5. **Signed download URLs are secrets; canonical sharing URLs are requested output.** Download URLs stay in memory for one download; never print, log, store in a manifest, or put them in an error message. Route errors through `redact.scrub`. `settings` returns the canonical sharing URL without embedded `pwd`; never substitute a signed download URL or read passcode input values.
6. **Only drive a declared browser.** Before attaching, `browser.verify_own_browser` checks that the process listening on the DevTools port was started with the declared profile directory. Never attach to an arbitrary Chrome.
7. **One JSON object per command.** Every `zoom-cli` (and `zoom-rec`) subcommand, including on error, prints exactly one JSON object to stdout (`--help` excepted). Progress goes to stderr.
8. **Writes plan by default.** Without `--execute`, downloads do not click anything; settings inspects but never unchecks Passcode or arms a write permit; `meetings schedule` and `meetings delete` read and verify controls but click nothing. Execute runs read back (reload, edit page, upcoming list) before reporting success. `--demo` only adds slow motion, narration and a temporary `pointer-events: none` outline; it never changes what is clicked or sent.
9. **No personal data in the repository.** No real meeting IDs, UUIDs, topics, names, emails, host or account IDs, signed URLs, join links, passcodes, recordings, transcripts, chat logs or absolute machine paths. Fixtures are synthetic. Command output carries a meeting number only as the handle for the next command and never a join link, passcode or email; nothing from meeting commands is written to disk.
10. **Update `docs/working.md`** with a dated changelog entry when you change behavior.

## Layout

```text
zoom-recording-skill/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/design.md                    # goals, boundaries, architecture, safety rules
├── docs/test.md                      # what the offline suite covers and what counts as verified
├── docs/working.md                   # changelog and lessons learned
├── skills/zoom-recording/SKILL.md    # recording agent instructions
├── skills/zoom-meeting/SKILL.md      # meeting skill: `zoom-cli meetings`, with by-hand fallback notes
├── src/zoom_recording/               # implementation (module map in docs/design.md)
└── tests/                            # offline tests and synthetic fixtures
```
