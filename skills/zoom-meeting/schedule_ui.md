# Zoom web schedule UI reference

Observed on the Zoom web meeting pages on 2026-10-28 and 2026-10-31. `zoom-cli meetings` encodes these observations; this file is for understanding its errors and for finishing one meeting by hand when the CLI cannot. Re-read the live control before using any label below. If the label is absent or the state is not the one you expect, stop. Do not turn this file into a Zoom REST call.

Examples below are fake: `ada@example.com`, `https://zoom.example/j/00000000000?pwd=EXAMPLE`, meeting number `00000000000`. Do not write a real join link, meeting id, name, or email into a git repository.

## Upcoming list

The upcoming list URL ends with `/meeting#/upcoming`. Navigate to `/meeting` (no fragment) to reload it; a fragment-only change does not reload. The page fetches the list itself with `POST /rest/meeting/list` (form fields `listType=upcoming`, `page`, `pageSize=15`, `dateDuration=YYYY-MM-DD,YYYY-MM-DD` covering about three months from today). Wait for that response, or for the list or the empty copy; a snapshot taken too early can look empty. Empty copy observed: "The user does not have any upcoming meetings."

"Schedule a Meeting" is an anchor `<a href="/meeting/schedule?from=upcoming" role="button">`. Look it up as a button named "Schedule a Meeting"; a link-role lookup finds nothing.

## Create form

The form lives in a `form` element containing `#topic`; Save and Cancel sit outside it. Several controls are an input before interaction and a text-only span afterwards, and the element carrying the `aria-label` is replaced. Re-query by `[role=combobox][aria-label="..."]` each time and read `value` for inputs and `innerText` for spans.

- Topic: `#topic`, `aria-label="Topic"`.
- Description starts behind a button "Add Description". After that click, the field is a textarea `aria-label="Add Description"`. The create request carries it as `agenda`.
- Date: combobox `aria-label="Choose date"` (a span showing `MM/DD/YYYY`). Day cells are `role=button` with labels like "Sunday,November 1,2026 not selected" (no space after the commas; today is "Today, Saturday,October 31,2026 selected"). Past cells have `aria-disabled="true"`. The grid is `role=grid` labelled "October 2026"; leading and trailing days of neighbouring months are in the grid too. "Next month" is a button. The recurring end control is a different label, "End by date".
- Start time: combobox input `aria-label="Select start time"`. Type `9:00`, then click the option `[aria-label='9:00']` (`div[role=option]`); options are quarter hours `12:00` … `11:45`. The UI shows `2:30` but the create request posts `02:30`.
- AM/PM: combobox `aria-label="Select start time unit"`, a span; options `li[role=option]` "AM" / "PM".
- Duration hours: combobox `aria-label="select duration hours"`, a span; options `li[role=option]` "0", "1", ….
- Duration minutes: combobox input `aria-label="select duration minutes"`. Typing "30" and pressing Tab resets it to 0; only a clicked option sticks. Options are `div[role=option]` "0", "15", "30", "45".
- Options appear a moment after the dropdown opens; wait until the option is visible before clicking.
- After an option is clicked, the option list (`role=listbox`) stays visible for about 300 ms while it closes. Wait until no listbox is visible before opening the next dropdown; otherwise duration hours and minutes both show an option "0" at once.
- Time zone: combobox input `aria-label="Select time zone"`, but its accessible name is "Time Zone" (`aria-labelledby` wins), so a role-and-name lookup for "Select time zone" finds nothing. The value reads like "(GMT-7:00) Pacific Time (US and Canada)". The page loads `GET /rest/meeting/timezone`, a map from IANA ids to these display strings; type the name part to filter and click the option with the full display string.
- DST: the `(GMT…)` prefix is the offset today, not on the meeting date. On 2026-11-01, the day US daylight time ends, the input still showed "(GMT-7:00) Pacific Time (US and Canada)" while the meeting information page showed "Pacific Time (US and Canada)". Compare time zone names and the date/time on the information or edit page, not the offset.
- Invitees: placeholder "Enter user names or email addresses". Leave it empty. A Zoom invitee can send a second email.
- Security rows are unlabeled checkboxes inside `span.zoom-checkbox__wrap` whose text is "Passcode" or "Waiting Room". On the observed create form Passcode was checked and Waiting Room unchecked. That default is not the verification. Do not toggle Passcode. Do not click "Edit global Dial-in Countries/Regions".
- Save is a button named Save. It sends `POST /rest/meeting/save` with a JSON body of `{"value": …}` fields (`topic`, `agenda`, `startDate`, `startTime`, `startTime2`, `duration`, `timezone`, `invitee`, `waitingRoom`, `passcode`, …). After save, the URL was `/meeting/{numeric id}?meetingMasterEventId=…` and the title "Meeting Information".

