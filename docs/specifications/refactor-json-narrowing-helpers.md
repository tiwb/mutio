# JSON 类型窄化工具链重构

**状态**：✅ 已完成
**日期**：2026-06-05
**类型**：重构

## 需求

1. `mutio` 大量接口返回 `dict[str, Any]`、`list[dict[str, Any]]`、`Any`，类型太宽，调用方需要反复 `isinstance` + `cast`，且 Pyright `reportUnknown*` 大面积报错
2. 旧 `get_as(obj, key, typ, default)` 只用浅层 `isinstance` 校验，不支持 `list[str]`、`dict[str, int]` 等容器形状递归检查
3. `get_as` 签名的 `default` 参数语义模糊——无法区分"key 缺失"和"类型不匹配"两种失败
4. 缺少按索引取 `list` 元素的对应原语

## 设计方案

### 核心三件套

引入三个窄化原语，覆盖 JSON 取值全场景：

| 函数 | 用途 | 入参 |
|------|------|------|
| `get_field(obj, key, typ)` | 从 dict 按键取值 + 窄化 | `JsonValue` → dict → field |
| `get_element(arr, index, typ)` | 从 list 按索引取值 + 窄化 | `JsonValue` → list → element |
| `narrow_value(value, typ)` | 任意 JsonValue 窄化 | 已有 JsonValue |

三者共享内部实现 `_coerce_value()`，底层调用 `check_type()` 做递归形状校验。

### 两种失败模式分离

每个函数通过 `overload` 签名区分三种调用模式：

| 模式 | 参数 | key 缺失行为 | 类型不匹配行为 |
|------|------|-------------|---------------|
| required（无 `default`/`fallback`） | `get_field(obj, key, int)` | 抛 `KeyError` | 抛 `TypeError` |
| default（仅 key 缺失兜底） | `get_field(obj, key, int, default=-1)` | 返回 `default` | 抛 `TypeError` |
| default + fallback（分别兜底） | `get_field(obj, key, int, default=-1, fallback=0)` | 返回 `default` | 返回 `fallback` |

`get_element` 同理（索引越界 → `IndexError` / `default`）。

`narrow_value` 只有 `fallback` 参数（无 key/index 的概念）。

### `check_type` 递归校验

运行时递归检查 JsonValue 是否匹配指定类型 shape：

- 基础类型：`str` / `int` / `float` / `bool` / `NoneType` → `isinstance`
- 泛型容器：`list[X]` / `dict[K, V]` → 递归 check_type 每个元素
- Union：`str | int` → 任一分支通过即可
- 终端短路：`JsonValue` / `Any` / `ForwardRef` / `str` → 直接通过
- int→float 兼容：`check_type(0, float)` → `True`（JSON `json.loads` 读回 int `0`，dataclass float 字段默认值为 int 字面量时 round-trip 失败）

### `JsonValue` 类型别名调整

```python
# 旧（list 不变性导致 list[dict[str, JsonValue]] 不满足 JsonValue）
JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

# 新（协变 Mapping/Sequence，绕过不变性）
JsonValue = JsonPrimitive | Sequence["JsonValue"] | Mapping[str, "JsonValue"]

# 便捷别名保持 dict/list，保证构建侧可原地修改
JsonObject = dict[str, JsonValue]
JsonArray = list[JsonValue]
```

### 全仓库类型收紧

依赖链上所有模块同步收紧：

| 模块 | 改动 |
|------|------|
| `mcp/client.py` | 声明接口 `dict[str, Any]` → `JsonObject` |
| `mcp/_client_impl.py` | 实现侧对齐 + `get_as` → `get_field` |
| `mcp/_schema.py` | `function_to_mcp_input_schema` 返回、内部变量 |
| `mcp/_view_impl.py` | 所有 handler 返回类型 |
| `mcp/protocol.py` | `JsonRpcError.data`、`make_request`/`make_notification` 参数 |
| `mcp/view.py` | `MCPView.extra_capabilities` 返回 |
| `net/server.py` | `Request.json()`、`JSONResponse`、`WebSocketConnection` |
| `net/_server_impl.py` | 实现侧对齐 |
| `schema/docstring.py` | `parse_annotations_section` 返回 |
| `schema/jsonschema.py` | `annotation_to_json_schema` 返回 |
| `schema/funcinfo.py` | `Callable` → `Callable[..., Any]` |

同时新增 `src/mutio/py.typed`（PEP 561 marker）并在 `pyproject.toml` 声明 package-data。

### Pyright 清理

- 移除不再需要的 `cast()` 调用和 `from typing import cast` 导入
- `_normalize_prompt_result(result)` 参数类型 `Any` → `object`
- `_is_json_compatible` 利用 `value: JsonValue` 参数约束简化内部 cast

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|--------|------|-----------|---------|
| `mcp/_client_impl._parse_sse_response` | 解析 SSE 错误响应 | `get_field(err, "code", int, default=-1)` | 类型窄化后无需手动 `isinstance` |
| 下游 `mutagent`/`mutbot` | MCP client 调用结果处理 | `list_tools()` → `list[JsonObject]` | Pyright 不再报 `reportUnknown*` |
| 所有 JSON 配置/响应消费者 | 从 `JsonValue` 提取嵌套字段 | `get_field` + `get_element` + `narrow_value` 三件套 | 递归 shape 校验 + 类型安全 |

## 关键参考

- `src/mutio/codec/json.py` — 核心实现：`check_type`、`get_field`、`get_element`、`narrow_value`、`_coerce_value`
- `tests/test_codec_json.py` — 测试：`TestCheckType*`、`TestGetField`、`TestGetElement`、`TestNarrowValue`、`TestPyrightTypeInference`
- `src/mutio/mcp/client.py` — MCPClient 声明接口类型收紧
- `src/mutio/mcp/_client_impl.py` — 调用方 `get_field` 使用示例
- `src/mutio/net/server.py` / `_server_impl.py` — Request/Response/WebSocket 接口收紧
- `src/mutio/schema/jsonschema.py` — `annotation_to_json_schema` 返回类型收紧
- `pyproject.toml` — `py.typed` package-data 声明
