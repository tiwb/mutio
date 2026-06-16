"""mutio.codec.json — JSON 类型安全工具 测试。"""

from __future__ import annotations

from typing import Any, assert_type

import pytest

from mutio.codec import json
from mutio.codec.json import (
    JsonObject,
    JsonValue,
    check_type,
    get_element,
    get_field,
    narrow_value,
)


# ============================================================================
# 类型导入
# ============================================================================


def test_types_importable():
    """类型别名可正常导入。"""
    from mutio.codec.json import JsonArray, JsonPrimitive
    assert JsonPrimitive
    assert JsonValue
    assert JsonObject
    assert JsonArray
    assert get_field
    assert get_element
    assert narrow_value


# ============================================================================
# 模块可替换 import json
# ============================================================================


class TestModuleReplacement:
    """`from mutio.codec import json` 能平替 `import json`。"""

    def test_loads_basic(self):
        assert json.loads("42") == 42
        assert json.loads('"hi"') == "hi"
        assert json.loads("[1,2]") == [1, 2]

    def test_dumps_basic(self):
        assert json.dumps(None) == "null"
        assert json.dumps([1, "a"]) == '[1, "a"]'

    def test_json_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads("bad")

    def test_json_decoder_available(self):
        assert json.JSONDecoder

    def test_json_encoder_available(self):
        assert json.JSONEncoder


# ============================================================================
# ensure_ascii 默认值
# ============================================================================


class TestEnsureAsciiDefault:
    def test_unicode_no_escape(self):
        """ensure_ascii 默认 False，中文原样输出。"""
        assert json.dumps("中文") == '"中文"'

    def test_ensure_ascii_true(self):
        result = json.dumps("中", ensure_ascii=True)
        assert "\\u" in result


# ============================================================================
# 类型兼容性
# ============================================================================


class TestTypeCompatibility:
    def test_json_value_to_any(self):
        v: JsonValue = json.loads('{"a": 1}')
        _: object = v  # 不抛异常即通过

    def test_json_object_to_dict_str_any(self):
        d: JsonObject = {"a": 1}
        _: dict[str, Any] = d


# ============================================================================
# isinstance 窄化（pyright 静态验证，运行时保证窄化不崩）
# ============================================================================


class TestIsinstanceNarrowing:
    """loads() 后 isinstance 窄化自动推导，无需额外注解。"""

    def test_dict_narrow(self):
        parsed = json.loads('{"a": 1}')
        if isinstance(parsed, dict):
            assert parsed.get("a") == 1
            assert parsed["a"] == 1

    def test_list_narrow(self):
        parsed = json.loads("[1, 2, 3]")
        if isinstance(parsed, list):
            assert len(parsed) == 3
            assert parsed[0] == 1

    def test_nested_narrow(self):
        parsed = json.loads('{"outer": [{"inner": "x"}]}')
        if isinstance(parsed, dict):
            outer = parsed["outer"]
            if isinstance(outer, list):
                item = outer[0]
                if isinstance(item, dict):
                    assert item["inner"] == "x"

    def test_primitive_narrow(self):
        parsed = json.loads("42")
        if isinstance(parsed, int):
            assert parsed == 42

    def test_null_narrow(self):
        parsed = json.loads("null")
        assert parsed is None

    def test_bool_narrow(self):
        parsed = json.loads("true")
        if isinstance(parsed, bool):
            assert parsed is True


# ============================================================================
# check_type — 递归 JSON 形状校验
# ============================================================================

class TestCheckTypeBasics:
    """基础类型检查。"""

    def test_str(self):
        assert check_type("hello", str)
        assert not check_type(42, str)

    def test_int(self):
        assert check_type(42, int)
        assert not check_type("42", int)

    def test_float(self):
        assert check_type(3.14, float)
        # int 可作为 float 通过（JSON round-trip 兼容），见 TestCheckTypeIntToFloatCompat
        assert check_type(42, float)
        assert not check_type("42", float)

    def test_bool(self):
        assert check_type(True, bool)
        assert not check_type(1, bool)

    def test_none(self):
        assert check_type(None, type(None))
        assert not check_type(0, type(None))

    def test_list_raw(self):
        assert check_type([1, 2], list[Any])
        assert not check_type("not_list", list[Any])

    def test_dict_raw(self):
        assert check_type({"a": 1}, dict[str, Any])
        assert not check_type([], dict[str, Any])


