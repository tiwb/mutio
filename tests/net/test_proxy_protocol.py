"""mutio.net — PROXY protocol v1 集成测试（L1）。

全部通过 socket + Server.start() + before_route 钩子走公开 API，
不 import 私有模块。
"""

from __future__ import annotations

import asyncio

import pytest

from mutio.net.server import Server, View, Request, Response

from tests.net.conftest import (
    _CaptureClientServer,
    _HttpResponse,
    free_port,
    start_server,
)


class EchoView(View):
    path = "/echo"
    async def get(self, request: Request) -> Response:
        return Response(status_code=200, body=b"echo")


class TestProxyProtocol:
    @pytest.mark.asyncio
    async def test_proxy_tcp4_sets_client(self, free_port):
        """PROXY TCP4 header 覆盖 scope["client"]，通过 before_route 观测。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"PROXY TCP4 10.219.26.186 192.168.1.1 56789 8741\r\n"
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert resp.body == b"echo"
        assert server.captured_client == ("10.219.26.186", 56789)

        await server.stop()

    @pytest.mark.asyncio
    async def test_no_proxy_header_uses_direct_client(self, free_port):
        """无 PROXY header 时 client 为直接连接地址。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert server.captured_client is not None
        assert server.captured_client[0] == "127.0.0.1"

        await server.stop()

    @pytest.mark.asyncio
    async def test_proxy_tcp6_sets_client(self, free_port):
        """PROXY TCP6 header 覆盖 client 地址。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"PROXY TCP6 ::ffff:10.0.0.1 ::1 12345 8741\r\n"
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert server.captured_client == ("::ffff:10.0.0.1", 12345)

        await server.stop()

    @pytest.mark.asyncio
    async def test_malformed_proxy_header_ignored(self, free_port):
        """格式错误（PROXY UNKNOWN）的 header 被忽略，client 不变。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"PROXY UNKNOWN\r\n"
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert server.captured_client is not None
        assert server.captured_client[0] == "127.0.0.1"

        await server.stop()

    @pytest.mark.asyncio
    async def test_invalid_port_falls_back_to_zero(self, free_port):
        """端口非数字时回退为 0。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"PROXY TCP4 10.0.0.1 0.0.0.0 abc 8741\r\n"
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert server.captured_client == ("10.0.0.1", 0)

        await server.stop()

    @pytest.mark.asyncio
    async def test_incomplete_proxy_line_falls_through(self, free_port):
        """不完整的 PROXY 行（无 \\r\\n）被当作 HTTP 请求处理，返回 400。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"PROXY TCP4 10.0.0.1 192.168.1.1 1234 8741"
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 400

        await server.stop()

    @pytest.mark.asyncio
    async def test_proxy_header_split_across_receives(self, free_port):
        """PROXY header 和 HTTP 数据分两次到达时正常工作。"""
        sock, port = free_port
        server = _CaptureClientServer(views=(EchoView,))
        await start_server(server, sock)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"PROXY TCP4 10.0.0.1 0.0.0.0 5555 8741\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(
            b"GET /echo HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.read(4096)
        writer.close()

        resp = _HttpResponse(raw)
        assert resp.status == 200
        assert resp.body == b"echo"
        assert server.captured_client == ("10.0.0.1", 5555)

        await server.stop()
