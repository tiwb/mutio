"""mutio.net.asgi L1 测试 — 直接测试 ASGIServer 公开 API。"""

from __future__ import annotations

import pytest

from mutio.net.asgi import ASGIServer


# ---------------------------------------------------------------------------
# 轻量 ASGI app 工厂
# ---------------------------------------------------------------------------


async def _echo_app(scope: dict, receive, send) -> None:
    """简单 ASGI app：处理 lifespan，正常启动/停止。"""
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


async def _failing_startup_app(scope: dict, receive, send) -> None:
    """ASGI app：lifespan 启动失败。"""
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.failed", "message": "db down"})


# ---------------------------------------------------------------------------
# TestASGIServer
# ---------------------------------------------------------------------------


class TestASGIServer:
    @pytest.mark.asyncio
    async def test_start_and_shutdown(self):
        """异步 start → shutdown 往返。"""
        server = ASGIServer(_echo_app)
        await server.start(host="127.0.0.1", port=0)
        await server.shutdown()

    @pytest.mark.asyncio
    async def test_ports_after_start(self):
        """start 后 ports() 返回监听的端口列表。"""
        server = ASGIServer(_echo_app)
        await server.start(host="127.0.0.1", port=0)
        ports = server.ports()
        assert len(ports) == 1
        assert isinstance(ports[0], int)
        await server.shutdown()

    def test_ports_before_start(self):
        """start 前 ports() 返回空列表。"""
        server = ASGIServer(_echo_app)
        assert server.ports() == []

    def test_signal_exit_before_start(self):
        """signal_exit 设置退出标志（不抛异常）。"""
        server = ASGIServer(_echo_app)
        server.signal_exit()

    def test_init_with_root_path(self):
        """__init__ 接受 root_path 参数。"""
        server = ASGIServer(_echo_app, root_path="/api")
        assert server is not None

    @pytest.mark.asyncio
    async def test_startup_failure_raises_runtime_error(self):
        """lifespan.startup.failed → RuntimeError。"""
        server = ASGIServer(_failing_startup_app)
        with pytest.raises(RuntimeError, match="Lifespan startup failed"):
            await server.start(host="127.0.0.1", port=0)

    @pytest.mark.asyncio
    async def test_start_with_sockets(self):
        """start(sockets=[...]) 路径。"""
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = ASGIServer(_echo_app)
        await server.start(sockets=[sock])
        await server.shutdown()

    @pytest.mark.asyncio
    async def test_start_without_listen_args_raises_value_error(self):
        """不传 host/port/sockets → ValueError。"""
        server = ASGIServer(_echo_app)
        with pytest.raises(ValueError, match=r"host.*port.*sockets"):
            await server.start()
