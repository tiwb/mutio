"""mutio.net — HTTPProtocol 集成测试（L1）。

全部通过 socket + Server.start() 走公开 API，不 import 私有模块。
PROXY protocol 测试见 test_proxy_protocol.py。
"""

from __future__ import annotations

import asyncio

import pytest

from mutio.net.server import Server, View, Request, Response, JSONResponse

from tests.net.conftest import (
    _HttpResponse,
    free_port,
    http_get,
    http_post,
    http_request,
    start_server,
)


# ---------------------------------------------------------------------------
# 测试用 View
# ---------------------------------------------------------------------------


class EchoView(View):
    path = "/echo"
    async def get(self, request: Request) -> Response:
        return Response(status_code=200, body=b"echo")

    async def post(self, request: Request) -> Response:
        body = await request.body()
        return Response(status_code=200, body=body)


class MirrorView(View):
    path = "/mirror"
    async def get(self, request: Request) -> Response:
        return JSONResponse({
            "method": request.method,
            "path": request.path,
            "query_params": dict(request.query_params),
        })


class PathParamView(View):
    path = "/user/{name}/posts/{id}"
    async def get(self, request: Request) -> Response:
        return JSONResponse({
            "name": request.path_params.get("name"),
            "id": request.path_params.get("id"),
        })


class StatusView(View):
    path = "/status/{code}"
    async def get(self, request: Request) -> Response:
        code = int(request.path_params["code"])
        return Response(status_code=code, body=f"status-{code}".encode())


class HeaderEchoView(View):
    path = "/headers"
    async def get(self, request: Request) -> Response:
        return JSONResponse(dict(request.headers))


_all_views = (EchoView, MirrorView, PathParamView, StatusView, HeaderEchoView)


# ---------------------------------------------------------------------------
# SSE（Server-Sent Events）
# ---------------------------------------------------------------------------


class SSEView(View):
    path = "/sse"

    async def get(self, request: Request) -> Response:
        async def stream():
            yield b"data: hello\n\n"
            yield b"event: update\ndata: world\n\n"
            yield b"id: 42\ndata: line1\ndata: line2\n\n"

        from mutio.net.server import StreamingResponse
        return StreamingResponse(
            body_iterator=stream().__aiter__(),
            media_type="text/event-stream",
        )


class TestSSE:
    @pytest.mark.asyncio
    async def test_sse_basic_format(self, free_port):
        """SSE 流式响应包含 data:/event:/id: 字段。"""
        sock, port = free_port
        server = Server(views=(SSEView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /sse HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), timeout=3)
        writer.close()

        text = data.decode()
        assert "data: hello" in text
        assert "event: update" in text
        assert "data: world" in text
        assert "id: 42" in text
        assert "data: line1" in text
        assert "data: line2" in text

        await server.stop()

    @pytest.mark.asyncio
    async def test_sse_content_type(self, free_port):
        """SSE 响应 content-type 为 text/event-stream。"""
        sock, port = free_port
        server = Server(views=(SSEView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /sse HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), timeout=3)
        writer.close()

        resp = _HttpResponse(data)
        assert "text/event-stream" in resp.headers.get("content-type", "")

        await server.stop()


# ---------------------------------------------------------------------------
# 基本 HTTP 方法
# ---------------------------------------------------------------------------


class TestBasicHttp:
    @pytest.mark.asyncio
    async def test_get_200(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        resp = await http_get(port, "/echo")
        assert resp.status == 200
        assert resp.body == b"echo"

        await server.stop()

    @pytest.mark.asyncio
    async def test_post_body(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        resp = await http_post(port, "/echo", body=b"hello-post")
        assert resp.status == 200
        assert resp.body == b"hello-post"

        await server.stop()

    @pytest.mark.asyncio
    async def test_404(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        resp = await http_get(port, "/nonexistent")
        assert resp.status == 404

        await server.stop()

    @pytest.mark.asyncio
    async def test_405_method_not_allowed(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        resp = await http_request(port, "PUT", "/echo")
        assert resp.status == 405

        await server.stop()

    @pytest.mark.asyncio
    async def test_multiple_requests_on_same_connection(self, free_port):
        """验证 HTTP/1.1 keep-alive：同一连接发多个请求。"""
        sock, port = free_port
        server = Server(views=_all_views)
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        writer.write(
            b"GET /echo HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            b"GET /mirror HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        )
        await writer.drain()

        raw = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=3.0)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            raw += chunk
            if raw.count(b"HTTP/1.1") >= 2:
                break

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        assert raw.count(b"HTTP/1.1") == 2
        assert b"echo" in raw
        assert b"mirror" in raw

        await server.stop()

    @pytest.mark.asyncio
    async def test_body_with_content_length(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        body = b"x" * 10000
        resp = await http_post(port, "/echo", body=body)
        assert resp.status == 200
        assert resp.body == body

        await server.stop()


# ---------------------------------------------------------------------------
# 请求路由
# ---------------------------------------------------------------------------


class TestRouting:
    @pytest.mark.asyncio
    async def test_query_params(self, free_port):
        sock, port = free_port
        server = Server(views=(MirrorView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /mirror?a=1&b=hello HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        assert b'"a"' in raw and b'"1"' in raw
        assert b'"b"' in raw and b'"hello"' in raw

        await server.stop()

    @pytest.mark.asyncio
    async def test_path_params(self, free_port):
        sock, port = free_port
        server = Server(views=(PathParamView,))
        await start_server(server, sock)

        resp = await http_get(port, "/user/alice/posts/42")
        assert resp.status == 200
        assert b'"name"' in resp.body
        assert b'"alice"' in resp.body
        assert b'"id"' in resp.body
        assert b'"42"' in resp.body

        await server.stop()

    @pytest.mark.asyncio
    async def test_custom_status_codes(self, free_port):
        sock, port = free_port
        server = Server(views=(StatusView,))
        await start_server(server, sock)

        for code in (201, 301, 400, 500):
            resp = await http_get(port, f"/status/{code}")
            assert resp.status == code, f"Expected {code}, got {resp.status}"
            assert f"status-{code}".encode() in resp.body

        await server.stop()


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------


class TestRequestHeaders:
    @pytest.mark.asyncio
    async def test_custom_header(self, free_port):
        sock, port = free_port
        server = Server(views=(HeaderEchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"GET /headers HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"X-Custom: hello-world\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        assert b"x-custom" in raw.lower()
        assert b"hello-world" in raw

        await server.stop()

    @pytest.mark.asyncio
    async def test_content_type_header(self, free_port):
        sock, port = free_port
        server = Server(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 4\r\n"
            b"\r\n"
            b"body"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert resp.body == b"body"

        await server.stop()
