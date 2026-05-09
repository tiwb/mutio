"""MCPView — MCP Streamable HTTP 端点。"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from mutio.net.server import View, Request, Response, StreamingResponse

if TYPE_CHECKING:
    from mutio.mcp.protocol import JsonRpcDispatcher


class MCPView(View):
    """MCP Streamable HTTP 端点。

    继承 View，被 Server.route 统一发现和分发。
    impl 中包含 JSON-RPC 分发、session 管理、MCPToolProvider 逻辑。

    子类可覆盖 ``extra_capabilities`` / ``register_extra_methods``
    向 ``initialize`` 响应注入额外 capability、向 dispatcher 注册扩展方法。
    用于 vendor 扩展（如 pysandbox namespace sharing），mutio 本身不感知具体协议。
    """
    path: str | tuple[str, ...] = ""
    name: str = ""
    version: str = ""
    instructions: str | None = None

    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response: ...

    def extra_capabilities(self) -> dict[str, Any]:
        """返回追加进 ``initialize`` 响应 ``capabilities`` 的字段。

        默认返回空字典；子类覆盖以宣告 vendor 扩展能力。
        顶层合并到标准 capabilities 字典。
        """
        ...

    def register_extra_methods(self, dispatch: JsonRpcDispatcher) -> None:
        """在 view 的 JSON-RPC dispatcher 上注册扩展方法。

        默认 noop；子类覆盖以挂 vendor 扩展方法
        （如 ``pysandbox/namespaces.list`` 等）。
        每个 view 实例在首次请求时调用一次。
        """
        ...
