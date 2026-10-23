# Test strategy

All automated tests are offline: `python -m pytest -q`. They never contact Zoom, never start Chrome, and need no account.

## Unit tests

- Parsing: list and detail payloads (synthetic fixtures shaped like the web app's responses) become meeting and file records; multi-clip meetings, files still processing, unknown file types, and payloads with `status: false` reported with the endpoint name.
- List request rewrite: only `from`, `to`, `p` and `search_value` change; other form fields pass through; date conversion from ISO to the page's format.
- File selection and naming: `--files` type aliases, collision-free local names, multi-clip prefixes, folder naming from date and topic.
- Signed-link matching: each link matched to exactly one file by its name; unmatched links and files reported, never guessed.
- Redaction: URLs lose their query strings; token-shaped strings are replaced; error strings from failed requests carry no signed link.
- Guards: allowlisted labels pass; deny words in label or text refuse the click; mutation paths are aborted, reads continue.
- Own-browser check: command lines with and without the declared port and profile.
- Source scan: no click, key press, event dispatch, cookie or storage-state calls outside `guard.py`; no reads of request headers.
- CLI contract: every command, including usage errors and failures, prints exactly one JSON object.

## Integration tests (loopback HTTP server)

- Ranged download from scratch, resume from a partial `.part` file, server that ignores `Range`, size mismatch, ETag md5 match and mismatch, retries after a dropped connection.
- Manifest written after a download and re-used: complete files are skipped on the next run.

## Manual end-to-end

Run against a signed-in browser: `status`, `list` over a date range, `show` on one meeting, `download` plan, `download --execute`, then a second `download --execute` that skips every file. Record the result in `docs/working.md`. There is no automated end-to-end test, because it needs a real account.

## What counts as verified

The offline suite passes on Python 3.10 and 3.12 in CI, and the manual run produced files whose sizes match Zoom's, whose video duration matches within two seconds, and whose transcript parses.
