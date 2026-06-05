"""MCPClient Declaration 实现 — @impl + Extension。"""

from __future__ import annotations

from mutio.codec import json
import logging
from typing import Any

from mutio.codec.json import JsonObject, JsonValue

import httpx

import mutio
import mutobj

from mutio.net.client import HttpClient
from mutio.mcp.client import MCPClient, MCPError
from mutio.mcp.protocol import PROTOCOL_VERSION

logger = logging.getLogger("mutio.mcp.client")


# ---------------------------------------------------------------------------
# MCPClient Extension — 承载运行时状态
# ---------------------------------------------------------------------------


class MCPClientExt(mutobj.Extension[MCPClient]):
    """MCPClient 的运行时私有状态。"""
    http: httpx.AsyncClient | None = None
    session_id: str | None = None
    request_id: int = 0


def _ext(client: MCPClient) -> MCPClientExt:
    return MCPClientExt.get_or_create(client)


# ---------------------------------------------------------------------------
# MCPClient @impl
# ---------------------------------------------------------------------------


@mutobj.impl(MCPClient.connect)
async def mcp_client_connect(self: MCPClient) -> None:
    ext = _ext(self)
    ext.http = HttpClient.create(
        user_agent=f"mutio-mcp/{mutio.__version__}",
        timeout=self.timeout,
    )
    await _initialize(self)


@mutobj.impl(MCPClient.close)
async def mcp_client_close(self: MCPClient) -> None:
    ext = _ext(self)
    if ext.http and ext.session_id:
        try:
            await ext.http.delete(
                self.url.rstrip("/"),
                headers={"Mcp-Session-Id": ext.session_id},
            )
        except Exception:
            pass
    if ext.http:
        await ext.http.aclose()
        ext.http = None


@mutobj.impl(MCPClient.list_tools)
async def mcp_client_list_tools(self: MCPClient) -> list[JsonObject]:
    result = await _request(self, "tools/list")
    return json.get_field(result, "tools", list[JsonObject], default=[])


@mutobj.impl(MCPClient.call_tool)
async def mcp_client_call_tool(self: MCPClient, name: str, **arguments: Any) -> JsonObject:
    result = await _request(self, "tools/call", {"name": name, "arguments": arguments})
    return result


@mutobj.impl(MCPClient.list_resources)
async def mcp_client_list_resources(self: MCPClient) -> list[JsonObject]:
    result = await _request(self, "resources/list")
    return json.get_field(result, "resources", list[JsonObject], default=[])


@mutobj.impl(MCPClient.read_resource)
async def mcp_client_read_resource(self: MCPClient, uri: str) -> JsonObject:
    result = await _request(self, "resources/read", {"uri": uri})
    return result


@mutobj.impl(MCPClient.list_prompts)
async def mcp_client_list_prompts(self: MCPClient) -> list[JsonObject]:
    result = await _request(self, "prompts/list")
    return json.get_field(result, "prompts", list[JsonObject], default=[])


@mutobj.impl(MCPClient.get_prompt)
async def mcp_client_get_prompt(self: MCPClient, name: str, arguments: JsonObject | None = None) -> JsonObject:
    params: JsonObject = {"name": name}
    if arguments:
        params["arguments"] = arguments
    result = await _request(self, "prompts/get", params)
    return result


@mutobj.impl(MCPClient.ping)
async def mcp_client_ping(self: MCPClient) -> None:
    await _request(self, "ping")


@mutobj.impl(MCPClient.request)
async def mcp_client_request(
    self: MCPClient,
    method: str,
    params: JsonObject | None = None,
) -> JsonObject:
    return await _request(self, method, params)


# ---------------------------------------------------------------------------
# MCPClient 内部方法
# ---------------------------------------------------------------------------


async def _initialize(client: MCPClient) -> None:
    """MCP initialize 握手。"""
    result = await _request(client, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {
            "name": client.client_name,
            "version": client.client_version,
        },
    })

    client.server_info = json.get_field(result, "serverInfo", JsonObject, default={})
    client.server_capabilities = json.get_field(result, "capabilities", JsonObject, default={})
    client.server_instructions = json.get_field(result, "instructions", str, default="")
    logger.info("MCP initialized: %s v%s (protocol %s)",
                client.server_info.get("name"),
                client.server_info.get("version"),
                json.get_field(result, "protocolVersion", str, default=""))

    await _notify(client, "notifications/initialized")


def _next_id(client: MCPClient) -> int:
    ext = _ext(client)
    ext.request_id += 1
    return ext.request_id


async def _request(client: MCPClient, method: str, params: JsonValue = None) -> JsonObject:
    """发送 JSON-RPC request，返回 result。"""
    ext = _ext(client)
    assert ext.http is not None
    msg_id = _next_id(client)
    payload: JsonObject = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    url = client.url.rstrip("/")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if ext.session_id:
        headers["Mcp-Session-Id"] = ext.session_id

    resp = await ext.http.post(url, json=payload, headers=headers)
    resp.raise_for_status()

    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        ext.session_id = session_id

    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        return _parse_sse_response(resp.text, msg_id)
    else:
        data = json.narrow_value(resp.json(), JsonObject)
        if "error" in data:
            err = json.get_field(data, "error", JsonObject)
            raise MCPError(
                json.get_field(err, "code", int, default=-1),
                json.get_field(err, "message", str, default="Unknown error"),
                err.get("data"),
            )
        return json.get_field(data, "result", JsonObject)


async def _notify(client: MCPClient, method: str, params: JsonValue = None) -> None:
    """发送 JSON-RPC notification。"""
    ext = _ext(client)
    assert ext.http is not None
    payload: JsonObject = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params

    url = client.url.rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ext.session_id:
        headers["Mcp-Session-Id"] = ext.session_id

    resp = await ext.http.post(url, json=payload, headers=headers)
    if resp.status_code not in (200, 202):
        logger.warning("Notification %s returned %d", method, resp.status_code)


def _parse_sse_response(text: str, expected_id: int) -> JsonObject:
    """解析 SSE 响应，提取 JSON-RPC result。"""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            items = json.narrow_value(data, list[JsonObject], fallback=None)
            if items is None:
                items = [json.narrow_value(data, JsonObject)]

            for item in items:
                if item.get("id") != expected_id:
                    continue
                if "error" in item:
                    err = json.narrow_value(item["error"], JsonObject)
                    raise MCPError(
                        json.get_field(err, "code", int, default=-1),
                        json.get_field(err, "message", str, default=""),
                        err.get("data"),
                    )
                return json.get_field(item, "result", JsonObject)

    raise MCPError(-1, "No response found in SSE stream")
