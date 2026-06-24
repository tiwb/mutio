"""mutio.net — WebSocket 集成测试（L1）。

全部通过 socket + Server.start() + WebSocketClient 走公开 API。
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from mutio.net.server import Server, WebSocketView, WebSocketConnection
from mutio.net.client import WebSocketClient

from tests.net.conftest import start_server, free_port


# ---------------------------------------------------------------------------
# Echo View
# ---------------------------------------------------------------------------


class EchoWS(WebSocketView):
    path = "/ws"

    async def connect(self, ws: WebSocketConnection) -> None:
        connect_msg = await ws.receive()
        assert connect_msg["type"] == "websocket.connect"
        await ws.accept()

        msg = await ws.receive()
        if "text" in msg:
            await ws.send_json(msg["text"])
        elif "bytes" in msg:
            await ws.send_bytes(msg["bytes"])
        await ws.close()


class MultiMsgWS(WebSocketView):
    path = "/ws-multi"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # connect
        await ws.accept()

        messages: list[str] = []
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if "text" in msg:
                messages.append(msg["text"])
            if len(messages) >= 3:
                break

        await ws.send_json(messages)
        await ws.close()


class BinaryEchoWS(WebSocketView):
    path = "/ws-bin"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # connect
        await ws.accept()

        msg = await ws.receive()
        if "bytes" in msg:
            await ws.send_bytes(msg["bytes"])
        await ws.close()


class CloseCodeWS(WebSocketView):
    path = "/ws-close"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # connect
        await ws.accept()
        await ws.close(code=4000)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWebSocketBasic:
    @pytest.mark.asyncio
    async def test_echo_text(self, free_port):
        """发送文本 → 收到 echo。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws")
        await ws.connect()
        await ws.send_text("hello world")
        msg = await ws.receive_text()
        await ws.close()
        await server.stop()

        # send_json 返回 JSON 字符串，带引号
        assert '"hello world"' in msg

    @pytest.mark.asyncio
    async def test_echo_unicode(self, free_port):
        """发送 Unicode 文本 → 正确回传。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws")
        await ws.connect()
        await ws.send_text("你好世界")
        msg = await ws.receive_text()
        await ws.close()
        await server.stop()

        assert "你好世界" in msg


class TestWebSocketBinary:
    @pytest.mark.asyncio
    async def test_echo_binary(self, free_port):
        """发送二进制 → 收到 echo。"""
        sock, port = free_port
        server = Server(views=(BinaryEchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-bin")
        await ws.connect()
        await ws.send_bytes(b"\x00\x01\x02\x03\xff")
        msg = await ws.receive_bytes()
        await ws.close()
        await server.stop()

        assert msg == b"\x00\x01\x02\x03\xff"

    @pytest.mark.asyncio
    async def test_large_binary(self, free_port):
        """发送大二进制帧。"""
        sock, port = free_port
        server = Server(views=(BinaryEchoWS,))
        await start_server(server, sock)

        data = b"x" * 60000 + b"tail"

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-bin")
        await ws.connect()
        await ws.send_bytes(data)
        msg = await ws.receive_bytes()
        await ws.close()
        await server.stop()

        assert msg == data


class TestWebSocketMultiMessage:
    @pytest.mark.asyncio
    async def test_multiple_messages(self, free_port):
        """发送多条消息 → 全部收到。"""
        sock, port = free_port
        server = Server(views=(MultiMsgWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-multi")
        await ws.connect()
        await ws.send_text("msg1")
        await ws.send_text("msg2")
        await ws.send_text("msg3")
        reply = await ws.receive_text()
        await ws.close()
        await server.stop()

        assert "msg1" in reply
        assert "msg2" in reply
        assert "msg3" in reply


class TestWebSocketClose:
    @pytest.mark.asyncio
    async def test_close_with_custom_code(self, free_port):
        """服务端 close(4000) → 客户端收到对应断开码。"""
        sock, port = free_port
        server = Server(views=(CloseCodeWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-close")
        await ws.connect()

        # 收到 close 帧时 receive_text 应抛出 WebSocketDisconnect
        from mutio.net.server import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            await ws.receive_text()

        assert exc_info.value.code == 4000
        await server.stop()


class RaiseDisconnectWS(WebSocketView):
    path = "/ws-raise"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # connect
        await ws.accept()
        from mutio.net.server import WebSocketDisconnect
        raise WebSocketDisconnect(4001)


class TestWebSocketDisconnectHandling:
    @pytest.mark.asyncio
    async def test_view_raises_disconnect_server_stays_up(self, free_port, caplog):
        """View 内抛出 WebSocketDisconnect → 服务端捕获为 debug 日志，不崩。"""
        import logging

        sock, port = free_port
        server = Server(views=(RaiseDisconnectWS, EchoWS))
        await start_server(server, sock)

        with caplog.at_level(logging.DEBUG, logger="mutio.net.protocol"):
            ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-raise")
            await ws.connect()
            await asyncio.sleep(0.3)

        # 服务端日志包含 disconnected
        assert "disconnected" in caplog.text.lower()

        # 再发一次普通请求，确认服务端没崩
        ws2 = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws")
        await ws2.connect()
        await ws2.send_text("ping")
        reply = await ws2.receive_text()
        await ws2.close()
        assert "ping" in reply

        await server.stop()


class TestWebSocketError:
    @pytest.mark.asyncio
    async def test_connect_to_invalid_path(self, free_port):
        """连接未注册的 WS 路径 → 应得到 404。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        from mutio.net.server import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/nonexistent")
            await ws.connect()

        # 404 会被映射到 1006
        assert exc_info.value.code in (1006, 4404)

        await server.stop()


