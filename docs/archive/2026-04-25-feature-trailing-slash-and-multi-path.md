# 路由规范化 + View.path 多绑定

**状态**：✅ 已完成
**日期**：2026-04-25
**类型**：功能设计

## 背景

mutio.net 当前对标 Starlette,提供最小化的 ASGI 服务器和 View 类发现。路由匹配过于严格——`path` 字段精确匹配(除 `{param}` 占位符),无 trailing-slash 规范化。访问 `/auth/`(注册的是 `/auth`)直接 404,而主流框架(Flask/Django/FastAPI/Starlette/Express)默认都会自动规范化。

同时,「同一个 endpoint 绑定多个 path」也无原生支持,只能写两个 View 子类共享 mixin,不优雅。

### 触发事件

mutbot 把登录入口拆为独立 HTML 页 `/auth/login`,期望访问 `/auth` 和 `/auth/` 都跳转过去。线上发现 `/auth/`(带 trailing slash)匹配不到任何 View,被迫把规范化逻辑塞进鉴权 middleware,违反了职责划分——鉴权不应关心 trailing slash。

### 设计边界

本规范**只做路由层**两件事:
1. trailing-slash 规范化
2. `View.path` 支持多绑定(`str | list[str] | tuple[str, ...]`)

**不做**(避免范围扩散):

- Response 便利函数 / 子类化(`JSONResponse` / `HTMLResponse` / `RedirectResponse` 等)——独立规范处理
- 装饰器风格 endpoint 注册(保持 mutio Declaration 模型)
- 路径类型转换器(`{id:int}` / `{path:path}`)
- 替换底座为 Starlette(mutio 最小依赖原则不变)

## 关键参考

### mutio 源码定位

- `mutio/src/mutio/net/server.py:97-145` — `Server` Declaration,`base_path` 已有,需追加 `redirect_slashes`
- `mutio/src/mutio/net/server.py:148-167` — `View` 基类,`path: str = ""` 单值字段
- `mutio/src/mutio/net/server.py:170-180` — `WebSocketView` 同样有 `path: str` 字段
- `mutio/src/mutio/net/_server_impl.py:242-254` — `_compile_path` 正则编译
- `mutio/src/mutio/net/_server_impl.py:257-264` — `_Route` 数据类
- `mutio/src/mutio/net/_server_impl.py:276-307` — `_discover_routes` 自动发现 + 一对一注册(需扩展为多绑定)
- `mutio/src/mutio/net/_server_impl.py:310-324` — `_match_route` 精确正则匹配(需追加 trailing-slash fallback)
- `mutio/src/mutio/net/_server_impl.py:464-523` — HTTP 路由分发,trailing-slash 重定向需要在此插入

### 触发该规范的下游案例

- `mutbot/src/mutbot/auth/middleware.py` — 当前在 `before_route` 早期硬编码处理 `/auth` 和 `/auth/` 的 302(应被本规范取代)

### 主流框架对照

| 能力 | mutio 现状 | Starlette | Flask | FastAPI |
|------|-----------|-----------|-------|---------|
| trailing-slash 规范化 | 无 | `redirect_slashes=True` (307) | `strict_slashes` (308) | `redirect_slashes=True` (307) |
| 同一 endpoint 多 path | 不支持 | 写两个 Route | 叠装饰器 | 叠装饰器 |

## 设计方案

### 1. trailing-slash 规范化

`Server` 增加配置项 `redirect_slashes: bool = True`(对齐 Starlette/Flask/Django/FastAPI 默认)。

匹配两步走:
1. 精确匹配 `path` → 命中则正常 dispatch
2. 精确未命中且开启规范化时:
   - 若 `path` 以 `/` 结尾(且非 `/`)→ 试 `path[:-1]`,命中 → **307 重定向**到去掉斜杠的 URL
   - 若 `path` 不以 `/` 结尾 → 试 `path + "/"`,命中 → **307 重定向**到加上斜杠的 URL
3. 仍未命中 → 走原有 404 / 静态文件 fallback

**为什么 307 而非 308**:对齐 Starlette。两者都保留 method+body,差别是 308=permanent / 307=temporary。trailing-slash 是服务端为这次请求做的纠正,本身没有"URL 永久变化"语义,且和 Starlette 一致降低用户迁移摩擦。

**重定向 URL 构造**:`Location` 头使用「base_path + 规范化后的 path + 原 query string」,不带 host(浏览器自动用当前 host),避免反向代理场景出错。如果 `Server.base_path` 非空,要把 base_path 拼回去——因为 `_server_route` 在路径匹配前已经把 base_path 剥掉了,但客户端看到的 URL 仍含 base_path。

**WebSocket 不参与规范化**(WS 没有 redirect 语义)。

### 2. View.path 支持多绑定

`View.path` 和 `WebSocketView.path` 类型从 `str` 扩展为 `str | tuple[str, ...]`。`_discover_routes` 注册时若是 tuple,展开为多个 `_Route`,共享同一个 view 实例(同一组 method handler、同一份状态)。

