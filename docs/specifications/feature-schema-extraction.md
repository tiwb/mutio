# mutio.schema 接口描述层

**状态**：✅ 已完成
**日期**：2026-05-25
**类型**：功能设计

## 需求

`mutio/mcp/_schema.py`（540 行）和 `mutagent/toolkits/schema.py`（219 行）各自独立实现了 Python 函数 → JSON Schema 推导，核心逻辑重复：

1. **类型映射**：annotation → JSON Schema type/enum/items。mutio 完整（Literal/Optional/list[T]/dict[K,V]），mutagent 退化（仅 4 种基本类型 + `__name__` 字符串匹配，连 `Optional[str]` 都识别不了）
2. **docstring 解析**：Google-style Args 段提取，两端实现重复但细节微妙不一致
3. **signature 遍历**：`inspect.signature` + 默认值/必填判断，逻辑相同

更重要的是：funcinfo 这类"Python 函数 → 接口描述"能力是 mcp tool schema、mutagent toolkit、未来 rpc / openapi 等所有"暴露 Python 函数给外部协议"场景的**共同祖先**。它不属于任何具体协议层，应单独成为 mutio 的一个基础分类。

## 关键参考

- `mutio/src/mutio/mcp/_schema.py` — 当前 MCP schema 推导实现（540 行，含 Annotations 段解析和冲突检测）
- `mutagent/src/mutagent/toolkits/schema.py` — 当前 mutagent Toolkit schema 推导（219 行，简单版）
- `mutio/docs/specifications/feature-mcp-schema-docstring-source.md` — MCP schema 设计规范（信息载体三分、Annotations 段语法、冲突检测规则）
- `mutagent/src/mutagent/core/_tool_set_impl.py` — mutagent ToolSet 实现，调用 `make_schema()` 和 `get_declaration_method()`
- mutio 现有顶层分类：`codec`（数据格式编解码）/ `mcp`（MCP 协议）/ `net`（网络 IO）

## 设计方案

### 分层

```
┌──────────────────────────────────────────────────────┐
│  mutio.schema/  （接口描述基础层，本规范）              │
│                                                       │
│  funcinfo.py    FunctionInfo / ParamInfo /            │
│                 extract_function_info                 │
│  jsonschema.py  annotation_to_json_schema             │
│  docstring.py   parse_google_args / extract_description│
└──────────┬──────────────────────┬────────────────────┘
           │                      │
┌──────────▼──────────┐  ┌────────▼──────────────────────┐
│ mutio.mcp._schema   │  │ mutagent.toolkits.schema       │
│ (MCP 应用层)         │  │ (LLM tool 应用层)               │
│                      │  │                                │
│ Annotations 段解析   │  │ make_schema() → ToolSchema     │
│ 冲突检测             │  │ get_declaration_method()       │
│ MCP 信封组装         │  │ (mutobj 特有，留在原处)         │
└─────────────────────┘  └────────────────────────────────┘

未来：
┌──────────────────────┐
│ mutio.rpc (假想)      │ ← 自然依赖 mutio.schema.funcinfo
└──────────────────────┘
```

### 公共层职责边界

只做"Python 函数 → 抽象接口描述"——这是语言层面的事实，不绑定任何具体协议。

**不做**：Annotations 段解析（MCP 特有）、冲突检测（MCP 特有）、信封组装（各应用层负责）。

### 模块布局

```
mutio/src/mutio/schema/
  __init__.py         # 导出主要符号
  funcinfo.py         # FunctionInfo / ParamInfo / extract_function_info
  jsonschema.py       # annotation_to_json_schema
  docstring.py        # parse_google_args / extract_description
```

`__init__.py` 导出（扁平 API，常用入口免子模块前缀）：

```python
from mutio.schema.funcinfo import FunctionInfo, ParamInfo, extract_function_info
from mutio.schema.jsonschema import annotation_to_json_schema
from mutio.schema.docstring import parse_google_args, parse_annotations_section, extract_description
```

### FunctionInfo / ParamInfo

```python
@dataclass
class FunctionInfo:
    """从 Python 函数提取的结构化接口描述。"""
    name: str                         # 函数名
    description: str                  # docstring 主描述（首段到第一个段头之前）
    params: dict[str, ParamInfo]      # 参数名 → 参数信息
    param_order: list[str]            # 参数顺序（与签名一致）


@dataclass
class ParamInfo:
    """单个参数的结构化信息。"""
    annotation: Any | None            # 原始 Python annotation（None = 无类型注解）
    has_annotation: bool              # 是否声明了类型
    has_default: bool                 # 是否有默认值
    default: Any                      # 默认值（仅 has_default=True 时有效）
    description: str                  # 参数描述（来自 docstring Args 段，无则为空）

    # 便利元信息（lazy property，由 annotation 派生，避免下游重算）
    @property
    def is_optional(self) -> bool: ...    # annotation 含 None 分支
    @property
    def is_literal(self) -> bool: ...     # Literal[...]
    @property
    def is_list(self) -> bool: ...        # list / list[T]
    @property
    def is_dict(self) -> bool: ...        # dict / dict[K, V]
```

