"""mutio.schema — 接口描述基础层。

Python 函数 → 抽象接口描述的公共能力，不绑定任何具体协议。
是 mcp tool schema、mutagent toolkit、rpc、openapi 等
"暴露 Python 函数给外部协议" 场景的共同祖先。
"""

from mutio.schema.funcinfo import FunctionInfo, ParamInfo, extract_function_info
from mutio.schema.jsonschema import annotation_to_json_schema
from mutio.schema.docstring import parse_google_args, parse_annotations_section, extract_description

__all__ = [
    "FunctionInfo",
    "ParamInfo",
    "extract_function_info",
    "annotation_to_json_schema",
    "parse_google_args",
    "parse_annotations_section",
    "extract_description",
]
