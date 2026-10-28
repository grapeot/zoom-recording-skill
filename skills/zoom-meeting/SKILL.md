---
name: zoom-meeting
description: Schedule one Zoom meeting in the signed-in visible Chrome window, turn off that meeting's waiting room, and optionally send an Outlook calendar invite. Use when asked to schedule a Zoom meeting or invite someone to a Zoom meeting. Not a CLI and not a Zoom API call.
---

# Zoom meeting

Schedule one meeting in the visible Chrome window the human already signed in to, then send an Outlook invite only when the user named a recipient. This is not a `zoom-rec` command. Do not add a schedule subcommand, do not call the Zoom REST API, and do not weaken, remove, or bypass the recording guards in `src/zoom_recording`.

## Done means

- The upcoming list was read before create and did not already contain this meeting. If the save result is uncertain, read that list again before creating another one.
- Reopen shows the requested topic, description, date, start, duration, and timezone.
- A fresh open of this meeting's edit page shows Waiting Room unchecked. Passcode stays as it was unless the user asked to change it. No other security control was toggled. Account-global settings were not opened or saved.
- The Zoom invitee field was left empty. Outlook, not Zoom, sends the invite when one was requested.
- If the user named an invitee: the calendar window was listed first; the sent event's absolute UTC matches the requested wall-clock time in the requested IANA timezone; `originalStartTimeZone` is that zone; attendees are only the requested addresses; location or body contains the join link; `isOnlineMeeting` is false; `onlineMeetingUrl` is empty. No Teams link was added.

## Boundaries

Creating the meeting does not email anyone when the invitee field stays empty, and the meeting can be deleted afterward. Do that when the user asked to schedule. Do not stop to ask whether creating is allowed.

Sending the Outlook invite emails the named person. If the user already asked to invite that person, send it. Do not add a confirmation step, and do not add anyone else. If the user did not name an invitee, do not send one.

Never read cookies, storage, or tokens. Never type into a sign-in page. If `zoom-rec status` is not both `browser_verified` and `signed_in`, stop and ask the human to sign in in that same window.

Meeting clicks stay outside `src/zoom_recording`. The recording source scan forbids `click`, `fill`, and cookie reads there; do not move this workflow into that package to get around the scan.

## How to drive the page

Reuse the visible Chrome and loopback CDP already declared for `zoom-rec status`. Confirm status before the first write. Attach with Playwright `connect_over_cdp`, then disconnect by ending the Playwright session. Do not close the user's Chrome, and do not call `cookies()` or `storage_state()`.

Read [schedule_ui.md](./schedule_ui.md) for labels observed on 2026-10-28. Those notes are not a script. Before each click or fill, read the live label and state. If the expected label is missing, stop and report the page instead of guessing another control.

The Outlook invite uses the local calendar tool's wall-clock start and end plus an IANA timezone. Do not convert the wall clock to UTC yourself, and do not set an online-meeting provider. Cross-check the tool's `start_utc` / `end_utc`, then `calendar get` the created event. A dry run is for catching a bad payload; it is not a second approval once the user has asked to send.
