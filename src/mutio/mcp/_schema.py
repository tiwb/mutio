"""Python 函数 → MCP `inputSchema` 推导。

信息载体三分：
- signature annotation + default → type / default / required / enum (via Literal) / items / additionalProperties
- docstring 主段 → tool description
- docstring `Args:` 段散文 → property.description
- docstring `Annotations:` 段（JSON 一次到底）→ 其他 mcp schema 字段（minimum / pattern / format / ...）

入口：
- `function_to_mcp_input_schema(fn)` → dict[str, Any]
- `function_to_mcp_description(fn)` → str

实现遵循 `mutio/docs/specifications/feature-mcp-schema-docstring-source.md`。
底层类型推导和 docstring 解析委托给 `mutio.schema` 公共层。
本模块保留 MCP 特有逻辑：Annotations 段解析、冲突检测、schema 信封组装。
"""

from __future__ import annotations

import inspect
import textwrap
import typing
from typing import Any, cast

from mutio.schema import (
    ParamInfo,
    annotation_to_json_schema,
    extract_description,
    extract_function_info,
    parse_annotations_section,
)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def function_to_mcp_input_schema(fn: Any, doc: str | None = None) -> dict[str, Any]:
    """从 python 函数推导 mcp tool 的 inputSchema。

    Args:
        fn: 待推导的函数 / 方法。
        doc: 可选的 docstring 覆盖。为 `None` 时取 `inspect.getdoc(fn)`；
            传入时使用该值（场景：@impl 覆盖后仍需取原始声明 docstring）。

    详见 `feature-mcp-schema-docstring-source.md`。
    """
    func_info = extract_function_info(fn, doc=doc)

    # 阶段 1：从 FunctionInfo 构建 properties / required
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name in func_info.param_order:
        param = func_info.params[name]

        prop: dict[str, Any] = (
            {} if not param.has_annotation
            else annotation_to_json_schema(param.annotation)
        )
        if param.has_default:
            if _is_json_compatible(param.default):
                prop["default"] = param.default
        else:
            required.append(name)

        if param.description:
            prop["description"] = param.description

        properties[name] = prop

    # 阶段 2：解析 docstring（Annotations 段）
    if doc is None:
        raw_doc = inspect.getdoc(fn) or ""
    else:
        raw_doc = inspect.cleandoc(doc)
    annotations_blocks = parse_annotations_section(raw_doc)

    # 阶段 3：Annotations 段合并 + 冲突检测
    param_names = list(func_info.params.keys())

    # 3a：Annotations 段中 signature 不存在的参数 → 报错（typo 防线）
    for name, (line_no, _raw) in annotations_blocks.items():
        if name not in func_info.params:
            candidates = ", ".join(repr(n) for n in param_names) or "(no parameters)"
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}) "
                f"does not exist in function signature. Candidates: {candidates}."
            )

    # 3b：冲突检测 + 合并
    for name, (line_no, parsed) in annotations_blocks.items():
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}): "
                f"value must be a JSON object, got {type(parsed).__name__}."
            )
        forbidden = _forbidden_keys(func_info.params[name])
        for key in parsed:
            if key in forbidden:
                raise ValueError(
                    f"Parameter {name!r}: {key!r} is already expressed by signature "
                    f"({forbidden[key]}). Remove it from the Annotations section."
                )
        properties[name].update(parsed)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    else:
        schema["required"] = []
    return schema


def function_to_mcp_description(fn: Any, doc: str | None = None) -> str:
    """提取 docstring 主段作为 tool description。

    主段 = 首段到任一 Google-style 段头之前的全部文本，去除前后空白。

    Args:
        fn: 待提取的函数。
        doc: 可选 docstring 覆盖，同 `function_to_mcp_input_schema`。
    """
    if doc is None:
        raw_doc = inspect.getdoc(fn) or ""
    else:
        raw_doc = inspect.cleandoc(doc)
    return extract_description(raw_doc)


# ---------------------------------------------------------------------------
# 冲突检测（MCP 特有）
# ---------------------------------------------------------------------------


def _forbidden_keys(param: ParamInfo) -> dict[str, str]:
    """根据 ParamInfo 计算 Annotations 段中禁止出现的字段。

    返回 {key: reason}。未声明类型的参数不限制 type。
    """
    forbidden: dict[str, str] = {
        "default": "signature default",
        "description": "Args section",
    }
    if param.has_annotation:
        forbidden["type"] = "signature annotation"
    if param.is_literal:
        forbidden["enum"] = "Literal[...]"
    if param.is_list:
        if param.annotation is not None and typing.get_args(param.annotation):
            forbidden["items"] = "list[T] annotation"
    if param.is_dict:
        if param.annotation is not None and len(typing.get_args(param.annotation)) == 2:
            forbidden["additionalProperties"] = "dict[K, V] annotation"
    return forbidden


# ---------------------------------------------------------------------------
# JSON 兼容性检查
# ---------------------------------------------------------------------------


def _is_json_compatible(value: Any) -> bool:
    """判断 default 值能否原样写入 schema。"""
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        value = cast(list[Any] | tuple[Any, ...], value)
        return all(_is_json_compatible(v) for v in value)
    if isinstance(value, dict):
        value = cast(dict[Any, Any], value)
        return all(isinstance(k, str) and _is_json_compatible(v) for k, v in value.items())
    return False


# 复用：textwrap dedent helper（保留以便未来扩展）
_dedent = textwrap.dedent
