# Working notes

## Changelog

### 2026-10-23

- Scaffold: README, AGENTS.md, design and test notes, skill stub, CI, MIT license.
- v0.1 CLI: `browser`, `status`, `list`, `show`, `download` (plan by default, `--execute` to fetch); own-browser check, click guard, mutation request guard, ranged resumable downloads through the browser context, sha256 and ETag md5 checks, ffprobe and WebVTT checks, per-meeting manifest that keeps keys it does not own.
- Offline suite: parsing, list-form rewrite, guards, own-browser check, ranged download (resume, ignored range, retries, size and hash mismatch), manifest, checks, CLI JSON contract, source scan.
- Clip Download button is found by its visible text and position, not a fixed label: some single-clip meetings label it "Download Recording" without a number.
- Meeting duration falls back to minutes, then to clip lengths, for older meetings without a millisecond duration.
- Independent review fixes: guard on the whole browser context; unknown batch-response shapes aborted instead of passed to the page; a 200 answer mid-file keeps the partial file; unknown sizes and 416 at end of file; failed content checks are not treated as done; `ok` false when requested files are still processing; unknown `--clip` is a usage error; `--version` and Ctrl-C print JSON; manifest merges keep user keys under `meeting` and file entries; warning when the meeting folder is inside a git repository; list de-duplicates and stops when a page adds nothing; identity-provider redirects count as signed out; clip-button count polled before refusing.
- Manual end-to-end against a real account: listed 46 recordings back to 2024 across 4 pages, downloaded 5 meetings (5 files each: video, audio, transcript, chat, captions), all sizes equal to Zoom's listing, all md5 equal to the server ETag, video durations within 0.1 s of Zoom's. One download was killed at 16 MB and resumed from there on the next run; a third run skipped every file.

## Lessons Learned

- The recordings page ignores date-range query parameters in its URL and defaults to the last 365 days. The range lives only in the form body of `POST /nws/recording/1.0/host-list` (`from`/`to` as MM/DD/YYYY, `p`, `search_value`); rewriting those fields on the page's own request works for any range, including years back.
- The page's API calls carry an authorization header taken from page globals. Do not replicate the calls from outside the page; let the page make them and only rewrite form fields or read responses.
- Signed download links from the batch-download response return 403 unless the request carries both a `Referer` from the Zoom web origin and the browser session's cookies. A plain HTTP client with the right `Referer` still gets 403. Fetch the bytes through the browser context's request API, in ranges.
- Do not let the guard block static assets. The app ships JavaScript and CSS for its delete and trash dialogs (file names contain "trash" and "delete"); aborting them stops the recordings list from loading at all. The guard only considers API and document requests.
- The detail page's per-file download icons are hidden until hover and are not reliably clickable; the per-clip Download button plus its confirmation dialog ("Download recording files") is stable. Its accessible label is "Download " + the clip name, which is "Recording 1", "Recording 2" for multi-clip meetings but plain "Recording" on some single-clip meetings (and owners can rename clips), so match the visible text "Download" and the clip's position instead of a fixed label. The dialog does not appear once the user ticked "Do not remind me again", so the confirmation step is optional.
- When the batch-download response is intercepted, return an empty file list to the page; otherwise Chrome starts its own downloads of every file into the user's Downloads folder.
