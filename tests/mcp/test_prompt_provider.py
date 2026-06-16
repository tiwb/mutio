"""mutio.mcp — MCPPromptSet L1 测试。

全部通过 MCPView dispatcher + prompts/list、prompts/get JSON-RPC 验证。
"""

from __future__ import annotations

import asyncio

import pytest

from mutio.mcp.promptset import MCPPromptSet
from mutio.mcp.view import MCPView
from mutio.mcp.protocol import PromptMessage
from mutio.mcp._view_impl import _get_ext


# ---------------------------------------------------------------------------
# PromptSet 定义（各自绑定独立 MCPView path）
# ---------------------------------------------------------------------------


class _ViewNoArg(MCPView):
    path = "/__test_prompts_no_arg"
    name = "test-no-arg"
    version = "1.0"


class _ViewWithArg(MCPView):
    path = "/__test_prompts_with_arg"
    name = "test-with-arg"
    version = "1.0"


class _ViewBad(MCPView):
    path = "/__test_prompts_bad"
    name = "test-bad"
    version = "1.0"


class _PromptsNoArg(MCPPromptSet):
    """Prompt set with zero-arg methods."""
    path = "/__test_prompts_no_arg"

    def status(self) -> str:
        """返回服务器状态概述。"""
        return "请汇报服务器状态。"


class _PromptsWithArg(MCPPromptSet):
    """Prompt set exercising the string argument surface."""
    path = "/__test_prompts_with_arg"
    prefix = "pfx_"

    def logs(self, level: str = "ERROR") -> str:
        """查看日志。"""
        return f"查看 level={level} 的日志"

    def review(self, target: str) -> PromptMessage:
        """审查目标。"""
        return PromptMessage(role="user", content={"type": "text", "text": f"review {target}"})

    def chat(self, topic: str = "general") -> list[PromptMessage]:
        """多轮预设对话。"""
        return [
            PromptMessage(role="user", content={"type": "text", "text": f"聊 {topic}"}),
            PromptMessage(role="assistant", content={"type": "text", "text": "好的。"}),
        ]


class _PromptsBad(MCPPromptSet):
    """Prompt set with non-str parameter."""
    path = "/__test_prompts_bad"

    def oops(self, n: int) -> str:  # type: ignore[override]
        return f"{n}"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _dispatch(view: MCPView, method: str, params: dict | None = None) -> dict:
    """同步调用 view dispatcher 上的 JSON-RPC 方法。"""
    ext = _get_ext(view)
    assert ext.dispatch is not None
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = asyncio.run(ext.dispatch.handle(msg))
    assert response is not None, f"{method} returned None"
    if "error" in response:
        raise RuntimeError(f"{method} error: {response['error']}")
    return response["result"]


def _prompts_list(view: MCPView) -> list[dict]:
    """调用 prompts/list，返回 prompt 列表。"""
    return _dispatch(view, "prompts/list")["prompts"]


# ---------------------------------------------------------------------------
# L1 测试：prompts/list
# ---------------------------------------------------------------------------


class TestListPrompts:
    def test_no_arg_prompt(self):
        prompts = _prompts_list(_ViewNoArg())
        names = [p["name"] for p in prompts]
        assert "status" in names
        entry = next(p for p in prompts if p["name"] == "status")
        assert entry["description"] == "返回服务器状态概述。"
        assert entry["arguments"] == []

    def test_with_arg_prompt_applies_prefix(self):
        prompts = {p["name"]: p for p in _prompts_list(_ViewWithArg())}
        assert "pfx_logs" in prompts
        assert "pfx_review" in prompts
        assert "pfx_chat" in prompts

    def test_arguments_inferred_from_signature(self):
        prompts = {p["name"]: p for p in _prompts_list(_ViewWithArg())}
        logs = prompts["pfx_logs"]
        assert logs["arguments"] == [{"name": "level", "required": False}]
        review = prompts["pfx_review"]
        assert review["arguments"] == [{"name": "target", "required": True}]

    def test_path_isolation(self):
        """不同 view 不应看到其他 path 的 prompt。"""
        names = [p["name"] for p in _prompts_list(_ViewNoArg())]
        assert "pfx_logs" not in names
        assert "pfx_review" not in names

    def test_non_str_parameter_surfaced_on_list(self):
        """prompts/list 对非 str 参数应报错。"""
        ext = _get_ext(_ViewBad())
        assert ext.dispatch is not None
        msg = {"jsonrpc": "2.0", "id": 1, "method": "prompts/list", "params": {}}
        response = asyncio.run(ext.dispatch.handle(msg))
        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32603  # Internal error


# ---------------------------------------------------------------------------
# L1 测试：prompts/get
# ---------------------------------------------------------------------------


class TestCallPrompt:
    def test_str_return_wrapped(self):
        result = _dispatch(_ViewNoArg(), "prompts/get", {"name": "status"})
        assert result["messages"] == [
            {"role": "user", "content": {"type": "text", "text": "请汇报服务器状态。"}}
        ]
        assert result["description"] == "返回服务器状态概述。"

    def test_with_string_arg(self):
        result = _dispatch(_ViewWithArg(), "prompts/get", {"name": "pfx_logs", "arguments": {"level": "WARNING"}})
        assert result["messages"][0]["content"]["text"] == "查看 level=WARNING 的日志"

    def test_default_arg(self):
        result = _dispatch(_ViewWithArg(), "prompts/get", {"name": "pfx_logs", "arguments": {}})
        assert result["messages"][0]["content"]["text"] == "查看 level=ERROR 的日志"

    def test_single_message_return(self):
        result = _dispatch(_ViewWithArg(), "prompts/get", {"name": "pfx_review", "arguments": {"target": "foo.py"}})
        assert result["messages"] == [
            {"role": "user", "content": {"type": "text", "text": "review foo.py"}}
        ]

    def test_list_message_return(self):
        result = _dispatch(_ViewWithArg(), "prompts/get", {"name": "pfx_chat", "arguments": {"topic": "mcp"}})
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"

    def test_unknown_prompt_raises(self):
        ext = _get_ext(_ViewNoArg())
        assert ext.dispatch is not None
        msg = {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "nope"}}
        response = asyncio.run(ext.dispatch.handle(msg))
        assert response is not None
        assert "error" in response
