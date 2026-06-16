"""mutio.net.client — HttpClient.create / set_default_user_agent 测试。"""

from __future__ import annotations

import pytest

import mutio.net  # noqa: F401  — 触发 impl 注册
from mutio.net.client import HttpClient


@pytest.fixture(autouse=True)
def _reset_default_ua():
    """每个测试后重置全局 user_agent。"""
    yield
    HttpClient.set_default_user_agent("")


class TestHttpClientCreate:
    def test_default_no_ua(self):
        client = HttpClient.create()
        assert client.headers["user-agent"].startswith("python-httpx/")

    def test_explicit_user_agent(self):
        client = HttpClient.create(user_agent="test-agent/1.0")
        assert client.headers["user-agent"] == "test-agent/1.0"

    def test_explicit_empty_string(self):
        HttpClient.set_default_user_agent("global/1.0")
        client = HttpClient.create(user_agent="")
        assert client.headers["user-agent"].startswith("python-httpx/")

    def test_explicit_none_uses_global(self):
        HttpClient.set_default_user_agent("global/1.0")
        client = HttpClient.create()
        assert client.headers["user-agent"] == "global/1.0"

    def test_explicit_overrides_global(self):
        HttpClient.set_default_user_agent("global/1.0")
        client = HttpClient.create(user_agent="override/2.0")
        assert client.headers["user-agent"] == "override/2.0"

    def test_existing_headers_preserved(self):
        client = HttpClient.create(
            user_agent="test/1.0",
            headers={"x-custom": "value"},
        )
        assert client.headers["user-agent"] == "test/1.0"
        assert client.headers["x-custom"] == "value"

    def test_existing_ua_header_not_overwritten(self):
        client = HttpClient.create(
            user_agent="test/1.0",
            headers={"user-agent": "existing/0.1"},
        )
        assert client.headers["user-agent"] == "existing/0.1"

    def test_kwargs_passthrough(self):
        client = HttpClient.create(timeout=42)
        assert client.timeout.connect == 42

    def test_returns_async_client(self):
        import httpx
        client = HttpClient.create()
        assert isinstance(client, httpx.AsyncClient)


class TestSetDefaultUserAgent:
    def test_set_and_use(self):
        HttpClient.set_default_user_agent("my-app/3.0")
        client = HttpClient.create()
        assert client.headers["user-agent"] == "my-app/3.0"

    def test_reset_to_empty(self):
        HttpClient.set_default_user_agent("my-app/3.0")
        HttpClient.set_default_user_agent("")
        client = HttpClient.create()
        assert client.headers["user-agent"].startswith("python-httpx/")
