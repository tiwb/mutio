"""mutio.mcp — MCP 协议层（工具注册 / Streamable HTTP 端点 / 客户端）。"""

import mutio.mcp._view_impl as _view_impl  # noqa: F401
import mutio.mcp._client_impl as _client_impl  # noqa: F401

from mutio.mcp.protocol import (  # noqa: F401
    JsonRpcDispatcher,
    JsonRpcError,
    ToolDef,
    ToolResult,
    ResourceDef,
    ResourceContent,
    PromptDef,
    PromptMessage,
    ServerCapabilities,
    PROTOCOL_VERSION,
)
from mutio.mcp.toolset import MCPToolSet  # noqa: F401
from mutio.mcp.view import MCPView  # noqa: F401
from mutio.mcp.client import MCPClient, MCPError  # noqa: F401
