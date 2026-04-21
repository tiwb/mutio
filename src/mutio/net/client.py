"""出站连接 Declaration — HttpClient。"""

from __future__ import annotations

from typing import Any

import httpx

import mutobj


class HttpClient(mutobj.Declaration):
    """HTTP 客户端工厂。

    提供统一的 httpx.AsyncClient 创建入口，集中管理默认 headers（User-Agent 等）。

    全局设置::

        HttpClient.set_default_user_agent("mutagent/0.2.0")

    创建时覆盖::

        client = HttpClient.create(user_agent="custom/1.0", timeout=30)
    """

    @classmethod
    def set_default_user_agent(cls, ua: str) -> None:
        """设置全局默认 User-Agent。"""
        ...

    @staticmethod
    def create(*, user_agent: str | None = None, **kwargs: Any) -> httpx.AsyncClient:
        """创建 httpx.AsyncClient。user_agent 传则覆盖全局默认。"""
        ...
