# WebSocket 预期断连归一化 设计规范

**状态**：✅ 已完成
**日期**：2026-04-30
**类型**：Bug修复

## 需求

1. Windows 下页面刷新或浏览器主动断开时，底层 transport 可能抛出 `ConnectionResetError`、`BrokenPipeError`、`ConnectionAbortedError` 或特定 `OSError`
2. 这类“预期断连”不应被上层 websocket 业务当成真实异常，也不应在 asyncio 未捕获异常日志里刷 traceback
3. mutio 需要在底层统一收口，让上层只处理 `WebSocketDisconnect` 语义

## 关键参考

- `src/mutio/net/server.py` — `WebSocketDisconnect` 定义；新增“预期断连”判断函数的公开收口位置
- `src/mutio/net/_protocol.py` — `WSProtocol` 的 send/close/connection_lost 路径；transport 异常归一化的核心位置
- `src/mutio/net/asgi.py` — mutio 自建 event loop / asyncio exception handler 安装点
- `tests/test_server.py` — 预期断连分类测试
- `tests/test_protocol.py` — websocket protocol 对 transport 断连与 debug 日志语义的测试

## 设计方案

- 在 `mutio.net.server` 提供 `_is_expected_disconnect_error()`，统一识别跨平台的预期断连异常
- `WSProtocol` 的写路径不再裸写 transport；统一经过 `_write_or_disconnect()`，命中预期断连时转成 `WebSocketDisconnect(1006)` 并入队 `websocket.disconnect`
- `WSProtocol._run_asgi()` 对 `WebSocketDisconnect` 和预期 transport 异常仅记 `DEBUG`，保留真实异常的 traceback
- mutio ASGI server 在 event loop 上安装最小 asyncio exception handler，把未被消费的预期断连降级为 debug，避免污染应用层日志

## 实施步骤清单

- [x] 在 `src/mutio/net/server.py` 增加预期断连识别函数
- [x] 在 `src/mutio/net/_protocol.py` 统一 websocket transport 写路径并归一化 disconnect 语义
- [x] 在 `src/mutio/net/asgi.py` 增加 asyncio exception handler 兜底
- [x] 在 `tests/test_server.py`、`tests/test_protocol.py` 补充回归测试
