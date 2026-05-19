# JSON 类型安全工具模块

**状态**：✅ 已完成
**日期**：2026-05-19
**类型**：功能设计

## 需求

1. `json.loads()` 返回 `Any`，`isinstance` 窄化后回退为 `dict[Unknown, Unknown]` / `list[Unknown]`，导致全项目 ~50 处 `reportUnknown*` pyright 错误
2. 当前无统一的 JSON 类型定义，各处手写 `dict[str, Any]` 且不递归，嵌套 JSON 结构类型丢失
3. 下游 `mutbot` / `mutagent` / `mutgui` 共有 23+ 文件使用 `json.loads`，存在共享类型词汇的客观需求

## 关键参考

- `src/mutio/codec/json.py` — 本模块实现
- `src/mutio/mcp/protocol.py` — `JsonRpcDispatcher.handle_bytes()` JSON-RPC 解析
- `src/mutio/mcp/_client_impl.py` — `_parse_sse_response()` SSE 响应解析
- `src/mutio/mcp/_view_impl.py` — `mcp_view_post()` 请求体解析
- `src/mutio/mcp/_schema.py` — `_parse_annotations_section()` 注解值解析
- `src/mutio/net/_server_impl.py` — `request_json()` / `receive_json()` 实现
- Python `typing.TypeAlias` — PEP 613（3.10+），显式声明类型别名
- Python `from __future__ import annotations` — PEP 563，让字符串前向引用在递归别名中自然工作
- 不可用：PEP 695 `type` 语句需 3.12+。mutio `requires-python = ">=3.11"`

## 设计方案

### 模块位置：`src/mutio/codec/json.py`

建立 `mutio/codec/` 子包，与 `mutio/mcp/`、`mutio/net/` 平级。首版只有 `json.py` 一个住户，但 codec（编解码）是清晰的领域归属（未来可加 msgpack/bson/cbor 等"JSON 兼容的 binary 表达"）。

选择"过早但无害"建包，而非顶级 `mutio/json.py` 起步，理由：
- 顶级单文件未来若迁入 `codec/`，需保留 deprecation shim，下游已硬编码 import 路径的会承担一次迁移
- 一步到位 `mutio/codec/json.py` 省一次重构。即使长期只有一个文件，目录成本也忽略不计
- 与 `mcp/` / `net/` 子包风格统一

**不放 `mutio/mcp/json.py`** 的原因：`net/_server_impl.py` 也是消费者，归入 `mcp/` 会让 `net` 反向依赖 `mcp`，破坏现有依赖方向。

### 核心类型

mutio 支持 3.11+，无法使用 PEP 695 `type` 语句。采用 `TypeAlias` + 字符串前向引用：

```python
from __future__ import annotations
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue:     TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject:    TypeAlias = dict[str, JsonValue]
JsonArray:     TypeAlias = list[JsonValue]
```

升级到 3.12+ 时可平滑迁移到 PEP 695 `type` 语句。

**命名采用 `JsonValue` / `JsonObject` / `JsonArray` / `JsonPrimitive`**（采用 JSON 规范术语而非 Python 术语；类型别名是变量语义，CamelCase 比 `JSONValue` 全大写更顺，与社区惯例一致）。

### TypeGuard 窄化函数（不需要）

`loads()` 返回 `JsonValue` 后，`isinstance(x, dict)` 就能被 pyright 正确窄化到 `dict[str, JsonValue]`——union 成员已知，`isinstance` 淘汰掉其他分支后类型自然收窄。不需要额外的 TypeGuard。

```python
parsed: JsonValue = json.loads(raw)
if isinstance(parsed, dict):
    parsed.get("key")   # JsonValue | None ✓
if isinstance(parsed, list):
    for item in parsed:           # item: JsonValue ✓
        if isinstance(item, dict):
            item.get("nested")    # JsonValue | None ✓
```

TypeGuard 只在变量是 `Any` 时才有意义。对于 mutio 自身，所有 `json.loads` 已替换为 `from mutio.codec import json`，变量类型始终是 `JsonValue`，不需要 TypeGuard。下游同理——直接从入口走 mutio 的 `json` 模块即可。