class TestCheckTypeGenerics:
    """泛型递归检查。"""

    def test_list_str_pass(self):
        assert check_type(["a", "b", "c"], list[str])

    def test_list_str_fail(self):
        assert not check_type(["a", 1, "c"], list[str])

    def test_list_list_int_pass(self):
        assert check_type([[1, 2], [3, 4]], list[list[int]])

    def test_list_list_int_fail(self):
        assert not check_type([[1, 2], [3, "x"]], list[list[int]])

    def test_dict_str_int_pass(self):
        assert check_type({"a": 1, "b": 2}, dict[str, int])

    def test_dict_str_int_fail_value(self):
        assert not check_type({"a": 1, "b": "x"}, dict[str, int])

    def test_dict_str_int_fail_key(self):
        assert not check_type({1: "x"}, dict[str, int])  # type: ignore[arg-type]

    def test_empty_list_matches(self):
        assert check_type([], list[int])

    def test_empty_dict_matches(self):
        assert check_type({}, dict[str, int])


class TestCheckTypeTerminal:
    """JsonValue / Any 终端短路。"""

    def test_any_terminal(self):
        from typing import Any
        assert check_type([1, "x", None], Any)
        assert check_type({"nested": [1, 2]}, Any)
        assert check_type(42, Any)

    def test_jsonvalue_terminal(self):
        from mutio.codec.json import JsonValue
        assert check_type(42, JsonValue)
        assert check_type([1, "two", None], JsonValue)
        assert check_type({"nested": [1, 2]}, JsonValue)

    def test_dict_str_any(self):
        from typing import Any
        assert check_type({"ns1": {"cmd": "python"}, "ns2": {"url": "http://x"}}, dict[str, Any])

    def test_dict_str_any_fail(self):
        from typing import Any
        assert not check_type("not_a_dict", dict[str, Any])


class TestCheckTypeJsonValueContainers:
    """list[JsonObject] / list[JsonArray] 形状检测。"""

    def test_list_jsonvalue(self):
        from mutio.codec.json import JsonValue
        assert check_type([1, "two", None, {"k": "v"}], list[JsonValue])

    def test_list_jsonobject_pass(self):
        from mutio.codec.json import JsonObject
        assert check_type([{"a": 1}, {"b": "x"}], list[JsonObject])

    def test_list_jsonobject_fail(self):
        from mutio.codec.json import JsonObject
        assert not check_type([{"a": 1}, "not_dict"], list[JsonObject])

    def test_list_jsonarray_pass(self):
        from mutio.codec.json import JsonArray
        assert check_type([[1, 2], [3, 4]], list[JsonArray])

    def test_list_jsonarray_fail(self):
        from mutio.codec.json import JsonArray
        assert not check_type([[1, 2], "not_list"], list[JsonArray])

    def test_dict_jsonvalue(self):
        from mutio.codec.json import JsonValue
        assert check_type({"k": [1, "x"]}, dict[str, JsonValue])


# ============================================================================
# check_type — int → float 兼容（JSON round-trip）
# ============================================================================


class TestCheckTypeIntToFloatCompat:
    """int → float 兼容：JSON number round-trip 时 int 可无损当 float 用。"""

    def test_int_accepted_as_float(self):
        """dataclass float 字段默认值 int 0 应被接受。"""
        assert check_type(0, float)
        assert check_type(1, float)
        assert check_type(-1, float)

    def test_float_still_passes(self):
        """原 float 检查不受影响。"""
        assert check_type(0.0, float)
        assert check_type(3.14, float)

    def test_float_not_accepted_as_int(self):
        """反向不兼容：float 不接受为 int（有信息丢失风险）。"""
        assert not check_type(0.0, int)
        assert not check_type(1.5, int)

    def test_nested_list_int_to_float(self):
        """容器内递归生效。"""
        assert check_type([0, 1, 2], list[float])

    def test_nested_dict_int_to_float(self):
        """dict 值递归生效。"""
        assert check_type({"dur": 0, "ts": 1}, dict[str, float])

    def test_get_as_int_for_float_field(self):
        """get_field 取 float 字段时 int 值通过。"""
        assert json.get_field({"duration": 0}, "duration", float) == 0


