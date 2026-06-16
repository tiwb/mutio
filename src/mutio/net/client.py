"""出站连接 Declaration — HttpClient / WebSocketClient。"""

from __future__ import annotations

from typing import Any

import httpx

import mutobj


class HttpClient(mutobj.Declaration):
    """HTTP 客户端工厂。

    提供统一的 httpx.AsyncClient 创建入口，集中管理默认 headers（User-Agent 等）。

    全局设置::

        HttpClient.set_default_user_agent("mutagent/0.2.0")

    创建时覆盖::

        client = HttpClient.create(user_agent="custom/1.0", timeout=30)
    """

    @classmethod
    def set_default_user_agent(cls, ua: str) -> None:
        """设置全局默认 User-Agent。"""
        ...

    @staticmethod
    def create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
        """创建 httpx.AsyncClient。user_agent 传则覆盖全局默认。"""
        ...


class WebSocketClient(mutobj.Declaration):
    """WebSocket 客户端。

    通过 ws:// URL 发起 WebSocket 连接，收发文本/二进制帧::

        ws = WebSocketClient(url="ws://127.0.0.1:8765/ws")
        await ws.connect()
        await ws.send_text("hello")
        msg = await ws.receive_text()
        await ws.close()
    """

    url: str = ""

    async def connect(self) -> None:
        """发起 WebSocket 连接（HTTP upgrade）。"""
        ...

    async def send_text(self, data: str) -> None:
        """发送文本帧。"""
        ...

    async def send_bytes(self, data: bytes) -> None:
        """发送二进制帧。"""
        ...

    async def receive_text(self) -> str:
        """接收文本帧。收到二进制帧时抛出 TypeError。"""
        ...

    async def receive_bytes(self) -> bytes:
        """接收二进制帧。收到文本帧时抛出 TypeError。"""
        ...

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """关闭连接。"""
        ...

    async def abort(self) -> None:
        """强制关闭底层连接，不走 WebSocket close 帧握手。"""
        ...


from . import _client_impl as _client_impl  # noqa: E402, F401 — trigger @impl registration
