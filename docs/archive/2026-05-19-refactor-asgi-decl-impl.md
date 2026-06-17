# ASGI Server Decl-Impl 分离

**状态**：✅ 已完成
**日期**：2026-05-19
**类型**：重构

## 需求

1. `mutio/net/asgi.py` 当前是纯具体实现（`Server` 类 + 辅助函数），需要跟 `client.py`/`server.py` 一样做 mutobj decl-impl 分离
2. 保持与其他 Decl-Impl 模式一致：Declaration 定义契约、Impl 通过 `@mutobj.impl` 注入、Extension 承载私有状态

## 关键参考

- `mutio/src/mutio/net/asgi.py` — 当前实现（需改造）
- `mutio/src/mutio/net/server.py` — Declaration 模式参考
- `mutio/src/mutio/net/_server_impl.py` — Impl 模式参考
- `mutio/src/mutio/net/client.py` — Declaration 模式参考
- `mutio/src/mutio/net/_client_impl.py` — Impl 模式参考
- `mutbot/src/mutbot/ptyhost/__main__.py` — 下游引用方（`setattr(server, "should_exit", True)`）
- `mutbot/tests/test_server.py` — 下游测试（直接调用 `_lifespan_startup()` 等内部方法）

## 设计方案

### 接口设计

`ASGIServer` Declaration 只暴露调用方真正需要的入口，内部生命周期编排不泄露。

```python
class ASGIServer(mutobj.Declaration):
    """轻量 ASGI 传输层 server。"""

    def __init__(self, app: Any, *, root_path: str = "") -> None: ...

    @property
    def ports(self) -> list[int]: ...

    def run(self, *, host=None, port=None, sockets=None, on_startup=None) -> None: ...
        """阻塞运行，内部自动完成 lifespan → TCP → main_loop → shutdown。"""

    async def start(self, *, host=None, port=None, sockets=None) -> None: ...
        """异步启动（在已有 event loop 中使用），内部自动完成 lifespan startup → TCP listen。"""

    async def shutdown(self) -> None: ...
        """Graceful shutdown，内部自动完成 TCP close → lifespan shutdown。"""

    def signal_exit(self) -> None: ...
        """外部信号触发优雅退出（中断 main_loop）。"""
```

### 关键决策

**1. 类名 `Server` → `ASGIServer`**：原 `asgi.py` 的 `Server` 与 `server.py` 的 `Server` 同名，下游都是 `from mutio.net.asgi import Server as _ASGIServer` 使用。改为 `ASGIServer` 后语义更准确，下游只需去掉 `as` 别名。

**2. `lifespan_startup()` / `_lifespan_shutdown()` 不暴露**：这两个是内部编排步骤，无子类覆盖场景。合并进 `start()` / `shutdown()` 内部。

**3. `lifespan_startup_failed` 标志消除**：ASGI 规范中 startup 失败是硬错误。实现层在收到 `startup.failed` 时直接 `raise RuntimeError`，不再用状态标志静默处理（对齐 uvicorn 做法）。

**4. `should_exit` → `signal_exit()`**：`should_exit` 是 main_loop 的轮询标志，pyt host 外部通过 `setattr` 设置。改为 `signal_exit()` 方法，内部委托到 Extension 设置标志。

**5. `scope_runner` 保留为内部辅助函数**：只被 `lifespan_startup` 内部调用，无外部引用，留在 impl 文件即可。

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `mutio/net/asgi.py` | 改造 | 变为 Declaration 文件，`ASGIServer` + `from . import _asgi_impl` |
| `mutio/net/_asgi_impl.py` | 新建 | 所有实现逻辑 + `ASGIServerExt` Extension |
| `mutio/net/_server_impl.py` | 修改 | 类名 `Server` → `ASGIServer`，简化 `server_start()` 调用 |
| `mutbot/ptyhost/__main__.py` | 修改 | 类名 + `setattr` → `signal_exit()` |
| `mutbot/tests/test_server.py` | 修改 | 类名 + 内部编排 → `start()` / `shutdown()` 高级接口 |

### 下游影响

| 引用方 | 改动内容 |
|--------|----------|
| mutagent | 不引用，无影响 |
| mutgui | 不引用，无影响 |
| chrome-cdp | 不引用 `asgi`，无影响 |

## 测试验证

- `mutio` 现有测试全部通过（`pytest mutio/tests/`）
- `mutbot` 现有测试全部通过（`pytest mutbot/tests/`）
- ptyhost 启动正常

## 实施步骤清单

- [x] 新建 `mutio/net/_asgi_impl.py` — 搬迁所有实现逻辑 + 定义 `ASGIServerExt` Extension
- [x] 改造 `mutio/net/asgi.py` — 替换为 `ASGIServer(mutobj.Declaration)` + 桩方法 + import 触发
- [x] 修改 `mutio/net/_server_impl.py` — 类名 `Server` → `ASGIServer`，简化 `server_start()`
- [x] 修改 `mutbot/ptyhost/__main__.py` — 类名 + `setattr` → `signal_exit()`
- [x] 修改 `mutbot/tests/test_server.py` — 类名 + 改为 `start()` / `shutdown()` 高级接口
- [x] 运行 mutio 和 mutbot 全部测试验证
