"""测试 mutio.mcp._schema：函数 → MCP inputSchema 推导。

覆盖点：
- 基本类型 / Literal / list / dict / Optional / Any
- docstring 主段与 Args 段提取
- Annotations 段单/多行 JSON
- 严格 json.loads（拒 True/None/单引号）
- 冲突检测各类禁止字段
- signature 不存在参数报错
- 段内未出现参数不报错
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

import pytest

from mutio.mcp._schema import (
    function_to_mcp_description,
    function_to_mcp_input_schema,
)


# ---------------------------------------------------------------------------
# signature → schema：基本类型
# ---------------------------------------------------------------------------


class TestBasicTypes:
    def test_str_int_float_bool(self):
        def f(a: str, b: int, c: float, d: bool) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["a"] == {"type": "string"}
        assert s["properties"]["b"] == {"type": "integer"}
        assert s["properties"]["c"] == {"type": "number"}
        assert s["properties"]["d"] == {"type": "boolean"}
        assert s["required"] == ["a", "b", "c", "d"]

    def test_default_makes_optional(self):
        def f(a: int, b: int = 10) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["required"] == ["a"]
        assert s["properties"]["b"]["default"] == 10

    def test_no_annotation_yields_empty(self):
        def f(a) -> None: ...
        s = function_to_mcp_input_schema(f)
        # 不报错，property 存在但无 type
        assert "a" in s["properties"]
        assert "type" not in s["properties"]["a"]

    def test_self_cls_skipped(self):
        class C:
            def m(self, x: int) -> None: ...
        s = function_to_mcp_input_schema(C().m)
        assert "self" not in s["properties"]
        assert "x" in s["properties"]


# ---------------------------------------------------------------------------
# Literal → enum
# ---------------------------------------------------------------------------


class TestLiteral:
    def test_literal_str(self):
        def f(level: Literal["DEBUG", "INFO", "ERROR"] = "INFO") -> None: ...
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["level"]
        assert p["type"] == "string"
        assert p["enum"] == ["DEBUG", "INFO", "ERROR"]
        assert p["default"] == "INFO"

    def test_literal_int(self):
        def f(n: Literal[1, 2, 3]) -> None: ...
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["n"]
        assert p["type"] == "integer"
        assert p["enum"] == [1, 2, 3]

    def test_literal_mixed_no_type(self):
        def f(x: Literal["a", 1]) -> None: ...
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["x"]
        assert "type" not in p
        assert p["enum"] == ["a", 1]


# ---------------------------------------------------------------------------
# list / dict
# ---------------------------------------------------------------------------


class TestContainers:
    def test_list_of_str(self):
        def f(xs: list[str]) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["xs"] == {"type": "array", "items": {"type": "string"}}

    def test_bare_list(self):
        def f(xs: list) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["xs"] == {"type": "array"}

    def test_dict_str_int(self):
        def f(d: dict[str, int]) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["d"] == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_bare_dict(self):
        def f(d: dict) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["d"] == {"type": "object"}


# ---------------------------------------------------------------------------
# Optional
# ---------------------------------------------------------------------------


class TestOptional:
    def test_optional_with_default_none(self):
        def f(x: int | None = None) -> None: ...
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["x"]
        assert p["type"] == ["integer", "null"]
        assert p["default"] is None
        assert "x" not in s["required"]

    def test_optional_typing_form(self):
        def f(x: Optional[str] = None) -> None: ...
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["x"]
        assert p["type"] == ["string", "null"]

    def test_none_required(self):
        def f(x: int | None) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert "x" in s["required"]


# ---------------------------------------------------------------------------
# Any 与未知类型
# ---------------------------------------------------------------------------


class TestAnyAndUnknown:
    def test_any(self):
        def f(x: Any) -> None: ...
        s = function_to_mcp_input_schema(f)
        assert "type" not in s["properties"]["x"]

    def test_unknown_class_falls_back(self):
        class Custom: ...
        def f(x: Custom) -> None: ...
        s = function_to_mcp_input_schema(f)
        # 不报错，无 type
        assert "type" not in s["properties"]["x"]


# ---------------------------------------------------------------------------
# docstring 主段 → tool description
# ---------------------------------------------------------------------------


class TestDescription:
    def test_main_section(self):
        def f(x: int) -> None:
            """Run the thing.

            Args:
                x: number.
            """
        assert function_to_mcp_description(f) == "Run the thing."

    def test_multiline_main(self):
        def f() -> None:
            """First line.

            Second paragraph still part of main.

            Args:
                x: ignored.
            """
        d = function_to_mcp_description(f)
        assert "First line." in d
        assert "Second paragraph" in d
        assert "Args" not in d

    def test_no_docstring(self):
        def f() -> None: ...
        assert function_to_mcp_description(f) == ""

    def test_doc_override(self):
        def f() -> None:
            """Replaced."""
        assert function_to_mcp_description(f, doc="Original.") == "Original."


# ---------------------------------------------------------------------------
# Args 段 → property.description
# ---------------------------------------------------------------------------


class TestArgsDescription:
    def test_simple(self):
        def f(x: int, y: str) -> None:
            """T.

            Args:
                x: an integer.
                y: a string.
            """
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["x"]["description"] == "an integer."
        assert s["properties"]["y"]["description"] == "a string."

    def test_continuation_line(self):
        def f(x: int) -> None:
            """T.

            Args:
                x: first line.
                    second line continues.
            """
        s = function_to_mcp_input_schema(f)
        assert "first line" in s["properties"]["x"]["description"]
        assert "second line" in s["properties"]["x"]["description"]


# ---------------------------------------------------------------------------
# Annotations 段
# ---------------------------------------------------------------------------


class TestAnnotations:
    def test_single_line_json(self):
        def f(pattern: str) -> None:
            """T.

            Annotations:
                pattern: {"format": "regex", "minLength": 1}
            """
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["pattern"]
        assert p["format"] == "regex"
        assert p["minLength"] == 1
        assert p["type"] == "string"  # signature 提供

    def test_multiline_json(self):
        def f(opts: dict) -> None:
            """T.

            Annotations:
                opts: {
                    "additionalProperties": false,
                    "propertyNames": {"pattern": "^[a-z]+$"}
                }
            """
        s = function_to_mcp_input_schema(f)
        p = s["properties"]["opts"]
        assert p["additionalProperties"] is False
        assert p["propertyNames"] == {"pattern": "^[a-z]+$"}

    def test_strict_json_rejects_python_literals(self):
        def f(x: int) -> None:
            """T.

            Annotations:
                x: {"flag": True}
            """
        with pytest.raises(ValueError, match="invalid JSON"):
            function_to_mcp_input_schema(f)

    def test_strict_json_rejects_single_quotes(self):
        def f(x: str) -> None:
            """T.

            Annotations:
                x: {'format': 'regex'}
            """
        with pytest.raises(ValueError, match="invalid JSON"):
            function_to_mcp_input_schema(f)

    def test_section_end_at_dedent(self):
        def f(x: int) -> None:
            """T.

            Annotations:
                x: {"minimum": 0}

            Returns:
                nothing.
            """
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["x"]["minimum"] == 0


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_duplicate_type(self):
        def f(x: int) -> None:
            """T.

            Annotations:
                x: {"type": "string"}
            """
        with pytest.raises(ValueError, match="'type' is already expressed"):
            function_to_mcp_input_schema(f)

    def test_duplicate_default(self):
        def f(x: int = 5) -> None:
            """T.

            Annotations:
                x: {"default": 10}
            """
        with pytest.raises(ValueError, match="'default' is already expressed"):
            function_to_mcp_input_schema(f)

    def test_duplicate_description(self):
        def f(x: int) -> None:
            """T.

            Args:
                x: from args.

            Annotations:
                x: {"description": "from annotations"}
            """
        with pytest.raises(ValueError, match="'description' is already expressed"):
            function_to_mcp_input_schema(f)

    def test_duplicate_enum_for_literal(self):
        def f(level: Literal["a", "b"] = "a") -> None:
            """T.

            Annotations:
                level: {"enum": ["c", "d"]}
            """
        with pytest.raises(ValueError, match="'enum' is already expressed"):
            function_to_mcp_input_schema(f)

    def test_duplicate_items_for_list(self):
        def f(xs: list[str]) -> None:
            """T.

            Annotations:
                xs: {"items": {"type": "integer"}}
            """
        with pytest.raises(ValueError, match="'items' is already expressed"):
            function_to_mcp_input_schema(f)

    def test_duplicate_additional_properties_for_dict(self):
        def f(d: dict[str, int]) -> None:
            """T.

            Annotations:
                d: {"additionalProperties": {"type": "string"}}
            """
        with pytest.raises(ValueError, match="'additionalProperties' is already expressed"):
            function_to_mcp_input_schema(f)


# ---------------------------------------------------------------------------
# Annotations 段 → signature 不存在的参数：报错（typo 防线）
# ---------------------------------------------------------------------------


class TestUnknownParamInAnnotations:
    def test_typo_raises(self):
        def f(pattern: str) -> None:
            """T.

            Annotations:
                paatern: {"format": "regex"}
            """
        with pytest.raises(ValueError, match="does not exist in function signature"):
            function_to_mcp_input_schema(f)

    def test_error_lists_candidates(self):
        def f(alpha: int, beta: int) -> None:
            """T.

            Annotations:
                gamma: {"minimum": 0}
            """
        with pytest.raises(ValueError, match="alpha.*beta"):
            function_to_mcp_input_schema(f)

    def test_args_section_unknown_param_silently_ignored(self):
        """Args 段散文里出现 signature 没有的参数：与 Google 解析器惯例一致，不报错。"""
        def f(x: int) -> None:
            """T.

            Args:
                x: real.
                y: phantom (no signature param).
            """
        # 不应抛
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["x"]["description"] == "real."

    def test_annotations_partial_param_subset_ok(self):
        """段内未出现的参数：无额外约束，不报错。"""
        def f(a: int, b: int) -> None:
            """T.

            Annotations:
                a: {"minimum": 0}
            """
        s = function_to_mcp_input_schema(f)
        assert s["properties"]["a"]["minimum"] == 0
        assert "minimum" not in s["properties"]["b"]


# ---------------------------------------------------------------------------
# 综合：示例文档中的 query 函数
# ---------------------------------------------------------------------------


class TestRealisticExample:
    def test_query_signature(self):
        def query(
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

        s = function_to_mcp_input_schema(query)
        d = function_to_mcp_description(query)

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
