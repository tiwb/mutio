"""JSON 类型安全工具 — 递归类型别名 + loads/dumps 包装。

提供完整的 JSON 类型描述，让 pyright 在严格模式下能正确推导
`json.loads` 的结果，消除 `reportUnknown*` 类错误。

`loads` / `dumps` 签名与标准库 `json.loads` / `json.dumps` 完全一致，
可直接替换 `import json` → `from mutio.codec import json`，调用处不变。

典型用法::

    from mutio.codec import json

    parsed = json.loads(raw)           # JsonValue（而非 Any）
    if isinstance(parsed, dict):
        value = parsed.get("key")      # JsonValue | None
        if isinstance(value, list):
            for item in value:         # item: JsonValue
                ...

类型别名采用 `TypeAlias` + 字符串前向引用（PEP 613 + PEP 563），
兼容 Python 3.11+。升级到 3.12+ 时可平滑迁移到 PEP 695 `type` 语句。

为什么不放在 `mutio/mcp/`：`net/_server_impl.py` 也是消费者，归入 `mcp/`
会让 `net` 反向依赖 `mcp`，破坏现有依赖方向。
"""

from __future__ import annotations

import json as _stdjson
from typing import Any, Callable, TypeAlias

__all__ = [
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    "JsonArray",
    "loads",
    "dumps",
]

# 透传标准库类型/异常，让 `from mutio.codec import json` 的调用方
# 仍能用 `json.JSONDecodeError` / `json.JSONDecoder` / `json.JSONEncoder`
JSONDecodeError = _stdjson.JSONDecodeError
JSONDecoder = _stdjson.JSONDecoder
JSONEncoder = _stdjson.JSONEncoder


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
JsonArray: TypeAlias = list[JsonValue]


# ---------------------------------------------------------------------------
# loads / dumps 包装
# ---------------------------------------------------------------------------

def loads(
    s: str | bytes | bytearray,
    *,
    cls: type[_stdjson.JSONDecoder] | None = None,
    object_hook: Callable[[dict[Any, Any]], Any] | None = None,
    parse_float: Callable[[str], Any] | None = None,
    parse_int: Callable[[str], Any] | None = None,
    parse_constant: Callable[[str], Any] | None = None,
    object_pairs_hook: Callable[[list[tuple[Any, Any]]], Any] | None = None,
    **kw: Any,
) -> JsonValue:
    """解析 JSON 文本/字节串，返回 `JsonValue`。

    签名与 `json.loads` 完全一致，区别仅在于返回值类型。
    异常透传：解析失败直接抛 `json.JSONDecodeError`。
    """
    return _stdjson.loads(
        s,
        cls=cls,
        object_hook=object_hook,
        parse_float=parse_float,
        parse_int=parse_int,
        parse_constant=parse_constant,
        object_pairs_hook=object_pairs_hook,
        **kw,
    )


def dumps(
    obj: JsonValue,
    *,
    skipkeys: bool = False,
    ensure_ascii: bool = False,
    check_circular: bool = True,
    allow_nan: bool = True,
    cls: type[_stdjson.JSONEncoder] | None = None,
    indent: int | str | None = None,
    separators: tuple[str, str] | None = None,
    default: Callable[[Any], Any] | None = None,
    sort_keys: bool = False,
    **kw: Any,
) -> str:
    """序列化 JSON 值为字符串。

    签名与 `json.dumps` 完全一致，区别仅在于入参 `obj` 约束为 `JsonValue`，
    且 `ensure_ascii` 默认为 `False`（mutio 全栈处理 Unicode）。
    """
    return _stdjson.dumps(
        obj,
        skipkeys=skipkeys,
        ensure_ascii=ensure_ascii,
        check_circular=check_circular,
        allow_nan=allow_nan,
        cls=cls,
        indent=indent,
        separators=separators,
        default=default,
        sort_keys=sort_keys,
        **kw,
    )
