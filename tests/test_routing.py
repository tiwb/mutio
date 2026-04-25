"""mutio.net.server — trailing-slash 规范化 + View.path 多绑定测试。

测试通过模拟 ASGI scope/receive/send 直接驱动 Server.route,不开 socket。
"""
from __future__ import annotations

from typing import Any

import pytest

from mutio.net.server import (
    Server, View, WebSocketView, WebSocketConnection,
    Request, Response,
)


# ---------------------------------------------------------------------------
# ASGI 模拟工具
# ---------------------------------------------------------------------------


class _Captured:
    """收集 ASGI send 输出。"""

    def __init__(self) -> None:
        self.start: dict[str, Any] | None = None
        self.body: bytes = b""
        self.ws_messages: list[dict[str, Any]] = []

    @property
    def status(self) -> int:
        assert self.start is not None
        return self.start["status"]

    @property
    def headers(self) -> dict[str, str]:
        assert self.start is not None
        return {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in self.start["headers"]
        }


async def _call_http(
    server: Server, method: str, path: str, *, query_string: bytes = b"",
) -> _Captured:
    cap = _Captured()
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": [],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            cap.start = msg
        elif msg["type"] == "http.response.body":
            cap.body += msg.get("body", b"")

    await server.route(scope, receive, send)
    return cap


