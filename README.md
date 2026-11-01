# zoom-recording-skill

Agent skills (`skills/zoom-recording/SKILL.md`, `skills/zoom-meeting/SKILL.md`) plus a Python command-line tool, `zoom-cli`, that finds and downloads your own Zoom cloud recordings, inspects sharing settings and canonical share links, can remove a recording's sharing passcode when explicitly asked, and lists, schedules and deletes your meetings. It works through a visible Chrome window that you sign in to yourself, so you do not need to register a Zoom app or keep an API credential around. `zoom-rec`, the original name, remains as a compatible alias.

## Who it is for

People who teach, present or host on Zoom and want their recordings somewhere else: a course platform, a video site, an editing timeline, a subtitle pipeline. A coding agent (Claude Code, Codex, Cursor, OpenCode and similar) does the searching and the downloading; you sign in once and decide what to download.

## What it will and will not do

- It lists recordings over any date range, shows what files a recording has, and downloads the ones you ask for into a folder per meeting, with a `manifest.json` describing them.
- Downloads resume after an interruption, are checked against the size Zoom reports and are hashed. Video and audio are checked with `ffprobe` when it is installed; transcripts are checked to be valid WebVTT.
- `download` shows a plan first and only fetches files when run with `--execute`.
- Recordings are read-only by default. The only supported recording write is `recordings settings ID --disable-passcode --execute`, which disables that recording's sharing passcode and reloads to verify the change. It never creates passcodes, changes audience/expiry/viewer permissions, deletes, trashes, renames, moves, uploads, or signs in for you.
- `meetings schedule` creates one meeting with no Zoom invitees, Waiting Room off and Passcode left as the form has it, then reopens the meeting's edit page to check every field. `meetings delete` deletes one meeting only when its number and `--expect-topic` both match; Zoom keeps it in Recently Deleted for 7 days. Both print a plan unless run with `--execute`, and `--demo` slows them down and outlines on the page what the tool is reading or about to click.
- Each write command lets exactly one request through, at the moment it presses Save or the dialog's Delete, and only if that request matches what was asked; every other write is aborted by a request guard on the whole browser context. A redesigned page can fool a selector, not the request check.
- It never adds Zoom invitees (they get email), opens or saves account-wide settings, empties Recently Deleted, or changes recordings from the meetings commands.
- `settings` returns sharing state and a canonical share URL without an embedded passcode; other sharing changes remain a human task.
- It never reads or prints your cookies or session, and never writes download links to disk.

Design and safety rules: `docs/design.md`.

## Install

Requires Python 3.10 or newer, [uv](https://github.com/astral-sh/uv) and Google Chrome. `ffprobe` (from ffmpeg) is optional.

```bash
git clone https://github.com/grapeot/zoom-recording-skill
cd zoom-recording-skill
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

The tool drives your installed Chrome; it does not download a browser.

### Installing the skill into your agent

Give your coding agent this repository's URL and ask it to install the skill. The agent should start from your workspace's `AGENTS.md` or `CLAUDE.md`, follow any routing file it points to, and add `skills/zoom-recording/SKILL.md` to the workspace's skill discovery: an entry in `rules/skills/INDEX.md` or `skills/INDEX.md` if one exists, otherwise a short pointer in `AGENTS.md` or `CLAUDE.md`.

## Quick start

```bash
zoom-cli browser                      # opens a separate Chrome window; sign in to Zoom there
zoom-cli status                       # browser verified? signed in?

zoom-cli recordings list --from 2024-01-01 --query "office hour"
zoom-cli recordings show RECORDING_ID
zoom-cli recordings settings RECORDING_ID                                # sharing state and share URL
zoom-cli recordings settings RECORDING_ID --disable-passcode --execute   # remove passcode, verify readback
zoom-cli recordings download RECORDING_ID --files video,transcript,chat  # plan only
zoom-cli recordings download RECORDING_ID --files video,transcript,chat --execute

zoom-cli meetings list
zoom-cli meetings schedule --topic "Office hour" --date 2026-11-03 --start 10:00 --duration 30 \
    --tz America/Los_Angeles                       # plan only
zoom-cli meetings schedule ... --execute           # create and read back
zoom-cli meetings delete MEETING_NUMBER --expect-topic "Office hour" --execute
```

`zoom-rec list`, `zoom-rec show`, `zoom-rec settings` and `zoom-rec download` keep working exactly as before. The skill files have the full command reference and the output format.

## License

MIT
