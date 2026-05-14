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
本模块为纯函数实现，不依赖 mutobj，便于姊妹文档 mutagent 渲染层做 round-trip 测试。
"""

from __future__ import annotations

import inspect
import json
import re
import textwrap
import typing
from typing import Any


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# Google-style docstring 段头，识别主段终止位置
_SECTION_HEADERS = (
    "Args:",
    "Arguments:",
    "Parameters:",
    "Returns:",
    "Return:",
    "Yields:",
    "Yield:",
    "Raises:",
    "Raise:",
    "Examples:",
    "Example:",
    "Note:",
    "Notes:",
    "Annotations:",
)

_SECTION_HEADER_RE = re.compile(
    r"^(" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")\s*$"
)

# Python 基本类型 → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


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
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    # 阶段 1：signature → properties / required（含 enum/items/additionalProperties/null）
    properties, required, sig_meta = _from_signature(sig, hints)

    # 阶段 2：解析 docstring
    if doc is None:
        doc = inspect.getdoc(fn) or ""
    else:
        doc = inspect.cleandoc(doc)
    args_descriptions = _parse_args_section(doc)
    annotations_blocks = _parse_annotations_section(doc)

    # 阶段 3：合并 + 冲突检测
    param_names = list(sig_meta.keys())

    # 3a：Annotations 段中 signature 不存在的参数 → 报错（typo 防线）
    for name, (line_no, _raw) in annotations_blocks.items():
        if name not in sig_meta:
            candidates = ", ".join(repr(n) for n in param_names) or "(no parameters)"
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}) "
                f"does not exist in function signature. Candidates: {candidates}."
            )

    # 3b：Args 段散文 → property.description
    for name, desc in args_descriptions.items():
        if name in properties:
            properties[name]["description"] = desc

    # 3c：Annotations JSON 合并 + 冲突检测
    for name, (line_no, parsed) in annotations_blocks.items():
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}): "
                f"value must be a JSON object, got {type(parsed).__name__}."
            )
        forbidden = _forbidden_keys(sig_meta[name])
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
        doc = inspect.getdoc(fn) or ""
    else:
        doc = inspect.cleandoc(doc)
    if not doc:
        return ""
    lines = doc.splitlines()
    main_lines: list[str] = []
    for line in lines:
        if _SECTION_HEADER_RE.match(line.strip()):
            break
        main_lines.append(line)
    return "\n".join(main_lines).strip()


# ---------------------------------------------------------------------------
# 阶段 1：signature → properties
# ---------------------------------------------------------------------------


def _from_signature(
    sig: inspect.Signature,
    hints: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, dict[str, Any]]]:
    """从 signature 推导 properties / required。

    返回 (properties, required, meta)。meta 记录每个参数的来源信息，
    供冲突检测使用：annotation / has_default / is_optional / kind。
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    meta: dict[str, dict[str, Any]] = {}

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if name in hints:
            annotation = hints[name]
            has_annotation = True
        elif param.annotation is not inspect.Parameter.empty:
            annotation = param.annotation
            has_annotation = True
        else:
            annotation = None
            has_annotation = False

        prop: dict[str, Any] = {} if not has_annotation else _annotation_to_schema(annotation)
        has_default = param.default is not inspect.Parameter.empty
        if has_default:
            # 默认值必须是 JSON 兼容值才能放入 schema
            if _is_json_compatible(param.default):
                prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop
        meta[name] = {
            "annotation": annotation,
            "has_annotation": has_annotation,
            "has_default": has_default,
            "is_optional": _is_optional(annotation) if has_annotation else False,
            "is_literal": _get_literal_args(annotation) is not None if has_annotation else False,
            "is_list": _get_origin(annotation) is list if has_annotation else False,
            "is_dict": _get_origin(annotation) is dict if has_annotation else False,
        }

    return properties, required, meta


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Python 类型注解 → JSON Schema 片段（含 type/enum/items/additionalProperties/null）。

    不识别的类型降级为空 dict（无 type）。
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
    if origin is typing.Union or origin is types_union():
        non_none = [a for a in args if a is not type(None)]
        has_null = len(non_none) != len(args)
        if len(non_none) == 1:
            inner = _annotation_to_schema(non_none[0])
            if has_null:
                return _add_null_type(inner)
            return inner
        # 多分支 Union：合并成 type 列表（仅当各分支都是简单 type 时）
        sub_schemas = [_annotation_to_schema(a) for a in non_none]
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
            return {"type": "array", "items": _annotation_to_schema(args[0])}
        return {"type": "array"}

    # dict[K, V]
    if origin is dict:
        if len(args) == 2:
            return {"type": "object", "additionalProperties": _annotation_to_schema(args[1])}
        return {"type": "object"}

    # 不识别 → 空 schema
    return {}


def _literal_to_schema(args: tuple[Any, ...]) -> dict[str, Any]:
    """Literal[...] → enum；同类型时附 type。"""
    types = {type(a) for a in args}
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
        out["type"] = list(t) + ["null"]
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


def types_union() -> Any:
    """返回 PEP 604 union 的 origin（types.UnionType），3.10+ 可用。"""
    try:
        import types
        return types.UnionType
    except AttributeError:
        return None


def _is_optional(annotation: Any) -> bool:
    """判断 annotation 是否包含 None 分支。"""
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types_union():
        return any(a is type(None) for a in typing.get_args(annotation))
    return False


