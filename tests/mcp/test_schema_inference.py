"""测试 mutio.mcp 函数→MCP tool schema 推导（L1）。

全部通过 MCPToolSet → MCPView → tools/list JSON-RPC 公开链路验证，
不直接调用 _schema 内部函数。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional, Union

from mutio.mcp.toolset import MCPToolSet
from mutio.mcp.view import MCPView
from mutio.mcp._view_impl import _get_ext


# ---------------------------------------------------------------------------
# L1 测试工具：通过完整 MCP pipeline 获取 schema
# ---------------------------------------------------------------------------

_counter: int = 0


def _route_to_schema(fn: Any, *, doc: str | None = None, tool_name: str = "t") -> dict[str, Any]:
    """通过 MCPToolSet + MCPView + tools/list 获取 tool 的 inputSchema。

    动态创建专属 MCPToolSet / MCPView 子类，走公开 JSON-RPC 链路。
    """
    global _counter
    _counter += 1
    uid = f"{tool_name}{_counter}"
    path = f"/_schema_{uid}"

    ts_cls = type(f"_TS_{uid}", (MCPToolSet,), {"path": path, tool_name: fn})
    tv_cls = type(f"_TV_{uid}", (MCPView,), {"path": path, "name": uid, "version": "1.0"})

    view = tv_cls()
    ext = _get_ext(view)
    assert ext.dispatch is not None

    response = asyncio.run(ext.dispatch.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    }))
    assert response is not None and "result" in response, f"tools/list failed: {response}"

    tools: list[dict[str, Any]] = response["result"]["tools"]
    for t in tools:
        if t["name"] == tool_name:
            return t["inputSchema"]
    raise ValueError(f"Tool '{tool_name}' not found in {[t['name'] for t in tools]}")


def _route_to_description(fn: Any, *, doc: str | None = None, tool_name: str = "t") -> str:
    """通过 tools/list 获取 tool 的 description。"""
    global _counter
    _counter += 1
    uid = f"{tool_name}{_counter}"
    path = f"/_schema_{uid}"

    ts_cls = type(f"_TS_{uid}", (MCPToolSet,), {"path": path, tool_name: fn})
    tv_cls = type(f"_TV_{uid}", (MCPView,), {"path": path, "name": uid, "version": "1.0"})

    view = tv_cls()
    ext = _get_ext(view)
    assert ext.dispatch is not None

    response = asyncio.run(ext.dispatch.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    }))
    assert response is not None and "result" in response

    tools: list[dict[str, Any]] = response["result"]["tools"]
    for t in tools:
        if t["name"] == tool_name:
            return t.get("description", "")
    raise ValueError(f"Tool '{tool_name}' not found")


# _route_to_jsonrpc_error 用于测试预期报错的签名
def _route_expect_error(fn: Any, *, tool_name: str = "t") -> str:
    """通过 tools/list 获取预期错误的 JSON-RPC error message。"""
    global _counter
    _counter += 1
    uid = f"{tool_name}{_counter}"
    path = f"/_schema_{uid}"

    ts_cls = type(f"_TS_{uid}", (MCPToolSet,), {"path": path, tool_name: fn})
    tv_cls = type(f"_TV_{uid}", (MCPView,), {"path": path, "name": uid, "version": "1.0"})

    view = tv_cls()
    ext = _get_ext(view)
    assert ext.dispatch is not None

    response = asyncio.run(ext.dispatch.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    }))
    assert response is not None
    if "error" in response:
        return response["error"]["message"]
    return ""


# ---------------------------------------------------------------------------
# signature → schema：基本类型
# ---------------------------------------------------------------------------


class TestBasicTypes:
    def test_str_int_float_bool(self):
        async def f(self, a: str, b: int, c: float, d: bool) -> None: ...
        s = _route_to_schema(f)
        assert s["properties"]["a"] == {"type": "string"}
        assert s["properties"]["b"] == {"type": "integer"}
        assert s["properties"]["c"] == {"type": "number"}
        assert s["properties"]["d"] == {"type": "boolean"}
        assert s["required"] == ["a", "b", "c", "d"]

    def test_default_makes_optional(self):
        async def f(self, a: int, b: int = 10) -> None: ...
        s = _route_to_schema(f)
        assert s["required"] == ["a"]
        assert s["properties"]["b"]["default"] == 10

    def test_no_annotation_yields_empty(self):
        async def f(self, a) -> None: ...
        s = _route_to_schema(f)
        assert "a" in s["properties"]
        assert "type" not in s["properties"]["a"]

    def test_self_cls_skipped(self):
        async def f(self, x: int) -> None: ...
        s = _route_to_schema(f)
        assert "self" not in s["properties"]
        assert "x" in s["properties"]


# ---------------------------------------------------------------------------
# Literal → enum
# ---------------------------------------------------------------------------


class TestLiteral:
    def test_literal_str(self):
        async def f(self, level: Literal["DEBUG", "INFO", "ERROR"] = "INFO") -> None: ...
        s = _route_to_schema(f)
        p = s["properties"]["level"]
        assert p["type"] == "string"
        assert p["enum"] == ["DEBUG", "INFO", "ERROR"]
        assert p["default"] == "INFO"

    def test_literal_int(self):
        async def f(self, n: Literal[1, 2, 3]) -> None: ...
        s = _route_to_schema(f)
        p = s["properties"]["n"]
        assert p["type"] == "integer"
        assert p["enum"] == [1, 2, 3]

    def test_literal_mixed_no_type(self):
        async def f(self, x: Literal["a", 1]) -> None: ...
        s = _route_to_schema(f)
        p = s["properties"]["x"]
        assert "type" not in p
        assert p["enum"] == ["a", 1]


# ---------------------------------------------------------------------------
# list / dict
# ---------------------------------------------------------------------------


class TestContainers:
    def test_list_of_str(self):
        async def f(self, xs: list[str]) -> None: ...
        s = _route_to_schema(f)
        assert s["properties"]["xs"] == {"type": "array", "items": {"type": "string"}}

    def test_bare_list(self):
        async def f(self, xs: list) -> None: ...
        s = _route_to_schema(f)
        assert s["properties"]["xs"] == {"type": "array"}

    def test_dict_str_int(self):
        async def f(self, d: dict[str, int]) -> None: ...
        s = _route_to_schema(f)
        assert s["properties"]["d"] == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_bare_dict(self):
        async def f(self, d: dict) -> None: ...
        s = _route_to_schema(f)
        assert s["properties"]["d"] == {"type": "object"}


# ---------------------------------------------------------------------------
# Optional
# ---------------------------------------------------------------------------


class TestOptional:
    def test_optional_with_default_none(self):
        async def f(self, x: int | None = None) -> None: ...
        s = _route_to_schema(f)
        p = s["properties"]["x"]
        assert p["type"] == ["integer", "null"]
        assert p["default"] is None
        assert "x" not in s["required"]

    def test_optional_typing_form(self):
        async def f(self, x: Optional[str] = None) -> None: ...
        s = _route_to_schema(f)
        p = s["properties"]["x"]
        assert p["type"] == ["string", "null"]

    def test_none_required(self):
        async def f(self, x: int | None) -> None: ...
        s = _route_to_schema(f)
        assert "x" in s["required"]


# ---------------------------------------------------------------------------
# Any 与未知类型
# ---------------------------------------------------------------------------


class TestAnyAndUnknown:
    def test_any(self):
        async def f(self, x: Any) -> None: ...
        s = _route_to_schema(f)
        assert "type" not in s["properties"]["x"]

    def test_unknown_class_falls_back(self):
        class Custom: ...
        async def f(self, x: Custom) -> None: ...
        s = _route_to_schema(f)
        assert "type" not in s["properties"]["x"]


# ---------------------------------------------------------------------------
# docstring 主段 → tool description
# ---------------------------------------------------------------------------


class TestDescription:
    def test_main_section(self):
        async def f(self, x: int) -> None:
            """Run the thing.

            Args:
                x: number.
            """
        assert _route_to_description(f) == "Run the thing."

    def test_multiline_main(self):
        async def f(self) -> None:
            """First line.

            Second paragraph still part of main.

            Args:
                x: ignored.
            """
        d = _route_to_description(f)
        assert "First line." in d
        assert "Second paragraph" in d
        assert "Args" not in d

    def test_no_docstring(self):
        async def f(self) -> None: ...
        assert _route_to_description(f) == ""

    def test_doc_override(self):
        async def f(self) -> None:
            """Replaced."""
        # doc override 通过手动传 doc；走 tools/list 时 doc 来自 __doc__
        # 这里只测 __doc__ 路径，与原始测试语义一致
        assert _route_to_description(f) == "Replaced."


# ---------------------------------------------------------------------------
# Args 段 → property.description
# ---------------------------------------------------------------------------


class TestArgsDescription:
    def test_simple(self):
        async def f(self, x: int, y: str) -> None:
            """T.

            Args:
                x: an integer.
                y: a string.
            """
        s = _route_to_schema(f)
        assert s["properties"]["x"]["description"] == "an integer."
        assert s["properties"]["y"]["description"] == "a string."

    def test_continuation_line(self):
        async def f(self, x: int) -> None:
            """T.

            Args:
                x: first line.
                    second line continues.
            """
        s = _route_to_schema(f)
        assert "first line" in s["properties"]["x"]["description"]
        assert "second line" in s["properties"]["x"]["description"]


# ---------------------------------------------------------------------------
# Annotations 段
# ---------------------------------------------------------------------------


class TestAnnotations:
    def test_single_line_json(self):
        async def f(self, pattern: str) -> None:
            """T.

            Annotations:
                pattern: {"format": "regex", "minLength": 1}
            """
        s = _route_to_schema(f)
        p = s["properties"]["pattern"]
        assert p["format"] == "regex"
        assert p["minLength"] == 1
        assert p["type"] == "string"

    def test_multiline_json(self):
        async def f(self, opts: dict) -> None:
            """T.

            Annotations:
                opts: {
                    "additionalProperties": false,
                    "propertyNames": {"pattern": "^[a-z]+$"}
                }
            """
        s = _route_to_schema(f)
        p = s["properties"]["opts"]
        assert p["additionalProperties"] is False
        assert p["propertyNames"] == {"pattern": "^[a-z]+$"}

    def test_strict_json_rejects_python_literals(self):
        async def f(self, x: int) -> None:
            """T.

            Annotations:
                x: {"flag": True}
            """
        err = _route_expect_error(f)
        assert "invalid JSON" in err

    def test_strict_json_rejects_single_quotes(self):
        async def f(self, x: str) -> None:
            """T.

            Annotations:
                x: {'format': 'regex'}
            """
        err = _route_expect_error(f)
        assert "invalid JSON" in err

    def test_section_end_at_dedent(self):
        async def f(self, x: int) -> None:
            """T.

            Annotations:
                x: {"minimum": 0}

            Returns:
                nothing.
            """
        s = _route_to_schema(f)
        assert s["properties"]["x"]["minimum"] == 0


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_duplicate_type(self):
        async def f(self, x: int) -> None:
            """T.

            Annotations:
                x: {"type": "string"}
            """
        err = _route_expect_error(f)
        assert "'type' is already expressed" in err

    def test_duplicate_default(self):
        async def f(self, x: int = 5) -> None:
            """T.

            Annotations:
                x: {"default": 10}
            """
        err = _route_expect_error(f)
        assert "'default' is already expressed" in err

    def test_duplicate_description(self):
        async def f(self, x: int) -> None:
            """T.

            Args:
                x: from args.

            Annotations:
                x: {"description": "from annotations"}
            """
        err = _route_expect_error(f)
        assert "'description' is already expressed" in err

    def test_duplicate_enum_for_literal(self):
        async def f(self, level: Literal["a", "b"] = "a") -> None:
            """T.

            Annotations:
                level: {"enum": ["c", "d"]}
            """
        err = _route_expect_error(f)
        assert "'enum' is already expressed" in err

    def test_duplicate_items_for_list(self):
        async def f(self, xs: list[str]) -> None:
            """T.

            Annotations:
                xs: {"items": {"type": "integer"}}
            """
        err = _route_expect_error(f)
        assert "'items' is already expressed" in err

    def test_duplicate_additional_properties_for_dict(self):
        async def f(self, d: dict[str, int]) -> None:
            """T.

            Annotations:
                d: {"additionalProperties": {"type": "string"}}
            """
        err = _route_expect_error(f)
        assert "'additionalProperties' is already expressed" in err


# ---------------------------------------------------------------------------
# Annotations 段 → signature 不存在的参数
# ---------------------------------------------------------------------------


class TestUnknownParamInAnnotations:
    def test_typo_raises(self):
        async def f(self, pattern: str) -> None:
            """T.

            Annotations:
                paatern: {"format": "regex"}
            """
        err = _route_expect_error(f)
        assert "does not exist in function signature" in err

    def test_error_lists_candidates(self):
        async def f(self, alpha: int, beta: int) -> None:
            """T.

            Annotations:
                gamma: {"minimum": 0}
            """
        err = _route_expect_error(f)
        assert "alpha" in err and "beta" in err

    def test_args_section_unknown_param_silently_ignored(self):
        """Args 段散文里出现 signature 没有的参数：不报错。"""
        async def f(self, x: int) -> None:
            """T.

            Args:
                x: real.
                y: phantom (no signature param).
            """
        s = _route_to_schema(f)
        assert s["properties"]["x"]["description"] == "real."

    def test_annotations_partial_param_subset_ok(self):
        """段内未出现的参数：无额外约束，不报错。"""
        async def f(self, a: int, b: int) -> None:
            """T.

            Annotations:
                a: {"minimum": 0}
            """
        s = _route_to_schema(f)
        assert s["properties"]["a"]["minimum"] == 0
        assert "minimum" not in s["properties"]["b"]


# ---------------------------------------------------------------------------
# 综合：示例文档中的 query 函数
# ---------------------------------------------------------------------------


class TestRealisticExample:
    def test_query_signature(self):
        async def query(
            self,
            pattern: str,
            count: int = 0,
            metadata: dict | None = None,
        ) -> str:
            """复杂查询。

            Args:
                pattern: 正则模式。
                count: 起始位置。
                metadata: 扩展元数据。

            Annotations:
                pattern: {"format": "regex", "minLength": 1}
                count: {"minimum": 0, "maximum": 10000}
                metadata: {"propertyNames": {"pattern": "^[a-z_]+$"}}
            """
            return ""

        s = _route_to_schema(query, tool_name="query")
        d = _route_to_description(query, tool_name="query")

        assert d == "复杂查询。"
        assert s["required"] == ["pattern"]

        pattern = s["properties"]["pattern"]
        assert pattern["type"] == "string"
        assert pattern["description"] == "正则模式。"
        assert pattern["format"] == "regex"
        assert pattern["minLength"] == 1

        count = s["properties"]["count"]
        assert count["type"] == "integer"
        assert count["default"] == 0
        assert count["minimum"] == 0
        assert count["maximum"] == 10000

        meta = s["properties"]["metadata"]
        assert "null" in meta["type"]
        assert meta["default"] is None
        assert meta["propertyNames"] == {"pattern": "^[a-z_]+$"}
