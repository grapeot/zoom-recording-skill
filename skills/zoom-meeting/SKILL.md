---
name: zoom-meeting
description: Schedule or delete one Zoom meeting through the signed-in visible Chrome window with `zoom-cli meetings`, Waiting Room off and no Zoom invitees, then optionally send an Outlook calendar invite. Use when asked to schedule a Zoom meeting, invite someone to one, or remove a test meeting. Not a Zoom REST API call.
---

# Zoom meeting

Schedule one meeting with `zoom-cli meetings schedule`, delete one with `zoom-cli meetings delete`, and read the upcoming list with `zoom-cli meetings list`. The CLI drives the visible Chrome the human already signed in to. It never calls the Zoom REST API. Send an Outlook invite only when the user named a recipient.

`zoom-rec` is the older name of the same tool. Scheduling is not a `zoom-rec` command of the recording group: use `zoom-cli meetings ...` (or `zoom-rec meetings ...`). The recording guards in `src/zoom_recording` stay as they are; meeting writes have their own one-shot permits there. Do not work around either with your own browser script.

## Done means

- `meetings schedule ... --execute` returned `ok: true` and `readback_verified: true`: a fresh open of the meeting's edit page shows the requested topic, date, start, AM/PM, duration and time zone, Waiting Room unchecked, Passcode as it was on the form, and the meeting is in the upcoming list. `allowed_requests` shows exactly one create.
- The Zoom invitee field stayed empty (the CLI cannot type into it). Account-global settings were not opened or saved.
- For a delete: `ok: true` and `absent_from_upcoming: true`; the meeting sits in Recently Deleted for 7 days. Nothing was removed from Recently Deleted.
- If the user named an invitee: the calendar window was listed first; the sent event's absolute UTC matches the CLI's `meeting.start_utc` / `end_utc`; `originalStartTimeZone` is the requested IANA zone; attendees are only the requested addresses; location or body contains the join link; `isOnlineMeeting` is false; `onlineMeetingUrl` is empty. No Teams link was added.

## Boundaries

Creating the meeting does not email anyone because the invitee field stays empty, and the meeting can be deleted afterward. Do that when the user asked to schedule; do not stop to ask whether creating is allowed.

Sending the Outlook invite emails the named person. If the user already asked to invite that person, send it. Do not add a confirmation step, and do not add anyone else. If the user did not name an invitee, do not send one.

Never read cookies, storage, or tokens. Never type into a sign-in page. If `zoom-cli status` is not both `browser_verified` and `signed_in`, stop and ask the human to sign in in that same window.

Never, in the CLI or by hand: fill Zoom invitees, open or save account-global settings, empty or permanently delete from Recently Deleted, delete or change recordings, or change a recording's sharing audience.

## Commands

All take the same `--cdp` and `--profile-dir` (or `ZOOM_REC_CDP` / `ZOOM_REC_PROFILE_DIR`) as `status`. Each prints one JSON object on stdout.

```bash
zoom-cli status
zoom-cli meetings list [--to YYYY-MM-DD]
zoom-cli meetings schedule --topic "Office hour" --date 2026-11-03 --start 10:00 --duration 30 \
    --tz America/Los_Angeles [--description "One line"]            # plan only: nothing is clicked
zoom-cli meetings schedule ... --execute                           # create, then read back
zoom-cli meetings delete 00000000000 --expect-topic "Office hour"   # plan only
zoom-cli meetings delete 00000000000 --expect-topic "Office hour" --execute
```

- `--start` takes `HH:MM` (24-hour) or `H:MM AM/PM` in `--tz`, on a quarter hour. `--duration` is minutes, a multiple of 15. `--tz` is an IANA zone. The plan prints `start_utc` / `end_utc` for the calendar invite.
- The plan reads the upcoming list and refuses a meeting with the same topic and start (`duplicate_meeting`). If an execute reports `outcome_unknown: true`, run `meetings list` before trying again; never create a second one blindly.
- `delete` needs the meeting number and `--expect-topic` to both match the meeting page's own data; otherwise `topic_mismatch` (or `page` when the page is a different meeting) and nothing is clicked. Recurring meetings are refused.
- The create request may change only what you asked for: every other setting must stay at the form's defaults. Meetings that need other settings are a by-hand job.
- `meeting.id` in the output is the handle for `delete`. Keep it, and the join link, out of any git repository.
- `--demo` slows each action and outlines on the real page the element about to be used or just read, with what the tool read there (for example `checkbox · row "Waiting Room" · unchecked`), narrating on stderr. Without it the command runs at full speed with no overlay.

## Errors

| `error` | Meaning | Next step |
|---|---|---|
| `selector` | A control was missing, ambiguous, or not the expected one (`selector.op`, `expected`, `found`). Nothing was clicked at that step. | Zoom's page probably changed. Report it; finish by hand with [schedule_ui.md](./schedule_ui.md) only if the user wants the meeting now. |
| `guard_refused` | A click or the write request did not match its permit (`permit_refused` lists the field names that did not match, for example a setting that differs from the form's defaults). Nothing was created or deleted. | Report it. Do not retry another way. |
| `duplicate_meeting` | The upcoming list already has this topic at this start. | Report the existing meeting. |
| `form_mismatch` | The form did not hold a requested value before Save; nothing was saved. | Report `form`. |
| `readback_mismatch` | The write happened, but the fresh read did not confirm every field. | Report `readback`; fix by hand or delete and reschedule. |
| `topic_mismatch`, `recurring_meeting` | Delete refused before any click. | Check the number with `meetings list`. |
| `page`, `interrupted` | Something did not load, or the command stopped. `outcome_unknown` says whether the one write may have gone out. | `meetings list` before retrying. |

## When the CLI cannot do it

If the CLI stops with `selector` because Zoom changed its page, or the user needs something it does not support (a recurring meeting, a different security setting), the agent may finish one meeting by hand in the same visible Chrome window. Read [schedule_ui.md](./schedule_ui.md) first. Those notes are not a script: re-read the live control before each click, and stop if the label or state is not the expected one. Keep the same boundaries: no Zoom invitees, no global settings, Waiting Room off, Passcode unchanged, read back the edit page.

The Outlook invite uses the local calendar tool's wall-clock start and end plus an IANA timezone. Do not convert the wall clock to UTC yourself, and do not set an online-meeting provider. Cross-check the tool's `start_utc` / `end_utc` against the CLI's, then `calendar get` the created event.
