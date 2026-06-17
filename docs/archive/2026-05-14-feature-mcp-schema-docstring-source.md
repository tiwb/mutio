# Python 函数 → MCP Schema：以 docstring 为信息源

**状态**：✅ 已完成
**日期**：2026-05-14
**类型**：功能设计

## 背景

mutio 通过 `MCPToolSet` 子类把 python 函数对外暴露为 mcp tool，需要一份完整的 `inputSchema`。当前 `_view_impl._infer_schema` 只覆盖基本类型映射，缺失：

- 参数级自然语言描述（`property.description`）
- 结构化约束（`minimum` / `pattern` / `format` / `uniqueItems` / `additionalProperties` / `propertyNames` / `multipleOf` / `minLength` / ...）

本规范定义如何从 python 函数源码完整推导出 mcp `inputSchema`。

## 设计方案

### 信息载体三分

mcp `inputSchema` 的三类信息分别落到 python 源码的三个独立位置：

| 信息类 | 源位置 | 备注 |
|---|---|---|
| 类型 / 默认 / 必填 / 同类型 enum | signature annotation + default | python typing 系统原生表达 |
| 自然语言描述 | docstring `Args:` 段散文 | Google-style 惯例 |
| 其他 mcp schema 字段 | docstring `Annotations:` 段 | 本规范新增 |

三个位置职责唯一，**不重叠**。

### `Annotations:` 段语法

参数名是 docstring 一等概念（与 `Args:` 段对齐）；mcp schema 字段不是 docstring 概念，原样透传 JSON：

```
Annotations:
    {param_name}: {json_blob}
    {param_name}: {json_blob}
```

- 段头 `Annotations:` 顶格（与 `Args:` / `Returns:` 同级）
- 段内每行：4 空格缩进 + 参数名 + `:` + 空格 + JSON 值
- value 是 mcp `properties[name]` 的子集，**不含** signature 已表达的字段（`type` / `default` / `enum`-via-Literal / `items.type`-via-list[T]）
- 长 value 允许多行 JSON（缩进自由），由 `json.loads` 原生增量解析支持

**度的判定标尺**：参数是 docstring 一等概念 → 留 docstring 层级；mcp schema 字段不是 → 走 JSON 一次到底。这保证嵌套对象（`propertyNames` / `patternProperties` / 嵌套 `items`）不需要发明第二套缩进规则。

### 严格 JSON 字面量

`Annotations:` 段内的 value **严格 `json.loads`**：

- `true` / `false` / `null`（小写）
- 字符串必须双引号
- 不接受 Python 字面量（`True` / `None` / 单引号字符串）

理由：

- mcp 协议本身就是 JSON wire format，"docstring 嵌 mcp schema 子集" 的 mental model 自带 JSON 语义
- 一条规则不留 fallback，避免 "json.loads 失败再 ast.literal_eval" 的规则膨胀
- mutio 加载 MCPToolSet 子类时启动期校验，typo 立即暴露
- 解析失败 → raise `ValueError`，给出参数名 + 行号 + 原始 `json.loads` 错误

### enum 归 signature

同类型 enum 通过 `Literal["a", "b"]` 表达；**不允许在 `Annotations:` 段写 `enum`**。理由：

- python first：`Literal[...]` 是 typing 系统原生 enum 表达
- IDE 在调用点直接补全可选值
- 切线清晰：signature 承担 "python typing 能表达的全部"，`Annotations:` 段承担 "typing 表达不了的剩余"

混合类型 enum（`enum: ["a", 1]`）— 极少出现，仍走 `Literal["a", 1]`（python typing 支持）。

### Python 类型 → JSON Schema 映射

| Python annotation | JSON Schema |
|---|---|
| `int` / `float` / `str` / `bool` | 基本 type |
| `list` / `list[T]` | `{"type": "array", "items": <T>}` |
| `dict` / `dict[str, T]` | `{"type": "object", "additionalProperties": <T>}` |
| `None` / `NoneType` | `{"type": "null"}` |
| `T \| None` / `Optional[T]` | type 含 `"null"` |
| `Literal["a","b"]`（同类型） | `{"type": "string", "enum": [...]}` |
| `Literal[1,2]` | `{"type": "integer", "enum": [...]}` |
| `Literal["a", 1]`（混合） | `{"enum": [...]}`（省略 type） |
| `Any` | 省略 type |
| 其他不识别 | 降级为无 type，不报错 |

