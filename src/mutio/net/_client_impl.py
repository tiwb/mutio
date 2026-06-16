"""client.py Declaration 实现 — HttpClient / WebSocketClient @impl。"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx
import wsproto
import wsproto.events as ws_events

import mutobj

from mutio.net.client import HttpClient, WebSocketClient
from mutio.net.server import WebSocketDisconnect

_default_user_agent = ""


# ---------------------------------------------------------------------------
# HttpClient @impl
# ---------------------------------------------------------------------------


@mutobj.impl(HttpClient.set_default_user_agent)
def http_client_set_default_user_agent(cls: type, ua: str) -> None:
    global _default_user_agent
    _default_user_agent = ua


@mutobj.impl(HttpClient.create)
def http_client_create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
    ua = user_agent if user_agent is not None else _default_user_agent
    headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})
    if ua:
        headers.setdefault("user-agent", ua)
    kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# WebSocketClient Extension
# ---------------------------------------------------------------------------


class _WSCLientExt(mutobj.Extension[WebSocketClient]):
    """WebSocketClient 运行时状态。"""
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    ws: wsproto.connection.Connection | None = None


# ---------------------------------------------------------------------------
# WebSocketClient @impl
# ---------------------------------------------------------------------------


@mutobj.impl(WebSocketClient.connect)
async def web_socket_client_connect(self: WebSocketClient) -> None:
    parsed = urlparse(self.url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query

    reader, writer = await asyncio.open_connection(host, port)

    # 手动 HTTP upgrade 请求
    request_lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    req = "\r\n".join(request_lines).encode() + b"\r\n\r\n"
    writer.write(req)
    await writer.drain()

    # 读取 101 响应
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = await reader.read(65536)
        if not chunk:
            raise WebSocketDisconnect(1006)
        raw += chunk

    header_block, remaining = raw.split(b"\r\n\r\n", 1)
    status_line = header_block.split(b"\r\n")[0].decode("ascii")
    if "101" not in status_line:
        writer.close()
        raise WebSocketDisconnect(1006)

    # 握手成功，创建 wsproto 连接
    ws = wsproto.connection.Connection(wsproto.connection.ConnectionType.CLIENT)
    ext = _WSCLientExt.get_or_create(self)
    ext.reader = reader
    ext.writer = writer
    ext.ws = ws
    # 服务器可能在 101 后立即发了帧
    if remaining:
        ws.receive_data(remaining)


def _ensure_ws_ext(self: WebSocketClient) -> _WSCLientExt:
    ext = _WSCLientExt.get(self)
    if ext is None or ext.ws is None:
        raise WebSocketDisconnect(1006)
    return ext


@mutobj.impl(WebSocketClient.send_text)
async def web_socket_client_send_text(self: WebSocketClient, data: str) -> None:
    ext = _ensure_ws_ext(self)
    assert ext.ws is not None
    msg = ext.ws.send(ws_events.TextMessage(data=data))
    ext.writer.write(msg)  # type: ignore[union-attr]
    await ext.writer.drain()  # type: ignore[union-attr]


@mutobj.impl(WebSocketClient.send_bytes)
async def web_socket_client_send_bytes(self: WebSocketClient, data: bytes) -> None:
    ext = _ensure_ws_ext(self)
    assert ext.ws is not None
    msg = ext.ws.send(ws_events.BytesMessage(data=data))
    ext.writer.write(msg)  # type: ignore[union-attr]
    await ext.writer.drain()  # type: ignore[union-attr]


@mutobj.impl(WebSocketClient.receive_text)
async def web_socket_client_receive_text(self: WebSocketClient) -> str:
    ext = _ensure_ws_ext(self)
    return await _ws_receive(ext, str)


@mutobj.impl(WebSocketClient.receive_bytes)
async def web_socket_client_receive_bytes(self: WebSocketClient) -> bytes:
    ext = _ensure_ws_ext(self)
    return await _ws_receive(ext, bytes)


async def _ws_receive(ext: _WSCLientExt, expected: type) -> Any:
    """轮询 wsproto 事件直到收到匹配类型的消息。"""
    assert ext.ws is not None
    while True:
        for event in ext.ws.events():
            if isinstance(event, ws_events.TextMessage):
                if expected is str:
                    return event.data
                raise TypeError(
                    f"Expected bytes message, got text: {event.data!r}"
                )
            if isinstance(event, ws_events.BytesMessage):
                if expected is bytes:
                    return event.data
                raise TypeError(
                    f"Expected text message, got bytes ({len(event.data)} bytes)"
                )
            if isinstance(event, ws_events.CloseConnection):
                raise WebSocketDisconnect(event.code)
            if isinstance(event, ws_events.Ping):
                # auto-pong
                pong = ext.ws.send(event.response())
                ext.writer.write(pong)  # type: ignore[union-attr]
                await ext.writer.drain()  # type: ignore[union-attr]
        data = await ext.reader.read(65536)  # type: ignore[union-attr]
        if not data:
            raise WebSocketDisconnect(1006)
        ext.ws.receive_data(data)


@mutobj.impl(WebSocketClient.close)
async def web_socket_client_close(self: WebSocketClient, code: int = 1000, reason: str = "") -> None:
    ext = _WSCLientExt.get(self)
    if ext is None or ext.ws is None:
        return
    try:
        msg = ext.ws.send(ws_events.CloseConnection(code=code, reason=reason))
        ext.writer.write(msg)  # type: ignore[union-attr]
        await ext.writer.drain()  # type: ignore[union-attr]
    except Exception:
        pass
    ext.writer.close()  # type: ignore[union-attr]
    try:
        await ext.writer.wait_closed()  # type: ignore[union-attr]
    except Exception:
        pass


@mutobj.impl(WebSocketClient.abort)
async def web_socket_client_abort(self: WebSocketClient) -> None:
    ext = _WSCLientExt.get(self)
    if ext is None or ext.writer is None:
        return
    ext.writer.close()
    try:
        await ext.writer.wait_closed()
    except Exception:
        pass