**ParamInfo 必须暴露 is_literal/is_optional/is_list/is_dict**（决策理由：MCP 层冲突检测当前用 `meta` dict 存这些标志，上提到公共层后若只存 raw annotation，MCP 层要重新 `typing.get_origin` 一遍——重复劳动。lazy property 零成本且语义正交）。

### extract_function_info

```python
def extract_function_info(fn: Callable, *, doc: str | None = None) -> FunctionInfo:
    """从 Python 函数提取结构化接口描述。

    组合 inspect.signature + Google-style docstring 解析。

    Args:
        fn: 待提取的函数
        doc: 可选 docstring 覆盖。为 None 时取 inspect.getdoc(fn)。
            用于 @impl 覆盖后仍需取原始声明 docstring 的场景。
    """
```

内部阶段：
1. **signature**：`inspect.signature(fn)` + `typing.get_type_hints(fn)` → params dict
   - **跳过 self/cls**（硬决定，不留 `include_self` 口子。理由：本 API 服务于"生成接口描述"，self 永远无意义；真有"看见 self"的需求那就不该用这个 API）
   - annotation 存原始对象，不在此阶段做 JSON Schema 映射
2. **docstring**：解析主描述 + Args 段，合并到 ParamInfo.description
   - Args 段出现但 signature 不存在的参数**忽略**（与 Google docstring 解析器惯例一致）

### annotation_to_json_schema

```python
def annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Python 类型注解 → JSON Schema 片段。

    不包含参数名、默认值、description——这些由调用方从 FunctionInfo 自行组装。

    Returns:
        JSON Schema 片段。可能含 type/enum/items/additionalProperties。
        不识别的类型返回空 dict（不报错，降级为 untyped）。
    """
```

类型映射表（从现有 `_annotation_to_schema` 整体移植）：

| Python annotation | JSON Schema |
|---|---|
| `str` / `int` / `float` / `bool` | 基本 type |
| `list` / `list[T]` | `{"type": "array", "items": <T>}` |
| `dict` / `dict[str, T]` | `{"type": "object", "additionalProperties": <T>}` |
| `None` / `NoneType` | `{"type": "null"}` |
| `T \| None` / `Optional[T]` | type 含 `"null"` |
| `Literal["a","b"]`（同类型） | `{"type": "string", "enum": [...]}` |
| `Literal["a", 1]`（混合） | `{"enum": [...]}`（省略 type） |
| `Any` | 省略 type |
| 其他不识别 | 空 dict |

### parse_google_args / extract_description

```python
def parse_google_args(doc: str) -> dict[str, str]:
    """提取 Google-style docstring 的 Args 段 → {name: description}。

    识别段头：Args: / Arguments: / Parameters:
    支持续行（缩进更深的下一行视为延续）。
    段结束：dedent 回段头层级 / 遇到下一个段头（Returns:/Raises:等）。
    """


def extract_description(doc: str) -> str:
    """提取 docstring 主描述（首段到第一个 Google-style 段头之前）。

    识别段头：Args/Arguments/Parameters/Returns/Raises/Yields/
             Note/Notes/Example/Examples/Attributes/Annotations。

    无 docstring 时返回空字符串。
    """
```

### 对现有代码的影响

**mutio 内部**：

- `mcp/_schema.py` 重命名 `_annotation_to_schema` → 删除，调用 `mutio.schema.annotation_to_json_schema`
- `mcp/_schema.py` 的 `_parse_args_section` → 删除，调用 `parse_google_args`
- `mcp/_schema.py` 的 `_parse_annotations_section` → 删除，调用 `parse_annotations_section`
- `mcp/_schema.py` 的 `function_to_mcp_description` → 改用 `extract_description`
- `mcp/_schema.py` 的 `_from_signature` → 改用 `extract_function_info`，元信息从 `ParamInfo.is_literal/is_optional/...` 读取（不再单独维护 meta dict）
- `mcp/_schema.py` 保留：`function_to_mcp_input_schema` 外层组装、`_forbidden_keys`（冲突检测）

