"""mcp.py Declaration 实现 — MCPView @impl + MCPToolProvider。"""

from __future__ import annotations

import inspect
from mutio.codec import json
import logging
import secrets
from typing import Any, cast

import mutobj

from mutio.codec.json import JsonObject, JsonValue
from mutio.net.server import Request, Response
from mutio.mcp.toolset import MCPToolSet
from mutio.mcp.promptset import MCPPromptSet
from mutio.mcp.view import MCPView
from mutio.mcp.protocol import (
    JsonRpcDispatcher,
    JsonRpcError,
    INVALID_PARAMS,
    PROTOCOL_VERSION,
    PromptMessage,
    ServerCapabilities,
    ToolResult,
)
from mutio.mcp._schema import function_to_mcp_input_schema, function_to_mcp_description
from mutio.net._protocol import format_sse

logger = logging.getLogger("mutio.mcp")


# ---------------------------------------------------------------------------
# MCPToolProvider — generation 检查 + 懒刷新
# ---------------------------------------------------------------------------


class MCPToolProvider:
    """generation 检查 + 懒刷新，桥接 Declaration 发现到 MCP handler。"""

    def __init__(self, target_view: type[MCPView] | None = None) -> None:
        self._gen: int = -1
        self._tools: dict[str, tuple[MCPToolSet, str]] = {}
        self._target_view = target_view
        # 缓存 target_view 的 path（从实例获取，避免 AttributeDescriptor）
        # MCPView.path 可能是 str 或 tuple,统一归一化为 tuple 简化比较
        self._target_paths: tuple[str, ...] = ()
        if target_view is not None:
            tv_path = target_view().path
            self._target_paths = (tv_path,) if isinstance(tv_path, str) else tuple(tv_path)

    def _match_view(self, toolset: MCPToolSet) -> bool:
        """检查 toolset 是否属于当前 view。

        优先使用 view 属性匹配（精确），其次使用 path 属性匹配。
        """
        if self._target_view is None:
            return True

        # 优先检查 view 属性
        toolset_view = toolset.view
        if toolset_view is not None:
            # 使用类名比较，避免 reload 导致的身份不匹配
            target_name = self._target_view.__name__
            if isinstance(toolset_view, tuple):
                return any(v.__name__ == target_name for v in toolset_view)
            return toolset_view.__name__ == target_name

        # 回退到 path 匹配
        toolset_path = toolset.path
        if not toolset_path:
            # 未指定 view 和 path 的 toolset 匹配所有 view
            return True
        if isinstance(toolset_path, tuple):
            return any(p in toolset_path for p in self._target_paths)
        return toolset_path in self._target_paths

    def refresh(self) -> None:
        gen = mutobj.get_registry_generation()
        if gen != self._gen:
            self._gen = gen
            self._tools = {}
            for cls in mutobj.discover_subclasses(MCPToolSet):
                instance = cls()
                # 过滤：只注册匹配当前 MCPView 的 toolset
                if not self._match_view(instance):
                    continue
                prefix = instance.prefix
                for name in dir(cls):
                    if name.startswith("_"):
                        continue
                    if name in ("prefix", "view", "path"):
                        continue
                    attr = getattr(cls, name, None)
                    if attr is not None and (inspect.isfunction(attr) or inspect.ismethod(attr)):
                        if name in dir(MCPToolSet):
                            continue
                        tool_name = f"{prefix}{name}" if prefix else name
                        self._tools[tool_name] = (instance, name)

    def list_tools(self) -> list[JsonObject]:
        """从类型注解 + docstring 自动生成 tool schema。"""
        self.refresh()
        result: list[JsonObject] = []
        for tool_name, (instance, method_name) in self._tools.items():
            method = getattr(instance, method_name)
            # 优先使用声明的 docstring，避免被 @impl 覆盖
            doc = mutobj.get_declaration_doc(type(instance), method_name)
            if doc is None:
                doc = method.__doc__
            schema = function_to_mcp_input_schema(method, doc=doc)
            description = function_to_mcp_description(method, doc=doc)
            result.append({
                "name": tool_name,
                "description": description,
                "inputSchema": schema,
            })
        return result

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.refresh()
        if name not in self._tools:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        instance, method_name = self._tools[name]
        method = getattr(instance, method_name)
        result = await method(**args)
        if isinstance(result, str):
            return ToolResult.text(result)
        if isinstance(result, ToolResult):
            return result
        return ToolResult.text(str(result))


