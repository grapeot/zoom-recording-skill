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

## Integration tests (fakes, no network)

- Ranged download against an in-memory fake: from scratch, resume from a partial `.part` file, server that ignores `Range`, a 200 error page mid-file (partial file kept), unknown total size, 416 at the end, size mismatch, ETag md5 match and mismatch, retries after a dropped connection, zero-byte files.
- `capture_links` against fake page, locator and route objects: the page receives an empty link list, unknown response shapes are aborted, a button-count mismatch clicks nothing, the clip is chosen by position.
- `download --execute` with fake page driving: manifest written, completed files skipped next time, a refused link answered by fresh links once, second clip, unknown clip, files still processing, user keys in the manifest kept.
- `settings` with fake page driving: preview and already-off no-op do not uncheck or save; explicit removal permits one Save, disarms on failure, and checks fresh passcode/access/checkbox state. A changed share token is accepted; unchanged passcode or drift in other settings is not reported as success. Locked passcodes and wrong IDs fail before clicking.
- Conditional readback: supported invitee-bypass labels may disappear only when the passcode clears and the boolean server bypass flag remains unchanged. Losing unrelated controls, unknown child labels, missing flags or changed permissions fails verification. Supported label variants currently have synthetic coverage and await live confirmation.
- Save timing: the fake server returns a successful response before its panel update settles. Readback must wait for Save hidden, then More settings visible. A timeout at either gate stops before readback and leaves the write permit disarmed.

## Manual end-to-end

Run against a signed-in browser: `status`, `list` over a date range, `show` on one meeting, `download` plan, `download --execute`, then a second `download --execute` that skips every file. Record the result in `docs/working.md`. There is no automated end-to-end test, because it needs a real account.

## What counts as verified

The offline suite passes on Python 3.10 and 3.12 in CI, and the manual run produced files whose sizes match Zoom's, whose video duration matches within two seconds, and whose transcript parses.

Initial CLI verification exercised inspection, preview and `--disable-passcode --execute` on an already-disabled recording, returning `executed: false` and `readback_verified: true`.

A subsequent targeted live run used a private direct browser script to remove a passcode. Waiting for Save hidden and More settings visible avoided stale readback; the independent checkboxes stayed unchanged. A fresh cookie-free context opened the copied, password-free share URL and showed the expected title/date, one video element and no password input. This verifies the browser workflow and page access, not full media playback or an end-to-end live run of the updated CLI. The implementation agent added the matching CLI wait gates and tested them offline.
