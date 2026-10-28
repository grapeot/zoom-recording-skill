---
name: zoom-recording
description: Find and download your own Zoom cloud recordings, inspect sharing settings and share links, or explicitly remove a recording's sharing passcode through a visible Chrome window the human signed in to. Read-only by default; resumable, verified downloads with a manifest per meeting.
---

# Zoom recording

Use this skill when someone wants their Zoom cloud recordings found, inspected or downloaded, needs a canonical share link, or asks to remove a recording's sharing passcode.

## Done means

- The requested meeting was identified unambiguously (topic and start time), or the candidates were reported back instead of guessing.
- For downloads: every requested file has state `downloaded` or `done`, its size equals Zoom's listing, and `manifest.json` records its sha256. Video has video/audio streams and duration within 2 s of Zoom's (when `ffprobe` is installed); transcripts/captions are WebVTT with at least one cue.
- For passcode removal: `ok: true` and `readback_verified: true` confirm `settings.passcode_enabled: false`; report `executed: false` honestly when already disabled. Return the final `settings.share_url`.
- Nothing on the Zoom side changed except the download counter or the explicitly requested passcode removal.

## Boundaries

- Read-only by default. The only supported write is `settings ID --disable-passcode --execute`. No deleting, trashing, renaming, moving, uploads, "Add to Zoom Clips", creating passcodes, audience/expiry/viewer permission changes or invitations. Never work around the guards with your own browser scripting. Scheduling a meeting is a separate browser skill (`skills/zoom-meeting/SKILL.md`) and must not be done by weakening recording guards or by adding a Zoom API schedule command.
- Never sign in for the human, never type into a Zoom or SSO sign-in page, never read or export cookies, storage or tokens.
- Signed download links are credentials. Do not print, log or save them. Canonical share URLs from `settings` are authorized output and contain no embedded `pwd`; never substitute a download URL for a share URL or read passcode input values.
- Only drive the tool's own browser, or one the human started and declared with `--cdp` and `--profile-dir`.

## Setup

Install from the repository root: `uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'`. Google Chrome must be installed; `ffprobe` is optional but recommended.

`zoom-rec browser` opens a separate Chrome window. Use a fixed, reusable profile directory outside all Git repositories (default `~/.zoom-recording/browser-profile`, change with `--home` or `--profile-dir`), and pass the same `--profile-dir` and loopback `--cdp` flags (or `ZOOM_REC_PROFILE_DIR` and `ZOOM_REC_CDP`) to `browser`, `status`, `list`, `show`, `settings`, and `download`:

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
| `zoom-rec settings ID` | Read-only sharing inspection: passcode state, canonical share URL, access flags and checkbox states. |
| `zoom-rec settings ID --disable-passcode` | Preview passcode removal without unchecking Passcode or saving. |
| `zoom-rec settings ID --disable-passcode --execute` | Disable only the sharing passcode, save through a one-shot request permit, and reload to verify. Already disabled is a no-op. |
| `zoom-rec download ID [--files TYPES] [--clip N] [--out DIR \| --root DIR]` | Print the download plan. Nothing is clicked. |
| `zoom-rec download ID ... --execute` | Download the pending files, verify them, update `manifest.json`. |

`ID` is the `id` field from `list`. File types for `--files`: `video` (or `mp4`), `audio` (`m4a`), `transcript` (Zoom's audio transcript, `.vtt`), `chat` (`.txt`), `cc` (closed captions, `.vtt`), any other Zoom type by its name, or `all` (default). The meeting folder defaults to `<root>/<YYYY-MM-DD>_<topic_slug>` with `--root` defaulting to `./zoom-recordings`.

## Working method

- Find the meeting with `list` over a range wide enough to contain it, filtered by `--query` when the topic is known. Match on topic and date together. If several recordings fit (a series reuses titles such as "Office Hour"), or none does, report the candidates with start time and duration and stop.
- Run `show` before downloading when you need to know which files exist; a meeting can have several clips ("Recording 1", "Recording 2") and several video layouts.
- For share links, run `settings ID`. For requested passcode removal, preview with `settings ID --disable-passcode`, then add `--execute` and confirm `ok` and `readback_verified`. Report the final `settings.share_url` (a fresh inspection may return a new share token). Removing a passcode does not change the link's audience; check `public_link_access` rather than assuming anonymous access.
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
| `page` | A response/control did not load or parse, or a settings save failed/had an uncertain outcome. | For a save, inspect with `settings ID` before retrying. For reads, retry once and report persistent failures. |
| `guard_refused` | The click guard refused an element. | Report it; the page layout probably changed. Never click it another way. |
| `link_rejected` (per file) | Zoom refused or expired a download link. | Rerun; the tool fetches fresh links and resumes. |
| `size_mismatch`, `hash_mismatch`, `http_status` (per file) | The bytes do not match Zoom's listing or the server's checksum, or the server answered unexpectedly. | Rerun; partial files are kept and resumed, a file that fails the checksum is moved aside as `.bad`. |
| `interrupted` | The command was stopped. | Downloads resume on rerun. For a settings write, inspect with `settings ID` before retrying. |
| `folder_in_use` | The output folder's manifest belongs to another meeting. | Choose another `--out`. |
| `readback_mismatch` | A passcode save was sent, but fresh state did not confirm only the intended change. | Report `before` and `settings`; stop rather than retrying blindly. |

`blocked_requests` in any output lists requests the guard aborted. It should be empty; if it is not, report it.

`settings.allowed_requests` audits the one permitted `POST /nws/recording/1.0/save-share-setting` with `enablePasscode: false` for the requested recording. Extra fields, another ID, enabling a passcode, and all other recording writes are refused. Locked or unexpected Passcode controls are not bypassed.

## Next steps that are out of scope here

All sharing changes other than passcode removal remain a human handoff: creating/changing passcodes, changing who can access the link, expiry, viewer permissions, or invitations. Complete supported inspection, passcode removal or downloads, and state which other changes were not performed.

Zoom's `transcript.vtt` is sentence-level with speaker prefixes. Turning it into edited, translated or bilingual subtitles is a separate step: parse the cues, map them onto the edited timeline, translate, and validate the result. The `subtitles` module of [circle-client-skill](https://github.com/grapeot/circle-client-skill) (`parse_vtt(..., speakers=True)`, `remap_cues`, `build_bilingual_vtt`, `validate_vtt`) reads this file directly; reuse it rather than writing another parser.