# ---------------------------------------------------------------------------
# MCPPromptProvider — generation 检查 + 懒刷新
# ---------------------------------------------------------------------------


def _infer_prompt_arguments(fn: Any) -> list[JsonObject]:
    """从方法签名提取 MCP prompt arguments。

    MCP 协议限制 prompt 参数只能是字符串。方法可以有默认值（→ required=False）。
    兼容 ``from __future__ import annotations`` — 用 get_type_hints 解析字符串注解。
    """
    import typing

    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    result: list[JsonObject] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if name in hints:
            annotation = hints[name]
            if annotation is not str:
                raise TypeError(
                    f"MCPPromptSet method parameter {name!r} must be str, got {annotation!r}"
                )
        required = param.default is inspect.Parameter.empty
        result.append({"name": name, "required": required})
    return result


def _normalize_prompt_result(result: object) -> list[PromptMessage]:
    """归一化 prompt 方法返回值为 list[PromptMessage]。

    支持三种形态：
    - ``str`` → 单条 user text message
    - ``PromptMessage`` → 单条
    - ``list[PromptMessage]`` → 多条
    """
    if isinstance(result, str):
        return [PromptMessage(role="user", content={"type": "text", "text": result})]
    if isinstance(result, PromptMessage):
        return [result]
    if isinstance(result, list):
        items = cast(list[Any], result)
        if all(isinstance(m, PromptMessage) for m in items):
            return [m for m in items if isinstance(m, PromptMessage)]
    raise TypeError(
        "Prompt method must return str | PromptMessage | list[PromptMessage], "
        f"got {result!r}"
    )


class MCPPromptProvider:
    """generation 检查 + 懒刷新，桥接 Declaration 发现到 MCP prompt handler。"""

    def __init__(self, target_view: type[MCPView] | None = None) -> None:
        self._gen: int = -1
        self._prompts: dict[str, tuple[MCPPromptSet, str]] = {}
        self._target_view = target_view
        self._target_paths: tuple[str, ...] = ()
        if target_view is not None:
            tv_path = target_view().path
            self._target_paths = (tv_path,) if isinstance(tv_path, str) else tuple(tv_path)

    def _match_view(self, promptset: MCPPromptSet) -> bool:
        if self._target_view is None:
            return True
        ps_view = promptset.view
        if ps_view is not None:
            target_name = self._target_view.__name__
            if isinstance(ps_view, tuple):
                return any(v.__name__ == target_name for v in ps_view)
            return ps_view.__name__ == target_name
        ps_path = promptset.path
        if not ps_path:
            return True
        if isinstance(ps_path, tuple):
            return any(p in ps_path for p in self._target_paths)
        return ps_path in self._target_paths

    def refresh(self) -> None:
        gen = mutobj.get_registry_generation()
        if gen != self._gen:
            self._gen = gen
            self._prompts = {}
            for cls in mutobj.discover_subclasses(MCPPromptSet):
                instance = cls()
                if not self._match_view(instance):
                    continue
                prefix = instance.prefix
                for name in dir(cls):
                    if name.startswith("_"):
                        continue
                    if name in ("prefix", "view", "path"):
                        continue
                    attr = getattr(cls, name, None)
                    if attr is not None and (inspect.isfunction(attr) or inspect.ismethod(attr)):
                        if name in dir(MCPPromptSet):
                            continue
                        prompt_name = f"{prefix}{name}" if prefix else name
                        self._prompts[prompt_name] = (instance, name)

    def list_prompts(self) -> list[JsonObject]:
        """生成 prompts/list 返回条目：name / description / arguments。"""
        self.refresh()
        result: list[JsonObject] = []
        for prompt_name, (instance, method_name) in self._prompts.items():
            method = getattr(instance, method_name)
            doc = mutobj.get_declaration_doc(type(instance), method_name)
            if doc is None:
                doc = method.__doc__ or ""
            arguments = _infer_prompt_arguments(method)
            result.append({
                "name": prompt_name,
                "description": doc,
                "arguments": arguments,
            })
        return result

    async def call_prompt(self, name: str, args: dict[str, Any]) -> JsonObject:
        """执行 prompt 方法并返回 prompts/get 响应体。"""
        self.refresh()
        if name not in self._prompts:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown prompt: {name}")
        instance, method_name = self._prompts[name]
        method = getattr(instance, method_name)
        raw = method(**args)
        if inspect.iscoroutine(raw):
            raw = await raw
        messages = _normalize_prompt_result(raw)
        doc = mutobj.get_declaration_doc(type(instance), method_name)
        if doc is None:
            doc = method.__doc__ or ""
        response: JsonObject = {"messages": [m.to_dict() for m in messages]}
        if doc:
            response["description"] = doc
        return response


