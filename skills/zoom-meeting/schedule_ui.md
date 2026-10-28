# Zoom web schedule UI reference

Observed on the Zoom web schedule UI on 2026-10-28. Re-read the live control before using any label below. If the label is absent or the state is not the one you expect, stop. Do not turn this file into a `zoom-rec` command or a Zoom REST call.

Examples below are fake: `ada@example.com`, `https://zoom.example/j/00000000000?pwd=EXAMPLE`. Do not write a real join link, meeting id, name, or email into a git repository.

## Before creating

The upcoming list URL ends with `/meeting#/upcoming`. Wait until the list or the empty copy is visible; a snapshot taken too early can look empty while the list is still loading. Empty copy observed: "The user does not have any upcoming meetings."

Schedule entry observed: an anchor `href` `/meeting/schedule?from=upcoming`, and a Schedule link to `https://zoom.us/meeting/schedule`. Prefer the visible Schedule control on the list page over a guessed URL.

## Create form

- Topic: `#topic`, `aria-label="Topic"`.
- Description starts behind the visible text "Add Description". After that click, the field is a textarea `aria-label="Add Description"`.
- Date is not a native date input. The visible control's `aria-label` is "Choose date". The popover day cells use labels like "Friday,October 30,2026 not selected". Match weekday, month, day, and year. Past cells can be `aria-disabled="true"`. A same day-number from the previous month is a different cell; do not click it. The recurring end control is a different label, "End by date". Use the visible "Choose date" control. If more than one visible control has that label, stop.
- Start time: input `aria-label="Select start time"`, a filter select. Filling `9:30` left one option `aria-label="9:30"`; click that option rather than leaving the raw text. AM/PM is separate: `aria-label="Select start time unit"`, options `AM` and `PM`.
- Duration: hour text `aria-label="select duration hours"`; minute input `aria-label="select duration minutes"`. Read both after setting the start time. Opening the time dropdown cleared the time field once; confirm the value after the option click.
- Time zone: input `aria-label="Select time zone"`. An account on Pacific Daylight Time showed "(GMT-7:00) Pacific Time (US and Canada)". Read the live value. Do not assume it.
- Invitees: placeholder "Enter user names or email addresses". Leave it empty. This skill never adds a Zoom invitee; Outlook sends the invite when one was requested. A Zoom invitee can send a second email.
- Security rows are unlabeled checkboxes whose nearest row text is "Passcode" and "Waiting Room". On the observed create form, Passcode was checked and Waiting Room was unchecked. That default is not the verification. Do not toggle Passcode. Do not click "Edit global Dial-in Countries/Regions".
- Save is a button named Save. After save, the URL was `/meeting/{numeric id}` and the title was "Meeting Information".

## Join link and waiting room

On the meeting information page, the join link is an anchor whose `href` contains `/j/` and `pwd=`. That href is the calendar join link. Do not log it into a repository.

The information page did not show a Waiting Room line. The edit page did. Open `/meeting/{id}/edit`. The Edit control on the information page was a link/span with that href, not a button role named Edit; the button-role lookup timed out.

On edit, read the Waiting Room checkbox by its row text. If it is checked, uncheck only that box and Save. If it is already unchecked, do not toggle it and do not Save; Cancel, leave the page, and open `/meeting/{id}/edit` again to read the checkbox. Passcode must still be checked unless the user asked otherwise.

If the edit page is not the schedule form you expect, stop. Do not hunt through account Settings.

## Outlook readback

List the calendar window before sending. If an event for this meeting is already there, do not send another.

Send with wall-clock start and end plus the IANA timezone. Do not hand-convert to UTC. Do not request an online meeting; a Teams link is not a substitute for the Zoom link.

Read back the created event:

- `originalStartTimeZone` is the requested IANA zone.
- Absolute UTC matches that wall clock. For 09:30–10:30 in `America/Los_Angeles` on a Pacific Daylight Time date, that is 16:30Z–17:30Z. Do not treat a UTC field labeled as local time.
- Attendees are only the requested addresses, for example `ada@example.com`.
- `isOnlineMeeting` is false and `onlineMeetingUrl` is empty.
- Location or body contains the join link.

## Reference inspection

Connect to the declared loopback CDP endpoint. End the Playwright session to disconnect. Do not close Chrome. Do not call `cookies()` or read storage. Do not copy this snippet into `src/zoom_recording`.

```python
from playwright.sync_api import sync_playwright

CDP_ENDPOINT = "http://127.0.0.1:9xxx"  # the declared loopback endpoint, not a guessed port

with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT)
    page = browser.contexts[0].pages[0]
    topic = page.locator("#topic")
    if topic.count() != 1 or topic.get_attribute("aria-label") != "Topic":
        raise RuntimeError("live Topic control did not match; stop")
    # read or fill only after the live label matches
# leaving this block disconnects; the Chrome window stays open
```
