"""FunctionInfo / ParamInfo — Python 函数的结构化接口描述。

不绑定任何具体协议，是 mcp tool schema、mutagent toolkit、rpc、openapi 等
"暴露 Python 函数给外部协议" 场景的共同祖先。
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import typing
from typing import Any, Callable

from mutio.schema.docstring import parse_google_args, extract_description


@dataclass
class ParamInfo:
    """单个参数的结构化信息。"""

    annotation: Any | None          # 原始 Python annotation（None = 无类型注解）
    has_annotation: bool
    has_default: bool
    default: Any                    # 默认值（仅 has_default=True 时有效）
    description: str                # 参数描述（来自 docstring Args 段，无则为空）

    @property
    def is_optional(self) -> bool:
        """annotation 含 None 分支。"""
        if self.annotation is None:
            return False
        if self.annotation is type(None):
            return True
        origin = typing.get_origin(self.annotation)
        if origin is typing.Union or origin is _types_union():
            return any(a is type(None) for a in typing.get_args(self.annotation))
        return False

    @property
    def is_literal(self) -> bool:
        """annotation 是 Literal[...]。"""
        if self.annotation is None:
            return False
        return typing.get_origin(self.annotation) is typing.Literal

    @property
    def is_list(self) -> bool:
        """annotation 是 list / list[T]。"""
        if self.annotation is None:
            return False
        return self.annotation is list or typing.get_origin(self.annotation) is list

    @property
    def is_dict(self) -> bool:
        """annotation 是 dict / dict[K, V]。"""
        if self.annotation is None:
            return False
        return self.annotation is dict or typing.get_origin(self.annotation) is dict


@dataclass
class FunctionInfo:
    """从 Python 函数提取的结构化接口描述。"""

    name: str                       # 函数名
    description: str                # docstring 主描述（首段到第一个段头之前）
    params: dict[str, ParamInfo]    # 参数名 → 参数信息
    param_order: list[str]          # 参数顺序（与签名一致）


def extract_function_info(fn: Callable[..., Any], *, doc: str | None = None) -> FunctionInfo:
    """从 Python 函数提取结构化接口描述。

    组合 inspect.signature + Google-style docstring 解析。

    Args:
        fn: 待提取的函数
        doc: 可选 docstring 覆盖。为 None 时取 inspect.getdoc(fn)。
            用于 @impl 覆盖后仍需取原始声明 docstring 的场景。
    """
    name = getattr(fn, '__name__', 'unknown')

    # docstring
    if doc is None:
        raw_doc = inspect.getdoc(fn) or ""
    else:
        raw_doc = inspect.cleandoc(doc)

    description = extract_description(raw_doc)
    args_descriptions = parse_google_args(raw_doc)

    # signature
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    params: dict[str, ParamInfo] = {}
    param_order: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue

        param_order.append(pname)

        # annotation
        if pname in hints:
            annotation = hints[pname]
            has_annotation = True
        elif param.annotation is not inspect.Parameter.empty:
            annotation = param.annotation
            has_annotation = True
        else:
            annotation = None
            has_annotation = False

        # default
        has_default = param.default is not inspect.Parameter.empty
        default = param.default if has_default else None

        params[pname] = ParamInfo(
            annotation=annotation,
            has_annotation=has_annotation,
            has_default=has_default,
            default=default,
            description=args_descriptions.get(pname, ""),
        )

    return FunctionInfo(
        name=name,
        description=description,
        params=params,
        param_order=param_order,
    )


def _types_union() -> Any:
    """返回 PEP 604 union 的 origin（types.UnionType），3.10+ 可用。"""
    try:
        import types
        return types.UnionType
    except AttributeError:
        return None