def _is_json_compatible(value: Any) -> bool:
    """判断 default 值能否原样写入 schema。"""
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_compatible(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_compatible(v) for k, v in value.items())
    return False


# ---------------------------------------------------------------------------
# 阶段 2：docstring 解析
# ---------------------------------------------------------------------------


def _parse_args_section(doc: str) -> dict[str, str]:
    """提取 Google-style `Args:` 段散文 → {param_name: description}。

    支持续行（缩进更深的下一行视为延续）。
    """
    lines = doc.splitlines()
    result: dict[str, str] = {}
    in_section = False
    section_indent = 0
    current: str | None = None
    current_buf: list[str] = []
    item_indent = 0

    args_headers = ("Args:", "Arguments:", "Parameters:")

    def flush() -> None:
        if current is not None:
            text = " ".join(s.strip() for s in current_buf).strip()
            if text:
                result[current] = text

    for raw in lines:
        stripped = raw.strip()
        if not in_section:
            if stripped in args_headers:
                in_section = True
                section_indent = len(raw) - len(raw.lstrip())
            continue

        # 段内
        if not stripped:
            # 空行：保留状态，但作为当前条目软分隔
            if current is not None:
                current_buf.append("")
            continue

        line_indent = len(raw) - len(raw.lstrip())
        # dedent 回到段头层级或更浅 → 段结束
        if line_indent <= section_indent:
            # 可能是下一段头
            flush()
            current = None
            current_buf = []
            in_section = False
            continue

        # 段内：识别 "name: desc" 起始行
        m = re.match(r"^(\w+)(\s*\([^)]*\))?:\s*(.*)$", stripped)
        if m and (current is None or line_indent <= item_indent):
            flush()
            current = m.group(1)
            current_buf = [m.group(3)] if m.group(3) else []
            item_indent = line_indent
        else:
            # 续行
            if current is not None:
                current_buf.append(stripped)

    flush()
    return result


def _parse_annotations_section(doc: str) -> dict[str, tuple[int, Any]]:
    """提取 `Annotations:` 段 → {param_name: (line_no_1based, parsed_json)}。

    解析规则：
    - 段头 `^Annotations:\\s*$`，顶格
    - 段内每条目以 4+ 空格缩进的 `name:` 起始
    - value 是 JSON，单行或多行（用 raw_decode 增量解析，跨行连接续行）
    - 段结束：dedent 回顶级 / 下一段头 / 文件末尾
    """
    lines = doc.splitlines()
    # 找段头位置
    start = -1
    for i, line in enumerate(lines):
        if re.match(r"^Annotations:\s*$", line):
            start = i + 1
            break
    if start == -1:
        return {}

    # 段终止：dedent 回顶级（无缩进非空行）或新段头
    end = len(lines)
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent == 0:
            # 顶格：如果是段头或其他文本，都视为段结束
            end = i
            break

    # 收集段内文本（保留行号映射）
    section_lines = lines[start:end]
    section_offset = start  # 段内行 0 对应原文档行号 start (0-based) → 1-based: start+1

    # 解析：以 "    name:" 起始的行作为条目起点
    result: dict[str, tuple[int, Any]] = {}
    item_re = re.compile(r"^( {4,})(\w+):\s*(.*)$")

    i = 0
    while i < len(section_lines):
        line = section_lines[i]
        m = item_re.match(line)
        if not m:
            i += 1
            continue

        indent = m.group(1)
        name = m.group(2)
        first_value = m.group(3)
        line_no = section_offset + i + 1  # 1-based 文档行号

        # 收集该条目的全部 value 文本（直到下一条目 / 段末）
        value_parts: list[str] = [first_value]
        j = i + 1
        while j < len(section_lines):
            nxt = section_lines[j]
            if item_re.match(nxt):
                break
            # 继续累加（保留可能的多行 JSON 内容）
            value_parts.append(nxt)
            j += 1

        value_text = "\n".join(value_parts).strip()
        if not value_text:
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}): empty value."
            )

        try:
            parsed = json.loads(value_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Annotations section parameter {name!r} (line {line_no}): "
                f"invalid JSON ({e.msg} at pos {e.pos}). "
                f"Note: only strict JSON is accepted (true/false/null lowercase, double-quoted strings)."
            ) from e

        result[name] = (line_no, parsed)
        i = j

    return result


# ---------------------------------------------------------------------------
# 阶段 3：冲突检测
# ---------------------------------------------------------------------------


def _forbidden_keys(meta_entry: dict[str, Any]) -> dict[str, str]:
    """根据参数元信息计算 Annotations 段中禁止出现的字段。

    返回 {key: reason}。未声明类型的参数不限制 type。
    """
    forbidden: dict[str, str] = {
        "default": "signature default",
        "description": "Args section",
    }
    if meta_entry.get("has_annotation"):
        forbidden["type"] = "signature annotation"
    if meta_entry.get("is_literal"):
        forbidden["enum"] = "Literal[...]"
    if meta_entry.get("is_list"):
        annotation = meta_entry["annotation"]
        if typing.get_args(annotation):  # 有具体 T
            forbidden["items"] = "list[T] annotation"
    if meta_entry.get("is_dict"):
        annotation = meta_entry["annotation"]
        args = typing.get_args(annotation)
        if len(args) == 2:
            forbidden["additionalProperties"] = "dict[K, V] annotation"
    return forbidden


# 复用：textwrap dedent helper（保留以便未来扩展）
_dedent = textwrap.dedent
