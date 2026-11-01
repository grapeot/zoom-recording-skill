# Test strategy

All automated tests are offline: `python -m pytest -q`. They never contact Zoom, never start Chrome, and need no account.

## Unit tests

- Parsing: list and detail payloads (synthetic fixtures shaped like the web app's responses) become meeting and file records; multi-clip meetings, files still processing, unknown file types, and payloads with `status: false` reported with the endpoint name.
- List request rewrite: only `from`, `to`, `p` and `search_value` change; other form fields pass through; date conversion from ISO to the page's format.
- File selection and naming: `--files` type aliases, collision-free local names, multi-clip prefixes, folder naming from date and topic.
- Signed-link matching: each link matched to exactly one file by its name; unmatched links and files reported, never guessed.
- Redaction: URLs lose their query strings; token-shaped strings are replaced; error strings from failed requests carry no signed link.
- Guards: allowlisted labels pass; deny words in label or text refuse the click; mutation paths are aborted, reads continue.
- Sharing guard: exact recording ID, endpoint, method, false value, and one-shot arming; reject extra/duplicate fields, enabling passcodes, unknown bodies, other origins and unrelated recording writes. Canonical share URL validation rejects non-Zoom origins and never embeds password/download fields.
- Checkbox wrapper: validate the input and exact parent wrapper before clicking, verify it clears, and reject wrong tags/classes/labels, multiple checkbox children, or still-checked inputs. Native `set_checked` calls remain prohibited by the source scan.
- Own-browser check: command lines with and without the declared port and profile.
- Source scan: no click, key press, event dispatch, cookie or storage-state calls outside `guard.py`; no reads of request headers.
- CLI contract: every command, including usage errors and failures, prints exactly one JSON object.

- Meeting permits (`tests/test_meetings.py`): the create permit accepts the exact new-meeting body once (including Zoom's zero-padded hour) and refuses every single-field change, edits (`meetingNumber` query or body field), invitees, alternative hosts, registration, recurrence, Waiting Room on, a changed Passcode, unwrapped or missing fields, duplicate keys, unparseable bodies, other endpoints, methods, resource types and origins, and anything while unarmed. The delete permit accepts one exact form body for the requested number and refuses other numbers, `sendMail=true`, a mail body, an occurrence, extra or duplicate fields, Recently Deleted endpoints and anything while unarmed; it lets only its own empty-body `confirm` lookup through without consuming.
- Meeting request rule: non-GET under `/meeting` and `/rest/meeting` is blocked except the list and view reads; settings, invite, recover and trash paths are blocked; static assets, other hosts and the bot-challenge path pass. Through `install_request_guard(meetings=True)`: no permit blocks both writes, an armed create permit opens one create and never a delete, an armed delete permit opens one delete for its number and never a create. The recording guard's matcher is unchanged without `meetings=True`.
- Meeting UI guards: zero or several matches raise `SelectorError` with operation, expected name and count, and click nothing; wrong role, wrong name, hidden or disabled controls, past date cells, a `Schedule a Meeting` that does not lead to the form, and unknown operations are refused. Save needs an armed, unconsumed create permit; the page's Delete needs a delete permit and must be outside a dialog; the dialog's Delete needs an armed delete permit and the recoverable dialog text; nothing else may be clicked inside a Delete Meeting dialog. Fills are limited to the allowlisted fields (no invitee field) and verify the typed value. Waiting Room is the only checkbox that may be cleared.
- Review hardening: the create permit refuses a personal meeting id, another `scheduleFor`, any untouched field or sub-field that differs from the form defaults, unknown fields and non-empty `frontParam` values, while the observed untouched-`null` sections and empty-over-unset values pass and `null` over any other default (for example mute on entry) is refused; the delete permit refuses a non-empty `meetingMasterEventId`; a permitted write answered with a redirect is aborted; the meeting view must carry the requested number; duplicate and "gone" checks fail when the upcoming list is incomplete, and a meeting listed twice is counted once.
- Meeting request parsing: 24-hour and AM/PM starts, quarter hours, duration limits, IANA zones, a start in the past, and `start_utc` across the end of daylight saving time. Upcoming-list parsing keeps only schedule fields; duplicate detection matches topic and start.

## Integration tests (fakes, no network)

- Ranged download against an in-memory fake: from scratch, resume from a partial `.part` file, server that ignores `Range`, a 200 error page mid-file (partial file kept), unknown total size, 416 at the end, size mismatch, ETag md5 match and mismatch, retries after a dropped connection, zero-byte files.
- `capture_links` against fake page, locator and route objects: the page receives an empty link list, unknown response shapes are aborted, a button-count mismatch clicks nothing, the clip is chosen by position.
- `download --execute` with fake page driving: manifest written, completed files skipped next time, a refused link answered by fresh links once, second clip, unknown clip, files still processing, user keys in the manifest kept.
- `settings` with fake page driving: preview and already-off no-op do not uncheck or save; explicit removal permits one Save, disarms on failure, and checks fresh passcode/access/checkbox state. A changed share token is accepted; unchanged passcode or drift in other settings is not reported as success. Locked passcodes and wrong IDs fail before clicking.
- Conditional readback: supported invitee-bypass labels may disappear only when the passcode clears and the boolean server bypass flag remains unchanged. Losing unrelated controls, unknown child labels, missing flags or changed permissions fails verification. Supported label variants currently have synthetic coverage and await live confirmation.
- Save timing: the fake server returns a successful response before its panel update settles. Readback must wait for Save hidden, then More settings visible. A timeout at either gate stops before readback and leaves the write permit disarmed.

- `meetings schedule` with faked page helpers: a matching body creates once and reads back; a body with an invitee is blocked and reported as nothing created; a duplicate stops before the form opens; a form that did not take a value stops before Save. `meetings delete`: a topic mismatch and a recurring meeting stop before any click; the plan clicks nothing.
- CLI: schedule and delete plans never execute and leave permits unarmed; `--demo` turns on slow motion; failures after the one write report `outcome_unknown`; selector failures are structured; usage errors are one JSON object; `zoom-rec list`, `zoom-rec recordings list` and `zoom-cli recordings list` give identical output; `zoom-rec meetings ...` works; `zoom-cli` has no flat recording commands.
- Source scan: `.click(` and `.fill(` are allowed only in `guard.py`, and there only inside the guard functions that own them; no allowlisted fill targets the invitee field.

## Manual end-to-end

Run against a signed-in browser: `status`, `list` over a date range, `show` on one meeting, `download` plan, `download --execute`, then a second `download --execute` that skips every file. Record the result in `docs/working.md`. There is no automated end-to-end test, because it needs a real account.

For meetings, on your own account with an obviously disposable topic and no invitees: `meetings list`; `meetings schedule` plan; `--execute` (expect `readback_verified: true` and one entry in `allowed_requests`); one run with `--demo` to see the outlines; `meetings delete NUMBER --expect-topic` with a wrong topic (expect `topic_mismatch`), as a plan, then with `--execute` (expect `absent_from_upcoming: true`); finish with `meetings list` showing no test meeting.

## What counts as verified

The offline suite passes on Python 3.10 and 3.12 in CI, and the manual run produced files whose sizes match Zoom's, whose video duration matches within two seconds, and whose transcript parses.

Initial CLI verification exercised inspection, preview and `--disable-passcode --execute` on an already-disabled recording, returning `executed: false` and `readback_verified: true`.

A subsequent targeted live run used a private direct browser script to remove a passcode. Waiting for Save hidden and More settings visible avoided stale readback; the independent checkboxes stayed unchanged. A fresh cookie-free context opened the copied, password-free share URL and showed the expected title/date, one video element and no password input. This verifies the browser workflow and page access, not full media playback or an end-to-end live run of the updated CLI. The implementation agent added the matching CLI wait gates and tested them offline.