### 推导流程

```
function_to_mcp_input_schema(fn) -> dict[str, Any]:

  1. signature 解析（inspect.signature + typing.get_type_hints）
       annotation → property.type / enum (via Literal) / items / additionalProperties
       default    → property.default
       无 default → required

  2. docstring 解析
       主描述（Args: 之前）→ tool.description（caller 决定是否使用）
       Args: 段散文       → property.description
       Annotations: 段     → 合并到对应 property（json.loads 严格）

  3. 合并 + 冲突检测
       Annotations 段写了 signature 已表达字段 → ValueError
```

### 冲突检测

`Annotations:` 段不允许写 signature 已表达的字段。检测集（按参数 annotation 决定）：

| 参数 annotation | 禁止在 Annotations 出现的 key |
|---|---|
| 任何参数 | `type` / `default` / `description` |
| `Literal[...]` | `enum` |
| `list[T]`（T 已识别） | `items` |
| `dict[str, T]`（T 已识别） | `additionalProperties` |
| `Optional[T]` / `T \| None` | `type`（含 null）|

违规 → 启动期 raise `ValueError`，含参数名 + 冲突字段 + 修复建议（"Remove from Annotations section"）。

### `Annotations:` 段解析规则细节

- 段头识别：`^Annotations:\s*$`（顶格 + 冒号）
- 段结束：dedent 回顶级 / 下一段头（`Returns:` / `Raises:` / `Yields:` / `Examples:`）/ 文件末尾
- 段内参数行起点：`^    (\w+):\s*(.*)$`（4 空格 + 参数名 + 冒号）
- 多行 value：第一行起 JSON 不闭合时继续读至完整（`json.JSONDecoder.raw_decode` 增量解析）
- 段内未出现的参数：无额外约束（不是错误）
- **`Annotations:` 段中出现 signature 没有的参数：报错**（启动期 raise `ValueError`，含参数名 + 行号 + 候选参数名）。理由：signature 是参数权威源，不一致几乎都是 typo，启动期暴露比静默忽略安全
- 注：`Args:` 段散文里出现 signature 没有的参数仍然忽略（与 Google docstring 解析器惯例一致；散文级别误差不影响 schema 正确性）

### 非目标：不预设唯一信息源

本规范定义 docstring `Annotations:` 段为**当前唯一支持的结构化约束源**，但 mutio 解析器在 API 层面**不预设其独占地位**。未来若引入其他源（typing metadata / 类属性 / 装饰器），本规范不需修改。

具体做法：

- `function_to_mcp_input_schema` 内部分阶段（signature → docstring → 合并），各阶段独立
- 不在对外 API 表达 "docstring 是唯一源"
- 第一版**不导出** Annotated metadata 类、不识别 `Annotated[T, dict]` / `Annotated[T, MCPSchema(...)]` 等扩展形态

未来扩展只需在合并阶段加新源 + 加新源的优先级语义，docstring 路径不变。

## 写法示例

### 常规情况（不写 Annotations 段）

```python
class MutbotTools(MCPToolSet):
    async def logs(
        self,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
        last_n: int = 50,
    ) -> str:
        """查询日志。

        Args:
            level: 最低级别。
            last_n: 返回条数上限。
        """
```

产出：

```json
{
    "type": "object",
    "properties": {
        "level":  {"type": "string", "enum": ["DEBUG","INFO","WARNING","ERROR"], "default": "INFO", "description": "最低级别。"},
        "last_n": {"type": "integer", "default": 50, "description": "返回条数上限。"}
    },
    "required": []
}
```

### 需要高级约束（写 Annotations 段）

```python
async def query(
    self,
    pattern: str,
    count: int = 0,
    metadata: dict | None = None,
) -> str:
    """复杂查询。

    Args:
        pattern: 正则模式。
        count: 起始位置。
        metadata: 扩展元数据。

    Annotations:
        pattern: {"format": "regex", "minLength": 1}
        count: {"minimum": 0, "maximum": 10000}
        metadata: {"additionalProperties": false, "propertyNames": {"pattern": "^[a-z_]+$"}}
    """
```

