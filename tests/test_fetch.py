import hashlib

import pytest

from zoom_recording import fetch

DATA = bytes(range(256)) * 400  # 102400 bytes
MD5 = hashlib.md5(DATA).hexdigest()


class Server:
    """A fake ranged-GET transport over an in-memory file."""

    def __init__(self, data=DATA, etag=MD5, honor_range=True, fail=None):
        self.data, self.etag, self.honor_range = data, etag, honor_range
        self.fail = list(fail or [])  # sequence of exceptions or status codes to return first
        self.calls = []

    def __call__(self, start, end):
        self.calls.append((start, end))
        if self.fail:
            f = self.fail.pop(0)
            if isinstance(f, Exception):
                raise f
            return f, {}, b""
        headers = {"etag": f'"{self.etag}"'} if self.etag else {}
        if not self.honor_range:
            return 200, headers, self.data
        chunk = self.data[start:end + 1]
        headers["content-range"] = f"bytes {start}-{start + len(chunk) - 1}/{len(self.data)}"
        return 206, headers, chunk


def test_download_in_chunks_and_hash(tmp_path):
    srv = Server()
    res = fetch.download(srv, tmp_path / "f.mp4", len(DATA), chunk=10000, backoff=0)
    out = (tmp_path / "f.mp4").read_bytes()
    assert out == DATA
    assert res["sha256"] == hashlib.sha256(DATA).hexdigest()
    assert res["etag_md5_match"] is True and res["resumed_from"] == 0
    assert len(srv.calls) == 11
    assert not (tmp_path / "f.mp4.part").exists()


def test_resume_from_partial_file(tmp_path):
    (tmp_path / "f.mp4.part").write_bytes(DATA[:30000])
    srv = Server()
    res = fetch.download(srv, tmp_path / "f.mp4", len(DATA), chunk=50000, backoff=0)
    assert res["resumed_from"] == 30000
    assert srv.calls[0][0] == 30000
    assert (tmp_path / "f.mp4").read_bytes() == DATA


def test_oversized_partial_is_discarded(tmp_path):
    (tmp_path / "f.mp4.part").write_bytes(DATA + b"extra")
    fetch.download(Server(), tmp_path / "f.mp4", len(DATA), chunk=60000, backoff=0)
    assert (tmp_path / "f.mp4").read_bytes() == DATA


def test_server_ignoring_range_from_zero(tmp_path):
    res = fetch.download(Server(honor_range=False), tmp_path / "f", len(DATA), backoff=0)
    assert res["bytes"] == len(DATA)


def test_server_ignoring_range_mid_file_restarts(tmp_path):
    (tmp_path / "f.part").write_bytes(DATA[:100])
    fetch.download(Server(honor_range=False), tmp_path / "f", len(DATA), backoff=0)
    assert (tmp_path / "f").read_bytes() == DATA


def test_size_mismatch_against_listing(tmp_path):
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(Server(), tmp_path / "f", len(DATA) + 1, chunk=10000, backoff=0)
    assert e.value.code == "size_mismatch"


def test_etag_mismatch_moves_file_aside(tmp_path):
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(Server(etag="0" * 32), tmp_path / "f", len(DATA), chunk=60000, backoff=0)
    assert e.value.code == "hash_mismatch"
    assert (tmp_path / "f.part.bad").exists() and not (tmp_path / "f").exists()


def test_non_md5_etag_is_not_compared(tmp_path):
    res = fetch.download(Server(etag="abc-3"), tmp_path / "f", len(DATA), chunk=60000, backoff=0)
    assert res["etag_md5_match"] is None


def test_retries_network_and_server_errors(tmp_path):
    srv = Server(fail=[ConnectionError("TimeoutError"), 503])
    fetch.download(srv, tmp_path / "f", len(DATA), chunk=60000, backoff=0)
    assert (tmp_path / "f").read_bytes() == DATA


def test_gives_up_after_retries(tmp_path):
    srv = Server(fail=[ConnectionError("x")] * 10)
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(srv, tmp_path / "f", len(DATA), retries=2, backoff=0)
    assert e.value.code == "network"


