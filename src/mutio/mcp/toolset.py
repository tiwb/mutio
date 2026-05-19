"""MCPToolSet — MCP tool 集合基类，零注册。"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import mutobj

if TYPE_CHECKING:
    from mutio.mcp.view import MCPView


class MCPToolSet(mutobj.Declaration):
    """MCP tool 集合基类。一个类定义一组 tool，方法名就是 tool name。

    归属目标 MCPView 通过两种方式指定（二选一）：

    - ``view``: 直接引用 MCPView 子类（或元组）
    - ``path``: 按路径匹配 MCPView.path（或元组）

    ``prefix`` 为 tool name 前缀，如 prefix="fs" 则方法 read 注册为 "fs_read"。
    """
    prefix: ClassVar[str] = ""
    view: ClassVar[type[MCPView] | tuple[type[MCPView], ...] | None] = None
    path: ClassVar[str | tuple[str, ...]] = ""
