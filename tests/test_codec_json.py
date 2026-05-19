"""mutio.codec.json — JSON 类型安全工具 测试。"""

from __future__ import annotations

from typing import Any

import pytest

from mutio.codec import json
from mutio.codec.json import JsonObject, JsonValue


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
