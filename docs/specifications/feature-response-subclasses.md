# Response 子类化 — JSONResponse / HTMLResponse / PlainTextResponse / RedirectResponse / FileResponse

**状态**：✅ 已完成
**日期**：2026-04-25
**类型**：功能设计 + 不兼容重构

## 背景

mutio 当前的 Response 体系**非常薄**:只有 `Response`(通用)和 `StreamingResponse`(流式)两个 Declaration。常用便利函数只有 `json_response()` / `html_response()`,而且是 snake_case 函数风格。

写 endpoint 的实际场景大量需要:JSON 响应、HTML 响应、纯文本响应、重定向、文件下载。当前只覆盖前两种,且命名、参数名、默认值都和主流框架(Starlette/FastAPI/Django)不一致。

### 触发事件

上一份规范([feature-trailing-slash-and-multi-path.md](feature-trailing-slash-and-multi-path.md))讨论时发现:

1. **mutbot/auth 已有 ~25 处 `json_response()` / `html_response()` 调用**,缺 `redirect_response`、`plain_text_response`、`file_response`,业务被迫手写 `Response(status=302, headers={"location": ...})` 这种容易出错的代码(auth 里 ~10 处)
2. **AI 直觉问题**:让 AI 凭空写"返回 JSON 的 endpoint",训练数据里压倒性多的是 `JSONResponse(content)`(Starlette/FastAPI),没人写 `json_response(data)`。AI 第一反应不会写出现有命名,要查文档
3. **命名 vs 子类的等价性**:`JSONResponse` 这种命名隐含"它是 `Response` 的子类",`isinstance(resp, JSONResponse)` 应该 True;如果只是 `JSONResponse = json_response` 的 alias,语义就是骗人的
4. **mutio 当前 `Response` 是 Declaration,子类化机制是否支持** — 已实测验证(`class JSONResponse(Response): def __init__(self, ...): super().__init__(status_code=..., body=..., headers=...)`),`isinstance` 双向正确,无副作用

### 核心目标:对齐 Starlette 主流框架

本次重构的总指导原则是**对齐 Starlette/FastAPI 的公开 API**,让 AI 凭直觉写出的代码能直接跑、用户从 Starlette 迁移过来零认知成本。这意味着:

- **类名对齐**:`JSONResponse` / `HTMLResponse` / `PlainTextResponse` / `RedirectResponse` / `FileResponse`
- **参数名对齐**:`content`(不是 `data` / `html` / `text`)、`status_code`(不是 `status`)、`media_type`、`filename`、`content_disposition_type`
- **默认值对齐**:`RedirectResponse` 默认 307(不是 302)
- **类型对齐**:`HTMLResponse` / `PlainTextResponse` 的 `content` 收 `str | bytes`(不是只收 str)
- **扩展点对齐**:`JSONResponse` 提供可覆盖的 `render(content) -> bytes` 钩子

为达成这个目标,接受**基类破坏性变更**(`Response.status` → `Response.status_code`)和**移除旧 API**(`json_response` / `html_response` 函数删除,无 alias、无过渡期)。工作区内调用点(mutio / mutbot / mutgui / mutagent tests)一次性同步迁移。

### 设计边界

本规范**做**:
- 5 个 Response 子类:`JSONResponse` / `HTMLResponse` / `PlainTextResponse` / `RedirectResponse` / `FileResponse`,继承自 `Response` Declaration
- 基类字段改名:`Response.status` / `StreamingResponse.status` → `status_code`
- 删除旧 `json_response()` / `html_response()` 函数
- 全工作区调用点同步迁移
- `_serve_file` 私有函数提升为 `FileResponse.__init__` 实现,`StaticView` 改用 `FileResponse`

**不做**:
- 不动 `Response` 的 `body` / `headers` 字段(只改 `status` → `status_code`)
- `StreamingResponse` 只跟着改字段名 `status` → `status_code`,不改 ASGI 发送体系(统一是另一个议题)
- `FileResponse` 沿用同步 `read_bytes()` 实现,**流式发送大文件留后续**(无大文件需求)
- 不改 `_send_response` 分发逻辑(子类行为完全复用基类)
- **不保留 alias、不加 deprecation warning**(不兼容一次到位)