# ---------------------------------------------------------------------------
# MCPView Extension — 承载运行时状态
# ---------------------------------------------------------------------------


class MCPViewExt(mutobj.Extension[MCPView]):
    """MCPView 的运行时状态。"""
    tool_provider: MCPToolProvider | None = None
    prompt_provider: MCPPromptProvider | None = None
    sessions: dict[str, MCPSession] = mutobj.field(default_factory=dict)
    dispatch: JsonRpcDispatcher | None = None


class MCPSession:
    """MCP session 状态。"""
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.initialized = False


def _get_ext(view: MCPView) -> MCPViewExt:
    ext = MCPViewExt.get_or_create(view)
    if ext.tool_provider is None:
        ext.tool_provider = MCPToolProvider(target_view=type(view))
        ext.prompt_provider = MCPPromptProvider(target_view=type(view))
        ext.dispatch = JsonRpcDispatcher()
        _setup_handlers(ext, view)
    return ext


def _setup_handlers(ext: MCPViewExt, view: MCPView) -> None:
    """注册 MCP JSON-RPC 方法。"""
    assert ext.dispatch is not None
    assert ext.tool_provider is not None
    assert ext.prompt_provider is not None

    tp = ext.tool_provider
    pp = ext.prompt_provider

    async def _handle_initialize(params: dict[str, Any]) -> JsonObject:
        tools = tp.list_tools()
        prompts = pp.list_prompts()
        capabilities = ServerCapabilities(
            tools={"listChanged": False} if tools else None,
            prompts={"listChanged": False} if prompts else None,
        )
        caps_dict = capabilities.to_dict()
        # 子类可以宣告 vendor 扩展 capability（顶层合并，不递归）
        try:
            extra = view.extra_capabilities() or {}
        except Exception:
            logger.exception("extra_capabilities() raised")
            extra = {}
        if extra:
            caps_dict.update(extra)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": caps_dict,
            "serverInfo": {"name": view.name, "version": view.version},
            **({"instructions": view.instructions} if view.instructions else {}),
        }

    async def _handle_initialized(params: dict[str, Any]) -> None:
        pass

    async def _handle_ping(params: dict[str, Any]) -> JsonObject:
        return {}

    async def _handle_tools_list(params: dict[str, Any]) -> JsonObject:
        return {"tools": tp.list_tools()}

    async def _handle_tools_call(params: dict[str, Any]) -> JsonObject:
        tool_name = params.get("name")
        if not tool_name:
            raise JsonRpcError(INVALID_PARAMS, "Missing tool name")
        arguments = params.get("arguments", {})
        try:
            result = await tp.call_tool(tool_name, arguments)
        except JsonRpcError:
            raise
        except Exception as e:
            logger.exception("Tool %s raised exception", tool_name)
            result = ToolResult.error(str(e))
        return result.to_dict()

    async def _handle_prompts_list(params: dict[str, Any]) -> JsonObject:
        return {"prompts": pp.list_prompts()}

    async def _handle_prompts_get(params: dict[str, Any]) -> JsonObject:
        prompt_name = params.get("name")
        if not prompt_name:
            raise JsonRpcError(INVALID_PARAMS, "Missing prompt name")
        arguments: dict[str, Any] = params.get("arguments", {}) or {}
        return await pp.call_prompt(prompt_name, arguments)

    ext.dispatch.add_method("initialize", _handle_initialize)
    ext.dispatch.add_notification("notifications/initialized", _handle_initialized)
    ext.dispatch.add_method("ping", _handle_ping)
    ext.dispatch.add_method("tools/list", _handle_tools_list)
    ext.dispatch.add_method("tools/call", _handle_tools_call)
    ext.dispatch.add_method("prompts/list", _handle_prompts_list)
    ext.dispatch.add_method("prompts/get", _handle_prompts_get)

    # 子类可注册 vendor 扩展方法（如 pysandbox/namespaces.*）
    try:
        view.register_extra_methods(ext.dispatch)
    except Exception:
        logger.exception("register_extra_methods() raised")


