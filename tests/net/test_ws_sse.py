"""mutio.net._protocol — 必要的 L2 契约测试。

_write_or_disconnect 的传输错误→WebSocketDisconnect 转换依赖 TCP 写缓冲出错，
loopback 上不可达，无法通过公开 API 触发。此测试锁定该内部契约。
"""

from __future__ import annotations

import asyncio

import pytest

from mutio.net._protocol import WSProtocol


# ---------------------------------------------------------------------------
# WSProtocol — 传输错误
# ---------------------------------------------------------------------------


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


@pytest.mark.l2("WebSocket 传输错误→WebSocketDisconnect 转换")
class TestWsProtocol:
    @pytest.mark.asyncio
    async def test_write_or_disconnect_translates_expected_transport_error(self):
        from mutio.net.server import WebSocketDisconnect

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