## 关键参考

### mutio 源码定位

- `mutio/src/mutio/net/server.py:37-42` — `Response` Declaration 基类(`status: int = 200` / `body: bytes = b""` / `headers: dict[str, str]`),**`status` 字段将改名为 `status_code`**
- `mutio/src/mutio/net/server.py:44-49` — `StreamingResponse`,**`status` 字段同步改名为 `status_code`**(其余字段不动)
- `mutio/src/mutio/net/server.py:188-211` — 公开 API 区,现有 `json_response()` / `html_response()`(本规范**删除**)
- `mutio/src/mutio/net/_server_impl.py:195-211` — `_serve_file(file_path: Path)`,内部辅助,被 `StaticView` 使用;本规范要把它的核心逻辑提升为 `FileResponse.__init__` 的实现,`StaticView` 改为通过 `FileResponse(...)` 复用,`_serve_file` 删除
- `mutio/src/mutio/net/_server_impl.py:429/444/488` — ASGI 发送 / WebSocket 关闭码逻辑,读取 `response.status`,需同步改为 `response.status_code`

### 子类化技术验证(已通过)

```python
class JSONResponse(Response):
    def __init__(self, content, status_code: int = 200):
        body = self.render(content)
        super().__init__(
            status_code=status_code, body=body,
            headers={"content-type": "application/json; charset=utf-8"},
        )

    def render(self, content) -> bytes:
        import json
        return json.dumps(content, ensure_ascii=False).encode("utf-8")

r = JSONResponse({"a": 1}, status_code=201)
# r.status_code == 201, r.body == b'{"a": 1}', r.headers["content-type"] == "application/json; charset=utf-8"
# isinstance(r, Response) == True, isinstance(r, JSONResponse) == True
```

`Declaration.__init__` 是 mutobj 自动生成的(从字段生成 kwargs),子类重写 + `super().__init__(...)` 工作正常,无副作用。

### 现有调用点盘点(全工作区)

| 调用形式 | 调用点数 | 主要消费者 |
|---------|---------|-----------|
| `Response(status=...)` | ~25 | mutio `_server_impl.py` 8 处、mutbot/auth 11 处、mcp `_view_impl.py` 3 处、tests/mutagent 若干 |
| `json_response(..., status=...)` | ~25 | mutbot/auth(views.py、relay.py)、mutio `_server_impl.py:539` |
| `html_response(..., status=...)` | ~10 | mutbot/auth、mutbot/proxy、mutgui/demo conftest |
| `response.status` 属性访问 | 3 | mutio `_server_impl.py:429/444/488`(ASGI 发送、日志、WS close code) |

**全部一次性迁移**,不留 alias。

### 主流框架对照(本规范目标:与 Starlette 一致)

| 能力 | Starlette/FastAPI | mutio 现状 | 本规范 |
|------|------------------|-----------|--------|
| `Response` 基类 | `Response(content, status_code=200, headers, media_type)` | `Response(status, body, headers)` | 字段 `status` → `status_code`,其他不动 |
| JSON | `JSONResponse(content, status_code=200)`,可覆盖 `render()` | `json_response(data, status=200)` | 新增 `JSONResponse(content, status_code)` 子类,带 `render()` 钩子;**删除** `json_response` |
| HTML | `HTMLResponse(content)`,content 收 str/bytes | `html_response(html)`,只收 str | 新增 `HTMLResponse(content, status_code)`,content 收 `str | bytes`;**删除** `html_response` |
| 纯文本 | `PlainTextResponse(content)`,content 收 str/bytes | 无 | 新增 `PlainTextResponse(content, status_code)`,content 收 `str | bytes` |
| 重定向 | `RedirectResponse(url, status_code=307)` | 无 | 新增 `RedirectResponse(url, status_code=307)` — 默认 307 对齐 Starlette |
| 文件 | `FileResponse(path, headers, media_type, filename, content_disposition_type)` | `_serve_file()` 私有 + `StaticView` | 新增 `FileResponse`,提升 `_serve_file` 为其实现,加 `filename` / `content_disposition_type` |

