# Design

## Goal

Let a coding agent find and download the owner's own Zoom cloud recordings (video, audio, transcript, chat, closed captions) into a local folder, verified and described by a manifest, so they can be edited, subtitled or re-published elsewhere. The human keeps one job: signing in to Zoom in a visible Chrome window.

## Non-goals

- Deleting, trashing, renaming, moving or sharing recordings; changing passcodes, sharing options or settings; uploading anything.
- Recordings the signed-in user cannot already see on the "Recordings & Transcripts" page.
- Unattended operation without a signed-in browser, and any form of automated sign-in.
- Bulk archiving of an organization's recordings.

## Why the browser and not the Zoom REST API

The official route is the Zoom REST API (`GET /users/me/recordings`, then each file's `download_url` with an access token). It needs an app on the Zoom App Marketplace first: a Server-to-Server OAuth app (only users with the admin permission to create such apps, and its credentials can read every user's recordings in the account) or a user-managed OAuth app with recording scopes, a redirect URL and token storage. For one person who wants their own recordings, that is a developer setup step with a long-lived credential at the end of it.

The web page the owner already uses lists recordings and hands out per-file download links to the signed-in session. Driving that page in a visible Chrome needs no app registration and no stored credential: the session lives in the browser profile, and the tool never sees it. The cost is dependence on undocumented web endpoints, which can change. The tool keeps that surface small (one list request, one detail request, one batch-download request), fails loudly with the endpoint name when a payload no longer parses, and keeps the parsing in one module so a change is a one-file fix. An API backend can be added later behind the same commands.

## How it works

1. `zoom-rec browser` starts Chrome with a dedicated profile and a DevTools port bound to 127.0.0.1, and opens the recordings page. The human signs in. Alternatively, the agent attaches to a Chrome the human started, declared with `--cdp` and `--profile-dir`.
2. Before every attach, the tool checks that the process listening on the port was started with the declared profile. It refuses any other browser.
3. `list` loads the recordings page. The page itself requests its list (`POST /nws/recording/1.0/host-list`); the tool intercepts that request and rewrites only its form fields (`from`, `to`, `p`, `search_value`), then reads the JSON response. The page's own headers, including its session headers, pass through untouched and unread.
4. `show <id>` opens the recording's detail page and reads the page's own detail response (`GET /nws/recording/1.0/detail`): clips, files, types, sizes, durations, processing state.
5. `download <id> --execute` opens the detail page, intercepts the batch-download response (`/nws/recording/1.0/download-meeting/...`) that the page requests when its "Download" button is pressed, and replaces the list the page receives with an empty one, so Chrome itself downloads nothing. The tool then presses that button (and the confirmation dialog's "Download", when shown) through the click guard. The response contains one signed link per file; the tool matches each to a file by name and fetches it in 16 MB ranges through the browser context's request API. Zoom only honors these links together with the browser session's cookies and a Zoom `Referer`, so the bytes are requested by the browser's own network stack; the tool never sees the cookies.
6. Each file is written to `<name>.part`, resumed from its current size on the next run, checked against the size Zoom reports, hashed (sha256; md5 compared with the server's ETag when the ETag is a plain md5), and renamed into place. Video and audio are checked with `ffprobe` when it is installed; transcripts and captions are checked for a WebVTT header and at least one cue.
7. `manifest.json` in the meeting folder records topic, start time, time zone, duration, and for every file its type, Zoom display name, local name, size, hashes and verification results. It never records signed links.

## Safety rules

- Read-only toward Zoom. While the tool is attached, a request guard aborts requests whose path matches mutation patterns (delete, trash, recover, update, rename, move, share, passcode, setting, upload, archive). Aborted requests are reported in the command output.
- Every click goes through `guard.safe_click`, which needs an allowlisted accessible label and refuses labels or text that mention delete, trash, share, link, copy, move, rename, edit, passcode, setting or upload.
- No sign-in automation. If the page is not signed in, commands stop with `needs_sign_in` and ask the human to sign in in the window.
- No credential handling. No code reads, prints or exports cookies, storage state, request headers or tokens.
- Signed download links are held in memory only and scrubbed from every error string.
- `download` prints a plan unless `--execute` is given. Pressing Download in Zoom counts as a download in Zoom's own statistics; that is the only side effect on the Zoom side.
- The tool's own browser profile must be outside any git repository.

## Module map

| Module | Role |
|---|---|
| `cli.py` | argument parsing, one JSON object per command |
| `browser.py` | launch, own-browser verification, CDP attach, work tab |
| `guard.py` | click guard and mutation request guard |
| `zoomweb.py` | page driving: list interception, detail capture, batch-link capture |
| `parse.py` | pure parsing of list and detail payloads into records |
| `fetch.py` | resumable ranged download through the browser context, hashing |
| `verify.py` | ffprobe and WebVTT checks |
| `manifest.py` | folder naming, file naming, manifest read/write |
| `redact.py` | scrubbing of URLs, query strings and token-like strings |
| `paths.py` | home directory, git-repository check |
