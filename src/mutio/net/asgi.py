"""ASGI 传输层 Declaration — ASGIServer。"""

from __future__ import annotations

import socket as _socket
from typing import Any

import mutobj


class ASGIServer(mutobj.Declaration):
    """轻量 ASGI 传输层 server。

    用法::

        server = ASGIServer(app)
        server.run(host="127.0.0.1", port=8000)

        # 预绑定 socket
        server = ASGIServer(app)
        server.run(sockets=[sock1, sock2])
    """

    def __init__(self, app: Any, *, root_path: str = "") -> None: ...

    def ports(self) -> list[int]:
        """返回所有 TCP server 实际绑定的端口。"""
        ...

    def run(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        sockets: list[_socket.socket] | None = None,
        on_startup: Any = None,
    ) -> None:
        """阻塞运行 server。内部自动完成 lifespan → TCP → main_loop → shutdown。

        on_startup: 可选的 async callback，在所有 TCP server 启动后调用。
        """
        ...

    async def start(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        sockets: list[_socket.socket] | None = None,
    ) -> None:
        """异步启动（在已有 event loop 中使用），内部自动完成 lifespan startup → TCP listen。"""
        ...

    async def shutdown(self) -> None:
        """异步停止。内部自动完成 TCP close → lifespan shutdown。"""
        ...

    def signal_exit(self) -> None:
        """外部信号触发优雅退出（中断 main_loop）。"""
        ...


from . import _asgi_impl as _asgi_impl  # noqa: E402, F401 — trigger @impl registration