### 长 value 多行 JSON（也合法）

```python
"""...

Annotations:
    options: {
        "additionalProperties": false,
        "propertyNames": {"pattern": "^[a-z]+$"},
        "patternProperties": {"^x_": {"type": "string"}}
    }
"""
```

### 冲突报错示例

```python
def bad(level: Literal["a", "b"] = "a") -> None:
    """...

    Annotations:
        level: {"enum": ["c", "d"]}
    """
```

启动期 raise：

```
ValueError: Parameter 'level': 'enum' is already expressed by signature
(Literal['a', 'b']). Remove it from the Annotations section.
```

## Optional 与 default null 的处理

`Optional[T] = None` 按 python 语义透传：`{type: [..., "null"], default: null}`，不在 `required`。`Optional[T]` 无默认值（罕见）：在 `required` 中。决策理由：mcp 客户端通用理解 `default: null` 即 "可选 + 默认空"，无需特殊编码。

## tool.description 来源

mcp tool 顶层 `description` 取 docstring 主段（首段到 `Args:` / `Returns:` / `Raises:` / `Yields:` / `Examples:` / `Annotations:` 任一段头之前的全部文本，去除前后空白）。Google-style 通行做法，与 `Args:` 段语义对齐。

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|---|---|---|---|
| `mutio.mcp._view_impl.MCPToolProvider.list_tools()` | mutio MCP server 的 `tools/list` 响应 | 升级后的 inputSchema | 现有 MCPToolSet 子类不改代码 schema 仍可用；新增 `Annotations:` 段后 schema 包含完整约束 |
| `mutbot.builtins.debug_tools.MutbotTools` 14 函数 | mutbot 对外 debug 工具 | 同上 | 重写后外部 mcp 客户端看到完整 schema |
| 第三方 `MCPToolSet` 子类作者 | 基于 mutio 构建 mcp server | 学习写法 | 不学 DSL 不看源码，按 python 直觉写 signature + Google docstring + 可选 `Annotations:` 段即可 |
| `mutagent` sandbox 渲染层（姊妹文档） | 见下方关键参考 | 共享 `Annotations:` 段语法定义 | **软语义对等**：mutagent 渲染输出的 docstring 能被 mutio 解析器原样读回为语义等价的 schema |

## 关键参考

### 当前代码

- `mutio/src/mutio/mcp/_view_impl.py::_infer_schema` — 升级起点
- `mutio/src/mutio/mcp/_view_impl.py::_get_declaration_doc` — docstring 获取（已有，复用）
- `mutio/src/mutio/mcp/toolset.py::MCPToolSet` — 契约声明
- `mutio/src/mutio/mcp/view.py::MCPView` — MCP 端点
- `mutio/tests/test_mcp_tool_doc.py` — 现有 tool doc 测试

### 姊妹文档

- `mutagent/docs/specifications/feature-mcp-schema-help-display.iter2.md` — 反向：mcp schema → docstring 渲染（sandbox `help()` 场景）。共享本规范的 `Annotations:` 段语法

### 已废弃 / 取代的探索方向（保留不删，作历史参考）

- `feature-mcp-schema-from-annotations.md` — Annotated metadata 鸭子识别路线，已否决（约束塞类型注解不符合 python first + 信息原始位置原则）
- `feature-mcp-schema-from-python.md` — 早期 python → schema 推导探索

### 外部协议

- JSON Schema: https://json-schema.org/
- MCP schema: https://raw.githubusercontent.com/modelcontextprotocol/specification/main/schema/2025-11-25/schema.ts
- PEP 257 Docstring Conventions: https://peps.python.org/pep-0257/
- Sphinx Napoleon (Google-style 解析参考): https://sphinxcontrib-napoleon.readthedocs.io/

## 实施步骤清单

