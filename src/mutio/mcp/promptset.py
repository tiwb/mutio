"""MCPPromptSet — MCP prompt 集合基类，零注册。"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import mutobj

if TYPE_CHECKING:
    from mutio.mcp.view import MCPView


class MCPPromptSet(mutobj.Declaration):
    """MCP prompt 集合基类。一个类定义一组 prompt，方法名就是 prompt name。

    方法签名约束：参数只能是 ``str`` 类型（MCP 协议要求），可以有默认值。
    返回值支持 ``str | PromptMessage | list[PromptMessage]``：

    - ``str`` → 自动包装成单条 user text message
    - ``PromptMessage`` → 作为单条 message
    - ``list[PromptMessage]`` → 作为多条 messages

    归属目标 MCPView 通过两种方式指定（二选一）：

    - ``view``: 直接引用 MCPView 子类（或元组）
    - ``path``: 按路径匹配 MCPView.path（或元组）

    ``prefix`` 为 prompt name 前缀，如 prefix="code_" 则方法 review 注册为 "code_review"。
    """
    prefix: ClassVar[str] = ""
    view: ClassVar[type[MCPView] | tuple[type[MCPView], ...] | None] = None
    path: ClassVar[str | tuple[str, ...]] = ""