# ---------------------------------------------------------------------------
# 客户端类型不匹配 / 边界场景
# ---------------------------------------------------------------------------


class TestWebSocketTypeMismatch:
    """receive_text/bytes 类型不匹配 → TypeError。"""

    @pytest.mark.asyncio
    async def test_receive_bytes_when_server_sends_text(self, free_port):
        """服务端发 text，客户端 receive_bytes → TypeError。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws")
        await ws.connect()
        await ws.send_text("hello")

        with pytest.raises(TypeError, match="Expected bytes message"):
            await ws.receive_bytes()

        await ws.close()
        await server.stop()

    @pytest.mark.asyncio
    async def test_receive_text_when_server_sends_bytes(self, free_port):
        """服务端发 bytes，客户端 receive_text → TypeError。"""
        sock, port = free_port
        server = Server(views=(BinaryEchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-bin")
        await ws.connect()
        await ws.send_bytes(b"data")

        with pytest.raises(TypeError, match="Expected text message"):
            await ws.receive_text()

        await ws.close()
        await server.stop()


class TestWebSocketClientEdgeCases:
    """客户端未连接 / URL query 等边界场景。"""

    @pytest.mark.asyncio
    async def test_send_text_without_connect_raises_disconnect(self):
        """未连接就 send_text → WebSocketDisconnect(1006)。"""
        ws = WebSocketClient(url="ws://127.0.0.1:1/ws")
        from mutio.net.server import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            await ws.send_text("hello")
        assert exc_info.value.code == 1006

    @pytest.mark.asyncio
    async def test_receive_text_without_connect_raises_disconnect(self):
        """未连接就 receive_text → WebSocketDisconnect(1006)。"""
        ws = WebSocketClient(url="ws://127.0.0.1:1/ws")
        from mutio.net.server import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            await ws.receive_text()
        assert exc_info.value.code == 1006

    @pytest.mark.asyncio
    async def test_url_with_query_string(self, free_port):
        """URL 含 query string → 正常连接并通信。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws?a=1&b=2")
        await ws.connect()
        await ws.send_text("hello")
        msg = await ws.receive_text()
        await ws.close()
        await server.stop()

        assert "hello" in msg

    @pytest.mark.asyncio
    async def test_close_without_connect_is_noop(self):
        """未连接时 close 不抛异常。"""
        ws = WebSocketClient(url="ws://127.0.0.1:1/ws")
        await ws.close()
        await ws.close()


# ---------------------------------------------------------------------------
# 大消息分片测试（wsproto 自动分片 → 客户端正确拼合）
# ---------------------------------------------------------------------------


class LargeTextEchoWS(WebSocketView):
    """接收一条文本消息后，原样 echo 回客户端。"""
    path = "/ws-large-text"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # connect
        await ws.accept()

        msg = await ws.receive()
        if "text" in msg:
            await ws.send_json(msg["text"])
        await ws.close()


class LargeTextDirectWS(WebSocketView):
    """连接后直接发送一条大文本消息给客户端（通过 send_json）。"""
    path = "/ws-large-direct"

    async def connect(self, ws: WebSocketConnection) -> None:
        await ws.receive()  # websocket.connect
        await ws.accept()
        # send_json 会 JSON 包裹（加引号转义），内容体积足够触发分片
        payload = "A" * 80000 + "TAIL"
        await ws.send_json(payload)
        await ws.close()


class TestWebSocketFragmentation:
    """大消息触发 wsproto 自动分片，客户端 receive_text/bytes 正确拼合。"""

    @pytest.mark.asyncio
    async def test_large_text_echo(self, free_port):
        """客户端发大文本 → 服务端 echo（JSON 包裹）→ 客户端收到完整回包。"""
        sock, port = free_port
        server = Server(views=(LargeTextEchoWS,))
        await start_server(server, sock)

        payload = "你好" * 30000 + "END"  # ~90KB，触发分片

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-large-text")
        await ws.connect()
        await ws.send_text(payload)
        reply = await ws.receive_text()
        await ws.close()
        await server.stop()

        # send_json 套了 JSON 引号，内容完整
        assert payload in reply

    @pytest.mark.asyncio
    async def test_large_text_direct(self, free_port):
        """服务端通过 send_json 发 ~80KB（触发 wsproto 分片）→ 客户端完整接收。"""
        sock, port = free_port
        server = Server(views=(LargeTextDirectWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-large-direct")
        await ws.connect()
        reply = await ws.receive_text()
        await ws.close()
        await server.stop()

        # send_json 包裹在 JSON 字符串中："..."
        assert len(reply) >= 80000 + 4
        assert "TAIL" in reply

    @pytest.mark.asyncio
    async def test_large_binary_fragmentation(self, free_port):
        """~100KB 二进制 echo → 分片拼合正确。"""
        sock, port = free_port
        server = Server(views=(BinaryEchoWS,))
        await start_server(server, sock)

        data = bytes(range(256)) * 400 + b"\x00\x01\x02"  # ~100KB

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws-bin")
        await ws.connect()
        await ws.send_bytes(data)
        reply = await ws.receive_bytes()
        await ws.close()
        await server.stop()

        assert reply == data

    @pytest.mark.asyncio
    async def test_normal_sized_message_still_works(self, free_port):
        """小消息不受分片逻辑影响，行为不变。"""
        sock, port = free_port
        server = Server(views=(EchoWS,))
        await start_server(server, sock)

        ws = WebSocketClient(url=f"ws://127.0.0.1:{port}/ws")
        await ws.connect()
        await ws.send_text("hello")
        msg = await ws.receive_text()
        await ws.close()
        await server.stop()

        assert '"hello"' in msg
