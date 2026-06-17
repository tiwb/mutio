# MCP Prompt Declaration 模式设计

**状态**：✅ 已完成
**日期**：2026-04-23
**类型**：功能设计

## 背景

`mutio.mcp` 已通过 `MCPToolSet` + `MCPToolProvider` 实现 tool 的 Declaration 自动发现（方法名 = tool name，签名 → inputSchema，返回值 → `ToolResult`）。MCP 协议另外两种能力 resource 和 prompt 目前只有协议 dataclass（`PromptDef`、`PromptMessage`、`ResourceContent`），没有业务层注册路径。

本规范只覆盖 **prompt**，对称 tool 的已有模式；resource 的 URI 标识问题另开规范。

### 使用场景

- mutbot 作为 MCP server 暴露给 Claude Code、Claude Desktop 等 host
- 用户通过斜杠命令（`/mcp__<server>__<prompt_name> [args]`）手动触发预设 prompt
- server 端基于 arguments 动态组装 messages 注入客户端对话

### 和 tool 的差异

| | Tool | Prompt |
|--|------|--------|
| 标识 | 方法名 | 方法名 |
| 输入 | 任意类型参数 | 仅字符串参数（MCP 协议约束） |
| 输出 | 文本 / `ToolResult` | 一条或多条 `PromptMessage` |
| 消费方 | agent 自动调用 | 人类用户手动触发 |
| 列表 API | `tools/list` | `prompts/list` |
| 调用 API | `tools/call` | `prompts/get` |

关键协议约束：**prompt 的 arguments 只能是字符串类型**（MCP spec 定义 `PromptArgument = {name, description, required}`，不含 JSON Schema）。这和 tool 的 inputSchema 根本不同。

## 关键参考

### 源码
- `mutio/src/mutio/mcp/toolset.py` — MCPToolSet 基类（对称参考）
- `mutio/src/mutio/mcp/_view_impl.py` — `MCPToolProvider` 实现（发现逻辑、list/call 分发）
- `mutio/src/mutio/mcp/protocol.py:257` — `PromptDef` / `PromptMessage` / `ServerCapabilities.prompts` 已有定义

### 相关规范
- `mutio/docs/specifications/refactor-extract-from-mutagent-net.md` — net 层下沉到 mutio（已完成）
- `mutagent/docs/specifications/feature-mcp-declarations.md` — 旧设计提案（prompt + resource 合并讨论，本文档从中拆出 prompt 部分）

### 协议规范
- MCP `prompts/list`：返回 `{prompts: [{name, description, arguments: [{name, description, required}]}]}`
- MCP `prompts/get`：请求 `{name, arguments: {k: v, ...}}`，返回 `{description?, messages: [{role, content}]}`
- content 类型：`text` / `image` / `audio` / `resource`（本规范只关注 `text`，其他透传）

## 设计方案

### 基类 `MCPPromptSet`

对称 `MCPToolSet`：

```python
# mutio/src/mutio/mcp/promptset.py
class MCPPromptSet(mutobj.Declaration):
    """MCP prompt 集合基类。方法名 = prompt name，方法参数 = prompt arguments。"""
    prefix = ""
    view = None       # type: type[MCPView] | tuple[type[MCPView], ...] | None
    path = ""         # type: str | tuple[str, ...]
```

三个元信息字段含义与 `MCPToolSet` 一致（prefix 拼接 prompt name；view/path 决定归属哪个 `MCPView`）。

### 方法签名约束

- **参数**：只支持 `str` 类型参数（协议约束）。允许有默认值（→ `required=False`）。
- **返回值多态**：
  - `str` → 单条 user text message
  - `PromptMessage` → 单条自定义 message
  - `list[PromptMessage]` → 多条 messages
- **方法级 description**：复用 `_get_declaration_doc()` 提取 docstring（与 tool 一致，沿 `_impl_chain` 取声明端 docstring，避开 `@impl` 覆盖）
- **参数级 description**：不支持（与 tool 对等；tool 当前 `_infer_schema` 也没生成参数 description。若将来要做，统一升级 tool + prompt 走 docstring 段落解析）

`_view_impl` 侧的归一化逻辑类似 tool：`str` 包装成 `[PromptMessage(role="user", content={"type":"text","text":s})]`。

### Provider（`MCPPromptProvider`）

在 `_view_impl.py` 里对称添加，与 `MCPToolProvider` 完全平行：

- `refresh()` 用 `discover_subclasses(MCPPromptSet)` + `_match_view()` 筛选
- 扫类的公共方法，过滤 `prefix`/`view`/`path` 和基类方法
- 生成 `{prompt_name: (instance, method_name)}` 映射
- `list_prompts()` 产生 `PromptDef` 列表；arguments 从签名提取（`str` 参数名 + 默认值判 required）
- `call_prompt(name, args)` 调方法，归一化返回值为 `list[PromptMessage]`

### JSON-RPC 分发

`_view_impl.py` 底部的 `ext._dispatch.add_method(...)` 追加：

```python
ext._dispatch.add_method("prompts/list", _handle_prompts_list)
ext._dispatch.add_method("prompts/get", _handle_prompts_get)
```

handler 从 ext 上取 `prompt_provider`（新增字段，与 `tool_provider` 并列）。

### Capabilities 声明

对齐 tool 的动态策略（`_view_impl.py:245-255` 现状）：`list_prompts()` 非空时才在 `ServerCapabilities` 里声明 `"prompts": {"listChanged": False}`，空则不声明该 capability。

