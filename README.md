# zoom-recording-skill

An agent skill (`skills/zoom-recording/SKILL.md`) plus a Python command-line tool (`zoom-rec`) that finds and downloads your own Zoom cloud recordings, inspects sharing settings and canonical share links, and can remove a recording's sharing passcode when explicitly asked. It works through a visible Chrome window that you sign in to yourself, so you do not need to register a Zoom app or keep an API credential around.

## Who it is for

People who teach, present or host on Zoom and want their recordings somewhere else: a course platform, a video site, an editing timeline, a subtitle pipeline. A coding agent (Claude Code, Codex, Cursor, OpenCode and similar) does the searching and the downloading; you sign in once and decide what to download.

## What it will and will not do

- It lists recordings over any date range, shows what files a recording has, and downloads the ones you ask for into a folder per meeting, with a `manifest.json` describing them.
- Downloads resume after an interruption, are checked against the size Zoom reports and are hashed. Video and audio are checked with `ffprobe` when it is installed; transcripts are checked to be valid WebVTT.
- `download` shows a plan first and only fetches files when run with `--execute`.
- Read-only by default. The only supported write is `settings ID --disable-passcode --execute`, which disables that recording's sharing passcode and reloads to verify the change. It never creates passcodes, changes audience/expiry/viewer permissions, deletes, trashes, renames, moves, uploads, or signs in for you.
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
zoom-rec browser                      # opens a separate Chrome window; sign in to Zoom there
zoom-rec status                       # browser verified? signed in?
zoom-rec list --from 2024-01-01 --query "office hour"
zoom-rec show MEETING_ID
zoom-rec settings MEETING_ID                                      # sharing state and share URL
zoom-rec settings MEETING_ID --disable-passcode                    # preview only
zoom-rec settings MEETING_ID --disable-passcode --execute          # remove passcode, verify readback
zoom-rec download MEETING_ID --files video,transcript,chat          # plan only
zoom-rec download MEETING_ID --files video,transcript,chat --execute
```

The skill file has the full command reference and the output format.

## License

MIT