async def _call_ws(server: Server, path: str) -> _Captured:
    cap = _Captured()
    scope = {
        "type": "websocket",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(msg: dict[str, Any]) -> None:
        cap.ws_messages.append(msg)

    await server.route(scope, receive, send)
    return cap


# ---------------------------------------------------------------------------
# trailing-slash 规范化
# ---------------------------------------------------------------------------


class _ProbeView(View):
    path = "/probe"

    async def get(self, request: Request) -> Response:
        return Response(status_code=200, body=b"hit", headers={"x-marker": "probe"})


class _ProbeSlashView(View):
    path = "/probe-slash/"

    async def get(self, request: Request) -> Response:
        return Response(status_code=200, body=b"hit-slash", headers={"x-marker": "probe-slash"})


class TestTrailingSlashNormalization:
    @pytest.mark.asyncio
    async def test_exact_match_unaffected(self):
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/probe")
        assert cap.status == 200
        assert cap.body == b"hit"

    @pytest.mark.asyncio
    async def test_add_slash_to_no_slash_path(self):
        # 注册 /probe,访问 /probe/ → 307 到 /probe
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/probe/")
        assert cap.status == 307
        assert cap.headers["location"] == "/probe"

    @pytest.mark.asyncio
    async def test_strip_slash_from_slash_path(self):
        # 注册 /probe-slash/,访问 /probe-slash → 307 到 /probe-slash/
        server = Server(views=(_ProbeSlashView,))
        cap = await _call_http(server, "GET", "/probe-slash")
        assert cap.status == 307
        assert cap.headers["location"] == "/probe-slash/"

    @pytest.mark.asyncio
    async def test_redirect_preserves_query_string(self):
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/probe/", query_string=b"a=1&b=2")
        assert cap.status == 307
        assert cap.headers["location"] == "/probe?a=1&b=2"

    @pytest.mark.asyncio
    async def test_redirect_preserves_method_via_307(self):
        # 307 语义上保留 method,这里只验证状态码
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "POST", "/probe/")
        assert cap.status == 307
        assert cap.headers["location"] == "/probe"

    @pytest.mark.asyncio
    async def test_redirect_includes_base_path(self):
        # base_path = /api,注册 /probe(逻辑路径),访问 /api/probe/ → 307 到 /api/probe
        server = Server(base_path="/api", views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/api/probe/")
        assert cap.status == 307
        assert cap.headers["location"] == "/api/probe"

    @pytest.mark.asyncio
    async def test_disabled_falls_back_to_404(self):
        server = Server(redirect_slashes=False, views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/probe/")
        assert cap.status == 404

    @pytest.mark.asyncio
    async def test_root_path_no_redirect(self):
        # `/` 不参与规范化(去掉变空,加上还是 /)
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/")
        assert cap.status == 404

    @pytest.mark.asyncio
    async def test_non_existent_path_still_404(self):
        server = Server(views=(_ProbeView,))
        cap = await _call_http(server, "GET", "/missing")
        assert cap.status == 404
        cap2 = await _call_http(server, "GET", "/missing/")
        assert cap2.status == 404


class _WSEcho(WebSocketView):
    path = "/ws-echo"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.accept()
        await ws.close()


class TestTrailingSlashWebSocket:
    @pytest.mark.asyncio
    async def test_websocket_does_not_redirect(self):
        # WebSocket 不参与规范化:注册 /ws-echo,连接 /ws-echo/ → 直接 close 4404
        server = Server(views=(_WSEcho,))
        cap = await _call_ws(server, "/ws-echo/")
        assert any(m.get("type") == "websocket.close" and m.get("code") == 4404 for m in cap.ws_messages)


# ---------------------------------------------------------------------------
# View.path 多绑定
# ---------------------------------------------------------------------------


class _Counter:
    """共享计数器,用于验证多 path 命中同一 view 实例。"""
    n = 0


class _MultiPathList(View):
    path = ("/m-list-a", "/m-list-b")

    async def get(self, request: Request) -> Response:
        # 用闭包外的类变量,但更直接:实例属性
        self.hits = getattr(self, "hits", 0) + 1
        return Response(status_code=200, body=str(self.hits).encode())


class _MultiPathTuple(View):
    path = ("/m-tuple-x", "/m-tuple-y")

    async def get(self, request: Request) -> Response:
        return Response(status_code=200, body=request.path.encode())


class _WSMultiPath(WebSocketView):
    path = ("/ws-multi-1", "/ws-multi-2")

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.accept()
        await ws.close(code=1234)


class TestViewPathMultiBinding:
    @pytest.mark.asyncio
    async def test_list_paths_both_match(self):
        server = Server(views=(_MultiPathList,))
        cap1 = await _call_http(server, "GET", "/m-list-a")
        assert cap1.status == 200
        cap2 = await _call_http(server, "GET", "/m-list-b")
        assert cap2.status == 200

    @pytest.mark.asyncio
    async def test_list_paths_share_view_instance_state(self):
        # 两个路径命中同一 view 实例:counter 累加而不是各 reset
        server = Server(views=(_MultiPathList,))
        cap1 = await _call_http(server, "GET", "/m-list-a")
        cap2 = await _call_http(server, "GET", "/m-list-b")
        cap3 = await _call_http(server, "GET", "/m-list-a")
        assert cap1.body == b"1"
        assert cap2.body == b"2"
        assert cap3.body == b"3"

    @pytest.mark.asyncio
    async def test_tuple_paths_both_match(self):
        server = Server(views=(_MultiPathTuple,))
        cap_x = await _call_http(server, "GET", "/m-tuple-x")
        assert cap_x.status == 200
        assert cap_x.body == b"/m-tuple-x"
        cap_y = await _call_http(server, "GET", "/m-tuple-y")
        assert cap_y.status == 200
        assert cap_y.body == b"/m-tuple-y"

    @pytest.mark.asyncio
    async def test_websocket_multi_path(self):
        server = Server(views=(_WSMultiPath,))
        cap1 = await _call_ws(server, "/ws-multi-1")
        cap2 = await _call_ws(server, "/ws-multi-2")
        # 两个路径都被 WS 路由命中(发出 accept + close),而不是 close 4404
        assert any(m.get("type") == "websocket.accept" for m in cap1.ws_messages)
        assert any(m.get("type") == "websocket.accept" for m in cap2.ws_messages)
        assert any(m.get("code") == 1234 for m in cap1.ws_messages)
        assert any(m.get("code") == 1234 for m in cap2.ws_messages)


# ---------------------------------------------------------------------------
# 多 path + trailing-slash 协同(mutbot /auth 场景)
# ---------------------------------------------------------------------------


class _AuthLike(View):
    path = ("/auth-like", "/auth-like/")

    async def get(self, request: Request) -> Response:
        return Response(status_code=302, headers={"location": "/auth-like/login"})


class TestMultiPathBypassesRedirect:
    @pytest.mark.asyncio
    async def test_both_forms_directly_match_no_307(self):
        # 两种形式都精确命中,不走 trailing-slash fallback,直接 302 而非 307
        server = Server(views=(_AuthLike,))
        cap_no_slash = await _call_http(server, "GET", "/auth-like")
        cap_slash = await _call_http(server, "GET", "/auth-like/")
        assert cap_no_slash.status == 302
        assert cap_no_slash.headers["location"] == "/auth-like/login"
        assert cap_slash.status == 302
        assert cap_slash.headers["location"] == "/auth-like/login"
