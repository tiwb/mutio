"""Python 类型注解 → JSON Schema 片段。

核心函数 annotation_to_json_schema 将 Python annotation 映射为 JSON Schema
片段（含 type/enum/items/additionalProperties）。

输出的片段遵循 JSON Schema 规范（https://json-schema.org/），可被 MCP、
OpenAPI 等协议的 tool / endpoint 描述直接使用。

本模块不绑定任何具体协议。
"""

from __future__ import annotations

import typing
from typing import Any, cast


# ---------------------------------------------------------------------------
# 基本类型映射
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Python 类型注解 → JSON Schema 片段。

    不包含参数名、默认值、description——这些由调用方从 FunctionInfo 自行组装。

    Returns:
        JSON Schema 片段。可能含 type/enum/items/additionalProperties。
        不识别的类型返回空 dict（不报错，降级为 untyped）。
    """
    if annotation is None or annotation is type(None):
        return {"type": "null"}
    if annotation is Any:
        return {}
    # 基本类型
    if annotation in _TYPE_MAP:
        return {"type": _TYPE_MAP[annotation]}

    # 裸容器 list / dict（无参）
    if annotation is list:
        return {"type": "array"}
    if annotation is dict:
        return {"type": "object"}

    origin = _get_origin(annotation)
    args = typing.get_args(annotation)

    # Literal[...]
    lit_args = _get_literal_args(annotation)
    if lit_args is not None:
        return _literal_to_schema(lit_args)

    # Optional / Union
    if origin is typing.Union or origin is _types_union():
        non_none = [a for a in args if a is not type(None)]
        has_null = len(non_none) != len(args)
        if len(non_none) == 1:
            inner = annotation_to_json_schema(non_none[0])
            if has_null:
                return _add_null_type(inner)
            return inner
        # 多分支 Union：合并成 type 列表（仅当各分支都是简单 type 时）
        sub_schemas = [annotation_to_json_schema(a) for a in non_none]
        types: list[str] = []
        for s in sub_schemas:
            t = s.get("type")
            if isinstance(t, str):
                types.append(t)
            else:
                types = []  # 放弃合并
                break
        if types:
            if has_null:
                types.append("null")
            return {"type": types}
        # 复杂 Union 退化为空（不报错）
        return {}

    # list[T]
    if origin is list:
        if args:
            return {"type": "array", "items": annotation_to_json_schema(args[0])}
        return {"type": "array"}

    # dict[K, V]
    if origin is dict:
        if len(args) == 2:
            return {"type": "object", "additionalProperties": annotation_to_json_schema(args[1])}
        return {"type": "object"}

    # 不识别 → 空 schema
    return {}


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _literal_to_schema(args: tuple[Any, ...]) -> dict[str, Any]:
    """Literal[...] → enum；同类型时附 type。"""
    types: set[type[Any]] = {type(a) for a in args}
    schema: dict[str, Any] = {"enum": list(args)}
    if len(types) == 1:
        t = next(iter(types))
        if t in _TYPE_MAP:
            schema["type"] = _TYPE_MAP[t]
        elif t is type(None):
            schema["type"] = "null"
    return schema


def _add_null_type(schema: dict[str, Any]) -> dict[str, Any]:
    """在 schema 上追加 null 类型支持。"""
    out = dict(schema)
    t = out.get("type")
    if t is None:
        out["type"] = "null"
    elif isinstance(t, str):
        out["type"] = [t, "null"]
    elif isinstance(t, list) and "null" not in t:
        out["type"] = [*cast(list[Any], t), "null"]
    return out


def _get_origin(annotation: Any) -> Any:
    if annotation is None:
        return None
    return typing.get_origin(annotation)


def _get_literal_args(annotation: Any) -> tuple[Any, ...] | None:
    """如果 annotation 是 Literal[...]，返回 args；否则 None。"""
    if annotation is None:
        return None
    if typing.get_origin(annotation) is typing.Literal:
        return typing.get_args(annotation)
    return None


def _types_union() -> Any:
    """返回 PEP 604 union 的 origin（types.UnionType），3.10+ 可用。"""
    try:
        import types
        return types.UnionType
    except AttributeError:
        return None
