"""测试 MCPView 的 extra_capabilities / register_extra_methods 扩展钩子。

验证：
- 子类可通过覆盖钩子向 initialize 响应注入 vendor capability
- 子类可通过覆盖钩子注册扩展 JSON-RPC 方法到 dispatcher
- 默认实现不破坏现有行为（capability 不出现意外字段；dispatch 无意外路由）
"""

import asyncio
from typing import Any

import pytest

from mutio.mcp.view import MCPView
from mutio.mcp._view_impl import _get_ext


# ---------------------------------------------------------------------------
# 测试用 MCPView 子类
# ---------------------------------------------------------------------------

class PlainView(MCPView):
    """不覆盖任何钩子 — 验证默认行为。"""
    path = "/plain"
    name = "plain"
    version = "1.0"


class HookedView(MCPView):
    """覆盖两个钩子 — 注入 vendor capability + 扩展方法。"""
    path = "/hooked"
    name = "hooked"
    version = "1.0"

    def extra_capabilities(self) -> dict[str, Any]:
        return {"myvendor": {"version": "1", "feature": "x"}}

    def register_extra_methods(self, dispatch) -> None:
        async def _handle_ping_ext(params):
            return {"pong": True, "echo": params.get("data")}
        dispatch.add_method("myvendor/ping", _handle_ping_ext)


# ---------------------------------------------------------------------------
# 工具：通过 dispatcher 直接发起 JSON-RPC 调用（绕过 ASGI）
# ---------------------------------------------------------------------------

def _call(view: MCPView, method: str, params: dict | None = None) -> dict:
    """同步调用 view 上的 JSON-RPC 方法，返回 result 字段。"""
    ext = _get_ext(view)
    assert ext.dispatch is not None
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = asyncio.run(ext.dispatch.handle(msg))
    assert response is not None, f"{method} returned no response"
    if "error" in response:
        raise RuntimeError(f"{method} error: {response['error']}")
    return response["result"]


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class TestDefaultBehavior:
    """未覆盖钩子时的默认行为 — 不破坏现有 initialize 响应。"""

    def test_initialize_no_extra_capability(self):
        view = PlainView()
        result = _call(view, "initialize", {})
        caps = result["capabilities"]
        # 默认不含 vendor 字段
        assert "myvendor" not in caps
        # 标准字段照常存在结构（这里 plain view 没 tools/prompts，所以是空）
        assert isinstance(caps, dict)

    def test_no_extra_methods_registered(self):
        view = PlainView()
        ext = _get_ext(view)
        # 标准 7 个方法 + 1 个 notification handler
        # 用 dispatch.handle 调用一个不存在的方法应当返回 method not found
        msg = {"jsonrpc": "2.0", "id": 1, "method": "myvendor/ping", "params": {}}
        response = asyncio.run(ext.dispatch.handle(msg))
        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32601  # Method not found


class TestHookedBehavior:
    """覆盖钩子时 — capability 注入 + 扩展方法可路由。"""

    def test_initialize_includes_vendor_capability(self):
        view = HookedView()
        result = _call(view, "initialize", {})
        caps = result["capabilities"]
        assert caps.get("myvendor") == {"version": "1", "feature": "x"}

    def test_extra_method_routable(self):
        view = HookedView()
        result = _call(view, "myvendor/ping", {"data": "hello"})
        assert result == {"pong": True, "echo": "hello"}

    def test_standard_methods_still_work(self):
        """覆盖钩子不影响标准方法。"""
        view = HookedView()
        result = _call(view, "ping", {})
        assert result == {}
