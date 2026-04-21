"""mutio.mcp.protocol — JsonRpcDispatcher / MCP 类型 / 消息构造 测试。"""

from __future__ import annotations

import json

import pytest

from mutio.mcp.protocol import (
    JsonRpcDispatcher, JsonRpcError,
    ToolDef, ToolResult, ResourceDef, ResourceContent,
    PromptDef, PromptMessage, ServerCapabilities,
    make_request, make_notification,
    PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INTERNAL_ERROR,
)


# ---------------------------------------------------------------------------
# MCP 类型 to_dict
# ---------------------------------------------------------------------------


class TestToolDef:
    def test_to_dict(self):
        t = ToolDef(name="search", description="Search things")
        d = t.to_dict()
        assert d["name"] == "search"
        assert d["description"] == "Search things"
        assert d["inputSchema"]["type"] == "object"

    def test_custom_schema(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        t = ToolDef(name="find", inputSchema=schema)
        assert t.to_dict()["inputSchema"] == schema


class TestToolResult:
    def test_text(self):
        r = ToolResult.text("hello")
        d = r.to_dict()
        assert d["content"] == [{"type": "text", "text": "hello"}]
        assert "isError" not in d

    def test_error(self):
        r = ToolResult.error("boom")
        d = r.to_dict()
        assert d["isError"] is True
        assert d["content"][0]["text"] == "boom"

    def test_empty(self):
        r = ToolResult()
        d = r.to_dict()
        assert d["content"] == []
        assert "isError" not in d


class TestResourceDef:
    def test_to_dict(self):
        r = ResourceDef(uri="file:///a.txt", name="a")
        d = r.to_dict()
        assert d["uri"] == "file:///a.txt"
        assert d["mimeType"] == "text/plain"


class TestResourceContent:
    def test_text(self):
        r = ResourceContent(uri="file:///a.txt", text="content")
        d = r.to_dict()
        assert d["text"] == "content"
        assert "blob" not in d

    def test_blob(self):
        r = ResourceContent(uri="file:///b.bin", blob="AQID", mimeType="application/octet-stream")
        d = r.to_dict()
        assert d["blob"] == "AQID"
        assert d["mimeType"] == "application/octet-stream"


class TestPromptDef:
    def test_to_dict(self):
        p = PromptDef(name="summarize", description="Summarize text")
        d = p.to_dict()
        assert d["name"] == "summarize"
        assert d["arguments"] == []


class TestPromptMessage:
    def test_to_dict(self):
        m = PromptMessage(role="user", content={"type": "text", "text": "hi"})
        d = m.to_dict()
        assert d["role"] == "user"
        assert d["content"]["text"] == "hi"


class TestServerCapabilities:
    def test_empty(self):
        c = ServerCapabilities()
        assert c.to_dict() == {}

    def test_with_tools(self):
        c = ServerCapabilities(tools={"listChanged": True})
        d = c.to_dict()
        assert d["tools"] == {"listChanged": True}
        assert "resources" not in d


class TestJsonRpcError:
    def test_to_dict(self):
        e = JsonRpcError(code=-32600, message="bad request")
        d = e.to_dict()
        assert d == {"code": -32600, "message": "bad request"}

    def test_to_dict_with_data(self):
        e = JsonRpcError(code=-32600, message="bad", data={"detail": "x"})
        d = e.to_dict()
        assert d["data"] == {"detail": "x"}


# ---------------------------------------------------------------------------
# make_request / make_notification
# ---------------------------------------------------------------------------


class TestMakeRequest:
    def test_basic(self):
        msg = make_request(1, "tools/list")
        assert msg == {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    def test_with_params(self):
        msg = make_request(2, "tools/call", {"name": "search"})
        assert msg["params"] == {"name": "search"}

    def test_no_params_key_when_none(self):
        msg = make_request(3, "ping")
        assert "params" not in msg


class TestMakeNotification:
    def test_basic(self):
        msg = make_notification("initialized")
        assert msg == {"jsonrpc": "2.0", "method": "initialized"}
        assert "id" not in msg

    def test_with_params(self):
        msg = make_notification("progress", {"token": 1})
        assert msg["params"] == {"token": 1}


# ---------------------------------------------------------------------------
# JsonRpcDispatcher
# ---------------------------------------------------------------------------


class TestJsonRpcDispatcher:
    @pytest.fixture
    def dispatcher(self):
        d = JsonRpcDispatcher()

        @d.method("echo")
        async def echo(params):
            return params

        @d.method("fail")
        async def fail(params):
            raise JsonRpcError(code=42, message="custom error")

        @d.method("crash")
        async def crash(params):
            raise RuntimeError("unexpected")

        @d.notification("log")
        async def on_log(params):
            pass

        return d

    async def test_method_call(self, dispatcher):
        resp = await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"x": 1}}
        )
        assert resp["id"] == 1
        assert resp["result"] == {"x": 1}

    async def test_method_not_found(self, dispatcher):
        resp = await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "nonexistent"}
        )
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    async def test_jsonrpc_error(self, dispatcher):
        resp = await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "fail"}
        )
        assert resp["error"]["code"] == 42
        assert resp["error"]["message"] == "custom error"

    async def test_internal_error(self, dispatcher):
        resp = await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 4, "method": "crash"}
        )
        assert resp["error"]["code"] == INTERNAL_ERROR

    async def test_notification(self, dispatcher):
        resp = await dispatcher.handle(
            {"jsonrpc": "2.0", "method": "log", "params": {"level": "info"}}
        )
        assert resp is None

    async def test_missing_jsonrpc_version(self, dispatcher):
        resp = await dispatcher.handle({"id": 1, "method": "echo"})
        assert resp["error"]["code"] == INVALID_REQUEST

    async def test_missing_method(self, dispatcher):
        resp = await dispatcher.handle({"jsonrpc": "2.0", "id": 5})
        assert resp["error"]["code"] == INVALID_REQUEST


class TestJsonRpcDispatcherHandleBytes:
    @pytest.fixture
    def dispatcher(self):
        d = JsonRpcDispatcher()

        @d.method("ping")
        async def ping(params):
            return "pong"

        return d

    async def test_valid_request(self, dispatcher):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        resp_bytes = await dispatcher.handle_bytes(raw)
        resp = json.loads(resp_bytes)
        assert resp["result"] == "pong"

    async def test_parse_error(self, dispatcher):
        resp_bytes = await dispatcher.handle_bytes(b"not json")
        resp = json.loads(resp_bytes)
        assert resp["error"]["code"] == PARSE_ERROR

    async def test_invalid_type(self, dispatcher):
        resp_bytes = await dispatcher.handle_bytes(b'"string"')
        resp = json.loads(resp_bytes)
        assert resp["error"]["code"] == INVALID_REQUEST

    async def test_notification_returns_none(self, dispatcher):
        d = JsonRpcDispatcher()

        @d.notification("noop")
        async def noop(params):
            pass

        raw = json.dumps({"jsonrpc": "2.0", "method": "noop"}).encode()
        resp = await d.handle_bytes(raw)
        assert resp is None

    async def test_batch(self, dispatcher):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
        resp_bytes = await dispatcher.handle_bytes(json.dumps(batch).encode())
        results = json.loads(resp_bytes)
        assert len(results) == 2
        assert all(r["result"] == "pong" for r in results)

    async def test_empty_batch(self, dispatcher):
        resp_bytes = await dispatcher.handle_bytes(b"[]")
        resp = json.loads(resp_bytes)
        assert resp["error"]["code"] == INVALID_REQUEST
