# AGENTS.md

Notes and invariants for coding agents working in `https://github.com/grapeot/zoom-recording-skill`.

## Commands

Check for a `.venv` at the repository root first. Activate it if present; otherwise create it with uv. Install dependencies with `uv pip install`.

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

Tests are fully offline. They never contact Zoom and never start a browser. Downloads are exercised against an in-memory ranged-GET fake; page driving against fake page, locator and route objects.

## Repository invariants

1. **Default branch is `master`.** Every change goes through a feature branch and a PR into `master`.
2. **Read-only toward Zoom by default; passcode removal is the only supported CLI write.** Only `settings ID --disable-passcode --execute` may disable the requested recording's sharing passcode. `guard.PasscodePermit` accepts one `POST /nws/recording/1.0/save-share-setting` on the page's own HTTPS Zoom origin with exact fields `meetingId` matching `ID` and `enablePasscode: false`, armed solely around `Save`. No other settings, audience, expiry, permissions, invitations, deletion, renaming, moving or uploads. Keep the context-wide `guard.install_request_guard` installed for every attached command. Scheduling a meeting is not a `zoom-rec` command and must not weaken this guard; it lives in `skills/zoom-meeting/`.
3. **Every UI interaction goes through `guard.py`.** General clicks retain their deny-word checks. Sharing clicks require exact `Share`, `More settings`, or `Save` operations; `guard.disable_passcode` only unchecks one visible, enabled checkbox labelled `Passcode`. Do not add `.click(`, `.set_checked(`, `dispatch_event`, `keyboard.press` or `el.click()` outside the guard; `tests/test_source_scan.py` enforces this.
4. **Never automate sign-in, never touch credentials.** No code reads, prints, exports or injects cookies, storage state, authorization headers or CSRF tokens. The human signs in in the visible window. Requests the page makes keep their own headers; the tool only rewrites the form fields of the recording-list request and reads JSON responses.
5. **Signed download URLs are secrets; canonical sharing URLs are requested output.** Download URLs stay in memory for one download; never print, log, store in a manifest, or put them in an error message. Route errors through `redact.scrub`. `settings` returns the canonical sharing URL without embedded `pwd`; never substitute a signed download URL or read passcode input values.
6. **Only drive a declared browser.** Before attaching, `browser.verify_own_browser` checks that the process listening on the DevTools port was started with the declared profile directory. Never attach to an arbitrary Chrome.
7. **One JSON object per command.** Every `zoom-rec` subcommand, including on error, prints exactly one JSON object to stdout (`--help` excepted). Progress goes to stderr.
8. **`download` and `settings --disable-passcode` plan by default.** Without `--execute`, downloads do not click anything; settings inspects but never unchecks Passcode or arms a write permit.
9. **No personal data in the repository.** No real meeting IDs, UUIDs, topics, names, emails, host or account IDs, signed URLs, recordings, transcripts, chat logs or absolute machine paths. Fixtures are synthetic.
10. **Update `docs/working.md`** with a dated changelog entry when you change behavior.

## Layout

```text
zoom-recording-skill/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/design.md                    # goals, boundaries, architecture, safety rules
├── docs/test.md                      # what the offline suite covers and what counts as verified
├── docs/working.md                   # changelog and lessons learned
├── skills/zoom-recording/SKILL.md    # recording agent instructions
├── skills/zoom-meeting/SKILL.md      # separate browser scheduling skill; not a CLI write path
├── src/zoom_recording/               # implementation (module map in docs/design.md)
└── tests/                            # offline tests and synthetic fixtures
```