## 设计方案

### 基类破坏性改名

```python
# server.py
class Response(mutobj.Declaration):
    """HTTP 响应。"""
    status_code: int = 200      # 原 status
    body: bytes = b""
    headers: dict[str, str] = mutobj.field(default_factory=dict)


class StreamingResponse(mutobj.Declaration):
    """流式 HTTP 响应。"""
    status_code: int = 200      # 原 status
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    body_iterator: AsyncIterator[bytes] | None = None
    media_type: str = "text/event-stream"
```

理由:Starlette/FastAPI 全线用 `status_code`,mutio 自己内部不能既叫 `status` 又叫 `status_code`。这次彻底统一,后面所有子类参数和基类字段同名,不需要 super 调用时映射。

WS 关闭码语义(`server.py:123` "WebSocket 场景:关闭连接(使用 Response.status 作为关闭码)")改为读 `Response.status_code`,语义不变。

### 子类签名(对齐 Starlette)

全部继承自 `Response`(Declaration),只重写 `__init__`(`JSONResponse` 额外提供可覆盖的 `render()`)。子类不增字段,唯一职责是「把高层参数转换为 status_code/body/headers 三元组」。

```python
class JSONResponse(Response):
    """JSON 响应。content 经 render() 序列化为 bytes,自动设 content-type。

    覆盖 render() 可替换序列化逻辑(如使用 orjson、自定义 datetime/Decimal 编码)。
    """
    def __init__(self, content: Any, status_code: int = 200):
        super().__init__(
            status_code=status_code,
            body=self.render(content),
            headers={"content-type": "application/json; charset=utf-8"},
        )

    def render(self, content: Any) -> bytes:
        import json
        return json.dumps(content, ensure_ascii=False).encode("utf-8")


class HTMLResponse(Response):
    """HTML 响应。content-type = text/html; charset=utf-8。"""
    def __init__(self, content: str | bytes, status_code: int = 200):
        body = content.encode("utf-8") if isinstance(content, str) else content
        super().__init__(
            status_code=status_code, body=body,
            headers={"content-type": "text/html; charset=utf-8"},
        )


class PlainTextResponse(Response):
    """纯文本响应。content-type = text/plain; charset=utf-8。"""
    def __init__(self, content: str | bytes, status_code: int = 200):
        body = content.encode("utf-8") if isinstance(content, str) else content
        super().__init__(
            status_code=status_code, body=body,
            headers={"content-type": "text/plain; charset=utf-8"},
        )


class RedirectResponse(Response):
    """重定向响应。

    默认 307(临时,保 method+body,对齐 Starlette);永久重定向用 308;
    需要降级为 GET 用 302/303(不推荐,语义模糊);永久且降级为 GET 用 301。
    """
    def __init__(
        self, url: str, status_code: int = 307,
        headers: dict[str, str] | None = None,
    ):
        merged = dict(headers or {})
        merged["location"] = url      # 用户传的 location 会被覆盖,防止冲突
        super().__init__(status_code=status_code, body=b"", headers=merged)


class FileResponse(Response):
    """文件响应。读取磁盘内容,自动推断 content-type,设 cache-control。

    media_type 为 None 时按扩展名推断,推不出来用 application/octet-stream。
    cache_control 为 None 时:html 用 no-cache,其他用 public, max-age=86400。
    filename 非 None 时设 Content-Disposition,默认 attachment(下载),
    传 "inline" 可在浏览器内预览。
    """
    def __init__(
        self, path: str | Path, *, status_code: int = 200,
        media_type: str | None = None, cache_control: str | None = None,
        filename: str | None = None,
        content_disposition_type: str = "attachment",
    ):
        ...  # 详见下节
```

### `FileResponse` 的实现来源

