import json

from zoom_recording import manifest, parse, verify


def test_meeting_dirname_and_slug():
    assert manifest.meeting_dirname("2024-06-01T12:37:03-07:00", "Example Workshop: Week 1") == \
        "2024-06-01_example_workshop_week_1"
    assert manifest.meeting_dirname(None, "???") == "undated_recording"
    assert manifest.slug("中文 标题：测试") == "中文_标题_测试"


def test_local_names_unique_and_prefixed_for_multi_clip(detail_body):
    d = parse.parse_detail(detail_body)
    manifest.assign_local_names(d["clips"])
    names = [f["local_name"] for c in d["clips"] for f in c["files"]]
    assert names[0] == "r1_shared_screen_with_speaker_view.mp4"
    assert names[4] == "r2_shared_screen_with_speaker_view.mp4"
    assert len(set(names)) == len(names)


def test_local_names_dedupe_within_one_clip():
    clips = [{"index": 1, "files": [{"name": "Gallery view", "ext": ".mp4"}, {"name": "Gallery view", "ext": ".mp4"}]}]
    manifest.assign_local_names(clips)
    assert [f["local_name"] for f in clips[0]["files"]] == ["gallery_view.mp4", "gallery_view_2.mp4"]


def test_manifest_roundtrip_keeps_foreign_keys(tmp_path):
    manifest.save(tmp_path, {"meeting": {"id": "M"}, "files": {}, "notes": {"lesson": "x"}})
    data = manifest.load(tmp_path)
    data["files"] = {"f1": {"local_name": "a.txt", "bytes": 3, "sha256": "h"}}
    manifest.save(tmp_path, data)
    again = json.loads((tmp_path / "manifest.json").read_text())
    assert again["schema"] == 1 and again["notes"] == {"lesson": "x"}
    assert not manifest.completed(again["files"]["f1"], tmp_path)
    (tmp_path / "a.txt").write_text("abc")
    assert manifest.completed(again["files"]["f1"], tmp_path)
    (tmp_path / "a.txt").write_text("abcd")
    assert not manifest.completed(again["files"]["f1"], tmp_path)


def test_load_tolerates_missing_or_broken(tmp_path):
    assert manifest.load(tmp_path) == {}
    (tmp_path / "manifest.json").write_text("{not json")
    assert manifest.load(tmp_path) == {}


VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.500
Alice: Hello and welcome.

2
00:29:58.000 --> 00:30:01.250
Bob: Thanks.
"""


def test_vtt_check(tmp_path):
    p = tmp_path / "t.vtt"
    p.write_text(VTT)
    r = verify.vtt_check(p)
    assert r == {"ok": True, "webvtt_header": True, "cues": 2, "last_cue_end_s": 1801.25}
    p.write_text("not a transcript")
    assert verify.vtt_check(p)["ok"] is False


def test_check_file_without_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0" * 10)
    assert verify.check_file(p, "video", 1000)["ok"] is None


def test_check_file_duration_tolerance(tmp_path, monkeypatch):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0")
    probe = {"ok": True, "duration_s": 100.0, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
    monkeypatch.setattr(verify, "ffprobe", lambda path: dict(probe))
    assert verify.check_file(p, "video", 101_500)["ok"] is True
    assert verify.check_file(p, "video", 103_000)["ok"] is False
    probe["streams"] = [{"codec_type": "audio"}]
    assert verify.check_file(p, "video", 100_000)["ok"] is False
    assert verify.check_file(p.with_suffix(".m4a"), "audio", 100_000)["ok"] is True


def test_failed_check_is_not_done(tmp_path):
    (tmp_path / "a.vtt").write_text("abc")
    entry = {"local_name": "a.vtt", "bytes": 3, "sha256": "h", "check": {"ok": False}}
    assert not manifest.completed(entry, tmp_path)
    entry["check"] = {"ok": None}
    assert manifest.completed(entry, tmp_path)
