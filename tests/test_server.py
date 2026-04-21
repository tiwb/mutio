"""mutio.net.server — json_response / html_response / Declaration 默认值测试。"""

from mutio.net.server import (
    Request, Response, StreamingResponse, WebSocketConnection,
    json_response, html_response,
)


class TestJsonResponse:
    def test_basic(self):
        resp = json_response({"key": "value"})
        assert resp.status == 200
        assert resp.headers["content-type"] == "application/json; charset=utf-8"
        assert b'"key"' in resp.body
        assert b'"value"' in resp.body

    def test_custom_status(self):
        resp = json_response({"error": "not found"}, status=404)
        assert resp.status == 404

    def test_unicode(self):
        resp = json_response({"msg": "你好"})
        assert "你好".encode("utf-8") in resp.body

    def test_list(self):
        resp = json_response([1, 2, 3])
        assert resp.body == b"[1, 2, 3]"

    def test_null(self):
        resp = json_response(None)
        assert resp.body == b"null"


class TestHtmlResponse:
    def test_basic(self):
        resp = html_response("<h1>Hello</h1>")
        assert resp.status == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.body == b"<h1>Hello</h1>"

    def test_custom_status(self):
        resp = html_response("<p>gone</p>", status=410)
        assert resp.status == 410

    def test_unicode(self):
        resp = html_response("<p>你好</p>")
        assert "你好".encode("utf-8") in resp.body


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
        assert resp.status == 200
        assert resp.body == b""
        assert resp.headers == {}

    def test_custom(self):
        resp = Response(status=301, body=b"moved", headers={"location": "/new"})
        assert resp.status == 301
        assert resp.body == b"moved"
        assert resp.headers["location"] == "/new"


class TestStreamingResponseDefaults:
    def test_defaults(self):
        resp = StreamingResponse()
        assert resp.status == 200
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
