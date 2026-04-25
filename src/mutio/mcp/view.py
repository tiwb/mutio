"""MCPView — MCP Streamable HTTP 端点。"""

from __future__ import annotations

from mutio.net.server import View, Request, Response, StreamingResponse


class MCPView(View):
    """MCP Streamable HTTP 端点。

    继承 View，被 Server.route 统一发现和分发。
    impl 中包含 JSON-RPC 分发、session 管理、MCPToolProvider 逻辑。
    """
    path: str | tuple[str, ...] = ""
    name: str = ""
    version: str = ""
    instructions: str | None = None

    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response: ...
