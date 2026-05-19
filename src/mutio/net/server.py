"""Web 框架 Declaration — Server / View / Request / Response 等。

所有公开类型均为 mutobj.Declaration，实现在 _server_impl.py 中。
"""

from __future__ import annotations

import socket as _socket
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Sequence

import mutobj


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class Request(mutobj.Declaration):
    """HTTP 请求。"""
    method: str = "GET"
    path: str = "/"
    raw_path: str = "/"
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)

    async def body(self) -> bytes:
        """读取原始请求体。"""
        ...

    async def json(self) -> Any:
        """读取请求体并解析为 JSON。"""
        ...


class Response(mutobj.Declaration):
    """HTTP 响应。"""
    status_code: int = 200
    body: bytes = b""
    headers: dict[str, str] = mutobj.field(default_factory=dict)


class StreamingResponse(mutobj.Declaration):
    """流式 HTTP 响应。"""
    status_code: int = 200
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    body_iterator: AsyncIterator[bytes] | None = None
    media_type: str = "text/event-stream"


class JSONResponse(Response):
    """JSON 响应。content 经 render() 序列化为 bytes,自动设 content-type。

    覆盖 render() 可替换序列化逻辑(如使用 orjson、自定义 datetime/Decimal 编码),
    通过 ``@mutobj.impl(JSONResponse.render)`` 注入新实现。
    """

    def __init__(self, content: Any, status_code: int = 200) -> None: ...

    def render(self, content: Any) -> bytes:
        """将 content 序列化为 bytes。子类/扩展通过 @impl 覆盖。"""
        ...


class HTMLResponse(Response):
    """HTML 响应。content-type = text/html; charset=utf-8。"""

    def __init__(self, content: str | bytes, status_code: int = 200) -> None: ...


class PlainTextResponse(Response):
    """纯文本响应。content-type = text/plain; charset=utf-8。"""

    def __init__(self, content: str | bytes, status_code: int = 200) -> None: ...


class RedirectResponse(Response):
    """重定向响应。

    默认 307(临时,保 method+body,对齐 Starlette);永久重定向用 308;
    需要降级为 GET 用 302/303(语义模糊,不推荐);永久且降级为 GET 用 301。
    """

    def __init__(
        self,
        url: str,
        status_code: int = 307,
        headers: dict[str, str] | None = None,
    ) -> None: ...


