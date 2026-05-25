"""测试 mutio.schema.funcinfo — extract_function_info / ParamInfo / FunctionInfo。

覆盖点：
- 基本参数提取（类型注解、默认值、无注解）
- self/cls 跳过
- ParamInfo 派生属性（is_optional / is_literal / is_list / is_dict）
- 多参数顺序保持
- doc 覆盖模式
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import pytest

from mutio.schema.funcinfo import (
    FunctionInfo,
    ParamInfo,
    extract_function_info,
)


# ---------------------------------------------------------------------------
# extract_function_info：基本参数提取
# ---------------------------------------------------------------------------


class TestExtractBasic:
    def test_simple_params(self):
        def f(a: str, b: int, c: float = 3.14) -> None: ...

        info = extract_function_info(f)
        assert info.name == "f"
        assert info.param_order == ["a", "b", "c"]

        pa = info.params["a"]
        assert pa.annotation is str
        assert pa.has_annotation is True
        assert pa.has_default is False
        assert pa.default is None

        pb = info.params["b"]
        assert pb.annotation is int
        assert pb.has_default is False

        pc = info.params["c"]
        assert pc.annotation is float
        assert pc.has_default is True
        assert pc.default == 3.14

    def test_no_annotation(self):
        def f(x, y="hello") -> None: ...

        info = extract_function_info(f)
        px = info.params["x"]
        assert px.annotation is None
        assert px.has_annotation is False
        assert px.has_default is False

        py = info.params["y"]
        assert py.annotation is None
        assert py.has_default is True
        assert py.default == "hello"

    def test_self_cls_skipped(self):
        class C:
            def m(self, x: int, y: str) -> None: ...
            @classmethod
            def cm(cls, z: float) -> None: ...

        info = extract_function_info(C().m)
        assert "self" not in info.params
        assert "x" in info.params

        info_cls = extract_function_info(C.cm)
        assert "cls" not in info_cls.params
        assert "z" in info_cls.params

    def test_param_order_preserved(self):
        def f(d: int, a: str, c: float, b: bool) -> None: ...

        info = extract_function_info(f)
        assert info.param_order == ["d", "a", "c", "b"]


# ---------------------------------------------------------------------------
# ParamInfo 派生属性
# ---------------------------------------------------------------------------


class TestParamInfoProperties:
    def test_is_optional(self):
        def f(x: str | None, y: Optional[int], z: str) -> None: ...

        info = extract_function_info(f)
        assert info.params["x"].is_optional is True
        assert info.params["y"].is_optional is True
        assert info.params["z"].is_optional is False

    def test_is_optional_none_type(self):
        def f(x: None) -> None: ...

        info = extract_function_info(f)
        assert info.params["x"].is_optional is True

    def test_is_literal(self):
        def f(mode: Literal["a", "b"], x: str, y: Literal[1, 2]) -> None: ...

        info = extract_function_info(f)
        assert info.params["mode"].is_literal is True
        assert info.params["x"].is_literal is False
        assert info.params["y"].is_literal is True

    def test_is_list(self):
        def f(xs: list[str], bare: list, s: str) -> None: ...

        info = extract_function_info(f)
        assert info.params["xs"].is_list is True
        assert info.params["bare"].is_list is True
        assert info.params["s"].is_list is False

    def test_is_dict(self):
        def f(d: dict[str, int], bare: dict, s: str) -> None: ...

        info = extract_function_info(f)
        assert info.params["d"].is_dict is True
        assert info.params["bare"].is_dict is True
        assert info.params["s"].is_dict is False

    def test_no_annotation_properties_are_false(self):
        def f(x) -> None: ...

        info = extract_function_info(f)
        p = info.params["x"]
        assert p.is_optional is False
        assert p.is_literal is False
        assert p.is_list is False
        assert p.is_dict is False


# ---------------------------------------------------------------------------
# docstring 集成
# ---------------------------------------------------------------------------


class TestDocstringIntegration:
    def test_description_extracted(self):
        def f(x: int) -> None:
            """Run the thing.

            Args:
                x: number.
            """
        info = extract_function_info(f)
        assert info.description == "Run the thing."

    def test_args_descriptions(self):
        def f(x: int, y: str) -> None:
            """T.

            Args:
                x: an integer.
                y: a string.
            """
        info = extract_function_info(f)
        assert info.params["x"].description == "an integer."
        assert info.params["y"].description == "a string."

    def test_no_docstring(self):
        def f(x: int) -> None: ...

        info = extract_function_info(f)
        assert info.description == ""
        assert info.params["x"].description == ""

    def test_doc_override(self):
        def f() -> None:
            """Replaced."""
        info = extract_function_info(f, doc="Original text.\n\nArgs:\n    x: desc.")
        assert info.description == "Original text."
        assert info.params == {}  # no parameters in signature


# ---------------------------------------------------------------------------
# Any 类型
# ---------------------------------------------------------------------------


class TestAnyType:
    def test_any_annotation(self):
        def f(x: Any) -> None: ...

        info = extract_function_info(f)
        assert info.params["x"].annotation is Any
        assert info.params["x"].has_annotation is True
