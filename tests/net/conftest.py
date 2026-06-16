"""net 模块 L1 测试共享 fixture — socket-based HTTP 集成测试。

通过 socket.socket() + loopback:0 + Server.start() 走完整 TCP→ASGI 链路，
全部使用公开 API，无私有 import。
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import mutobj
import pytest

from mutio.net.server import Server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def free_port() -> tuple[socket.socket, int]:
    """创建监听 socket（loopback:0 自动端口），返回 (sock, port)。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    yield sock, port
    sock.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def start_server(server: Server, sock: socket.socket) -> Server:
    """在指定 socket 上启动 Server，返回已启动的 server 实例。"""
    await server.start(listen=[sock])
    return server


class _HttpResponse:
    """解析后的 HTTP 响应。"""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.status = 0
        self.reason = ""
        self.headers: dict[str, str] = {}
        self.body = b""

        parts = raw.split(b"\r\n\r\n", 1)
        header_block = parts[0]
        self.body = parts[1] if len(parts) > 1 else b""

        lines = header_block.split(b"\r\n")
        if lines:
            status_line = lines[0].decode("ascii")
            # "HTTP/1.1 200 OK"
            tokens = status_line.split(" ", 2)
            self.status = int(tokens[1]) if len(tokens) > 1 else 0
            self.reason = tokens[2] if len(tokens) > 2 else ""

        for line in lines[1:]:
            kv = line.decode("latin-1").split(":", 1)
            if len(kv) == 2:
                self.headers[kv[0].strip().lower()] = kv[1].strip()


async def http_request(
    port: int,
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> _HttpResponse:
    """发送 HTTP 请求到 127.0.0.1:<port>，返回解析后的响应。

    自动构造 Host header。headers 参数会合并到请求头中。
    """
    hdrs = headers.copy() if headers else {}
    if "host" not in {k.lower() for k in hdrs}:
        hdrs["Host"] = f"127.0.0.1:{port}"

    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    request_lines = [f"{method} {path} HTTP/1.1"]
    for k, v in hdrs.items():
        request_lines.append(f"{k}: {v}")
    request_lines.append("")

    req = "\r\n".join(request_lines).encode() + b"\r\n"
    if body:
        req += body

    writer.write(req)
    await writer.drain()

    raw = b""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=3.0)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        raw += chunk
        # Stop reading when we have complete body (Content-Length or chunked end)
        if b"\r\n\r\n" in raw:
            # Try to see if we have complete body
            parts = raw.split(b"\r\n\r\n", 1)
            header_block = parts[0]
            body_so_far = parts[1] if len(parts) > 1 else b""
            # Check Content-Length
            cl = _get_content_length(header_block)
            if cl is not None and len(body_so_far) >= cl:
                break
            # Check for chunked end (0\r\n\r\n)
            if body_so_far.endswith(b"0\r\n\r\n"):
                break

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    return _HttpResponse(raw)


def _get_content_length(header_block: bytes) -> int | None:
    for line in header_block.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except ValueError:
                return None
    return None


async def http_get(port: int, path: str = "/", **headers: str) -> _HttpResponse:
    """便捷函数：发送 GET 请求。"""
    return await http_request(port, "GET", path, headers=headers if headers else None)


async def http_post(
    port: int, path: str = "/", body: bytes = b"", **headers: str,
) -> _HttpResponse:
    """便捷函数：发送 POST 请求。"""
    hdrs = dict(headers) if headers else {}
    hdrs["Content-Length"] = str(len(body))
    return await http_request(port, "POST", path, headers=hdrs, body=body)


# ---------------------------------------------------------------------------
# 用于断言 PROXY protocol 等协议级行为的 Server 基类
# ---------------------------------------------------------------------------


class _CaptureClientServer(Server):
    """覆写 before_route 捕获 scope["client"]。"""

    captured_client: tuple[str, int] | None = mutobj.field(default=None)  # type: ignore[assignment]

    async def before_route(self, scope: dict[str, Any], path: str) -> Any:
        self.captured_client = scope.get("client")
        return None