# ---------------------------------------------------------------------------
# MCPView @impl
# ---------------------------------------------------------------------------


async def _send_json_response(
    status: int, data: JsonValue,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(data).encode()
    headers["content-length"] = str(len(body))
    return Response(status_code=status, body=body, headers=headers)


async def _send_empty_response(status: int) -> Response:
    return Response(status_code=status, headers={"content-length": "0"})


@mutobj.impl(MCPView.post)
async def mcp_view_post(self: MCPView, request: Request) -> Response:
    ext = _get_ext(self)
    assert ext.dispatch is not None

    raw = await request.body()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return await _send_json_response(400, {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        })

    messages: list[JsonValue] = parsed if isinstance(parsed, list) else [parsed]
    has_request = any(
        isinstance(m, dict) and "id" in m and "method" in m
        for m in messages
    )

    if not has_request:
        for msg in messages:
            if isinstance(msg, dict):
                await ext.dispatch.handle(msg)
        return await _send_empty_response(202)

    result_data: JsonValue
    if isinstance(parsed, list):
        responses: list[JsonValue] = []
        for msg in parsed:
            if isinstance(msg, dict):
                resp = await ext.dispatch.handle(msg)
                if resp is not None:
                    responses.append(resp)
        result_data = responses if len(responses) != 1 else responses[0]
    elif isinstance(parsed, dict):
        result_data = await ext.dispatch.handle(parsed)
    else:
        return await _send_json_response(400, {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        })

    if result_data is None:
        return await _send_empty_response(202)

    extra_headers: dict[str, str] = {}
    if isinstance(parsed, dict) and parsed.get("method") == "initialize":
        session_id = secrets.token_hex(16)
        session = MCPSession(session_id=session_id)
        ext.sessions[session_id] = session
        extra_headers["mcp-session-id"] = session_id

    accept = request.headers.get("accept", "")

    if "text/event-stream" in accept:
        sse_data = format_sse(json.dumps(result_data), event="message")
        headers = {
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
        }
        headers.update(extra_headers)
        headers["content-length"] = str(len(sse_data))
        return Response(status_code=200, body=sse_data, headers=headers)
    else:
        return await _send_json_response(200, result_data, extra_headers)


@mutobj.impl(MCPView.delete)
async def mcp_view_delete(self: MCPView, request: Request) -> Response:
    ext = _get_ext(self)
    session_id = request.headers.get("mcp-session-id", "")
    if session_id and session_id in ext.sessions:
        del ext.sessions[session_id]
        return await _send_empty_response(200)
    else:
        return await _send_empty_response(404)


# ---------------------------------------------------------------------------
# MCPView 扩展钩子默认实现 — 子类可覆盖
# ---------------------------------------------------------------------------


@mutobj.impl(MCPView.extra_capabilities)
def mcp_view_extra_capabilities(self: MCPView) -> JsonObject:
    return {}


@mutobj.impl(MCPView.register_extra_methods)
def mcp_view_register_extra_methods(self: MCPView, dispatch: Any) -> None:
    return None