# ============================================================================
# get_field / get_element / narrow_value
# ============================================================================

class TestGetField:
    def test_required_field_passes(self):
        assert get_field({"code": 200}, "code", int) == 200

    def test_missing_key_raises(self):
        with pytest.raises(KeyError, match="missing"):
            get_field({}, "missing", int)

    def test_non_dict_without_default_raises_keyerror(self):
        with pytest.raises(KeyError, match="name"):
            get_field([], "name", str)  # type: ignore[arg-type]

    def test_missing_key_returns_default(self):
        assert get_field({}, "missing", int, default=-1) == -1

    def test_non_dict_returns_default(self):
        assert get_field([], "missing", int, default=-1) == -1  # type: ignore[arg-type]

    def test_type_mismatch_raises(self):
        with pytest.raises(TypeError, match=r"Key 'code': expected.*int"):
            get_field({"code": "err"}, "code", int)

    def test_present_none_is_type_mismatch(self):
        """值为 None 且未提供 default 时仍抛 TypeError。"""
        with pytest.raises(TypeError, match=r"Key 'name': expected.*str"):
            get_field({"name": None}, "name", str)

    def test_null_value_falls_back_to_default(self):
        """JSON null（None）有 default 时回退到 default，与 key 缺失行为一致。"""
        assert get_field({"content": None}, "content", str, default="") == ""
        assert get_field({"finish_reason": None}, "finish_reason", str, default="") == ""

    def test_null_value_with_optional_type_keeps_none(self):
        """typ 包含 None 时不触发 default 回退，保持原值 None。"""
        assert get_field({"name": None}, "name", str | None) is None
        assert get_field({"name": None}, "name", str | None, default="fallback") is None

    def test_null_value_default_and_fallback_together(self):
        """值为 None 时优先走 default 而非 fallback。"""
        assert get_field({"hint": None}, "hint", str, default="d", fallback="f") == "d"

    def test_fallback_on_type_mismatch(self):
        assert get_field({"hint": 1}, "hint", str, fallback="") == ""

    def test_default_and_fallback_split_missing_vs_type_error(self):
        assert get_field({}, "hint", str, default="default", fallback="fallback") == "default"
        assert get_field({"hint": 1}, "hint", str, default="default", fallback="fallback") == "fallback"

    def test_generic_list_str_pass(self):
        default_names: list[str] = []
        assert get_field({"mm": ["a", "b"]}, "mm", list[str], default=default_names) == ["a", "b"]

    def test_generic_list_str_fail_raises(self):
        default_names: list[str] = []
        with pytest.raises(TypeError, match=r"Key 'mm': expected.*list\[str\]"):
            get_field({"mm": ["a", 1]}, "mm", list[str], default=default_names)

    def test_loads_result_can_be_used_directly(self):
        parsed = json.loads('{"model": "gpt-4"}')
        assert get_field(parsed, "model", str, default="") == "gpt-4"


class TestGetElement:
    def test_required_element_passes(self):
        assert get_element(["a", "b"], 0, str) == "a"

    def test_out_of_range_raises(self):
        with pytest.raises(IndexError):
            get_element([], 0, str)

    def test_non_list_without_default_raises_indexerror(self):
        with pytest.raises(IndexError, match="expected list"):
            get_element({}, 0, str)  # type: ignore[arg-type]

    def test_out_of_range_returns_default(self):
        assert get_element([], 0, str, default="fallback") == "fallback"

    def test_non_list_returns_default(self):
        assert get_element({}, 0, str, default="fallback") == "fallback"  # type: ignore[arg-type]

    def test_type_mismatch_raises(self):
        with pytest.raises(TypeError, match=r"Index 0: expected.*str"):
            get_element([1], 0, str)

    def test_fallback_on_type_mismatch(self):
        assert get_element([1], 0, str, fallback="") == ""

    def test_default_and_fallback_split_bounds_vs_type_error(self):
        assert get_element([], 0, str, default="default", fallback="fallback") == "default"
        assert get_element([1], 0, str, default="default", fallback="fallback") == "fallback"

    def test_negative_index_supported(self):
        assert get_element(["a", "b"], -1, str) == "b"

    def test_generic_jsonobject_passes(self):
        default_obj: JsonObject = {}
        assert get_element([{"name": "x"}], 0, JsonObject, default=default_obj) == {"name": "x"}


