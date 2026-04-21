"""client.py Declaration 实现 — HttpClient @impl。"""

from __future__ import annotations

from typing import Any

import httpx

import mutobj

from mutio.net.client import HttpClient

_default_user_agent = ""


@mutobj.impl(HttpClient.set_default_user_agent)
def _set_default_user_agent(cls: type, ua: str) -> None:
    global _default_user_agent
    _default_user_agent = ua


@mutobj.impl(HttpClient.create)
def _create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
    ua = user_agent if user_agent is not None else _default_user_agent
    headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})
    if ua:
        headers.setdefault("user-agent", ua)
    kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)
