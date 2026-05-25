"""测试 mutio.schema.jsonschema — annotation_to_json_schema。

覆盖点：
- 基本类型映射（str/int/float/bool）
- Literal（同类型 + 混合类型）
- list / list[T]
- dict / dict[str, T]
- None / NoneType
- Optional / T | None
- Any
- 多分支 Union
- 不识别类型降级
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

import pytest

from mutio.schema.jsonschema import annotation_to_json_schema


# ---------------------------------------------------------------------------
# 基本类型
# ---------------------------------------------------------------------------


class TestBasicTypes:
    def test_str(self):
        assert annotation_to_json_schema(str) == {"type": "string"}

    def test_int(self):
        assert annotation_to_json_schema(int) == {"type": "integer"}

    def test_float(self):
        assert annotation_to_json_schema(float) == {"type": "number"}

    def test_bool(self):
        assert annotation_to_json_schema(bool) == {"type": "boolean"}


# ---------------------------------------------------------------------------
# None / NoneType
# ---------------------------------------------------------------------------


class TestNoneType:
    def test_none(self):
        assert annotation_to_json_schema(None) == {"type": "null"}

    def test_none_type(self):
        assert annotation_to_json_schema(type(None)) == {"type": "null"}


# ---------------------------------------------------------------------------
# Literal
# ---------------------------------------------------------------------------


class TestLiteral:
    def test_literal_str(self):
        result = annotation_to_json_schema(Literal["DEBUG", "INFO", "ERROR"])
        assert result["type"] == "string"
        assert result["enum"] == ["DEBUG", "INFO", "ERROR"]

    def test_literal_int(self):
        result = annotation_to_json_schema(Literal[1, 2, 3])
        assert result["type"] == "integer"
        assert result["enum"] == [1, 2, 3]

    def test_literal_bool(self):
        result = annotation_to_json_schema(Literal[True, False])
        assert result["type"] == "boolean"
        assert result["enum"] == [True, False]

    def test_literal_mixed_no_type(self):
        result = annotation_to_json_schema(Literal["a", 1])
        assert "type" not in result
        assert result["enum"] == ["a", 1]


# ---------------------------------------------------------------------------
# list / dict
# ---------------------------------------------------------------------------


class TestContainers:
    def test_list_of_str(self):
        assert annotation_to_json_schema(list[str]) == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_list_of_int(self):
        assert annotation_to_json_schema(list[int]) == {
            "type": "array",
            "items": {"type": "integer"},
        }

    def test_bare_list(self):
        assert annotation_to_json_schema(list) == {"type": "array"}

    def test_nested_list(self):
        result = annotation_to_json_schema(list[list[str]])
        assert result["type"] == "array"
        assert result["items"] == {"type": "array", "items": {"type": "string"}}

    def test_dict_str_int(self):
        assert annotation_to_json_schema(dict[str, int]) == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_bare_dict(self):
        assert annotation_to_json_schema(dict) == {"type": "object"}


# ---------------------------------------------------------------------------
# Optional / T | None
# ---------------------------------------------------------------------------


class TestOptional:
    def test_optional_str_typing_form(self):
        result = annotation_to_json_schema(Optional[str])
        assert result["type"] == ["string", "null"]

    def test_union_with_none(self):
        result = annotation_to_json_schema(int | None)
        assert result["type"] == ["integer", "null"]

    def test_optional_list(self):
        result = annotation_to_json_schema(Optional[list[int]])
        assert result["type"] == ["array", "null"]
        assert result["items"] == {"type": "integer"}


# ---------------------------------------------------------------------------
# 多分支 Union
# ---------------------------------------------------------------------------


class TestUnion:
    def test_union_str_int(self):
        result = annotation_to_json_schema(str | int)
        assert "type" in result
        assert set(result["type"]) == {"string", "integer"}

    def test_union_with_null(self):
        result = annotation_to_json_schema(str | int | None)
        assert "type" in result
        assert set(result["type"]) == {"string", "integer", "null"}

    def test_complex_union_falls_back(self):
        """Union[str, Any] — Any 无 type，非全简单分支，退化为空。"""
        result = annotation_to_json_schema(Union[str, Any])
        assert result == {}


# ---------------------------------------------------------------------------
# Any 与未知类型
# ---------------------------------------------------------------------------


class TestAnyAndUnknown:
    def test_any(self):
        assert annotation_to_json_schema(Any) == {}

    def test_unknown_class_falls_back(self):
        class Custom: ...

        assert annotation_to_json_schema(Custom) == {}

    def test_unknown_class_instance(self):
        class Custom: ...

        assert annotation_to_json_schema(Custom()) == {}