def test_rejected_link_is_reported_without_url(tmp_path):
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(Server(fail=[403]), tmp_path / "f", len(DATA), backoff=0)
    assert e.value.code == "link_rejected"
    assert "http" not in str(e.value).lower().replace("http 403", "")


def test_wrong_range_answer_is_an_error(tmp_path):
    def bad(start, end):
        return 206, {"content-range": f"bytes 5-10/{len(DATA)}"}, DATA[5:11]
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(bad, tmp_path / "f", len(DATA), backoff=0)
    assert e.value.code == "bad_range"


def test_unknown_size_learned_from_content_range(tmp_path):
    res = fetch.download(Server(), tmp_path / "f", None, chunk=40000, backoff=0)
    assert res["bytes"] == len(DATA)


class _FakeResponse:
    def __init__(self, status):
        self.status = status
        self.headers = {"Content-Range": "bytes 0-1/2", "Set-Cookie": "secret=1", "ETag": "x"}

    def body(self):
        return b"ab"

    def dispose(self):
        pass


class _FakeRequestContext:
    def __init__(self, status=206, raise_with=None):
        self.status, self.raise_with, self.calls = status, raise_with, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.raise_with:
            raise self.raise_with
        return _FakeResponse(self.status)


def test_browser_getter_sends_range_and_referer_and_drops_cookies():
    ctx = _FakeRequestContext()
    get = fetch.browser_getter(ctx, "https://files.example.com/x?Signature=s", "https://zoom.example/")
    status, headers, body = get(0, 1)
    assert status == 206 and body == b"ab"
    assert "set-cookie" not in headers and headers["content-range"] == "bytes 0-1/2"
    kw = ctx.calls[0][1]
    assert kw["headers"] == {"Referer": "https://zoom.example/", "Range": "bytes=0-1"}
    assert kw["fail_on_status_code"] is False


def test_browser_getter_errors_carry_only_the_type():
    ctx = _FakeRequestContext(raise_with=RuntimeError("failed https://files.example.com/x?Signature=SECRET"))
    get = fetch.browser_getter(ctx, "https://files.example.com/x?Signature=SECRET", "r")
    with pytest.raises(ConnectionError) as e:
        get(0, 1)
    assert "SECRET" not in str(e.value)


def test_200_with_wrong_body_keeps_partial(tmp_path):
    (tmp_path / "f.part").write_bytes(DATA[:500])

    def page_instead(start, end):
        return 200, {}, b"<html>expired</html>"
    with pytest.raises(fetch.FetchError) as e:
        fetch.download(page_instead, tmp_path / "f", len(DATA), backoff=0)
    assert e.value.code == "http_status"
    assert (tmp_path / "f.part").read_bytes() == DATA[:500]


def test_unknown_total_with_star_range(tmp_path):
    def star(start, end):
        chunk = DATA[start:end + 1]
        if not chunk:
            return 416, {"content-range": "bytes */*"}, b""
        return 206, {"content-range": f"bytes {start}-{start + len(chunk) - 1}/*"}, chunk
    res = fetch.download(star, tmp_path / "f", None, chunk=30000, backoff=0)
    assert res["bytes"] == len(DATA)


def test_416_at_end_with_known_size(tmp_path):
    def exact(start, end):
        if start >= len(DATA):
            return 416, {"content-range": f"bytes */{len(DATA)}"}, b""
        chunk = DATA[start:end + 1]
        return 206, {"content-range": f"bytes {start}-{start + len(chunk) - 1}/*"}, chunk
    res = fetch.download(exact, tmp_path / "f", None, chunk=len(DATA) // 4, backoff=0)
    assert res["bytes"] == len(DATA)


def test_zero_byte_file(tmp_path):
    res = fetch.download(lambda s, e: pytest.fail("no request needed"), tmp_path / "f", 0, backoff=0)
    assert res["bytes"] == 0 and (tmp_path / "f").exists()
