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
2. **Read-only toward Zoom.** The tool lists, inspects and downloads. It never deletes, trashes, renames, moves, shares, changes passcodes, sharing or settings, and never uploads. While attached, `guard.install_request_guard` (installed on the browser context) aborts any API or document request whose path looks like a mutation; keep it installed for every command that attaches.
3. **Every click goes through `guard.safe_click`.** It only clicks an element whose accessible label matches an allowlisted pattern and refuses anything whose label or text mentions delete, trash, share, link, copy, move, rename, edit, passcode, setting or upload. Do not add `.click(`, `dispatch_event`, `keyboard.press` or `el.click()` in `page.evaluate` anywhere else; `tests/test_source_scan.py` fails if you do.
4. **Never automate sign-in, never touch credentials.** No code reads, prints, exports or injects cookies, storage state, authorization headers or CSRF tokens. The human signs in in the visible window. Requests the page makes keep their own headers; the tool only rewrites the form fields of the recording-list request and reads JSON responses.
5. **Signed download URLs are secrets.** They stay in memory for the length of one download. Never print, log, store in a manifest, or put them in an error message; route every error string through `redact.scrub`.
6. **Only drive a declared browser.** Before attaching, `browser.verify_own_browser` checks that the process listening on the DevTools port was started with the declared profile directory. Never attach to an arbitrary Chrome.
7. **One JSON object per command.** Every `zoom-rec` subcommand, including on error, prints exactly one JSON object to stdout (`--help` excepted). Progress goes to stderr.
8. **`download` plans by default.** Without `--execute` it prints which files would be fetched and where, and does not click anything.
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
├── skills/zoom-recording/SKILL.md    # agent instructions
├── src/zoom_recording/               # implementation (module map in docs/design.md)
└── tests/                            # offline tests and synthetic fixtures
```
