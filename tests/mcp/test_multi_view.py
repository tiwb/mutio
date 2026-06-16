"""测试多 MCPView 场景下 tool 隔离（L1）。

通过 MCPView 实例 + tools/list JSON-RPC 调用验证 path 隔离行为。
"""

from __future__ import annotations

import asyncio

import mutobj
from mutio.mcp.view import MCPView
from mutio.mcp.toolset import MCPToolSet
from mutio.mcp._view_impl import _get_ext


# ---------------------------------------------------------------------------
# 定义两个独立的 MCPView
# ---------------------------------------------------------------------------

class ViewA(MCPView):
    path = "/mcp-a"
    name = "server-a"
    version = "1.0"


class ViewB(MCPView):
    path = "/mcp-b"
    name = "server-b"
    version = "1.0"


# ---------------------------------------------------------------------------
# 定义归属不同 view 的 tool 集合
# ---------------------------------------------------------------------------

class ToolsForA(MCPToolSet):
    """只应该注册到 ViewA"""
    path = "/mcp-a"

    async def tool_a1(self) -> str:
        """Tool A1"""
        return "a1"

    async def tool_a2(self, name: str) -> str:
        """Tool A2"""
        return f"a2: {name}"


class ToolsForB(MCPToolSet):
    """只应该注册到 ViewB"""
    path = "/mcp-b"

    async def tool_b1(self) -> str:
        """Tool B1"""
        return "b1"

    async def tool_b2(self, count: int) -> str:
        """Tool B2"""
        return f"b2: {count}"


class ToolsForBoth(MCPToolSet):
    """应该注册到两个 view（通过 path 元组）"""
    path = ("/mcp-a", "/mcp-b")

    async def shared_tool(self) -> str:
        """Shared tool"""
        return "shared"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _tools_list(view: MCPView) -> set[str]:
    """通过 tools/list JSON-RPC 获取 view 可见的 tool 名称集合。"""
    ext = _get_ext(view)
    assert ext.dispatch is not None
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = asyncio.run(ext.dispatch.handle(msg))
    assert response is not None
    assert "result" in response, f"Unexpected response: {response}"
    tools = response["result"]["tools"]
    return {t["name"] for t in tools}


# ---------------------------------------------------------------------------
# L1 测试：通过 tools/list 公开 API 验证 path 隔离
# ---------------------------------------------------------------------------


class TestMCPToolIsolation:
    """通过 tools/list JSON-RPC 验证多 MCPView 的 tool 隔离。"""

    def test_tools_isolated_by_path(self):
        """不同 view 的 tools/list 返回各自的 tool 集合。"""
        tool_names_a = _tools_list(ViewA())
        tool_names_b = _tools_list(ViewB())

        expected_a = {"tool_a1", "tool_a2", "shared_tool"}
        expected_b = {"tool_b1", "tool_b2", "shared_tool"}

        assert tool_names_a == expected_a, f"ViewA tools: {sorted(tool_names_a)}"
        assert tool_names_b == expected_b, f"ViewB tools: {sorted(tool_names_b)}"

    def test_shared_tool_visible_on_both_views(self):
        """path 为 tuple 的 toolset 在两个 view 上都可见。"""
        tools_a = _tools_list(ViewA())
        tools_b = _tools_list(ViewB())

        assert "shared_tool" in tools_a
        assert "shared_tool" in tools_b

    def test_view_a_tools_not_visible_on_view_b(self):
        """view A 专属 tool 不在 view B 的 tools/list 中。"""
        tools_b = _tools_list(ViewB())
        assert "tool_a1" not in tools_b
        assert "tool_a2" not in tools_b

    def test_view_b_tools_not_visible_on_view_a(self):
        """view B 专属 tool 不在 view A 的 tools/list 中。"""
        tools_a = _tools_list(ViewA())
        assert "tool_b1" not in tools_a
        assert "tool_b2" not in tools_a
