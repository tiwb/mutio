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

"""

from __future__ import annotations

import json as _stdjson
import typing
from collections.abc import Mapping, Sequence
from types import UnionType
from typing import Union, Any, Callable, ForwardRef, TypeAlias, TypeVar, get_args, get_origin, overload
from typing_extensions import TypeForm

__all__ = [
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    "JsonArray",
    "get_field",
    "get_element",
    "narrow_value",
    "check_type",
    "loads",
    "dumps",
    "JSONDecodeError",
    "JSONDecoder",
    "JSONEncoder",
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
# 联合体用协变的 Mapping/Sequence，这样 list[dict[str, JsonValue]] ⊆ JsonValue（绕过 list 不变性）
JsonValue: TypeAlias = JsonPrimitive | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
# 便捷别名保持 dict/list，保证构建侧可原地修改
JsonObject: TypeAlias = dict[str, JsonValue]
JsonArray: TypeAlias = list[JsonValue]


# ---------------------------------------------------------------------------
# narrowing helpers — 从 JsonValue 安全提取/收窄指定类型
# ---------------------------------------------------------------------------

T = TypeVar("T")
_MISSING = object()


@overload
def get_field(obj: JsonValue, key: str, typ: TypeForm[T], /) -> T: ...


@overload
def get_field(obj: JsonValue, key: str, typ: TypeForm[T], /, *, default: T) -> T: ...


@overload
def get_field(obj: JsonValue, key: str, typ: TypeForm[T], /, *, fallback: T) -> T: ...


@overload
def get_field(obj: JsonValue, key: str, typ: TypeForm[T], /, *, default: T, fallback: T) -> T: ...


def get_field(
    obj: JsonValue,
    key: str,
    typ: TypeForm[T],
    /,
    *,
    default: T = _MISSING,
    fallback: T = _MISSING,
) -> T:
    """从 JSON 值按字段取值，并在返回前做递归类型窄化。

    - ``obj`` 不是 dict 或 ``key`` 缺失时：返回 ``default`` 或抛 ``KeyError``
    - 命中字段但类型不匹配时：返回 ``fallback`` 或抛 ``TypeError``
    """
    if not isinstance(obj, dict) or key not in obj:
        if default is not _MISSING:
            return default
        raise KeyError(key)
    value = obj[key]
    # JSON API 常见 null 语义等同于"无值"：值为 None 且期望类型不包含 None
    # 时回退到 default（与 key 缺失一致），而非抛 TypeError
    if value is None and default is not _MISSING and not check_type(None, typ):
        return default
    return _coerce_value(value, typ, prefix=f"Key {key!r}", fallback=fallback)


@overload
def get_element(arr: JsonValue, index: int, typ: TypeForm[T], /) -> T: ...


@overload
def get_element(arr: JsonValue, index: int, typ: TypeForm[T], /, *, default: T) -> T: ...


@overload
def get_element(arr: JsonValue, index: int, typ: TypeForm[T], /, *, fallback: T) -> T: ...


@overload
def get_element(arr: JsonValue, index: int, typ: TypeForm[T], /, *, default: T, fallback: T) -> T: ...


def get_element(
    arr: JsonValue,
    index: int,
    typ: TypeForm[T],
    /,
    *,
    default: T = _MISSING,
    fallback: T = _MISSING,
) -> T:
    """从 JSON 值按索引取元素，并在返回前做递归类型窄化。"""
    if not isinstance(arr, list):
        if default is not _MISSING:
            return default
        raise IndexError(f"Index {index}: expected list, got {type(arr).__name__}")
    try:
        value = arr[index]
    except IndexError:
        if default is not _MISSING:
            return default
        raise
    return _coerce_value(value, typ, prefix=f"Index {index}", fallback=fallback)


@overload
def narrow_value(value: JsonValue, typ: TypeForm[T], /) -> T: ...

@overload
def narrow_value(value: JsonValue, typ: TypeForm[T], /, *, fallback: T) -> T: ...

def narrow_value(
    value: JsonValue,
    typ: TypeForm[T],
    /,
    *,
    fallback: T = _MISSING,
) -> T:
    """对已有 ``JsonValue`` 做递归类型窄化。"""
    return _coerce_value(value, typ, prefix=None, fallback=fallback)


def _coerce_value(
    value: JsonValue,
    typ: TypeForm[T],
    /,
    *,
    prefix: str | None,
    fallback: T = _MISSING,
) -> T:
    if check_type(value, typ):
        return typing.cast(T, value)
    if fallback is not _MISSING:
        return fallback
    if prefix is None:
        raise TypeError(f"Expected {typ}, got {type(value).__name__}")
    else:
        raise TypeError(f"{prefix}: expected {typ}, got {type(value).__name__}")



def check_type(value: JsonValue, typ: TypeForm[Any], /) -> bool:
    """递归检查 JSON 值是否匹配指定类型结构（shape）。

    前设：所有 value 天然是 JsonValue。

    支持基础类型（str, int, float, bool, NoneType）、容器泛型
    （list[X], dict[K, V]）、Union。JsonValue / Any 作为终端类型
    直接通过，ForwardRef 和字符串前向引用宽容通过。

    适用场景：
    - 从 JSON 配置中提取值后验证结构
    - 运行时类型守卫，确保数据符合预期 shape

    示例::

        check_type(["a", "b"], list[str])          # True
        check_type(["a", 1], list[str])            # False
        check_type({"a": 1}, dict[str, Any])       # True
        check_type(42, JsonValue)                  # True（终端短路）
    """
    # 终端类型
    if typ is JsonValue or typ is Any:  # pyright: ignore[reportUnnecessaryComparison]
        return True
    # 无法执行的类型检查：ForwardRef / 字符串前向引用
    if isinstance(typ, (ForwardRef, str)):
        return True
    origin = get_origin(typ)
    if origin is not None:
        if origin in (Union, UnionType):
            return any(check_type(value, arg) for arg in get_args(typ))
        args = get_args(typ)
        # 用字面量 isinstance 以便 pyright 窄化（origin 是变量时 pyright 无法窄化）
        if origin is list:
            if not isinstance(value, list):
                return False
            return all(check_type(v, args[0]) for v in value) if args else True
        if origin is dict:
            if not isinstance(value, dict):
                return False
            return all(
                check_type(k, args[0]) and check_type(v, args[1])
                for k, v in value.items()
            ) if args else True
        if not isinstance(value, origin):
            return False
        if not args:
            return True
        return True
    # 兼容 JSON round-trip: int 值可无损赋给 float 字段
    # （如 dataclass float 字段默认值为 int 字面量 0，json.dumps 输出 "0"，
    #  json.loads 读回 int 0，无法通过 float isinstance 检查）
    if typ is float and isinstance(value, int):
        return True
    return isinstance(value, typing.cast(type[Any], typ))


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
