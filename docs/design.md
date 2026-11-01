# Design

## Goal

Let a coding agent find and download the owner's own Zoom cloud recordings into a verified local archive, inspect sharing state and canonical share links, remove a recording's sharing passcode when explicitly requested, and list, schedule and delete the owner's meetings. The human signs in to Zoom in a visible Chrome window. One CLI, `zoom-cli`, groups the commands (`browser`, `status`, `recordings ...`, `meetings ...`); `zoom-rec` stays as an alias whose flat commands are the recording group.

## Non-goals

- Deleting, trashing, renaming, moving or uploading recordings; creating passcodes, changing audience, expiry, registration or viewer permissions, sending invitations, or any recording settings change other than disabling a recording's sharing passcode.
- For meetings: Zoom invitees (Zoom emails them), recurring meetings, editing an existing meeting, security settings other than turning Waiting Room off, account-global settings, and anything in Recently Deleted.
- Recordings the signed-in user cannot already see on the "Recordings & Transcripts" page.
- Unattended operation without a signed-in browser, and any form of automated sign-in.
- Bulk archiving of an organization's recordings.

## Why the browser and not the Zoom REST API

The official route is the Zoom REST API (`GET /users/me/recordings`, then each file's `download_url` with an access token). It needs an app on the Zoom App Marketplace first: a Server-to-Server OAuth app (only users with the admin permission to create such apps, and its credentials can read every user's recordings in the account) or a user-managed OAuth app with recording scopes, a redirect URL and token storage. For one person who wants their own recordings, that is a developer setup step with a long-lived credential at the end of it.

The web page the owner already uses lists recordings and hands out per-file download links to the signed-in session. Driving that page in a visible Chrome needs no app registration or exported credential. The cost is dependence on undocumented web endpoints, which can change. The surface is limited to list, detail, batch-download, share-data inspection, and one-shot `save-share-setting` for passcode removal; unexpected payloads or controls fail closed.

## How it works

