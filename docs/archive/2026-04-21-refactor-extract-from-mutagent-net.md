# mutio 项目初始化 — 从 mutagent.net 抽取基础设施库 设计规范

**状态**：✅ 已完成
**日期**：2026-04-21
**类型**：重构

## 需求

1. 将 `mutagent.net` 模块抽取为独立项目 `mutio`，作为 mutobj 生态的基础设施工具箱
2. 正确分层：`mutio.net`（网络）+ `mutio.mcp`（MCP 协议）作为独立顶层模块
3. mutagent 保留兼容 re-export 层，现有代码零改动可工作
4. 统一 HttpClient API：全局默认 user_agent + 创建时覆盖
5. MCPClient 统一走 HttpClient.create，不再直接创建 httpx.AsyncClient

## 关键参考

- `mutagent/src/mutagent/net/` — 源模块，10 个文件 ~3010 行
- `mutagent/src/mutagent/net/_protocol.py` — HTTP/1.1 + WebSocket 协议，依赖 h11/wsproto
- `mutagent/src/mutagent/net/asgi.py` — ASGI server
- `mutagent/src/mutagent/net/server.py` + `_server_impl.py` — View/Request/Response/Server 声明与实现
- `mutagent/src/mutagent/net/client.py` + `_client_impl.py` — HttpClient/MCPClient 声明与实现
- `mutagent/src/mutagent/net/mcp.py` + `_mcp_impl.py` + `_mcp_proto.py` — MCP 协议层
- `mutobj/docs/specifications/feature-class-level-attr-assignment.md` — 未来 DeclarationMeta.__setattr__ 支持

### 依赖分析

**mutagent.net 对 mutagent 的唯一依赖**：`_client_impl.py:28` 的 `mutagent.__version__`（User-Agent）

**mutagent 内部依赖 net/ 的文件**（6 个）：
- `builtins/web_toolkit_impl.py` → `HttpClient`
- `builtins/web_local.py` → `HttpClient`
- `builtins/web_jina.py` → `HttpClient`
- `builtins/anthropic_provider.py` → `HttpClient`
- `builtins/openai_provider.py` → `HttpClient`
- `sandbox/tools.py` → `MCPToolSet`, `ToolResult`

**mutbot 依赖 net/ 的文件**（13+ 个）：
- `auth/middleware.py` → `Server, Response`
- `auth/relay.py` → `View, Request, Response, json_response`
- `auth/views.py` → `View, Request, Response, json_response, html_response`
- `builtins/http_client.py` → `HttpClient`
- `proxy/routes.py` → `HttpClient, View, Request, Response, StreamingResponse, json_response, html_response`
- `ptyhost/__main__.py` → `asgi.Server`
- `web/mcp.py` → `MCPView, MCPToolSet`
- `web/routes.py` → `View, WebSocketView, WebSocketConnection, WebSocketDisconnect, json_response, Response`
- `web/server.py` → `Server, StaticView`
- `web/transport.py` → `WebSocketConnection`

## 设计方案

### 项目结构

```
mutio/
  pyproject.toml
  src/mutio/
    __init__.py             # __version__ = "0.1.999"
    net/
      __init__.py           # loads _server_impl, _client_impl
      _protocol.py          # HTTP/1.1 + WebSocket (h11, wsproto)
      asgi.py               # ASGI server
      server.py             # View, Request, Response, Server 等 Declaration
      _server_impl.py       # @impl
      client.py             # HttpClient Declaration
      _client_impl.py       # @impl
    mcp/
      __init__.py           # loads _view_impl, _client_impl; re-exports 公共 API
      protocol.py           # JSON-RPC 2.0 + MCP 类型（原 _mcp_proto.py，零外部依赖）
      toolset.py            # MCPToolSet Declaration（只依赖 mutobj）
      view.py               # MCPView(View) Declaration（imports mutio.net.server）
      _view_impl.py         # MCPView impl + MCPToolProvider
      client.py             # MCPClient Declaration
      _client_impl.py       # MCPClient impl
```

依赖方向：`mutio.mcp → mutio.net → mutobj`（单向）

### MCP 独立为顶层模块的理由

MCP 代码内部有两种截然不同的关注点：

| 组件 | 依赖 net/ ? | 典型使用者 |
|------|-------------|-----------|
| `protocol.py`（JSON-RPC + MCP 类型） | 零依赖，纯标准库 | 内部 |
| `MCPToolSet`（工具注册） | 零依赖，只用 mutobj | 工具作者（mutagent/sandbox） |
| `MCPView`（HTTP 端点） | 依赖 View | 服务端（mutbot/web） |
| `MCPClient`（HTTP 客户端） | 依赖 HttpClient | 客户端 |

MCPToolSet 是最高频公共 API，工具作者不应从 net 模块导入。MCP 规范本身不限于 HTTP（还有 stdio transport），独立模块为未来扩展预留空间。

### HttpClient API 设计

声明文件（`mutio/net/client.py`）只有干净定义：

```python
class HttpClient(mutobj.Declaration):
    @classmethod
    def set_default_user_agent(cls, ua: str) -> None:
        """设置全局默认 User-Agent。"""
        ...

    @staticmethod
    def create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
        """创建 httpx.AsyncClient。user_agent 传则覆盖全局默认。"""
        ...
```

