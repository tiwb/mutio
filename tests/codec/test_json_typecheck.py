"""mutio.codec.json — pyright 类型推断验证测试。

仅包含使用 ``assert_type`` 验证 pyright 推断结果的测试。
此文件被 pyright ``include`` 配置单独扫描。
"""

from __future__ import annotations

from typing import assert_type

from mutio.codec import json
from mutio.codec.json import (
    JsonObject,
    JsonValue,
    get_element,
    get_field,
    narrow_value,
)


class TestPyrightTypeInference:
    def test_primitive_inference(self):
        raw: JsonValue = {"model": "gpt-4", "count": 3}
        assert_type(get_field(raw, "model", str), str)
        assert_type(get_field(raw, "model", str, default=None), str | None)
        assert_type(get_field(raw, "model", str, fallback=None), str | None)
        assert_type(get_element(["x"], 0, str), str)
        assert_type(get_element(["x"], 0, str, default=None), str | None)
        assert_type(narrow_value("x", str), str)
        assert_type(narrow_value("x", str, fallback=None), str | None)

    def test_generic_inference_from_typed_defaults(self):
        raw: JsonValue = {"meta": {"id": "x"}, "names": ["a", "b"]}
        default_obj: JsonObject = {}
        default_names: list[str] = []
        fallback_obj: JsonObject = {}
        assert_type(get_field(raw, "meta", JsonObject, default=default_obj), JsonObject)
        assert_type(get_field(raw, "names", list[str], default=default_names), list[str])
        assert_type(get_element(raw, 0, JsonObject, default=default_obj), JsonObject)
        assert_type(narrow_value(raw, JsonObject, fallback=fallback_obj), JsonObject)

    def test_generic_alias_with_untyped_fallback(self):
        """GenericAlias（JsonObject/list[int]）+ 无类型 fallback 应正确推断 T | TFallback。

        pyright 1.1.410 已将 GenericAlias 视为匹配 ``type[T]``，推断正确。
        本测试起回归作用，确保未来版本不会退化。
        """
        raw: JsonValue = {"meta": {"id": "x"}, "items": [1, 2], "count": 3}
        # get_element 的 fallback 只覆盖"元素类型不匹配"，非 list 走 default。
        # 所以 get_element 测试用 arr 而非 raw（dict）。
        arr: JsonValue = [1, "two", 3]
        empty_obj: JsonObject = {}
        empty_list: list[int] = []

        # JsonObject + untyped None fallback
        assert_type(
            get_field(raw, "meta", JsonObject, fallback=None),
            JsonObject | None,
        )
        assert_type(
            get_field(raw, "meta", JsonObject, default=empty_obj, fallback=None),
            JsonObject | None,
        )
        assert_type(
            narrow_value(raw, JsonObject, fallback=None),
            JsonObject | None,
        )
        assert_type(
            get_element(arr, 0, JsonObject, fallback=None),
            JsonObject | None,
        )

        # list[int] + untyped None fallback
        assert_type(
            get_field(raw, "items", list[int], fallback=None),
            list[int] | None,
        )
        assert_type(
            get_field(raw, "items", list[int], default=empty_list, fallback=None),
            list[int] | None,
        )
        assert_type(
            narrow_value(raw, list[int], fallback=None),
            list[int] | None,
        )
        assert_type(
            get_element(arr, 0, list[int], fallback=None),
            list[int] | None,
        )

        # JsonArray + untyped None fallback
        assert_type(
            narrow_value(raw, json.JsonArray, fallback=None),
            json.JsonArray | None,
        )

        # 不带 fallback 的 GenericAlias 也应正确推断
        assert_type(
            get_field(raw, "meta", JsonObject),
            JsonObject,
        )
        assert_type(
            narrow_value(raw, JsonObject),
            JsonObject,
        )
        assert_type(
            get_field(raw, "items", list[int]),
            list[int],
        )

    def test_untyped_empty_list_default_regression(self):
        """`default=[]` with GenericAlias infers `list[Unknown]` (known limitation).

        调用 ``get_field(data, "items", list[JsonObject], default=[])`` 时，
        pyright 将 ``default=[]`` 推断为 ``list[Unknown]``。原因：``TDefault``
        独立于 ``T``，``[]`` 无元素类型可供推导。

        当前 workaround：``default=list[JsonObject]()`` 或用类型变量。

        预期修复：pyright 完整支持 PEP 747 ``TypeForm[T]``（含 ``UnionType``）后，
        ``default`` 可与 ``typ`` 共享 ``T`` 约束，届时取消下方注释验证。
        """
        raw: JsonValue = {"items": [{"a": 1}], "names": ["x", "y"]}

        # 当前触发 reportUnknownArgumentType——保留待 pyright 完整支持
        # PEP 747 TypeForm[T]（含 UnionType）后，default 可共享 T 约束，
        # [] 被上下文类型化为 list[JsonObject]，届时取消注释：
        # result = get_field(raw, "items", list[JsonObject], default=[])
        # assert result == [{"a": 1}]

        # Workaround A：显式类型构造
        result2 = get_field(raw, "items", list[JsonObject],
                            default=list[JsonObject]())
        assert result2 == [{"a": 1}]

        # Workaround B：类型变量（现有测试已覆盖的模式）
        default_names: list[str] = []
        names = get_field(raw, "names", list[str], default=default_names)
        assert names == ["x", "y"]
