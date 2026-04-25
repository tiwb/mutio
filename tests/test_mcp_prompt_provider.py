"""mutio.mcp._view_impl — MCPPromptProvider 测试。"""

from __future__ import annotations

import pytest

from mutio.mcp.promptset import MCPPromptSet
from mutio.mcp.protocol import JsonRpcError, PromptMessage
from mutio.mcp._view_impl import (
    MCPPromptProvider,
    _infer_prompt_arguments,
    _normalize_prompt_result,
)


class _PromptsNoArg(MCPPromptSet):
    """Prompt set with zero-arg methods, isolated via unique path."""
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
    """Prompt set with non-str parameter — should trigger error when listed."""
    path = "/__test_prompts_bad"

    def oops(self, n: int) -> str:  # type: ignore[override]
        return f"{n}"


class _FakeView:
    """符合 target_view 需要的最小接口：callable 返回带 path 的实例。"""
    def __init__(self, path: str) -> None:
        self.path = path


def _make_provider_for_path(path: str) -> MCPPromptProvider:
    # 构造一个 provider，手动注入匹配目标 path，绕开 MCPView 实例依赖
    provider = MCPPromptProvider(target_view=None)
    provider._target_paths = (path,)
    provider._target_view = type("T", (), {"__name__": f"T{path}"})  # type: ignore[assignment]
    return provider


class TestInferPromptArguments:
    def test_no_args(self):
        def fn():
            pass
        assert _infer_prompt_arguments(fn) == []

    def test_required_str(self):
        def fn(x: str):
            pass
        assert _infer_prompt_arguments(fn) == [{"name": "x", "required": True}]

    def test_optional_str(self):
        def fn(x: str = "a"):
            pass
        assert _infer_prompt_arguments(fn) == [{"name": "x", "required": False}]

    def test_no_annotation_treated_as_str(self):
        def fn(x, y="a"):
            pass
        assert _infer_prompt_arguments(fn) == [
            {"name": "x", "required": True},
            {"name": "y", "required": False},
        ]

    def test_non_str_raises(self):
        def fn(n: int):
            pass
        with pytest.raises(TypeError, match="must be str"):
            _infer_prompt_arguments(fn)

    def test_self_skipped(self):
        class C:
            def m(self, x: str):
                pass
        assert _infer_prompt_arguments(C.m) == [{"name": "x", "required": True}]


class TestNormalizePromptResult:
    def test_str(self):
        msgs = _normalize_prompt_result("hello")
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == {"type": "text", "text": "hello"}

    def test_single_message(self):
        m = PromptMessage(role="assistant", content={"type": "text", "text": "hi"})
        assert _normalize_prompt_result(m) == [m]

    def test_list_messages(self):
        m1 = PromptMessage(role="user", content={"type": "text", "text": "a"})
        m2 = PromptMessage(role="assistant", content={"type": "text", "text": "b"})
        assert _normalize_prompt_result([m1, m2]) == [m1, m2]

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="must return"):
            _normalize_prompt_result(42)

    def test_list_with_non_message_raises(self):
        with pytest.raises(TypeError):
            _normalize_prompt_result(["not a message"])


class TestListPrompts:
    def test_no_arg_prompt(self):
        provider = _make_provider_for_path("/__test_prompts_no_arg")
        prompts = provider.list_prompts()
        names = [p["name"] for p in prompts]
        assert "status" in names
        entry = next(p for p in prompts if p["name"] == "status")
        assert entry["description"] == "返回服务器状态概述。"
        assert entry["arguments"] == []

    def test_with_arg_prompt_applies_prefix(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        prompts = {p["name"]: p for p in provider.list_prompts()}
        assert "pfx_logs" in prompts
        assert "pfx_review" in prompts
        assert "pfx_chat" in prompts

    def test_arguments_inferred_from_signature(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        prompts = {p["name"]: p for p in provider.list_prompts()}
        logs = prompts["pfx_logs"]
        assert logs["arguments"] == [{"name": "level", "required": False}]
        review = prompts["pfx_review"]
        assert review["arguments"] == [{"name": "target", "required": True}]

    def test_path_isolation(self):
        """target_path 不匹配时，不应看到其他 promptset."""
        provider = _make_provider_for_path("/__test_prompts_no_arg")
        names = [p["name"] for p in provider.list_prompts()]
        assert "pfx_logs" not in names
        assert "pfx_review" not in names

    def test_non_str_parameter_surfaced_on_list(self):
        provider = _make_provider_for_path("/__test_prompts_bad")
        with pytest.raises(TypeError, match="must be str"):
            provider.list_prompts()


class TestCallPrompt:
    @pytest.mark.asyncio
    async def test_str_return_wrapped(self):
        provider = _make_provider_for_path("/__test_prompts_no_arg")
        result = await provider.call_prompt("status", {})
        assert result["messages"] == [
            {"role": "user", "content": {"type": "text", "text": "请汇报服务器状态。"}}
        ]
        assert result["description"] == "返回服务器状态概述。"

    @pytest.mark.asyncio
    async def test_with_string_arg(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        result = await provider.call_prompt("pfx_logs", {"level": "WARNING"})
        assert result["messages"][0]["content"]["text"] == "查看 level=WARNING 的日志"

    @pytest.mark.asyncio
    async def test_default_arg(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        result = await provider.call_prompt("pfx_logs", {})
        assert result["messages"][0]["content"]["text"] == "查看 level=ERROR 的日志"

    @pytest.mark.asyncio
    async def test_single_message_return(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        result = await provider.call_prompt("pfx_review", {"target": "foo.py"})
        assert result["messages"] == [
            {"role": "user", "content": {"type": "text", "text": "review foo.py"}}
        ]

    @pytest.mark.asyncio
    async def test_list_message_return(self):
        provider = _make_provider_for_path("/__test_prompts_with_arg")
        result = await provider.call_prompt("pfx_chat", {"topic": "mcp"})
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_unknown_prompt_raises(self):
        provider = _make_provider_for_path("/__test_prompts_no_arg")
        with pytest.raises(JsonRpcError):
            await provider.call_prompt("nope", {})
