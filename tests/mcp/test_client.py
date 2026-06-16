"""mutio.mcp.client — MCPClient L1 测试。

全部通过真实 HTTP server（Server + MCPView）→ MCPClient 公开 API 验证，
覆盖 connect / close / list_tools / call_tool / list_prompts / get_prompt /
ping / request 及 MCPError 路径。
"""

from __future__ import annotations

import asyncio

import pytest

from mutio.net.server import Server
from mutio.mcp.view import MCPView
from mutio.mcp.toolset import MCPToolSet
from mutio.mcp.promptset import MCPPromptSet
from mutio.mcp.client import MCPClient, MCPError

from tests.net.conftest import free_port, start_server


# ---------------------------------------------------------------------------
# 测试用 MCPView + ToolSet + PromptSet
# ---------------------------------------------------------------------------


class _TestView(MCPView):
    path = "/mcp"
    name = "test-server"
    version = "1.0"
    instructions = "Test MCP server for client tests."


class _TestTools(MCPToolSet):
    path = "/mcp"

    async def echo(self, msg: str) -> str:
        """Echo back the message.

        Args:
            msg: The message to echo.
        """
        return msg

    async def add(self, a: int, b: int) -> str:
        """Add two integers.

        Args:
            a: First number.
            b: Second number.
        """
        return str(a + b)

    async def fail(self) -> str:
        """Always raises an error."""
        raise ValueError("intentional test failure")


class _TestPrompts(MCPPromptSet):
    path = "/mcp"

    def hello(self, name: str = "world") -> str:
        """Greet someone.

        Args:
            name: Who to greet.
        """
        return f"Hello, {name}!"


# ---------------------------------------------------------------------------
# Fixture：启动带 MCPView 的 HTTP server
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_url(free_port):
    """启动 MCP server 并返回 URL。"""
    sock, port = free_port
    server = Server(views=(_TestView,))
    await start_server(server, sock)
    yield f"http://127.0.0.1:{port}/mcp"
    await server.stop()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _connected_client(url: str) -> MCPClient:
    """创建并连接 MCPClient。"""
    client = MCPClient(url=url, client_name="test", client_version="1.0")
    await client.connect()
    return client


# ---------------------------------------------------------------------------
# L1 测试
# ---------------------------------------------------------------------------


class TestConnectAndInitialize:
    """connect → initialize 握手。"""

    @pytest.mark.asyncio
    async def test_connect_sets_server_info(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            assert client.server_info["name"] == "test-server"
            assert client.server_info["version"] == "1.0"
            assert client.server_instructions == "Test MCP server for client tests."
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_connect_sets_capabilities(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            caps = client.server_capabilities
            assert "tools" in caps
            assert "prompts" in caps
        finally:
            await client.close()


class TestListTools:
    """tools/list → 返回 tool 名称和 inputSchema。"""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            tools = await client.list_tools()
            names = {t["name"] for t in tools}
            assert names == {"echo", "add", "fail"}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_tools_includes_schema(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            tools = {t["name"]: t for t in await client.list_tools()}
            echo_schema = tools["echo"]["inputSchema"]
            assert echo_schema["properties"]["msg"]["type"] == "string"
            assert echo_schema["properties"]["msg"]["description"] == "The message to echo."
            assert echo_schema["required"] == ["msg"]
            assert tools["echo"]["description"] == "Echo back the message."
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_tools_default_no_default(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            # add 有 a, b 两个参数，b 没有默认值
            tools = {t["name"]: t for t in await client.list_tools()}
            add_schema = tools["add"]["inputSchema"]
            assert add_schema["required"] == ["a", "b"]
            assert add_schema["properties"]["a"]["type"] == "integer"
            assert add_schema["properties"]["b"]["type"] == "integer"
        finally:
            await client.close()


class TestCallTool:
    """tools/call → 调用 tool 并返回结果。"""

    @pytest.mark.asyncio
    async def test_call_echo(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            result = await client.call_tool("echo", msg="hello")
            assert result["content"][0]["text"] == "hello"
            # 成功的 tool 调用通常不含 isError 字段
            if "isError" in result:
                assert result["isError"] is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_call_add(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            result = await client.call_tool("add", a=3, b=4)
            assert result["content"][0]["text"] == "7"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_call_unknown_tool_raises(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            with pytest.raises(MCPError, match="Unknown tool"):
                await client.call_tool("nope")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_call_tool_internal_error(self, mcp_url):
        """Tool 内部异常 → isError: true + error text。"""
        client = await _connected_client(mcp_url)
        try:
            result = await client.call_tool("fail")
            assert result["isError"] is True
            assert "intentional test failure" in result["content"][0]["text"]
        finally:
            await client.close()


class TestListPrompts:
    """prompts/list → 返回 prompt 名称和参数。"""

    @pytest.mark.asyncio
    async def test_list_prompts_returns_prompts(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            prompts = await client.list_prompts()
            names = {p["name"] for p in prompts}
            assert "hello" in names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_prompts_includes_arguments(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            prompts = {p["name"]: p for p in await client.list_prompts()}
            hello = prompts["hello"]
            assert hello["arguments"] == [{"name": "name", "required": False}]
        finally:
            await client.close()


class TestGetPrompt:
    """prompts/get → 获取 prompt 内容。"""

    @pytest.mark.asyncio
    async def test_get_prompt_default_arg(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            result = await client.get_prompt("hello")
            msg = result["messages"][0]
            assert msg["role"] == "user"
            assert msg["content"]["text"] == "Hello, world!"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_prompt_with_arg(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            result = await client.get_prompt("hello", arguments={"name": "Alice"})
            msg = result["messages"][0]
            assert msg["content"]["text"] == "Hello, Alice!"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_prompt_unknown_raises(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            with pytest.raises(MCPError, match="Unknown prompt"):
                await client.get_prompt("nope")
        finally:
            await client.close()


class TestPing:
    """ping → 心跳不抛异常。"""

    @pytest.mark.asyncio
    async def test_ping_succeeds(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            await client.ping()  # 不应抛异常
        finally:
            await client.close()


class TestRequest:
    """通用 JSON-RPC request → 直接调用任意方法。"""

    @pytest.mark.asyncio
    async def test_request_tools_list(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            result = await client.request("tools/list")
            assert "tools" in result
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_request_unknown_method_raises(self, mcp_url):
        client = await _connected_client(mcp_url)
        try:
            with pytest.raises(MCPError, match="Method not found"):
                await client.request("nonexistent/method")
        finally:
            await client.close()


class TestClose:
    """close → 正常关闭连接。"""

    @pytest.mark.asyncio
    async def test_close_succeeds(self, mcp_url):
        client = await _connected_client(mcp_url)
        await client.close()
        # 二次 close 不应抛异常
        await client.close()

    @pytest.mark.asyncio
    async def test_close_without_connect(self, mcp_url):
        """未 connect 就 close 也不应抛异常。"""
        client = MCPClient(url=mcp_url)
        await client.close()
