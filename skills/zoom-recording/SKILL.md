---
name: zoom-recording
description: Find and download your own Zoom cloud recordings (video, audio, transcript, chat, captions) through a visible Chrome window the human signed in to. Read-only toward Zoom; resumable, size- and hash-checked downloads with a manifest per meeting.
---

# Zoom recording

Use this skill when someone wants their Zoom cloud recordings found, inspected or downloaded: to re-publish a talk, cut a clip, build subtitles from Zoom's transcript, or archive a series.

## Done means

- The requested meeting was identified unambiguously (topic and start time), or the candidates were reported back instead of guessing.
- Every requested file sits in the meeting folder with state `downloaded` or `done`, its size equals Zoom's listing, and `manifest.json` records its sha256.
- Content checks passed: video has video and audio streams and a duration within 2 s of Zoom's (when `ffprobe` is installed); transcripts and captions are WebVTT with at least one cue.
- Nothing on the Zoom side changed except Zoom's own download counter.

## Boundaries

- Read-only toward Zoom. No deleting, trashing, renaming, moving, sharing, passcode or settings changes, uploads, or "Add to Zoom Clips". The tool's request guard and click guard enforce this; do not work around them with your own browser scripting.
- Never sign in for the human, never type into a Zoom or SSO sign-in page, never read or export cookies, storage or tokens.
- Signed download links are credentials. Do not print, log or save them; the tool never outputs them.
- Only drive the tool's own browser, or one the human started and declared with `--cdp` and `--profile-dir`.

## Setup

Install from the repository root: `uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'`. Google Chrome must be installed; `ffprobe` is optional but recommended.

`zoom-rec browser` opens a separate Chrome window. Use a fixed, reusable profile directory outside all Git repositories (default `~/.zoom-recording/browser-profile`, change with `--home` or `--profile-dir`), and pass the same `--profile-dir` and loopback `--cdp` flags (or `ZOOM_REC_PROFILE_DIR` and `ZOOM_REC_CDP`) to `browser`, `status`, `list`, `show`, and `download`:

```bash
zoom-rec browser --profile-dir "$HOME/.zoom-recording/browser-profile" --cdp http://127.0.0.1:9391
# Ask the human to sign in there and open Recordings & Transcripts.
zoom-rec status --profile-dir "$HOME/.zoom-recording/browser-profile" --cdp http://127.0.0.1:9391
```

`ZOOM_REC_PROFILE_DIR` overrides the default profile location. `zoom-rec browser` rejects any profile inside a Git repository (`error: "config"`), even when that path is gitignored. Replace an older workspace-local override with the external profile and have the human sign in there; never copy cookies or browser storage between profiles or bypass the own-browser check.

A persisted profile reuses browser state across runs, but does not guarantee that Zoom stays signed in indefinitely. Before listing or downloading, run `status` and confirm both `browser_verified: true` and `signed_in: true`. If it returns `needs_sign_in`, ask the human to re-authenticate in that same dedicated Chrome window and rerun `status`; do not assume a fixed session lifetime.

## Commands

Every command prints exactly one JSON object on stdout; progress goes to stderr. `ok: false` comes with `error` and `message` (or `action`).

| Command | What it does |
|---|---|
| `zoom-rec browser` | Launch the visible Chrome with the tool's profile. |
| `zoom-rec status` | Verify the browser belongs to the declared profile and that Zoom is signed in. |
| `zoom-rec list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--query TEXT] [--max-pages N]` | List recordings. Default range is the last 365 days; older recordings need `--from`. `--query` is Zoom's own topic search. |
| `zoom-rec show ID` | Clips and files of one recording: type, display name, size, duration, readiness, and the local name a download would use. |
| `zoom-rec download ID [--files TYPES] [--clip N] [--out DIR \| --root DIR]` | Print the download plan. Nothing is clicked. |
| `zoom-rec download ID ... --execute` | Download the pending files, verify them, update `manifest.json`. |

