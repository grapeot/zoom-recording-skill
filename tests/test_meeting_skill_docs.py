"""The meeting skill is documentation, not a second write path through the recording CLI."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEETING = ROOT / "skills" / "zoom-meeting"
SKILL = MEETING / "SKILL.md"
UI = MEETING / "schedule_ui.md"
RECORDING = ROOT / "skills" / "zoom-recording" / "SKILL.md"
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net"}


def test_meeting_skill_stays_out_of_the_recording_cli():
    text = SKILL.read_text(encoding="utf-8") + "\n" + UI.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "zoom rest api" in lowered
    assert "not a `zoom-rec` command" in text or "not a zoom-rec command" in lowered
    assert "waiting room" in lowered
    assert "re-read the live control" in lowered or "before each click" in lowered
    assert "src/zoom_recording" in text


def test_meeting_docs_use_only_example_identities():
    text = SKILL.read_text(encoding="utf-8") + "\n" + UI.read_text(encoding="utf-8")
    domains = {match.group(1).lower() for match in re.finditer(r"[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})", text)}
    assert domains <= EXAMPLE_DOMAINS
    assert not re.search(r"https://[^)\s]*zoom\.us/j/", text)
    assert not re.search(r"/meeting/\d{6,}", text)
    assert "pwd=EXAMPLE" in text
    assert "00000000000" in text
    assert "9xxx" in text
    for banned in ("/Users" + "/", "/home" + "/", "C:" + "\\Users\\"):
        assert banned not in text


def test_meeting_skill_has_no_executable_helper():
    names = sorted(path.name for path in MEETING.iterdir())
    assert names == ["SKILL.md", "schedule_ui.md"]
    blocks = re.findall(r"```python\n(.*?)```", UI.read_text(encoding="utf-8") + SKILL.read_text(encoding="utf-8"), re.S)
    assert blocks, "reference snippet missing"
    for block in blocks:
        for banned in (".cookies(", ".storage_state(", "zoom_recording", "PasscodePermit"):
            assert banned not in block


def test_recording_skill_does_not_hand_scheduling_to_the_cli():
    text = RECORDING.read_text(encoding="utf-8")
    assert "zoom-meeting/SKILL.md" in text
    assert "weakening recording guards" in text
    assert "Zoom API schedule command" in text
