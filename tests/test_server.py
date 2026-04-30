"""mutio.net.server — Response 子类 / Declaration 默认值测试。"""

import pytest

from mutio.net.server import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Request,
    Response,
    StreamingResponse,
    WebSocketConnection,
    _is_expected_disconnect_error,
)


class TestJSONResponse:
    def test_basic(self):
        resp = JSONResponse({"key": "value"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json; charset=utf-8"
        assert b'"key"' in resp.body
        assert b'"value"' in resp.body

    def test_custom_status(self):
        resp = JSONResponse({"error": "not found"}, status_code=404)
        assert resp.status_code == 404

    def test_unicode(self):
        resp = JSONResponse({"msg": "你好"})
        assert "你好".encode("utf-8") in resp.body

    def test_list(self):
        resp = JSONResponse([1, 2, 3])
        assert resp.body == b"[1, 2, 3]"

    def test_null(self):
        resp = JSONResponse(None)
        assert resp.body == b"null"

    def test_isinstance(self):
        resp = JSONResponse({"a": 1})
        assert isinstance(resp, JSONResponse)
        assert isinstance(resp, Response)

    def test_render_override(self):
        """覆盖 render() 替换序列化逻辑。"""

        class UpperJSONResponse(JSONResponse):
            def render(self, content):
                import json
                return json.dumps(content).upper().encode("utf-8")

        resp = UpperJSONResponse({"key": "value"})
        assert resp.body == b'{"KEY": "VALUE"}'


class TestHTMLResponse:
    def test_basic(self):
        resp = HTMLResponse("<h1>Hello</h1>")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.body == b"<h1>Hello</h1>"

    def test_custom_status(self):
        resp = HTMLResponse("<p>gone</p>", status_code=410)
        assert resp.status_code == 410

    def test_unicode(self):
        resp = HTMLResponse("<p>你好</p>")
        assert "你好".encode("utf-8") in resp.body

    def test_bytes_input(self):
        resp = HTMLResponse(b"<h1>raw bytes</h1>")
        assert resp.body == b"<h1>raw bytes</h1>"

    def test_isinstance(self):
        resp = HTMLResponse("<p>x</p>")
        assert isinstance(resp, HTMLResponse)
        assert isinstance(resp, Response)


class TestPlainTextResponse:
    def test_basic(self):
        resp = PlainTextResponse("hello")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert resp.body == b"hello"

    def test_bytes_input(self):
        resp = PlainTextResponse(b"raw bytes")
        assert resp.body == b"raw bytes"

    def test_unicode(self):
        resp = PlainTextResponse("你好")
        assert resp.body == "你好".encode("utf-8")

    def test_custom_status(self):
        resp = PlainTextResponse("nope", status_code=404)
        assert resp.status_code == 404

    def test_isinstance(self):
        resp = PlainTextResponse("x")
        assert isinstance(resp, PlainTextResponse)
        assert isinstance(resp, Response)


class TestRedirectResponse:
    def test_default_307(self):
        resp = RedirectResponse("/new")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/new"
        assert resp.body == b""

    def test_permanent_308(self):
        resp = RedirectResponse("/new", status_code=308)
        assert resp.status_code == 308

    def test_legacy_301(self):
        resp = RedirectResponse("/new", status_code=301)
        assert resp.status_code == 301

    def test_custom_headers(self):
        resp = RedirectResponse("/new", headers={"x-marker": "v"})
        assert resp.headers["location"] == "/new"
        assert resp.headers["x-marker"] == "v"

    def test_location_overrides_headers(self):
        """url 参数始终覆盖 headers 里的 location,防止冲突。"""
        resp = RedirectResponse("/new", headers={"location": "/wrong"})
        assert resp.headers["location"] == "/new"

    def test_isinstance(self):
        resp = RedirectResponse("/x")
        assert isinstance(resp, RedirectResponse)
        assert isinstance(resp, Response)


class TestFileResponse:
    def test_basic(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_bytes(b"file body")
        resp = FileResponse(p)
        assert resp.status_code == 200
        assert resp.body == b"file body"
        assert resp.headers["content-length"] == "9"

    def test_media_type_inferred(self, tmp_path):
        p = tmp_path / "page.html"
        p.write_text("<h1>x</h1>")
        resp = FileResponse(p)
        assert resp.headers["content-type"].startswith("text/html")

    def test_media_type_unknown(self, tmp_path):
        p = tmp_path / "blob.xyz123"
        p.write_bytes(b"x")
        resp = FileResponse(p)
        assert resp.headers["content-type"] == "application/octet-stream"

    def test_explicit_media_type(self, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"x")
        resp = FileResponse(p, media_type="image/png")
        assert resp.headers["content-type"] == "image/png"

    def test_html_cache_no_cache(self, tmp_path):
        p = tmp_path / "page.html"
        p.write_text("<h1>x</h1>")
        resp = FileResponse(p)
        assert resp.headers["cache-control"] == "no-cache"

    def test_other_cache_max_age(self, tmp_path):
        p = tmp_path / "img.png"
        p.write_bytes(b"x")
        resp = FileResponse(p)
        assert "max-age=86400" in resp.headers["cache-control"]

    def test_explicit_cache_control(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_bytes(b"x")
        resp = FileResponse(p, cache_control="private, max-age=60")
        assert resp.headers["cache-control"] == "private, max-age=60"

    def test_filename_attachment(self, tmp_path):
        p = tmp_path / "report.pdf"
        p.write_bytes(b"PDF")
        resp = FileResponse(p, filename="annual.pdf")
        assert resp.headers["content-disposition"] == 'attachment; filename="annual.pdf"'

    def test_filename_inline(self, tmp_path):
        p = tmp_path / "view.pdf"
        p.write_bytes(b"PDF")
        resp = FileResponse(p, filename="view.pdf", content_disposition_type="inline")
        assert resp.headers["content-disposition"] == 'inline; filename="view.pdf"'

    def test_no_filename_no_disposition(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_bytes(b"x")
        resp = FileResponse(p)
        assert "content-disposition" not in resp.headers

    def test_path_str(self, tmp_path):
        """path 接受 str 或 Path。"""
        p = tmp_path / "x.txt"
        p.write_bytes(b"x")
        resp = FileResponse(str(p))
        assert resp.body == b"x"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FileResponse(tmp_path / "missing.txt")

    def test_isinstance(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_bytes(b"x")
        resp = FileResponse(p)
        assert isinstance(resp, FileResponse)
        assert isinstance(resp, Response)


class TestRequestDefaults:
    def test_defaults(self):
        req = Request()
        assert req.method == "GET"
        assert req.path == "/"
        assert req.raw_path == "/"
        assert req.headers == {}
        assert req.query_params == {}
        assert req.path_params == {}

    def test_factory_isolation(self):
        r1 = Request()
        r2 = Request()
        r1.headers["x"] = "1"
        assert "x" not in r2.headers


class TestResponseDefaults:
    def test_defaults(self):
        resp = Response()
        assert resp.status_code == 200
        assert resp.body == b""
        assert resp.headers == {}

    def test_custom(self):
        resp = Response(status_code=301, body=b"moved", headers={"location": "/new"})
        assert resp.status_code == 301
        assert resp.body == b"moved"
        assert resp.headers["location"] == "/new"


class TestStreamingResponseDefaults:
    def test_defaults(self):
        resp = StreamingResponse()
        assert resp.status_code == 200
        assert resp.headers == {}
        assert resp.body_iterator is None
        assert resp.media_type == "text/event-stream"


class TestWebSocketConnectionDefaults:
    def test_defaults(self):
        ws = WebSocketConnection()
        assert ws.path == "/"
        assert ws.query_params == {}
        assert ws.path_params == {}
        assert ws.headers == {}


class TestExpectedDisconnectError:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (ConnectionResetError(10054, "reset by peer"), True),
            (BrokenPipeError(32, "broken pipe"), True),
            (ConnectionAbortedError(10053, "software caused connection abort"), True),
            (OSError(10054, "socket reset"), True),
            (OSError(22, "invalid argument"), False),
            (RuntimeError("boom"), False),
        ],
    )
    def test_classifies_expected_transport_disconnects(self, exc, expected):
        assert _is_expected_disconnect_error(exc) is expected