把 `_server_impl.py:195` 的 `_serve_file(file_path: Path) -> Response` 内部逻辑提升为 `FileResponse.__init__` 的实现:

1. 推断 media_type(显式参数 > `mimetypes.guess_type` > `application/octet-stream`)
2. 同步 `path.read_bytes()`(沿用现状,**大文件流式留后续**)
3. 设 `content-length`
4. cache-control(显式参数 > html 用 `no-cache` > 其他 `public, max-age=86400`)
5. **新增** filename 处理:非 None 时设 `Content-Disposition: <type>; filename="<name>"`(对齐 Starlette,支持下载场景)
6. 文件不存在时抛 `FileNotFoundError`(让上层调用者决定 404 还是其他处理)

`StaticView` 内部从「调用 `_serve_file(resolved)` 拿 Response」改为「直接 `return FileResponse(resolved)`」。`_serve_file` 函数移除(成为 `FileResponse` 的私有实现细节)。

`_server_route` 静态文件 fallback 分支同样改用 `FileResponse(resolved)`。

### 移除旧函数

`json_response()` / `html_response()` 函数从 `server.py:188-211` 公开 API 区**直接删除**,不留 alias。

理由:
- 全部调用点(~35 处)都在工作区内(mutio / mutbot / mutgui),一次 sed 替换搞定
- alias 留下会让"为什么有两套 API"成为永远要解释的问题
- 不兼容一次到位,迁移完成后 import 路径干净

迁移规则:
- `json_response(data, status=N)` → `JSONResponse(data, status_code=N)`
- `html_response(html, status=N)` → `HTMLResponse(html, status_code=N)`

### 命名:为什么用 PascalCase 类风格

之前 mutio 走 snake_case 函数风格(`json_response`)是因为最早只把它当"构造函数"。这次改成类:

1. **AI 直觉**:Starlette/FastAPI 训练数据压倒性是 `JSONResponse(content)`,没人写 `json_response`
2. **isinstance 真实可用**:`if isinstance(resp, RedirectResponse): ...` 在中间件、测试、日志格式化中有真实用例
3. **mutobj 不阻碍子类化**:`Declaration` 子类重写 `__init__` 已实测可用

### `render()` 钩子:为什么 `JSONResponse` 加,其他子类不加

Starlette 的 `render(content) -> bytes` 是 `JSONResponse` 的核心扩展点(用户自定义 datetime/Decimal 序列化、用 orjson 替换标准库)。其他子类(HTML / PlainText)的"序列化"就是 `str.encode("utf-8")`,没有可定制空间。`RedirectResponse` 没有 body,`FileResponse` 的 body 来自磁盘。

所以**只有 `JSONResponse` 提供 `render()`**,其他子类需要定制时直接传 bytes 即可(HTMLResponse / PlainTextResponse 的 content 已经收 `str | bytes`)。

### 协同关系

- 五个子类相互独立,但**基类改名 + 旧函数删除是前置条件**,必须一次性完成
- `FileResponse` 是唯一涉及 `StaticView` 内部重构的(`_serve_file` 提升),其他四个纯增量
- 全工作区调用点同步迁移,迁移完成前不能合入

### 兼容性

**这是一次不兼容重构**,以下 API 变化:

- `Response.status` 字段不存在,改用 `Response.status_code`
- `StreamingResponse.status` 字段不存在,改用 `StreamingResponse.status_code`
- `json_response()` / `html_response()` 函数不存在,改用 `JSONResponse` / `HTMLResponse` 类

工作区内所有调用点(mutio / mutbot / mutgui / mutagent tests)在本规范实施步骤中**一次性同步迁移**,迁移完成后无遗留旧调用。外部用户(若有)需要按上述规则改名。

## 关键决策

