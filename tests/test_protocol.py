"""mutio.net._protocol 测试。"""

from __future__ import annotations

import asyncio
import logging

import pytest

from mutio.net._protocol import WSProtocol, format_sse
from mutio.net.server import WebSocketDisconnect


class TestFormatSse:
    def test_data_only(self):
        result = format_sse("hello")
        assert result == b"data: hello\n\n"

    def test_with_event(self):
        result = format_sse("payload", event="message")
        assert result == b"event: message\ndata: payload\n\n"

    def test_with_id(self):
        result = format_sse("payload", id="42")
        assert result == b"id: 42\ndata: payload\n\n"

    def test_with_event_and_id(self):
        result = format_sse("payload", event="update", id="7")
        assert result == b"id: 7\nevent: update\ndata: payload\n\n"

    def test_multiline_data(self):
        result = format_sse("line1\nline2\nline3")
        assert result == b"data: line1\ndata: line2\ndata: line3\n\n"

    def test_empty_data(self):
        result = format_sse("")
        assert result == b"data: \n\n"

    def test_returns_bytes(self):
        result = format_sse("test")
        assert isinstance(result, bytes)

    def test_unicode_data(self):
        result = format_sse("你好")
        assert "你好".encode("utf-8") in result


class _DummyTransport(asyncio.Transport):
    def __init__(self, write_error: Exception | None = None) -> None:
        self.write_error = write_error
        self.closed = False
        self.read_paused = False

    def write(self, data: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error

    def close(self) -> None:
        self.closed = True

    def pause_reading(self) -> None:
        self.read_paused = True

    def resume_reading(self) -> None:
        self.read_paused = False


class TestWsProtocol:
    @pytest.mark.asyncio
    async def test_write_or_disconnect_translates_expected_transport_error(self):
        protocol = WSProtocol(
            app=lambda scope, receive, send: None,
            scope={"path": "/ws/test"},
            server_state={"connections": set()},
        )
        protocol.transport = _DummyTransport(
            write_error=ConnectionResetError(10054, "reset by peer"),
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            protocol._write_or_disconnect(b"payload")

        assert exc_info.value.code == 1006
        assert protocol.queue.get_nowait() == {"type": "websocket.disconnect", "code": 1006}

    @pytest.mark.asyncio
    async def test_run_asgi_treats_disconnect_as_debug_log(self, caplog: pytest.LogCaptureFixture):
        async def app(scope, receive, send):
            raise WebSocketDisconnect(1006)

        protocol = WSProtocol(
            app=app,
            scope={"path": "/ws/test"},
            server_state={"connections": set()},
        )
        protocol.transport = _DummyTransport()

        with caplog.at_level(logging.DEBUG, logger="mutio.net.protocol"):
            await protocol._run_asgi()

        assert "disconnected" in caplog.text
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)
        assert protocol.transport.closed is True
