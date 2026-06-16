"""MCPView HTTP 集成测试（L1）— 通过 Server.route() 驱动 MCPView.post/delete。

测试 SSE 响应、纯通知 batch、session DELETE 等 HTTP 层行为。
全部通过公开 API，零私有 import。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mutio.mcp.view import MCPView
from mutio.mcp.toolset import MCPToolSet
from mutio.net.server import Server, Request, Response


# ---------------------------------------------------------------------------
# 测试用 MCPView + ToolSet（供 tools/list 使用）
# ---------------------------------------------------------------------------


class _HttpTestTools(MCPToolSet):
    """最小 toolset，供 MCPView.post 的 tools/list 使用。"""
    path = "/mcp-http"

    async def echo(self, text: str) -> str:
        """Echo the input."""
        return text


class _HttpTestView(MCPView):
    path = "/mcp-http"
    name = "test"
    version = "1.0"


# ---------------------------------------------------------------------------
# ASGI 模拟工具
# ---------------------------------------------------------------------------


class _Captured:
    """收集 ASGI send 输出。"""

    def __init__(self) -> None:
        self.start: dict[str, Any] | None = None
        self.body: bytes = b""

    @property
    def status(self) -> int:
        assert self.start is not None
        return self.start["status"]

    @property
    def headers(self) -> dict[str, str]:
        assert self.start is not None
        return {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in self.start["headers"]
        }


async def _call_mcp(
    method: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    server: Server | None = None,
) -> tuple[_Captured, Server]:
    """通过 Server.route() 发送请求到 MCPView，返回 (捕获的响应, Server)。

    支持复用 Server（跨请求共享 session）。
    """
    if server is None:
        server = Server(views=(_HttpTestView,))
    cap = _Captured()

    scope_headers: list[tuple[bytes, bytes]] = []
    if headers:
        for k, v in headers.items():
            scope_headers.append((k.encode("latin-1"), v.encode("latin-1")))

    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": "/mcp-http",
        "raw_path": b"/mcp-http",
        "query_string": b"",
        "headers": scope_headers,
    }

    body_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            cap.start = msg
        elif msg["type"] == "http.response.body":
            cap.body += msg.get("body", b"")

    await server.route(scope, receive, send)
    return cap, server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPViewHttp:
    """通过 HTTP 层测试 MCPView.post/delete 的行为。"""

    @pytest.mark.asyncio
    async def test_sse_response(self):
        """Accept: text/event-stream → SSE 格式响应。"""
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/list", "params": {},
        }).encode()
        cap, _server = await _call_mcp(
            "POST", body=payload, headers={"accept": "text/event-stream"},
        )
        assert cap.status == 200
        h = cap.headers
        assert h["content-type"] == "text/event-stream"
        assert "cache-control" in h
        # SSE 格式：event: message + data: ...
        assert b"event: message" in cap.body
        assert b"data: " in cap.body

    @pytest.mark.asyncio
    async def test_json_response_without_sse_header(self):
        """无 Accept: text/event-stream → 普通 JSON 响应。"""
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/list", "params": {},
        }).encode()
        cap, _server = await _call_mcp("POST", body=payload)
        assert cap.status == 200
        assert cap.headers["content-type"] == "application/json"
        parsed = json.loads(cap.body)
        assert "result" in parsed

    @pytest.mark.asyncio
    async def test_notification_only_batch_returns_202(self):
        """批量消息全是 notification（无 id）→ 202 Accepted。"""
        payload = json.dumps([
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        ]).encode()
        cap, _server = await _call_mcp("POST", body=payload)
        assert cap.status == 202
        assert cap.body == b""

    @pytest.mark.asyncio
    async def test_notification_single_returns_202(self):
        """单条 notification → 202 Accepted。"""
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }).encode()
        cap, _server = await _call_mcp("POST", body=payload)
        assert cap.status == 202
        assert cap.body == b""

    @pytest.mark.asyncio
    async def test_delete_session_not_found_returns_404(self):
        """DELETE 不存在的 session → 404。"""
        cap, _server = await _call_mcp(
            "DELETE", headers={"mcp-session-id": "nonexistent"},
        )
        assert cap.status == 404

    @pytest.mark.asyncio
    async def test_delete_existing_session_returns_200(self):
        """DELETE 先 initialize 创建的 session → 200。"""
        # 先 initialize 获取 session ID（复用同一个 Server）
        init_payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize", "params": {},
        }).encode()
        cap_init, server = await _call_mcp("POST", body=init_payload)
        assert cap_init.status == 200
        session_id = cap_init.headers.get("mcp-session-id")
        assert session_id

        # 用获取的 session ID 发起 DELETE（复用 Server）
        cap_del, _ = await _call_mcp(
            "DELETE", headers={"mcp-session-id": session_id}, server=server,
        )
        assert cap_del.status == 200
        assert cap_del.body == b""

    @pytest.mark.asyncio
    async def test_initialize_returns_session_id(self):
        """initialize 响应包含 mcp-session-id header。"""
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize", "params": {},
        }).encode()
        cap, _server = await _call_mcp("POST", body=payload)
        assert cap.status == 200
        assert "mcp-session-id" in cap.headers
        # 验证响应体包含 protocolVersion + capabilities
        parsed = json.loads(cap.body)
        result = parsed["result"]
        assert "protocolVersion" in result
        assert "capabilities" in result