class TestNarrowValue:
    def test_strict_pass(self):
        assert narrow_value("hello", str) == "hello"

    def test_generic_pass(self):
        assert narrow_value({"name": "x"}, JsonObject) == {"name": "x"}

    def test_type_mismatch_raises(self):
        with pytest.raises(TypeError, match=r"Expected.*str"):
            narrow_value(1, str)

    def test_fallback_returns_value(self):
        assert narrow_value(1, str, fallback="") == ""

    def test_union_narrowing(self):
        assert narrow_value(None, str | None) is None


# ============================================================================
# default=[] / default={} 在不同上下文中的推断一致性
# ============================================================================
#
# 行为矩阵：pyright 对 GenericAlias (JsonObject, list[JsonObject] 等) 作为 typ 参数
# 时，T 推断路径在有无「上下文类型约束」下不一致。
#
# ┌───────────────────┬────────────────┬───────────────────────┐
# │ 上下文             │ typ 解析路径    │ default={}/[] 结果      │
# ├───────────────────┼────────────────┼───────────────────────┤
# │ 无约束赋值          │ typ → T ✅     │ {} → dict[JsonValue] ✅ │
# │ 显式变量注解        │ default → T ❌ │ {} → dict[Unknown]  ❌ │
# │ return 类型约束     │ default → T ❌ │ {} → dict[Unknown]  ❌ │
# │ __init__ kwargs    │ default → T ❌ │ {} → dict[Unknown]  ❌ │
# └───────────────────┴────────────────┴───────────────────────┘
#
# 根因：pyright overload 解析中 GenericAlias 同时匹配 type[T] 和
# type[Any] (来自 _TypeExpr)。无上下文时优先 type[T]，有上下文双向
# 推断时优先 type[Any]（精确匹配），T 从 default 反推。
#
# 以下测试按 import 方式分两组：直接 import (get_field) 和模块访问
# (json.get_field)，分别覆盖四种上下文。预期：无约束赋值通过，三种
# 约束上下文失败（已知 pyright 限制）。

from dataclasses import dataclass, field as dc_field


# ── 辅助类型 ───────────────────────────────────────────

@dataclass
class _WithJsonFields:
    """模拟 __init__ kwargs 上下文."""

    obj: JsonObject = dc_field(default_factory=JsonObject)
    arr: list[JsonObject] = dc_field(default_factory=lambda: list[JsonObject]())


def _return_obj(data: JsonValue, key: str) -> JsonObject:
    """return 语句上下文 — 预期应通过."""
    return get_field(data, key, JsonObject, default={})


def _return_arr(data: JsonValue, key: str) -> list[JsonObject]:
    """return 语句上下文 — 预期应通过."""
    return get_field(data, key, list[JsonObject], default=[])


# ── 直接 import get_field ──────────────────────────────

def test_no_context_direct_import():
    """无约束赋值 — 通过."""
    data: JsonObject = {"obj": {"a": 1}, "arr": [{"b": 2}]}
    obj = get_field(data, "obj", JsonObject, default={})
    arr = get_field(data, "arr", list[JsonObject], default=[])
    assert obj == {"a": 1}
    assert arr == [{"b": 2}]
    assert_type(obj, JsonObject)
    assert_type(arr, list[JsonObject])


