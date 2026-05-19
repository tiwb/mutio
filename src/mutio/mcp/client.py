"""MCPClient — 通过 Streamable HTTP 连接 MCP server。"""

from __future__ import annotations

from typing import Any

import mutobj


class MCPClient(mutobj.Declaration):
    """MCP client — 通过 Streamable HTTP 连接 MCP server。

    用法::

        client = MCPClient(url="http://localhost:8000/mcp")
        await client.connect()
        try:
            tools = await client.list_tools()
            result = await client.call_tool("search", query="hello")
        finally:
            await client.close()
    """
    url: str = ""
    client_name: str = "mutio"
    client_version: str = "0.1.0"
    timeout: float = 30.0
    server_info: dict[str, Any] = mutobj.field(default_factory=dict)
    server_capabilities: dict[str, Any] = mutobj.field(default_factory=dict)
    server_instructions: str = ""

    async def connect(self) -> None:
        """连接并完成 MCP initialize 握手。"""
        ...

    async def close(self) -> None:
        """关闭连接。"""
        ...

    async def list_tools(self) -> list[dict[str, Any]]:
        """获取 server 可用 tools。"""
        ...

    async def call_tool(self, name: str, **arguments: Any) -> dict[str, Any]:
        """调用 tool。返回 ``{"content": [...], "isError": bool}``。"""
        ...

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """通用 JSON-RPC 请求 — 直接转发到 server，返回 ``result`` 字段。

        服务端宣告自定义扩展方法时（如 ``pysandbox/namespaces.list``），
        client 通过本入口调用，不必在 ``MCPClient`` 上为每个扩展方法
        加专门的方法。错误会以 :class:`MCPError` 形式抛出。
        """
        ...

    async def list_resources(self) -> list[dict[str, Any]]:
        """获取 server 可用 resources。"""
        ...

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """读取 resource。"""
        ...

    async def list_prompts(self) -> list[dict[str, Any]]:
        """获取 server 可用 prompts。"""
        ...

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """获取 prompt。"""
        ...

    async def ping(self) -> None:
        """Ping server。"""
        ...


class MCPError(Exception):
    """MCP 协议错误。"""
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP error {code}: {message}")


from . import _client_impl as _client_impl  # noqa: E402, F401 — trigger @impl registration
