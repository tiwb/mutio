# mutio 单元测试 设计规范

**状态**：✅ 已完成
**日期**：2026-04-21
**类型**：功能设计

## 需求

1. mutio 作为独立库需要自己的单元测试，当前仅靠 mutagent/mutbot 间接覆盖
2. 覆盖公共 API：`mutio.net`（HttpClient、Request/Response、辅助函数）、`mutio.mcp`（protocol、toolset）
3. 不需要集成测试（Server 启停、真实 HTTP 连接等），那些由上层项目覆盖

## 关键参考

- `src/mutio/net/client.py` — HttpClient Declaration（`set_default_user_agent` + `create`）
- `src/mutio/net/_client_impl.py` — HttpClient impl，模块级 `_default_user_agent`
- `src/mutio/net/server.py` — Request/Response/View/Server Declaration + `json_response`/`html_response` 辅助函数
- `src/mutio/mcp/protocol.py` — JsonRpcDispatcher、MCP 类型（ToolDef/ToolResult/...）、`make_request`/`make_notification`
- `src/mutio/mcp/toolset.py` — MCPToolSet Declaration
- `src/mutio/mcp/client.py` — MCPClient/MCPError Declaration
- `src/mutio/net/_protocol.py` — `format_sse` 函数
- `pyproject.toml:47-49` — pytest 配置（testpaths=tests, asyncio_mode=auto）

## 设计方案

### 测试范围

按优先级分三层：

**P0 — 纯逻辑，无 I/O 依赖**：
- `format_sse` — SSE 格式化（data/event/id 组合）
- `json_response` / `html_response` — 辅助函数（status、body、content-type）
- MCP 类型 `to_dict` — ToolDef、ToolResult（含 `.text()`/`.error()` 快捷方法）、ResourceDef、ResourceContent、PromptDef、PromptMessage、ServerCapabilities
- `make_request` / `make_notification` — JSON-RPC 消息构造
- `JsonRpcError.to_dict`

**P1 — 需要 mutobj impl 注册**：
- `HttpClient.create` — 默认创建、user_agent 参数、headers 合并、set_default_user_agent 全局设置
- `JsonRpcDispatcher.handle` — 方法分发、notification、错误码、batch 处理
- `JsonRpcDispatcher.handle_bytes` — JSON 解析错误、非法类型

**P2 — Declaration 结构验证**：
- MCPToolSet 类变量（prefix/view/path）可正常访问
- MCPClient 属性默认值
- Request/Response 属性默认值和 `field(default_factory=dict)` 行为

### 测试文件结构

```
tests/
  test_protocol.py      # format_sse
  test_server.py        # json_response, html_response, Request/Response 默认值
  test_client.py        # HttpClient.create, set_default_user_agent
  test_mcp_protocol.py  # JsonRpcDispatcher, MCP 类型, make_request/make_notification
  test_mcp_toolset.py   # MCPToolSet 类变量
```

### 设计要点

- HttpClient 测试需注意全局状态：`_default_user_agent` 是模块级变量，测试间需重置（fixture 中 teardown 调用 `set_default_user_agent("")`）
- JsonRpcDispatcher 测试用 async handler，依赖 `asyncio_mode = "auto"`
- 不 mock httpx.AsyncClient 的内部行为，只验证 `HttpClient.create` 返回的 client 的 headers 配置正确

## 实施步骤清单

- [x] 创建 `tests/test_protocol.py` — format_sse 测试（8 tests）
- [x] 创建 `tests/test_server.py` — json_response、html_response、Request/Response 默认值（14 tests）
- [x] 创建 `tests/test_client.py` — HttpClient.create、set_default_user_agent（11 tests）
- [x] 创建 `tests/test_mcp_protocol.py` — JsonRpcDispatcher、MCP 类型、make_request/make_notification（32 tests）
- [x] 创建 `tests/test_mcp_toolset.py` — MCPToolSet 类变量（4 tests）
- [x] 全部测试通过（69 passed）