class FileResponse(Response):
    """文件响应。读取磁盘内容,自动推断 content-type,设 cache-control。

    media_type 为 None 时按扩展名推断,推不出来用 application/octet-stream。
    cache_control 为 None 时:html 用 no-cache,其他用 public, max-age=86400。
    filename 非 None 时设 Content-Disposition,默认 attachment(下载),
    传 "inline" 可在浏览器内预览。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        status_code: int = 200,
        media_type: str | None = None,
        cache_control: str | None = None,
        filename: str | None = None,
        content_disposition_type: str = "attachment",
    ) -> None: ...


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class WebSocketDisconnect(Exception):
    """WebSocket 正常断开异常。"""
    def __init__(self, code: int = 1000) -> None:
        self.code = code
        super().__init__(f"WebSocket disconnected (code={code})")


_EXPECTED_DISCONNECT_ERRNOS = {32, 54, 104}
_EXPECTED_DISCONNECT_WINERRORS = {64, 10053, 10054}


def is_expected_disconnect_error(exc: BaseException) -> bool:
    """判断是否为底层 transport 的预期断连异常。"""
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return True
    if not isinstance(exc, OSError):
        return False
    if exc.errno in _EXPECTED_DISCONNECT_ERRNOS:
        return True
    return getattr(exc, "winerror", None) in _EXPECTED_DISCONNECT_WINERRORS


class WebSocketConnection(mutobj.Declaration):
    """WebSocket 连接。

    在 WebSocketView.connect() 中使用，通过 accept/receive/send/close 管理生命周期。
    """
    path: str = "/"
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)
    headers: dict[str, str] = mutobj.field(default_factory=dict)

    async def accept(self) -> None: ...

    async def receive(self) -> dict[str, Any]:
        """接收消息。返回 ``{"type": "websocket.receive", "text": ...}`` 或 ``{"bytes": ...}``。

        对端关闭时抛出 WebSocketDisconnect。
        """
        ...

    async def receive_json(self) -> Any:
        """接收并解析 JSON 消息。"""
        ...

    async def send_json(self, data: Any) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


# ---------------------------------------------------------------------------
# Server / View / WebSocketView / StaticView
# ---------------------------------------------------------------------------


class Server(mutobj.Declaration):
    """ASGI Server。

    自动发现 View/WebSocketView/StaticView 子类并路由分发。
    子类覆盖 on_startup/on_shutdown 实现生命周期管理。

    子类可设置 ``views`` 限制只路由到指定的 View 子类（元组），
    用于多 Server 实例避免路由冲突。
    """
    host: str = "127.0.0.1"
    port: int = 0
    base_path: str = ""
    redirect_slashes: bool = True
    """精确路径未命中时,自动尝试加/去 trailing slash 并 307 重定向(对齐 Starlette/FastAPI 默认)。"""
    # ClassVar 避免被 DeclarationMeta 转换为 AttributeDescriptor
    views: ClassVar[tuple[type[View], ...] | None] = None

    async def route(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """ASGI 入口 — 自动发现 View/WebSocketView 并路径匹配分发。"""
        ...

    async def before_route(self, scope: dict[str, Any], path: str) -> Response | None:
        """路由前钩子 — 在路径匹配后、handler 调用前执行。

        返回 None 表示放行，返回 Response 表示拦截。
        HTTP 场景：直接发送该 Response（如 302 重定向）。
        WebSocket 场景：关闭连接（使用 Response.status_code 作为关闭码）。

        子类通过 @impl 覆盖以注入认证等逻辑。
        """
        ...

    async def on_startup(self) -> None:
        """生命周期：启动时调用。子类覆盖以初始化资源。"""
        ...

    async def on_shutdown(self) -> None:
        """生命周期：关闭时调用。子类覆盖以清理资源。"""
        ...

    def run(self, *, listen: Sequence[str | _socket.socket] | None = None) -> None:
        """阻塞运行。listen 接受 "ip:port" 字符串或预创建 socket 的数组。"""
        ...

    async def start(self, *, listen: Sequence[str | _socket.socket] | None = None) -> None:
        """异步启动（在已有 event loop 中使用）。"""
        ...

    async def stop(self) -> None:
        """异步停止。"""
        ...


class View(mutobj.Declaration):
    """HTTP 路由视图基类。

    子类设置 ``path`` 并覆盖对应 HTTP method。path 支持路径参数,如 ``/api/{id}``,
    匹配值通过 ``request.path_params["id"]`` 获取。Server 自动发现所有 View 子类。

    ``path`` 可以是字符串或字符串元组。设为元组时,多条路径共享同一个 view 实例
    (同一组 method handler、同一份状态),适合「两形式 URL 直接命中避免 307 跳转」
    的场景::

        class AuthRedirect(View):
            path = ("/auth", "/auth/")   # 必须是 tuple,不能是 list(mutobj 限制)

    示例::

        class HelloView(View):
            path = "/hello/{name}"

            async def get(self, request: Request) -> Response:
                return JSONResponse({"hello": request.path_params["name"]})
    """
    path: str | tuple[str, ...] = ""

    async def get(self, request: Request) -> Response | StreamingResponse: ...
    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def put(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response | StreamingResponse: ...


class WebSocketView(mutobj.Declaration):
    """WebSocket 路由视图基类。

    子类设置 ``path`` 并覆盖 ``connect``。path 格式同 View(支持单值 str 或多绑定 tuple)。
    ``connect`` 返回即断开连接。
    """
    path: str | tuple[str, ...] = ""

    async def connect(self, ws: WebSocketConnection) -> None:
        """WebSocket 生命周期入口。方法返回即断开。"""
        ...


class StaticView(View):
    """静态文件服务。directory 为文件系统绝对路径。"""
    directory: str = ""


from . import _server_impl as _server_impl  # noqa: E402, F401 — trigger @impl registration
