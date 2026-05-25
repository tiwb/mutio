"""Google-style docstring 解析工具。

遵循 Google Python Style Guide 的 docstring 约定
（https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings），
提供三个公共函数：
- parse_google_args：提取 Args 段散文 → {param_name: description}
- parse_annotations_section：提取 Annotations 段 JSON → {param_name: (line_no, parsed)}
- extract_description：提取主描述（首段到第一个段头之前）
"""

from __future__ import annotations

import re
from typing import Any

from mutio.codec import json


# ---------------------------------------------------------------------------
# 段头识别
# ---------------------------------------------------------------------------

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
    "Attributes:",
    "Annotations:",
)

_SECTION_HEADER_RE = re.compile(
    r"^(" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")\s*$"
)

_ARGS_HEADERS = ("Args:", "Arguments:", "Parameters:")


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def parse_google_args(doc: str) -> dict[str, str]:
    """提取 Google-style docstring 的 Args 段 → {name: description}。

    识别段头：Args: / Arguments: / Parameters:
    支持续行（缩进更深的下一行视为延续）。
    段结束：dedent 回段头层级 / 遇到下一个段头（Returns:/Raises:等）。
    """
    lines = doc.splitlines()
    result: dict[str, str] = {}
    in_section = False
    section_indent = 0
    current: str | None = None
    current_buf: list[str] = []
    item_indent = 0

    def flush() -> None:
        if current is not None:
            text = " ".join(s.strip() for s in current_buf).strip()
            if text:
                result[current] = text

    for raw in lines:
        stripped = raw.strip()
        if not in_section:
            if stripped in _ARGS_HEADERS:
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
            flush()
            current = None
            current_buf = []
            in_section = False
            continue

        # 段内：识别 "name: desc" 起始行（可选 type 括号）
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


def extract_description(doc: str) -> str:
    """提取 docstring 主描述（首段到第一个 Google-style 段头之前）。

    识别段头：Args/Arguments/Parameters/Returns/Raises/Yields/
             Note/Notes/Example/Examples/Attributes/Annotations。

    无 docstring 时返回空字符串。
    """
    if not doc:
        return ""
    lines = doc.splitlines()
    main_lines: list[str] = []
    for line in lines:
        if _SECTION_HEADER_RE.match(line.strip()):
            break
        main_lines.append(line)
    return "\n".join(main_lines).strip()


def parse_annotations_section(doc: str) -> dict[str, tuple[int, Any]]:
    """提取 `Annotations:` 段 → {param_name: (line_no_1based, parsed_json)}。

    解析规则：
    - 段头 `^Annotations:\\s*$`，顶格
    - 段内每条目以 4+ 空格缩进的 `name:` 起始
    - value 是 JSON，单行或多行（用 json.loads 严格解析）
    - 段结束：dedent 回顶级 / 下一段头 / 文件末尾

    Returns:
        空 dict 如果文档不含 Annotations 段。
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