1. `zoom-rec browser` starts Chrome with a dedicated profile and a DevTools port bound to 127.0.0.1, and opens the recordings page. The human signs in. Alternatively, the agent attaches to a Chrome the human started, declared with `--cdp` and `--profile-dir`.
2. Before every attach, the tool checks that the process listening on the port was started with the declared profile. It refuses any other browser.
3. `list` loads the recordings page. The page itself requests its list (`POST /nws/recording/1.0/host-list`); the tool intercepts that request and rewrites only its form fields (`from`, `to`, `p`, `search_value`), then reads the JSON response. The page's own headers, including its session headers, pass through untouched and unread.
4. `show <id>` opens the recording's detail page and reads the page's own detail response (`GET /nws/recording/1.0/detail`): clips, files, types, sizes, durations, processing state.
5. `download <id> --execute` opens the detail page, intercepts the batch-download response (`/nws/recording/1.0/download-meeting/...`) that the page requests when its "Download" button is pressed, and replaces the list the page receives with an empty one, so Chrome itself downloads nothing. The tool then presses that button (and the confirmation dialog's "Download", when shown) through the click guard. The response contains one signed link per file; the tool matches each to a file by name and fetches it in 16 MB ranges through the browser context's request API. Zoom only honors these links together with the browser session's cookies and a Zoom `Referer`. Playwright's request API sends the browser context's cookies along with the request inside its driver; the Python code never reads or handles them.
6. Each file is written to `<name>.part`, resumed from its current size on the next run, checked against the size Zoom reports, hashed (sha256; md5 compared with the server's ETag when the ETag is a plain md5), and renamed into place. Video and audio are checked with `ffprobe` when it is installed; transcripts and captions are checked for a WebVTT header and at least one cue.
7. `manifest.json` in the meeting folder records topic, start time, time zone, duration, and for every file its type, Zoom display name, local name, size, hashes and verification results. It never records signed links.
8. `settings <id>` verifies the detail and sharing response IDs, clicks `Share` to read `GET /nws/recording/1.0/share-data`, and builds the canonical share URL from `baseUrl` and `encryptMeetId` without embedded `pwd`. `More settings` exposes checkbox states without reading password inputs. `--disable-passcode` previews; adding `--execute` unchecks only `Passcode`, arms a one-shot permit around `Save`, and reloads to verify passcode removal and unchanged other controls/access flags. An already-disabled passcode is a no-op (`executed: false`, `readback_verified: true`). Fresh share-data responses may issue a different share token, so return the final URL rather than requiring token equality.

## Safety rules

- Read-only by default. The context-wide request guard aborts mutation-looking requests. For `settings`, all non-GET recording requests are also refused unless accepted by the one-shot `PasscodePermit`: exactly one POST to the page's HTTPS Zoom origin at `/nws/recording/1.0/save-share-setting`, with only the requested `meetingId` and `enablePasscode: false`. Extra/duplicate fields, other IDs, enabling passcodes, unknown bodies and other recording writes are rejected. `allowed_requests` audits the permitted operation without credentials.
- General actions go through `guard.safe_click` and retain deny-word checks; explicit sharing actions allow only `Share`, `More settings`, and `Save`. `guard.disable_passcode` verifies a single visible, enabled checkbox labelled `Passcode`, then clicks only its verified parent `SPAN.recording-share-panel-checkbox__wrap` (one checkbox child, exact text `Passcode`) and confirms the input clears. No force clicks. The write permit is armed only around Save and is disarmed even on failure.
- Readback compares controls by label, not count or order. A supported invitee-bypass child may disappear when Passcode is disabled only if its boolean server `allowInviteesBypassPasscode` flag remains unchanged. All independent controls and access fields must still match.
- A successful save response can precede the panel's asynchronous state refresh. After the response, disarm the permit, wait for the current dialog's Save button to hide and then for More settings to become visible before reloading for readback. A timeout stops with an uncertain outcome; never retry the write automatically.
- No sign-in automation. If the page is not signed in, commands stop with `needs_sign_in` and ask the human to sign in in the window.
- No credential handling. No code reads, prints or exports cookies, storage state, request headers or tokens.
- Signed download links are held in memory only and scrubbed from every error string. Canonical share URLs are requested output; no embedded passcode or signed download credentials are included.
- `download` and `settings --disable-passcode` plan unless `--execute` is given. Besides Zoom's own download counter, explicit passcode removal is the only supported Zoom-side change. Failed or uncertain saves require inspection before retrying; readback drift reports failure.
- The tool's own browser profile must be outside any git repository.

## Meetings

### Commands

- `meetings list [--to DATE]` loads `/meeting` and reads the page's own `POST /rest/meeting/list` response (upcoming meetings; the page asks for about three months, `--to` widens `dateDuration`, more pages rewrite `page`). Output: number, topic, Zoom's start text, duration, recurring flag. Join links, passcodes and the page's internal ids are dropped.
- `meetings schedule --topic --date --start --duration --tz [--description] [--execute] [--demo]` validates the request (quarter-hour start and duration, IANA zone, start in the future), reads the upcoming list and refuses an existing meeting with the same topic and start. Without `--execute` it also confirms that exactly one "Schedule a Meeting" control exists, and stops. With `--execute` it presses that control, waits for the form and the page's time-zone table (`GET /rest/meeting/timezone`, IANA id to display name), fills topic and description, picks the date cell, start time, AM/PM, duration hours and minutes and, when different, the time zone, reading each value back before moving on. It unchecks Waiting Room if the form has it on, records Passcode as the form shows it, compares the whole form with the request, and only then arms the create permit and presses Save. The page's redirect to `/meeting/{number}` gives the new meeting's number. Readback opens `/meeting/{number}/edit` fresh and compares topic, date, start, AM/PM, duration, time-zone name, Waiting Room off and Passcode unchanged (and the description when given), without saving, then checks that the upcoming list contains the meeting.
- `meetings delete NUMBER --expect-topic TOPIC [--execute] [--demo]` opens the meeting's information page and reads the page's own `POST /rest/meeting/view`. The view's `meetingId` must be the requested number, and number and topic must both match, and recurring meetings are refused, before any click. With `--execute` it presses the page's Delete, requires the dialog to be the recoverable "Delete Meeting" dialog with nothing pre-selected, arms the delete permit, presses the dialog's Delete, waits for the page's own return to the list, and reads every page of the upcoming list (widened to the meeting's date) to confirm the meeting is gone. Duplicate checks before a create also read every page; an incomplete list fails the check instead of passing it.

### Request interception is the main defence against page redesigns

Selectors describe what the page looks like today. A redesign can move a control, rename it, or put a different control under the same name; a careful selector can still be fooled. What the page sends when Save or Delete is pressed is much harder to change by accident, and it is what actually changes the account. So the guarantee is placed on the request:

- During a meeting command the whole browser context is guarded with `meetings=True`. Every non-GET request under the prefixes where account state lives (`/rest`, `/nws`, `/meeting(s)`, `/recording(s)`, `/profile`, `/account`, `/user(s)`, `/settings`, `/webinar`, `/billing` except its wording lookup, and a few more) is aborted except the page's observed reads (`/rest/meeting/list`, `/rest/meeting/view`, `/rest/coach_mark/v1/web_read`); write-sounding paths anywhere else (`setting`, `invite`, `mail`, `delete`, `recover`, `trash`, `save` and similar) are aborted as well, along with everything the recording guard already blocks. The page's own token, telemetry, notice and bot-challenge POSTs live outside those prefixes and pass; blocking them breaks the form, and the token request is something this code must not even name. The create endpoint `/rest/meeting/save` matches none of the recording guard's mutation words, which is why meetings need their own rule.
- The one permitted meeting write is sent by the guard itself through `route.fetch(max_redirects=0)` and handed back to the page; a 3xx answer is aborted and reported. Playwright calls a route handler only for a request's first URL, so a followed redirect would otherwise reach the network unguarded. (The recording passcode permit keeps its original pass-through.)
- Each write command registers exactly one permitted request, armed only for the moment its final button is pressed, consumed on first use, and disarmed in `finally`. `CreateMeetingPermit` checks the JSON body field by field (topic, agenda, date, start with Zoom's zero-padded hour accepted, AM/PM, duration, IANA zone, not recurring, no invitees, no alternative hosts, no registration, generated meeting id rather than the personal one, scheduled for the user the form defaulted to, Waiting Room off, Passcode as the form had it). Every other field and sub-field must equal the form's own defaults, which the page loads as `GET /rest/meeting/schedule` when the form opens and the tool keeps in memory, An empty value is accepted only where the default is also unset, and `null` over a value only for the nine sections the real form was observed to post that way when untouched (`breakoutRoom`, `encryption`, `focusMode`, `internalMeeting`, `meetingSummary`, `meetingSummaryTemplate`, `moderateUnmute`, `requestUnmuteAll`, `waitingRoom2`); an unknown field is drift. It refuses edits (a `meetingNumber` query or body field), duplicate keys and unparseable bodies. `DeleteMeetingPermit` requires the exact observed form fields with the requested number, `sendMail=false` and empty `user_id`, `meetingMasterEventId`, occurrence and mail body; the page's pre-dialog `POST /meeting/{number}/confirm` lookup (empty body) is let through as a read for that number only. Refusals record field names, never values, in `permit_refused`.
- If the page sends anything else when Save is pressed, the request is aborted, the command reports `guard_refused`, and nothing was created. This happened during development: the form posts `02:30` for 2:30 PM, which the first permit refused rather than guessed.

Selectors are still checked strictly, so a changed page stops early instead of clicking the wrong thing: every click names an operation (`guard.MEETING_CLICKS`), and the locator, scoped to the schedule form or the open dialog, must resolve to exactly one visible, enabled element with that role and exact name (aria-label, or text when there is none). Zero or several matches raise `SelectorError` with the operation, the expected name and the count. Save is clickable only while the create permit is armed; the dialog's Delete only while the delete permit is armed and the dialog text is the recoverable "Delete Meeting" text; the page's Delete only for a delete command and only outside a dialog. Typing is limited to Topic, description and the start-time and time-zone filters; there is no operation for the invitee field. Waiting Room is the only checkbox a meeting command may change.

### Never

Zoom invitees; account-global settings (opening or saving); Recently Deleted (restoring or purging); deleting, editing or re-sharing recordings from a meeting command; editing an existing meeting; recurring meetings. Sign-in and credentials are never touched.

### Speed and `--demo`

Commands wait for state, not time: an option to be visible before clicking it, a control's value to read back before the next step, the saved meeting's URL, the dialog to appear. Controls that turn from inputs into text spans are re-queried by role and aria-label each time. In the final live run (2026-10-31) a schedule took 14.8 s end to end at normal speed: upcoming list checked at 1.6 s, form open at 4.4 s, form filled at 9.7 s, saved at 10.7 s, edit-page readback and list check done at 14.4 s. A delete took 4.8 s.

`--demo` connects with Playwright `slow_mo`, outlines each element just before it is used or after it is read with a caption such as `checkbox · row "Waiting Room" · unchecked`, narrates the same line on stderr and pauses briefly. The outline is a fixed-position DOM element with `pointer-events: none`, drawn through `locator.evaluate`; it never receives clicks and is removed at the end. Without `--demo` nothing is drawn and nothing pauses.

### Output and privacy

The meeting number is printed because it is the handle for `meetings delete`. Join links, passcodes, emails, host/user ids and the page's internal event ids are never printed or written to disk. Plans print `start_utc` / `end_utc` (computed from the IANA zone, so daylight-saving transitions are handled) for an optional calendar invite. On 2026-11-01, the day US daylight time ended, the form's time-zone input still read "(GMT-7:00) Pacific Time (US and Canada)"; readback therefore compares time-zone names and the date and time on the edit page, not the displayed offset.

## Module map

| Module | Role |
|---|---|
| `cli.py` | `zoom-cli` / `zoom-rec` argument parsing, one JSON object per command |
| `browser.py` | launch, own-browser verification, CDP attach, work tab |
| `guard.py` | click/fill/checkbox guards, mutation and meeting request guards, one-shot passcode, create-meeting and delete-meeting permits |
| `zoomweb.py` | page driving: list interception, detail capture, batch-link capture |
| `sharing.py` | sharing inspection, canonical URL, passcode removal and readback |
| `meetings.py` | upcoming list, schedule form driving and readback, delete flow |
| `demo.py` | `--demo` narration and on-page outlines |
| `parse.py` | pure parsing of list and detail payloads into records |
| `fetch.py` | resumable ranged download through the browser context, hashing |
| `verify.py` | ffprobe and WebVTT checks |
| `manifest.py` | folder naming, file naming, manifest read/write |
| `redact.py` | scrubbing of URLs, query strings and token-like strings |
| `paths.py` | home directory, git-repository check |