**mutagent**：

- `toolkits/schema.py` 的 `_annotation_to_json_type` → 删除，调用 `annotation_to_json_schema`
- `toolkits/schema.py` 的 `parse_docstring` → 删除，调用 `extract_description` + `parse_google_args`
- `toolkits/schema.py` 的 `make_schema` → 用 `extract_function_info` + `annotation_to_json_schema` 重写
- `toolkits/schema.py` 保留：`get_declaration_method`（mutobj 特有逻辑，不下沉到 mutio——mutio 不依赖 mutobj 是设计原则）

### mutagent schema 行为变更说明

mutagent 现有 `_annotation_to_json_type` 用 `__name__` 字符串匹配，能力极弱。切换后会发生**对 LLM 端可见的 schema 行为变更**：

| annotation | 切换前 | 切换后 |
|---|---|---|
| `Optional[str]` | `{"type": "string"}`（错误） | `{"type": ["string", "null"]}` |
| `Literal["a","b"]` | `{"type": "string"}` | `{"type": "string", "enum": ["a","b"]}` |
| `list[int]` | `{"type": "string"}`（兜底坏值） | `{"type": "array", "items": {"type": "integer"}}` |

这是**质量提升**，但需在实施阶段做 LLM provider 端回归（至少覆盖 OpenAI/Anthropic/DeepSeek），确认 `type: [...]` 数组形式被接受。如果某 provider 不接受，由 mutagent 应用层做下沉处理（不影响 mutio 公共层）。

### 测试策略

- **公共层独立单元测试**：`mutio/tests/test_schema_funcinfo.py` / `test_schema_jsonschema.py` / `test_schema_docstring.py`，覆盖边界（`Any`、裸 `list`、`Union[X,Y,None]` 多分支退化、Literal 混合类型、空 docstring、Args 段缩进异常、Annotations 单行/多行 JSON、严格 json.loads 等）
- **mutio MCP 集成回归**：现有 `_schema.py` test suite 全部通过，输出 schema bytes-equal
- **mutagent toolkit 集成回归**：现有 ToolSchema 生成测试通过；新增覆盖 Optional / Literal / list[T] 场景的测试（验证质量提升后的输出形态）
- **LLM provider 端回归**：实施阶段对 mutagent 至少 3 个 provider 跑一次冒烟，确认 schema 被接受

## 实施步骤清单

### mutio 变更

- [x] 新建 `mutio/src/mutio/schema/__init__.py` — 扁平 API 导出
- [x] 新建 `mutio/src/mutio/schema/funcinfo.py` — FunctionInfo / ParamInfo / extract_function_info
- [x] 新建 `mutio/src/mutio/schema/jsonschema.py` — annotation_to_json_schema
- [x] 新建 `mutio/src/mutio/schema/docstring.py` — parse_google_args / extract_description
- [x] 重构 `mutio/mcp/_schema.py` — 替换内部实现为 mutio.schema 公共层函数，保留 MCP 特有逻辑
- [x] 新建 `mutio/tests/test_schema_funcinfo.py` — FunctionInfo / ParamInfo / extract_function_info 单元测试（15 用例）
- [x] 新建 `mutio/tests/test_schema_jsonschema.py` — annotation_to_json_schema 单元测试（20 用例）
- [x] 新建 `mutio/tests/test_schema_docstring.py` — parse_google_args / extract_description 单元测试（17 用例）
- [x] 运行 MCP 全套测试回归（`pytest tests/test_mcp_*`），116/116 全过，输出 bytes-equal

## 待定问题

（无）

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|---|---|---|---|
| `mutio.mcp._schema` | MCP tool inputSchema 生成 | `extract_function_info` + `annotation_to_json_schema` + `parse_google_args` + `parse_annotations_section` + `extract_description` + `ParamInfo.is_literal/is_optional/is_list/is_dict` | 现有 MCP test suite 全部通过；schema 输出 bytes-equal |
| `mutagent.toolkits.schema` | LLM tool schema 生成 | 同上（不需 ParamInfo 元信息便利属性，如需 Annotations 段可自行调用 `parse_annotations_section`） | 现有 ToolSchema 生成测试通过；自动获得 Literal→enum / Optional→nullable / list[T]→array 等能力；至少 3 个 LLM provider 端冒烟通过 |
| `mutio.rpc`（未来假想） | RPC endpoint 接口描述 | `extract_function_info` | 可直接获取结构化接口信息，自行组装协议帧 |
| 第三方 | 自定义 tool schema 格式 | `extract_function_info` | 拿到 FunctionInfo 后自行映射到任意目标格式 |