- **PascalCase 类风格 + 真子类化**(对齐 Starlette/FastAPI 直觉,`isinstance` 真实可用,而非 alias 装类)
- **基类破坏性改名 `status` → `status_code`**(一次到位,基类、子类、所有调用点统一,避免"字段叫 status 但参数叫 status_code"的永久不一致)
- **`JSONResponse` 提供 `render()` 钩子,其他子类不提供**(对齐 Starlette 扩展模式;HTML/PlainText 没有可定制空间,RedirectResponse 无 body,FileResponse body 来自磁盘)
- **`HTMLResponse` / `PlainTextResponse` 的 content 收 `str | bytes`**(对齐 Starlette,避免模板引擎已编码 bytes 时强制 decode→encode)
- **`RedirectResponse` 默认 307**(对齐 Starlette,保 method+body 更安全;trailing-slash 规范化已经在 Server 层用 307,与本子类默认值一致)
- **`FileResponse` 取代 `_serve_file`**(`_serve_file` 提升为 `FileResponse.__init__` 实现,`StaticView` 改用 `FileResponse`,移除重复;新增 `filename` / `content_disposition_type` 支持下载场景)
- **`FileResponse` 沿用同步 `read_bytes()` 实现**(无大文件需求,流式发送留后续单独议题)
- **删除 `json_response` / `html_response`,无 alias 无 deprecation**(全工作区调用点一次性同步迁移,迁移完成后干净)
- **`StreamingResponse` 只跟着改字段名 `status` → `status_code`**(独立 Declaration 体系,统一发送逻辑是另一议题)

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|--------|------|-----------|---------|
| 新写 endpoint 的开发者(包括 AI) | 直接写 `return JSONResponse(content)` 而不是查 mutio 文档 | `JSONResponse` 类 | AI 第一反应能写出 `JSONResponse(...)`,无需查文档,运行结果与 Starlette 等价 |
| mutbot/auth 现有 ~25 处 `json_response(..., status=400)` | 一次性迁移为 `JSONResponse(..., status_code=400)` | `JSONResponse` 类 + 旧函数已删除 | 全部调用点改名后测试通过;`isinstance(resp, JSONResponse)` 和 `isinstance(resp, Response)` 双向 True |
| mutbot/auth 现有 ~10 处 `Response(status=302, headers={"location":...})` | 顺便改为 `RedirectResponse(url)`(本规范实施时清理) | `RedirectResponse` 类 | 调用一行,Location 头自动设置,语义清晰 |
| 未来 mutio 用户写文件下载 endpoint | `return FileResponse("/data/x.pdf", filename="report.pdf")` | `FileResponse` 类 | 自动推断 media_type、设 cache-control、设 Content-Disposition,无需手动 read_bytes + 拼 headers |
| 中间件/测试代码 | `if isinstance(resp, RedirectResponse): ...` 判断响应类型 | 真子类语义 | isinstance 判断成立,可以分流处理 |
| 需要自定义 JSON 序列化的开发者 | 子类化 `JSONResponse` 覆盖 `render()`(如使用 orjson、自定义 datetime 编码) | `render(content) -> bytes` 钩子 | 覆盖 render 后,所有 `JSONResponse(...)` 调用自动用新序列化逻辑 |

## 实施步骤清单

- [x] **基类改名** — `server.py` `Response.status` → `Response.status_code`,`StreamingResponse.status` → `StreamingResponse.status_code`
- [x] **5 个子类定义** — `server.py` 公开 API 区追加 `JSONResponse` / `HTMLResponse` / `PlainTextResponse` / `RedirectResponse` / `FileResponse`,均继承自 `Response`,签名与 Starlette 对齐(`content` / `status_code` / `media_type` / `filename` / `content_disposition_type`)
  - [x] `JSONResponse` 实现 `render(content) -> bytes` 钩子,默认用 `json.dumps`
  - [x] `HTMLResponse` / `PlainTextResponse` 的 content 接受 `str | bytes`
  - [x] `RedirectResponse` 默认 status_code=307,自动设 location 头(覆盖用户传入的 location)
