import pytest

from conftest import signed_link
from zoom_recording import parse


def test_parse_list_keeps_only_needed_fields(list_body):
    out = parse.parse_list(list_body)
    assert out["total"] == 2 and out["page"] == 1 and out["page_size"] == 15
    first = out["recordings"][0]
    assert first == {
        "id": "TESTMEETINGAAAAAAAAAAA==",
        "topic": "Example Workshop: Week 1",
        "start": "2024-06-01T12:37:03-07:00",
        "timezone": "America/Los_Angeles",
        "duration_s": 1740,
        "file_count": 5,
        "total_bytes": 1000,
    }
    text = str(out)
    for private in ("alice@example.com", "HOST_TEST", "ACCOUNT_TEST", "Bob Example", "ENCRYPTED_TEST_ID"):
        assert private not in text


def test_parse_list_falls_back_to_minutes_and_utc_for_unknown_zone(list_body):
    second = parse.parse_list(list_body)["recordings"][1]
    assert second["duration_s"] == 3600
    assert second["start"].endswith("+00:00")


def test_parse_list_reports_web_app_errors():
    with pytest.raises(parse.PayloadError, match="host-list.*errorCode=201"):
        parse.parse_list({"status": False, "errorCode": 201, "errorMessage": "x", "result": None})
    with pytest.raises(parse.PayloadError, match="not a JSON object"):
        parse.parse_list([])


def test_parse_list_rejects_entries_without_id():
    body = {"status": True, "result": {"recordings": [{"topic": "x"}]}}
    with pytest.raises(parse.PayloadError, match="meetingId"):
        parse.parse_list(body)


def test_parse_detail_clips_files_and_readiness(detail_body):
    d = parse.parse_detail(detail_body)
    assert d["id"] == "TESTMEETINGAAAAAAAAAAA==" and d["duration_s"] == 2340
    assert [c["index"] for c in d["clips"]] == [1, 2]
    types = [f["type"] for f in d["clips"][0]["files"]]
    assert types == ["video", "audio", "transcript", "chat"]
    poll = d["clips"][1]["files"][1]
    assert poll["type"] == "poll" and poll["ready"] is False
    assert d["clips"][0]["files"][2]["ready"] is True


def test_transcript_not_ready_when_status_is_not_ok(detail_body):
    detail_body["result"]["transcriptStatusMap"] = {"file-transcript-1": "PROCESSING"}
    files = parse.parse_detail(detail_body)["clips"][0]["files"]
    assert files[2]["ready"] is False


def test_public_file_drops_internal_fields(detail_body):
    f = parse.parse_detail(detail_body)["clips"][0]["files"][0]
    assert "_server_name" in f
    assert "_server_name" not in parse.public_file(f)


def test_match_links_by_file_name_never_guesses(detail_body):
    files = parse.parse_detail(detail_body)["clips"][0]["files"]
    links = [signed_link("GMT_TEST_1_video.mp4"), signed_link("GMT_TEST_1_chat.txt"),
             signed_link("something_else.mp4")]
    mapping, unmatched = parse.match_links(links, files)
    assert set(mapping) == {"file-video-1", "file-chat-1"}
    assert unmatched == 1


def test_match_links_ambiguous_names_are_left_out(detail_body):
    files = parse.parse_detail(detail_body)["clips"][0]["files"]
    files[1]["_server_name"] = files[0]["_server_name"]
    mapping, unmatched = parse.match_links([signed_link("GMT_TEST_1_video.mp4")], files)
    assert mapping == {} and unmatched == 1


def test_parse_batch():
    body = {"status": True, "result": {"batchDownloadFileList": [signed_link("a.mp4")]}}
    assert parse.parse_batch(body) == [signed_link("a.mp4")]
    with pytest.raises(parse.PayloadError):
        parse.parse_batch({"status": True, "result": {}})


@pytest.mark.parametrize("spec,expected", [
    (None, None), ("all", None), ("mp4,transcript", {"video", "transcript"}),
    ("m4a, chat ,cc", {"audio", "chat", "cc"}), ("poll", {"poll"}),
])
def test_normalize_types(spec, expected):
    assert parse.normalize_types(spec) == expected


def test_meeting_duration_fallbacks(detail_body):
    meet = detail_body["result"]["meet"]
    del meet["meetingDurationInMs"]
    assert parse.parse_detail(detail_body)["duration_s"] == 29 * 60 + 10 * 60
    meet["meetingDuration"] = 41
    assert parse.parse_detail(detail_body)["duration_s"] == 41 * 60