- [x] 新建 `mutio/src/mutio/mcp/_schema.py`，主入口 `function_to_mcp_input_schema(fn) -> dict[str, Any]`（含 signature 推导、docstring 解析、Annotations 段解析、合并冲突检测；模块内私有，不进 `__init__.py` 导出）
- [x] 同模块新增 `function_to_mcp_description(fn) -> str` 提取 docstring 主段
- [x] signature 阶段：实现 Python 类型 → JSON Schema 映射表（基本类型 / `list[T]` / `dict[str,T]` / `Optional[T]` / `Literal` 同类型与混合类型 / `None` / `Any` / 不识别降级）
- [x] docstring 阶段：Args 段散文 → `property.description`；主段 → tool description；`Annotations:` 段按解析规则细节实现（顶格段头、4 空格参数行、多行 JSON 用 `json.JSONDecoder.raw_decode` 增量解析、严格 `json.loads`）
- [x] 合并 + 冲突检测：实现禁止字段表（type/default/description 通用；Literal→enum；list[T]→items；dict[str,T]→additionalProperties；Optional→type 含 null），违规 raise `ValueError` 含参数名 + 字段 + 修复建议
- [x] **新增**：`Annotations:` 段中 signature 不存在的参数 → raise `ValueError`，含参数名 + 行号 + 候选参数名
- [x] 集成 `_view_impl.py`：`MCPToolProvider.list_tools` 用 `function_to_mcp_input_schema` 替换旧 `_infer_schema`；tool description 改用 `function_to_mcp_description`（兜底仍走 `_get_declaration_doc`/`__doc__`）
- [x] 删除旧 `_infer_schema` 函数（无外部引用确认后）
- [x] 新建 `mutio/tests/test_mcp_schema_inference.py`，覆盖：基本类型 / Literal / list / dict / Optional / Any；docstring 主段与 Args 段提取；Annotations 单行与多行 JSON；严格 json.loads（拒绝 `True`/`None`/单引号）；冲突检测每类禁止字段；signature 不存在参数报错；Annotations 段未出现参数不报错
- [x] 运行 mcp 全套测试回归（`pytest tests/test_mcp_*`），确保 `test_mcp_tool_doc.py` 等现有测试不破
- [x] 跑下游消费者：`mutbot/builtins/debug_tools.MutbotTools` 14 函数 schema 验证（启动 mutbot 调 `tools/list` 或单测调用 schema 函数即可，无需写新 docstring）

## 测试验证

- `tests/test_mcp_schema_inference.py` 新增 38 个用例，全过
- `pytest tests/test_mcp_*` 回归 116 个用例，全过
- `pytest`（mutio 全套）213 个用例，全过
- mutbot `MutbotTools` 15 个工具函数全部推导成功，description 可读、properties 表示正确（包括 `Optional[str]` 的 `client_id`、`int` 默认值、空参数 properties）

## 实施自检

- 待定问题已清零（Q1/Q2 已转入设计规范）
- 设计方案各部分无矛盾：
  - 严格 `json.loads` ↔ `Annotations:` 段 multi-line JSON：兼容（`raw_decode` 仍是 JSON 严格解析）
  - signature 没有参数报错 ↔ 段内未出现的参数不报错：方向不冲突（前者是 typo 检测，后者是无约束声明）
  - 不导出 Annotated metadata ↔ 不预设唯一源：模块内分阶段实现已预留扩展点
- 模块边界清晰：`_schema.py` 纯函数无 mutobj 依赖，便于姊妹文档 mutagent 渲染层做 round-trip 测试

## 设计讨论脉络

本规范从 2026-05-14 与用户的协同设计讨论中推导：

1. 否决 mutio L1 路线（Annotated metadata 鸭子识别 — `Annotated[T, Ge(1)]`）— 不符合 "python first + 信息原始位置" 原则
2. 否决 mutagent D1 路线（Args 段 8 空格续行约束行）— 不符合 Google docstring 续行约定，会被 Sphinx Napoleon 当 description 续行
3. 收敛到 docstring 独立 `Annotations:` 段 — python first + 信息原始位置 + 写法 = 展示
4. 收敛到梯度 2（参数名是 docstring 层级、value 是 JSON 一次到底）— 度的标尺：参数是 docstring 一等概念，mcp schema 字段不是
5. 收敛到严格 `json.loads`（无 Python 字面量 fallback）— 一条规则、启动期校验、心智模型与 mcp 协议一致
6. 收敛到 enum 归 signature `Literal[...]`（python first + IDE 补全）
7. 第一版不实现 Annotated 路径，但消极保护 "不预设唯一源"，未来扩展无锁