```python
async def _handle_initialize(params):
    tools = tp.list_tools()
    prompts_list = pp.list_prompts()
    capabilities = ServerCapabilities(
        tools={"listChanged": False} if tools else None,
        prompts={"listChanged": False} if prompts_list else None,
    )
```

### Arguments schema 推断

独立的小函数（对称 `_infer_schema`）：

```python
def _infer_prompt_arguments(method) -> list[dict[str, Any]]:
    """从签名提取 arguments：仅支持 str 类型参数。"""
    sig = inspect.signature(method)
    args = []
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        # 类型必须是 str 或无注解（默认当 str 处理）
        annotation = param.annotation
        if annotation is not inspect.Parameter.empty and annotation is not str:
            raise TypeError(f"MCPPromptSet method parameter must be str, got {annotation}")
        required = param.default is inspect.Parameter.empty
        args.append({"name": name, "required": required})
    return args
```

非 `str` 参数直接抛错，帮用户尽早发现（tool 那边是宽松 Any，prompt 这边协议卡死只能 str）。

### 返回值归一化

```python
def _normalize_prompt_result(result) -> list[PromptMessage]:
    if isinstance(result, str):
        return [PromptMessage(role="user", content={"type": "text", "text": result})]
    if isinstance(result, PromptMessage):
        return [result]
    if isinstance(result, list) and all(isinstance(m, PromptMessage) for m in result):
        return result
    raise TypeError(f"Prompt method must return str | PromptMessage | list[PromptMessage], got {type(result)}")
```

## 使用示例

```python
from mutio.mcp import MCPPromptSet, PromptMessage

class MutbotPrompts(MCPPromptSet):
    path = "/mcp"

    def status(self) -> str:
        """查看 mutbot 服务器状态。"""
        return "请调用 `mcp__mutbot__pysandbox` 执行 `mutbot.status()` 并汇总。"

    def logs(self, level: str = "ERROR") -> str:
        """查看最近 mutbot 日志。"""
        return (
            f"请调用 `mcp__mutbot__pysandbox` 执行 `mutbot.logs(level='{level}', last_n=20)`，"
            "展开未捕获异常的 traceback，总结关键错误。"
        )
```

暴露后在 Claude Code 里：
- `/mcp__mutbot__status`
- `/mcp__mutbot__logs ERROR`

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|--------|------|-----------|---------|
| mutbot 的 MCPView | 注册若干 `MCPPromptSet` 子类 | `prompts/list` 返回正确的 name/description/arguments；`prompts/get` 能注入 messages | Claude Code 连接后 `/mcp__mutbot__xxx` 出现在斜杠补全；触发后对话中插入预设 user 消息 |
| 本规范之外的 MCP client | 任意符合 MCP 协议的 host | 同上 | JSON-RPC 报文严格符合 MCP spec |

## 待定问题

（无）

## 遗留问题

### 动态 prompt 增删（`list_changed` 通知）
- **现状**：Step 1 硬编码 `listChanged: False`，prompt 集合变化需要 client 重连才能看到。
- **协议支持**：MCP 有 `notifications/prompts/list_changed`，server 声明 `listChanged: true` 后可主动推送。
- **实现障碍**：mutio 目前只实现 Streamable HTTP 的 POST 分支（request-response），没有 server-initiated notification 通道（需要 client 持有一条长连 SSE GET 流）。
- **客户端兼容性**：Claude Code 是否订阅 `list_changed` 未经验证。即使 server 推了，client 不订阅也白搭。
- **触发条件**：当 mutbot 热重载频繁且有明确"不重连也要看到新 prompt"需求时再做。先让用户重连 MCP server 是可接受的折中。

## 实施步骤清单

- [x] 新建 `mutio/src/mutio/mcp/promptset.py` — `MCPPromptSet` 基类（prefix/view/path 三字段，对称 `toolset.py`）
- [x] `mutio/src/mutio/mcp/__init__.py` 导出 `MCPPromptSet`、`PromptMessage`（便于消费者 `from mutio.mcp import ...`）
- [x] `_view_impl.py` 新增 `MCPPromptProvider` 类 — 参照 `MCPToolProvider`，用 `discover_subclasses(MCPPromptSet)` + `_match_view` 筛选
  - [x] `_infer_prompt_arguments()` 辅助函数，仅接受 `str` 参数，非 str 抛 `TypeError`（内部走 `typing.get_type_hints` 以兼容 `from __future__ import annotations`）
  - [x] `_normalize_prompt_result()` 辅助函数，`str | PromptMessage | list[PromptMessage]` 归一化为 `list[PromptMessage]`
  - [x] `list_prompts()` 复用 `_get_declaration_doc()` 取 description
  - [x] `call_prompt(name, args)` 同步/异步方法通用（参考 `call_tool` 的 await 模式）
- [x] `_MCPViewExt` 追加 `_prompt_provider: MCPPromptProvider | None = None` 字段
- [x] `_get_ext()` 初始化 `_prompt_provider`（与 `_tool_provider` 并列）
- [x] `_setup_handlers()` 注册 `prompts/list` 和 `prompts/get` handler
- [x] `_handle_initialize()` capabilities 追加 prompts 声明（动态策略，prompt 非空才声明）
- [x] 单元测试 `tests/test_mcp_promptset.py` — `MCPPromptSet` 默认值、子类继承（对齐 `test_mcp_toolset.py`）
- [x] 单元测试 `tests/test_mcp_prompt_provider.py` — 覆盖发现、arguments 推断（必填/可选/非 str 报错）、返回值三种形态归一化、`list_prompts`/`call_prompt` 语义
- [x] 跑 `pytest` 全量，确认无回归（95 passed）