def test_annotation_fails_direct_import():
    """显式注解 — API 修复后预期应通过."""
    data: JsonObject = {"obj": {"a": 1}}
    # 去掉注解后通过
    obj = get_field(data, "obj", JsonObject, default={})
    assert_type(obj, JsonObject)
    # API 修复后，以下写法应通过：
    obj2: JsonObject = get_field(data, "obj", JsonObject, default={})
    assert_type(obj2, JsonObject)


def test_return_fails_direct_import():
    """return 类型约束 — API 修复后预期应通过."""
    data: JsonObject = {"obj": {"a": 1}, "arr": [{"b": 2}]}
    r1 = _return_obj(data, "obj")
    r2 = _return_arr(data, "arr")
    assert r1 == {"a": 1}
    assert r2 == [{"b": 2}]


def test_kwargs_fails_direct_import():
    """__init__ kwargs — API 修复后预期应通过."""
    data: JsonObject = {"obj": {"a": 1}, "arr": [{"b": 2}]}
    w = _WithJsonFields(
        obj=get_field(data, "obj", JsonObject, default={}),
        arr=get_field(data, "arr", list[JsonObject], default=[]),
    )
    assert w.obj == {"a": 1}
    assert w.arr == [{"b": 2}]
    # ❌ w = _WithJsonFields(obj=get_field(data, "obj", JsonObject, default={}))


# ── 模块访问 json.get_field ────────────────────────────
# (_client_impl.py 使用 from mutio.codec import json → json.get_field)

def test_no_context_module_access():
    """无约束赋值 (模块访问) — 预期通过."""
    data: JsonObject = {"obj": {"a": 1}, "arr": [{"b": 2}]}
    obj = json.get_field(data, "obj", JsonObject, default={})
    arr = json.get_field(data, "arr", list[JsonObject], default=[])
    assert obj == {"a": 1}
    assert arr == [{"b": 2}]
    assert_type(obj, JsonObject)
    assert_type(arr, list[JsonObject])


def test_annotation_fails_module_access():
    """显式注解 (模块访问) — API 修复后预期应通过."""
    data: JsonObject = {"obj": {"a": 1}}
    # 去掉注解后通过
    obj = json.get_field(data, "obj", JsonObject, default={})
    assert_type(obj, JsonObject)
    # API 修复后，以下写法应通过：
    obj2: JsonObject = json.get_field(data, "obj", JsonObject, default={})
    assert_type(obj2, JsonObject)


def test_return_fails_module_access():
    """return 类型约束 (模块访问) — API 修复后预期应通过."""
    data: JsonObject = {"arr": [{"b": 2}]}

    # 在函数内直接用 json.get_field 的 return 上下文（模拟 @impl 函数）
    def _return_list_from_module(data2: JsonValue) -> list[JsonObject]:
        return json.get_field(data2, "arr", list[JsonObject], default=[])

    result = _return_list_from_module(data)
    assert result == [{"b": 2}]


# ---------------------------------------------------------------------------
# check_type 边界场景
# ---------------------------------------------------------------------------


class TestCheckTypeEdgeCases:
    """check_type 的覆盖边界：float-int 兼容、泛型 origin 路径。"""

    def test_int_compatible_with_float(self):
        """JSON round-trip 兼容：int 值可赋给 float 字段。"""
        assert check_type(0, float) is True
        assert check_type(42, float) is True
        assert check_type(-1, float) is True

    def test_float_not_compatible_with_int(self):
        """逆向：float 不能赋给 int。"""
        assert check_type(3.14, int) is False

    def test_generic_origin_with_args(self):
        """泛型类型（如 Tuple[int, ...]）走 isinstance(origin) 路径。

        注意：当前实现仅对 list/dict 做递归元素检查，其他泛型（tuple 等）
        只检查 isinstance(value, origin)，不深入验证元素类型。
        """
        import typing
        # 通过了 isinstance 检查（是 tuple）→ True
        assert check_type((1, 2), typing.Tuple[int, ...]) is True
        # 不是 tuple → False
        assert check_type([1, 2], typing.Tuple[int, ...]) is False

    def test_generic_origin_no_args(self):
        """无 type args 的泛型（如 bare list）。"""
        assert check_type([1, 2], list) is True
        assert check_type("not list", list) is False