### loads / dumps — 替代标准库 json 模块

`mutio.codec.json` 的 `loads` / `dumps` 返回 / 接受 `JsonValue` 而非 `Any`。**不以裸函数方式导入，而是替换 `import json`**：

```python
# 原来
import json
parsed = json.loads(raw)      # Any

# 现在
from mutio.codec import json
parsed = json.loads(raw)      # JsonValue
```

调用处 `json.loads(...)` / `json.dumps(...)` 写法不变，替换 import 即可。`json.JSONDecodeError` 也通过模块透传，`except json.JSONDecodeError` 照常工作。

```python

def loads(
    s: str | bytes | bytearray,
    *,
    cls: ... | None = None,
    object_hook: ... | None = None,
    parse_float: ... | None = None,
    parse_int: ... | None = None,
    parse_constant: ... | None = None,
    object_pairs_hook: ... | None = None,
    **kw: Any,
) -> JsonValue:
    return _stdjson.loads(s, **kw)


def dumps(
    obj: JsonValue,
    *,
    skipkeys: bool = False,
    ensure_ascii: bool = False,
    check_circular: bool = True,
    allow_nan: bool = True,
    cls: ... | None = None,
    indent: int | str | None = None,
    separators: tuple[str, str] | None = None,
    default: ... | None = None,
    sort_keys: bool = False,
    **kw: Any,
) -> str:
    return _stdjson.dumps(obj, **kw)
```

- **签名与标准库完全一致**：所有参数透传，仅入参/返回值类型收紧
- **模块替换而非函数导入**：`from mutio.codec import json` 替换 `import json`，调用处 diff 最小（只改一行 import）
- **`ensure_ascii` 默认为 `False`**：mutio 全栈处理 Unicode，默认输出原字符更友好

### 与现有代码的关系

| 现有代码 | 改动 |
|---------|------|
| `import json` | → `from mutio.codec import json` |
| 无注解 `for key in parsed:` | → 无需改动，`json.loads` 返回 `JsonValue` 后自动推导 |

`json.loads(...)` / `json.dumps(...)` / `isinstance(x, dict)` / `isinstance(x, list)` 调用处写法全部不变。

### 为什么不用 cast 或 TypeGuard

- `cast` 无条件信任，绕过类型检查，丢失安全性
- `loads()` 返回 `JsonValue` 后，`isinstance(x, dict)` 就能被 pyright 正确窄化到 `dict[str, JsonValue]`——union 成员已知，不需要额外 TypeGuard 辅助
- TypeGuard 只在变量是 `Any` 时才需要。全部走 mutio 的 `json` 模块即可消除 `Any` 入口

### 互操作

`JsonValue` 可赋值给 `Any`（协变），`dict[str, JsonValue]` 可赋值给 `dict[str, Any]`。现有接受 `dict[str, Any]` 的函数无需改动即可接受 `JsonObject`：

```python
d: JsonObject = {"key": "value"}

def handle(payload: dict[str, Any]) -> None: ...
handle(d)  # ✓ 兼容
```

### 未来演进路径（非本次实施）

记录待两个真实需求触发时的方向，避免本次过度设计：

- **加入 binary codec（msgpack / bson / cbor）**：
  ```
  mutio/codec/
  ├── types.py      # 从 codec/json.py 提升出共享类型
  ├── json.py       # loads/dumps + JSON 类型
  └── msgpack.py    # 新增
  ```
- **抽离 JSON-RPC 协议层**（mcp 之外出现第二个 JSON-RPC 消费者时）：
  - JSON-RPC 是 protocol 不是 codec，归属 `mutio/jsonrpc.py` 顶级，**不放 codec/ 下**
  - 当前仅 mcp 一家使用，留在 `mcp/protocol.py` 不抽

## 实施步骤清单

- [x] 创建 `src/mutio/codec/__init__.py`
- [x] 创建 `src/mutio/codec/json.py`，实现递归类型别名 + loads/dumps + JSONDecodeError 透传
- [x] 跑 `pytest` 确认无回归（213 passed）