`ID` is the `id` field from `list`. File types for `--files`: `video` (or `mp4`), `audio` (`m4a`), `transcript` (Zoom's audio transcript, `.vtt`), `chat` (`.txt`), `cc` (closed captions, `.vtt`), any other Zoom type by its name, or `all` (default). The meeting folder defaults to `<root>/<YYYY-MM-DD>_<topic_slug>` with `--root` defaulting to `./zoom-recordings`.

## Working method

- Find the meeting with `list` over a range wide enough to contain it, filtered by `--query` when the topic is known. Match on topic and date together. If several recordings fit (a series reuses titles such as "Office Hour"), or none does, report the candidates with start time and duration and stop.
- Run `show` before downloading when you need to know which files exist; a meeting can have several clips ("Recording 1", "Recording 2") and several video layouts.
- Run `download` without `--execute` first, read the plan, then run it with `--execute`. Re-running is safe: finished files are skipped, partial files resume from their `.part`.
- Report the folder, each file's size and check result, and `missing_types` when a requested type does not exist for that meeting (for example no chat). `ok` is true only when every requested file is `downloaded` or `done`; files Zoom is still processing show as `not_ready`.
- Keep the meeting folder out of any git repository; the output carries a warning when it is inside one.

## Output folder

```
<meeting folder>/
  manifest.json                              # meeting topic, start, time zone, duration; per file: type, name,
                                             # local name, bytes, duration, sha256, md5, ETag match, check result
  shared_screen_with_speaker_view.mp4
  audio_only.m4a
  audio_transcript_english_original.vtt      # Zoom's transcript, "Speaker: text" cues
  chat_file.txt
  closed_caption.vtt
```

Keys you add to `manifest.json` yourself (for example where the recording will be published) are kept on later runs.

## Errors and what to do

| `error` | Meaning | Next step |
|---|---|---|
| `config` | Invalid configuration; the message identifies the cause. | For an in-repository profile, choose an external `--profile-dir` (as above) or `--home`, replace any stale `ZOOM_REC_PROFILE_DIR`, and have the human sign in there. For invalid `--cdp`, use a loopback address with a port. |
| `port_in_use` | Something already listens on the DevTools port. | Run `status` with the same profile if it is the intended window; otherwise ask the human to close the old dedicated window or choose an unused local port consistently across commands. Do not kill arbitrary browsers. |
| `no_browser` | Nothing listens on the DevTools port. | `zoom-rec browser`, or check `--cdp`. |
| `foreign_browser` | The browser on that port was not started with the declared profile. | Declare the right `--profile-dir`, or use the tool's own browser. Do not bypass. |
| `needs_sign_in` | Zoom shows a sign-in page or redirects to an identity provider. | Ask the human to sign in in that same dedicated profile window, then rerun `status`. |
| `page` | A Zoom web response did not load or did not parse; the message names the endpoint. | Retry once; if it persists the web app changed, report the endpoint name. |
| `guard_refused` | The click guard refused an element. | Report it; the page layout probably changed. Never click it another way. |
| `link_rejected` (per file) | Zoom refused or expired a download link. | Rerun; the tool fetches fresh links and resumes. |
| `size_mismatch`, `hash_mismatch`, `http_status` (per file) | The bytes do not match Zoom's listing or the server's checksum, or the server answered unexpectedly. | Rerun; partial files are kept and resumed, a file that fails the checksum is moved aside as `.bad`. |
| `interrupted` | The command was stopped. | Rerun the same command; it resumes. |
| `folder_in_use` | The output folder's manifest belongs to another meeting. | Choose another `--out`. |

`blocked_requests` in any output lists requests the guard aborted. It should be empty; if it is not, report it.

## Next steps that are out of scope here

If a request combines downloading with removing a sharing passcode or obtaining a Zoom share link, `zoom-rec` cannot satisfy the sharing portion: `show` lists clip files, not a share URL, and the click guard refuses Share/Copy controls and settings. Complete the requested read-only inspection or download, state that passcode removal and share-link retrieval were not performed, and hand off to the recording owner to update sharing settings and copy the share link in the Zoom web UI. Never substitute a signed download URL for a share link, work around the guards, or add write APIs to complete the request.

Zoom's `transcript.vtt` is sentence-level with speaker prefixes. Turning it into edited, translated or bilingual subtitles is a separate step: parse the cues, map them onto the edited timeline, translate, and validate the result. The `subtitles` module of [circle-client-skill](https://github.com/grapeot/circle-client-skill) (`parse_vtt(..., speakers=True)`, `remap_cues`, `build_bilingual_vtt`, `validate_vtt`) reads this file directly; reuse it rather than writing another parser.