- [x] **`FileResponse` 实现** — 把 `_server_impl.py:195` 的 `_serve_file` 核心逻辑搬到 `FileResponse.__init__`,处理:`media_type` 推断、读 bytes、`content-length`、`cache-control` 默认策略、新增 `filename` + `content_disposition_type` 处理、文件不存在抛 `FileNotFoundError`
- [x] **`StaticView` 内部改用 `FileResponse`** — `_static_view_get` 和 `_server_route` 静态 fallback 分支从 `_serve_file(resolved)` 改为 `FileResponse(resolved)`,移除 `_serve_file` 函数
- [x] **删除旧函数** — `server.py` 删除 `json_response` 和 `html_response` 函数定义
- [x] **mutio 内部调用点迁移** —
  - [x] `_server_impl.py` 所有 `Response(status=...)` → `Response(status_code=...)`
  - [x] `_server_impl.py` `response.status` → `response.status_code`(ASGI 发送 + WS close code)
  - [x] `_server_impl.py:539` `json_response({"error": "Internal Server Error"}, status=500)` → `JSONResponse({"error": "Internal Server Error"}, status_code=500)`
  - [x] `mcp/_view_impl.py` 的 3 处 `Response(status=...)` → `Response(status_code=...)`
- [x] **mutio tests 迁移** — `tests/test_server.py` 全面重写为新子类风格,`tests/test_routing.py` 全部 `Response(status=...)` 改名
- [x] **mutbot 调用点迁移** —
  - [x] `mutbot/auth/views.py`、`mutbot/auth/relay.py` 的 `json_response(..., status=...)` → `JSONResponse(..., status_code=...)`
  - [x] `mutbot/auth/views.py`、`mutbot/auth/setup_login.py`、`mutbot/auth/relay.py`、`mutbot/auth/middleware.py` 的 `Response(status=302, headers={"location":...})` → `RedirectResponse(url, status_code=302)`(顺手清理 ~10 处)
  - [x] `mutbot/auth/middleware.py` 的 `Response(status=4401)` / `Response(status=403)` → `Response(status_code=...)`
  - [x] `mutbot/proxy/routes.py` 全部 `json_response/html_response` → `JSONResponse/HTMLResponse`
  - [x] `mutbot/web/routes.py` `json_response` → `JSONResponse`
  - [x] mutbot tests:`test_login_view.py` / `test_setup_token_login.py` / `test_public_access_hardening.py` 的 `resp.status` / `result.status` → `.status_code`
- [x] **mutgui 调用点迁移** — `mutgui/tests/integration/conftest.py` 的 `html_response` → `HTMLResponse`、`mutgui/demo/framework/_server.py` 全部 `html_response` → `HTMLResponse` + `Response(status=301, headers={location})` → `RedirectResponse(..., status_code=301)`
- [x] **mutagent tests 迁移** — `test_server_views.py` 4 处 `Response(status=...)` → `Response(status_code=...)`,6 处 `resp*.status` → `.status_code`
- [x] **mutobj tests** — 检查后无需迁移(`tests/test_positional_init.py` 的 `Response` 是测试本地定义的 mutobj.Declaration 子类,与 mutio Response 无关,字段命名属于测试用例自身设定)
- [x] **新增子类测试** — `mutio/tests/test_server.py` 新增 `TestJSONResponse` / `TestHTMLResponse` / `TestPlainTextResponse` / `TestRedirectResponse` / `TestFileResponse`:
  - 5 个子类的 `status_code` / body / headers 正确性
  - `isinstance(resp, JSONResponse)` 和 `isinstance(resp, Response)` 双向 True
  - `JSONResponse` 子类覆盖 `render()` 后行为变化
  - `HTMLResponse` / `PlainTextResponse` 的 content 同时支持 str 和 bytes
  - `RedirectResponse` 默认 307、自定义 308/301、自定义 headers 合并、location 不能被 headers 覆盖
  - `FileResponse` 推断 media_type、html 文件 cache-control = no-cache、其他文件 cache-control = max-age、`filename` + `content_disposition_type` 设 Content-Disposition、文件不存在抛 `FileNotFoundError`
- [x] **下游验证** — 全工作区测试通过:mutio 138 / mutbot 594 / mutgui 140 / mutagent 771 / mutobj 231,合计 **1874 通过 0 失败**
