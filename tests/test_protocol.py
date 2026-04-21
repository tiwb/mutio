"""mutio.net._protocol — format_sse 测试。"""

from mutio.net._protocol import format_sse


class TestFormatSse:
    def test_data_only(self):
        result = format_sse("hello")
        assert result == b"data: hello\n\n"

    def test_with_event(self):
        result = format_sse("payload", event="message")
        assert result == b"event: message\ndata: payload\n\n"

    def test_with_id(self):
        result = format_sse("payload", id="42")
        assert result == b"id: 42\ndata: payload\n\n"

    def test_with_event_and_id(self):
        result = format_sse("payload", event="update", id="7")
        assert result == b"id: 7\nevent: update\ndata: payload\n\n"

    def test_multiline_data(self):
        result = format_sse("line1\nline2\nline3")
        assert result == b"data: line1\ndata: line2\ndata: line3\n\n"

    def test_empty_data(self):
        result = format_sse("")
        assert result == b"data: \n\n"

    def test_returns_bytes(self):
        result = format_sse("test")
        assert isinstance(result, bytes)

    def test_unicode_data(self):
        result = format_sse("你好")
        assert "你好".encode("utf-8") in result