实现文件（`mutio/net/_client_impl.py`）：

```python
_default_user_agent = ""

@mutobj.impl(HttpClient.set_default_user_agent)
def _set_default_user_agent(cls, ua: str) -> None:
    global _default_user_agent
    _default_user_agent = ua

@mutobj.impl(HttpClient.create)
def _create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
    ua = user_agent if user_agent is not None else _default_user_agent
    headers = dict(kwargs.pop("headers", None) or {})
    if ua:
        headers.setdefault("user-agent", ua)
    kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)
```

优先级：**参数 > 全局默认**，空则不设 header。

设计决策：user_agent 不用 Declaration 属性声明，因为 `AttributeDescriptor` 不支持运行时类级赋值（mutobj 未来会修复，见 `mutobj/docs/specifications/feature-class-level-attr-assignment.md`）。通过 classmethod 提供 OOP 风格接口，模块变量存值在 impl 文件中。

### MCPClient 统一走 HttpClient

当前 `_client_impl.py:57` 直接 `httpx.AsyncClient(timeout=self.timeout)` 绕过了 HttpClient。迁移时统一：

```python
@mutobj.impl(MCPClient.connect)
async def _connect(self: MCPClient) -> None:
    ext._http = HttpClient.create(
        user_agent=f"mutio-mcp/{mutio.__version__}",
        timeout=self.timeout,
    )
    ...
```

### pyproject.toml 依赖

```
mutio:     dependencies = ["mutobj~=0.6.0", "h11", "wsproto", "httpx>=0.27"]
mutagent:  dependencies 中 +mutio~=0.1.0，去掉 h11/wsproto/httpx（由 mutio 传递）
mutbot:    不变（通过 mutagent 传递依赖 mutio）
```

### mutagent 兼容层

`mutagent.net` 变为 re-export 薄层，现有 import 路径不变：

```python
# mutagent/net/__init__.py — 触发 impl 注册 + re-export
from mutio.net import *
from mutio.net import _server_impl as _server_impl
from mutio.net import _client_impl as _client_impl

# mutagent/net/mcp.py
from mutio.mcp import MCPToolSet, MCPView

# mutagent/net/client.py
from mutio.net.client import *
from mutio.mcp.client import *

# mutagent/net/_mcp_proto.py
from mutio.mcp.protocol import *

# mutagent/net/server.py
from mutio.net.server import *

# mutagent/net/asgi.py
from mutio.net.asgi import *

# mutagent/net/_protocol.py
from mutio.net._protocol import *
```

mutagent 启动时设置 user_agent：

```python
# mutagent/__init__.py 或合适的入口
from mutio.net.client import HttpClient
HttpClient.set_default_user_agent(f"mutagent/{__version__}")
```

### import 路径迁移对照

| 旧路径 | 新路径 | 兼容层保留 |
|--------|--------|-----------|
| `mutagent.net.server.View` | `mutio.net.server.View` | 是 |
| `mutagent.net.server.Request` | `mutio.net.server.Request` | 是 |
| `mutagent.net.server.Response` | `mutio.net.server.Response` | 是 |
| `mutagent.net.server.Server` | `mutio.net.server.Server` | 是 |
| `mutagent.net.client.HttpClient` | `mutio.net.client.HttpClient` | 是 |
| `mutagent.net.client.MCPClient` | `mutio.mcp.client.MCPClient` | 是 |
| `mutagent.net.mcp.MCPToolSet` | `mutio.mcp.toolset.MCPToolSet` | 是 |
| `mutagent.net.mcp.MCPView` | `mutio.mcp.view.MCPView` | 是 |
| `mutagent.net._mcp_proto.ToolResult` | `mutio.mcp.protocol.ToolResult` | 是 |
| `mutagent.net._mcp_proto.JsonRpcDispatcher` | `mutio.mcp.protocol.JsonRpcDispatcher` | 是 |
| `mutagent.net.asgi.Server` | `mutio.net.asgi.Server` | 是 |
| `mutagent.net._protocol.format_sse` | `mutio.net._protocol.format_sse` | 是 |

### 版本号

0.1.999 起步，独立版本线。

### Logger name

迁移为 `mutio.net`、`mutio.mcp` 等，mutagent/mutbot 日志配置同步更新。

## 实施步骤清单

- [x] 创建 mutio 项目骨架（pyproject.toml、src/mutio/__init__.py、git init）
- [x] 迁移 mutio.net 模块（_protocol.py、asgi.py、server.py、_server_impl.py、client.py、_client_impl.py），更新内部 import
- [x] 迁移 mutio.mcp 模块（protocol.py、toolset.py、view.py、_view_impl.py、client.py、_client_impl.py），更新内部 import
- [x] HttpClient API 改造（set_default_user_agent classmethod，create 增加 user_agent 参数）
- [x] MCPClient 统一走 HttpClient.create
- [x] Logger name 更新为 mutio.*
- [x] 验证 mutio 独立可用（pip install -e、import 测试）
- [x] mutagent 兼容层（net/ 目录改为 re-export 薄层，pyproject.toml 加 mutio 依赖）
- [x] 验证 mutagent 测试通过（752 passed, 5 skipped）
- [x] 验证 mutbot 启动正常（522 passed，1 pre-existing failure 不相关）