## Information and edit pages

The information page loads `POST /rest/meeting/view` (form `number={id}`); its time line reads like "Nov 3, 2026 10:00 AM Pacific Time (US and Canada)". The join link is an anchor whose `href` contains `/j/` and `pwd=`. That href is the calendar join link. Do not log it into a repository.

The information page did not show a Waiting Room line. Open `/meeting/{id}/edit`: same form, same labels, filled with the meeting. The Edit control on the information page was a link with that href, not a button named Edit. Read Waiting Room by its row text. If it is already unchecked, do not toggle it and do not Save; leave the page. Saving the edit form sends `POST /rest/meeting/save?meetingNumber={id}`; the CLI's create permit refuses that shape.

If the edit page is not the schedule form you expect, stop. Do not hunt through account Settings.

## Delete

The information page has a button "Delete". Pressing it first sends a lookup `POST /meeting/{id}/confirm?occurrence=undefined` (empty body; the response holds the registrant count), then opens a dialog whose text contains "Delete Meeting" and "You can recover this meeting within 7 days from Recently Deleted.", with buttons "Delete" and "Cancel". The dialog's Delete sends `POST /meeting/delete` with form fields `user_id`, `id`, `meetingMasterEventId`, `occurrence`, `sendMail=false`, `mailBody`, then returns to the upcoming list. Never open Recently Deleted to purge it.

## Outlook readback

List the calendar window before sending. If an event for this meeting is already there, do not send another.

Send with wall-clock start and end plus the IANA timezone. Do not hand-convert to UTC. Do not request an online meeting; a Teams link is not a substitute for the Zoom link.

Read back the created event:

- `originalStartTimeZone` is the requested IANA zone.
- Absolute UTC matches that wall clock. For 09:30–10:30 in `America/Los_Angeles` on a Pacific Daylight Time date, that is 16:30Z–17:30Z; on a Pacific Standard Time date, 17:30Z–18:30Z. Do not treat a UTC field labeled as local time.
- Attendees are only the requested addresses, for example `ada@example.com`.
- `isOnlineMeeting` is false and `onlineMeetingUrl` is empty.
- Location or body contains the join link.

## Reference inspection

For reading a page by hand when the CLI has stopped. Connect to the declared loopback CDP endpoint. End the Playwright session to disconnect. Do not close Chrome. Do not call `cookies()` or read storage. Do not copy this snippet into `src/zoom_recording`.

```python
from playwright.sync_api import sync_playwright

CDP_ENDPOINT = "http://127.0.0.1:9xxx"  # the declared loopback endpoint, not a guessed port

with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT)
    page = browser.contexts[0].pages[0]
    topic = page.locator("#topic")
    if topic.count() != 1 or topic.get_attribute("aria-label") != "Topic":
        raise RuntimeError("live Topic control did not match; stop")
    zone = page.locator('[role="combobox"][aria-label="Select time zone"]')
    print(zone.input_value())  # read only after the live label matches
# leaving this block disconnects; the Chrome window stays open
```