```python
class AuthRedirect(View):
    path = ("/auth", "/auth/")   # 必须是 tuple,不能是 list
```

**为什么只支持 tuple,不支持 list**:`mutobj.Declaration` 出于"类属性可变默认值"考虑,禁止 list/dict/set 作为字段默认值(会要求用 `field(default_factory=...)`)。tuple 不可变,无此限制,且 Python 字面量 `("a", "b")` 可读性与 list 相当。这是 mutobj 强加的约束,不是路由设计的取舍。

兼容性:`path: str` 仍合法(最常见情形,零迁移)。

`StaticView` 的 `path` 不强制支持多绑定(它有 `directory` 配对语义,多 path 含义不清晰),`_discover_routes` 对 StaticView 走另一条分支处理(取首个非空 path 作为前缀)。

### 二者协同

- (1) 是无侵入全局开关,大部分 trailing-slash 问题自动消失
- (2) 让"两条 URL 走同一 handler"声明式表达,适合 mutbot 这种希望显式两形式注册避免一次跳转的场景
- 二者独立,可分别使用

### 兼容性

- `redirect_slashes` 默认 True 是行为变化,但只影响"原本 404 的请求"——这些请求现在拿到 307 而非 404,是改进而非回归
- `View.path: str` 仍合法,既有项目零迁移
- 显式不希望规范化的项目:`Server(redirect_slashes=False)`

## 关键决策

- **trailing-slash 状态码 307**(对齐 Starlette,而非 Flask 的 308)
- **`redirect_slashes` 默认 True**(对齐主流框架默认值,且只改善 404 路径)
- **`View.path` 列表展开为多个 `_Route` 共享同一 view 实例**(不是注册多个 view 实例,保证状态唯一)
- **WebSocket 不参与 trailing-slash 规范化**(WS 无 redirect 语义)
- **重定向 Location 拼接 base_path**(对客户端透明,反向代理场景不出错)

## 消费者场景

| 消费者 | 场景 | 依赖的输出 | 验收标准 |
|--------|------|-----------|---------|
| mutbot | 删除 middleware 里的 `/auth` 和 `/auth/` 早期拦截硬编码 | (1) trailing-slash + (2) 多 path | 注册 `class AuthView(View): path = ["/auth", "/auth/"]` + handler 返回重定向到 `/auth/login`,两形式 URL 都能直接命中(单次跳转,无 307 中间步) |
| mutbot | API 路径漏/多 trailing slash 不再 404 | (1) trailing-slash | 访问 `/api/sessions/`(注册的是 `/api/sessions`)→ 307 到 `/api/sessions`,浏览器/curl/httpx 自动跟随 |
| 未来 mutio 用户 | 多版本 API 共享 handler | (2) 多 path | `path = ["/v1/foo", "/v2/foo"]` 注册后两个路径都路由到同一 view 实例 |

## 实施步骤清单

- [x] **Server 字段** — `Server.redirect_slashes: bool = True` 字段追加到 `server.py`,对应 docstring
- [x] **路由匹配 fallback** — `_match_route` 在精确匹配失败后,根据 `redirect_slashes` 尝试加/去 trailing slash;命中时返回特殊标记(如 `("redirect", normalized_path)`)而非普通 `(view, params)`,避免污染调用方语义
- [x] **HTTP 重定向构造** — `_server_route` HTTP 分支识别"redirect"标记,构造 307 Response(Location 头 = `base_path + normalized_path + (query string)`),通过 `_send_response` 发出
- [x] **View.path 类型扩展** — `View.path` / `WebSocketView.path` 类型注解改为 `str | tuple[str, ...]`,默认值仍 `""`;docstring 说明多绑定语义(只支持 tuple,不支持 list,源于 mutobj Declaration 对可变默认值的限制)
- [x] **_discover_routes 多绑定展开** — 遍历 View 子类时,通过 `_iter_paths` 归一化 `view.path`,对每个 path 创建 `_Route(path, view_instance)`(共享同一 view 实例);WebSocketView 同步;StaticView 仍按 str 处理(取 path[0] 兜底)
- [x] **测试** — `tests/test_routing.py` 新增 15 个测试,覆盖:
  - trailing-slash 双向(`/x` 注册 → `/x/` 触发 307;`/x/` 注册 → `/x` 触发 307)
  - `redirect_slashes=False` 时仍 404
  - 重定向 Location 包含 base_path 和 query string
  - WebSocket 不规范化(注册 `/ws`,连接 `/ws/` → close 4404 而非 307)
  - View.path 为 tuple 时两个 URL 都命中同一 view 实例(用 view 内 counter 验证状态共享)
  - WebSocketView tuple 多绑定
  - 多 path 直接命中绕过 307(mutbot `/auth` 场景)
- [x] **mutbot 联动验证** — 把 `mutbot/src/mutbot/auth/middleware.py` 里 `/auth` 和 `/auth/` 的硬编码拦截删除,注册 `class AuthView(View): path = ("/auth", "/auth/")` + handler 返回 302 到 `/auth/login`,验证两形式 URL 都能跳转到登录页(本步骤在 mutio 实施完成且发版后执行)
