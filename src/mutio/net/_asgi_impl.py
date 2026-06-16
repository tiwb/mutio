"""asgi.py Declaration 实现 — ASGIServer @impl + Extension。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket as _socket
import sys
from typing import TYPE_CHECKING, Any

import mutobj

from mutio.net.asgi import ASGIServer

if TYPE_CHECKING:
    from mutio.net._protocol import HTTPProtocol

logger = logging.getLogger("mutio.net.server")


# ---------------------------------------------------------------------------
# ASGIServerExt — 承载运行时状态
# ---------------------------------------------------------------------------


class ASGIServerExt(mutobj.Extension[ASGIServer]):
    """ASGIServer 的运行时状态。"""
    app: Any = None
    root_path: str = ""
    servers: list[asyncio.AbstractServer] = mutobj.field(default_factory=list)
    server_state: dict[str, set[Any]] = mutobj.field(default_factory=dict)
    should_exit: bool = False
    force_exit: bool = False
    lifespan_task: asyncio.Task[None] | None = None
    lifespan_receive_queue: asyncio.Queue[dict[str, Any]] = mutobj.field(default_factory=asyncio.Queue)
    lifespan_startup_complete: asyncio.Event = mutobj.field(default_factory=asyncio.Event)
    lifespan_shutdown_complete: asyncio.Event = mutobj.field(default_factory=asyncio.Event)
    app_state: dict[str, Any] = mutobj.field(default_factory=dict)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """为 mutio server 安装最小 asyncio 异常兜底。"""
    if getattr(loop, "_mutio_exception_handler_installed", False):
        return

    previous_handler = loop.get_exception_handler()

    def _asyncio_exception_handler(
        current_loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        from mutio.net._protocol import is_expected_disconnect_error
        exception = context.get("exception")
        message = context.get("message", "Unhandled exception in asyncio callback")
        if exception and is_expected_disconnect_error(exception):
            logger.debug("%s: %s", message, exception)
            return
        if previous_handler is not None:
            previous_handler(current_loop, context)
            return
        current_loop.default_exception_handler(context)

    loop.set_exception_handler(_asyncio_exception_handler)
    setattr(loop, "_mutio_exception_handler_installed", True)


async def _scope_runner(app: Any, scope: dict[str, Any],
                        receive: Any, send: Any) -> None:
    """运行 ASGI app，捕获异常。"""
    try:
        await app(scope, receive, send)
    except Exception:
        logger.exception("ASGI lifespan raised exception")


# ---------------------------------------------------------------------------
# ASGIServer @impl
# ---------------------------------------------------------------------------


@mutobj.impl(ASGIServer.__init__)
def asgi_server_init(self: ASGIServer, app: Any, *, root_path: str = "") -> None:
    ext = ASGIServerExt.get_or_create(self)
    ext.app = app
    ext.root_path = root_path
    ext.server_state = {"connections": set()}


@mutobj.impl(ASGIServer.ports)
def asgi_server_ports(self: ASGIServer) -> list[int]:
    ext = ASGIServerExt.get_or_create(self)
    result: list[int] = []
    for server in ext.servers:
        sockets = getattr(server, 'sockets', ())
        if sockets:
            for sock in sockets:
                result.append(sock.getsockname()[1])
    return result


@mutobj.impl(ASGIServer.run)
def asgi_server_run(
    self: ASGIServer,
    *,
    host: str | None = None,
    port: int | None = None,
    sockets: list[_socket.socket] | None = None,
    on_startup: Any = None,
) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        _install_asyncio_exception_handler(loop)
        loop.run_until_complete(
            _serve(self, host=host, port=port, sockets=sockets, on_startup=on_startup)
        )
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


@mutobj.impl(ASGIServer.start)
async def asgi_server_start(
    self: ASGIServer,
    *,
    host: str | None = None,
    port: int | None = None,
    sockets: list[_socket.socket] | None = None,
) -> None:
    ext = ASGIServerExt.get_or_create(self)
    await _lifespan_startup(self, ext)
    await _startup(self, ext, host=host, port=port, sockets=sockets)


@mutobj.impl(ASGIServer.shutdown)
async def asgi_server_shutdown(self: ASGIServer) -> None:
    ext = ASGIServerExt.get_or_create(self)
    await _shutdown(ext)
    await _lifespan_shutdown(ext)


@mutobj.impl(ASGIServer.signal_exit)
def asgi_server_signal_exit(self: ASGIServer) -> None:
    ext = ASGIServerExt.get_or_create(self)
    ext.should_exit = True


# ---------------------------------------------------------------------------
# 内部编排逻辑
# ---------------------------------------------------------------------------


async def _serve(
    self: ASGIServer,
    *,
    host: str | None,
    port: int | None,
    sockets: list[_socket.socket] | None,
    on_startup: Any,
) -> None:
    """主服务循环：lifespan startup → TCP startup → main_loop → shutdown。"""
    ext = ASGIServerExt.get_or_create(self)
    await _lifespan_startup(self, ext)
    await _startup(self, ext, host=host, port=port, sockets=sockets)

    if on_startup:
        await on_startup()

    _install_signal_handlers(ext)

    await _main_loop(ext)

    await _shutdown(ext)
    await _lifespan_shutdown(ext)


async def _lifespan_startup(self: ASGIServer, ext: ASGIServerExt) -> None:
    """发送 lifespan.startup 事件给 ASGI app。startup 失败抛 RuntimeError。"""
    scope: dict[str, Any] = {
        "type": "lifespan",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "state": ext.app_state,
    }

    startup_failed = False
    startup_message = ""

    async def receive() -> dict[str, Any]:
        return await ext.lifespan_receive_queue.get()

    async def send(message: dict[str, Any]) -> None:
        nonlocal startup_failed, startup_message
        msg_type = message["type"]
        if msg_type == "lifespan.startup.complete":
            ext.lifespan_startup_complete.set()
        elif msg_type == "lifespan.startup.failed":
            startup_failed = True
            startup_message = message.get("message", "")
            ext.lifespan_startup_complete.set()
        elif msg_type == "lifespan.shutdown.complete":
            ext.lifespan_shutdown_complete.set()

    ext.lifespan_task = asyncio.get_running_loop().create_task(
        _scope_runner(ext.app, scope, receive, send)
    )

    await ext.lifespan_receive_queue.put({"type": "lifespan.startup"})
    await ext.lifespan_startup_complete.wait()

    if startup_failed:
        logger.error("Lifespan startup failed: %s", startup_message)
        raise RuntimeError(f"Lifespan startup failed: {startup_message}")


async def _lifespan_shutdown(ext: ASGIServerExt) -> None:
    """发送 lifespan.shutdown 事件给 ASGI app。失败只记日志，不抛异常。"""
    if ext.lifespan_task is None or ext.lifespan_task.done():
        return

    await ext.lifespan_receive_queue.put({"type": "lifespan.shutdown"})

    try:
        await asyncio.wait_for(ext.lifespan_shutdown_complete.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Lifespan shutdown timed out")

    if not ext.lifespan_task.done():
        ext.lifespan_task.cancel()
        try:
            await ext.lifespan_task
        except asyncio.CancelledError:
            pass


async def _startup(
    self: ASGIServer,
    ext: ASGIServerExt,
    *,
    host: str | None = None,
    port: int | None = None,
    sockets: list[_socket.socket] | None = None,
) -> None:
    """创建 TCP server 并开始监听。"""
    loop = asyncio.get_running_loop()
    _install_asyncio_exception_handler(loop)

    def _create_protocol() -> HTTPProtocol:
        from mutio.net._protocol import HTTPProtocol
        return HTTPProtocol(
            ext.app,
            server_state=ext.server_state,
            root_path=ext.root_path,
        )

    if sockets:
        for sock in sockets:
            server = await loop.create_server(
                _create_protocol,
                sock=sock,
            )
            ext.servers.append(server)
    elif host is not None and port is not None:
        server = await loop.create_server(
            _create_protocol,
            host=host,
            port=port,
            reuse_address=True,
        )
        ext.servers.append(server)
    else:
        raise ValueError("Must provide either (host, port) or sockets")

    logger.info("ASGI server started (%d listener(s))", len(ext.servers))


async def _main_loop(ext: ASGIServerExt) -> None:
    """主循环 — 等待退出信号。"""
    while not ext.should_exit:
        await asyncio.sleep(0.1)


async def _shutdown(ext: ASGIServerExt, timeout: float = 10.0) -> None:
    """Graceful shutdown — 关闭 TCP 连接。"""
    logger.info("Shutting down...")

    for server in ext.servers:
        server.close()

    connections = set(ext.server_state["connections"])
    if connections:
        logger.info("Shutting down %d connection(s)", len(connections))
        for conn in connections:
            conn.shutdown()

    for server in ext.servers:
        await server.wait_closed()

    remaining = ext.server_state["connections"]
    if remaining:
        try:
            await asyncio.wait_for(
                _wait_connections_closed(ext),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Shutdown timeout — closing %d remaining connection(s)",
                           len(remaining))
            for conn in set(remaining):
                conn.transport.close()

    logger.info("Server stopped")


async def _wait_connections_closed(ext: ASGIServerExt) -> None:
    while ext.server_state["connections"]:
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------


def _install_signal_handlers(ext: ASGIServerExt) -> None:
    """安装 SIGINT/SIGTERM 处理器。"""
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda signum, frame: _handle_signal(ext))
        signal.signal(signal.SIGBREAK, lambda signum, frame: _handle_signal(ext))  # type: ignore[attr-defined]
    else:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, lambda: _handle_signal(ext))
        loop.add_signal_handler(signal.SIGTERM, lambda: _handle_signal(ext))


def _handle_signal(ext: ASGIServerExt) -> None:
    if ext.should_exit:
        ext.force_exit = True
        print("\nForce shutting down...", flush=True)
        os._exit(0)
    ext.should_exit = True
    print("\nShutting down gracefully... Press Ctrl+C again to force exit", flush=True)
