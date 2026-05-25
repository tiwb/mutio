"""测试 mutio.schema.docstring — parse_google_args / parse_annotations_section / extract_description。

覆盖点：
- 基本 Args 段解析（单参数、多参数）
- 续行处理
- 带类型括号的参数行
- 空 docstring
- 无 Args 段
- Annotations 段：单行 / 多行 JSON、严格 json.loads、段结束
- extract_description：主描述提取、段头前截断、空字符串
"""

from __future__ import annotations

import pytest

from mutio.schema.docstring import (
    parse_google_args,
    parse_annotations_section,
    extract_description,
)


# ---------------------------------------------------------------------------
# parse_google_args
# ---------------------------------------------------------------------------


class TestParseGoogleArgs:
    def test_simple(self):
        doc = """Do something.

        Args:
            x: an integer.
            y: a string.
        """
        result = parse_google_args(doc)
        assert result == {"x": "an integer.", "y": "a string."}

    def test_arguments_header(self):
        doc = """T.

        Arguments:
            foo: bar.
        """
        result = parse_google_args(doc)
        assert result == {"foo": "bar."}

    def test_parameters_header(self):
        doc = """T.

        Parameters:
            foo: bar.
        """
        result = parse_google_args(doc)
        assert result == {"foo": "bar."}

    def test_continuation_line(self):
        doc = """T.

        Args:
            x: first line.
                second line continues.
        """
        result = parse_google_args(doc)
        assert "first line" in result["x"]
        assert "second line" in result["x"]

    def test_type_annotation_in_args(self):
        doc = """T.

        Args:
            name (str): The person to greet.
            count (int): How many times.
        """
        result = parse_google_args(doc)
        assert result["name"] == "The person to greet."
        assert result["count"] == "How many times."

    def test_section_ends_at_dedent(self):
        doc = """T.

        Args:
            x: an integer.

        Returns:
            something.
        """
        result = parse_google_args(doc)
        assert result == {"x": "an integer."}

    def test_no_args_section(self):
        doc = """A tool with no parameters.

        Returns:
            Some result.
        """
        result = parse_google_args(doc)
        assert result == {}

    def test_empty_docstring(self):
        assert parse_google_args("") == {}

    def test_unknown_param_silently_ignored(self):
        """Args 段散文里出现 signature 没有的参数：不报错（与 Google 解析器惯例一致）。"""
        doc = """T.

        Args:
            real: exists.
            phantom: does not exist.
        """
        result = parse_google_args(doc)
        assert result["real"] == "exists."
        assert result["phantom"] == "does not exist."


# ---------------------------------------------------------------------------
# parse_annotations_section
# ---------------------------------------------------------------------------


class TestParseAnnotationsSection:
    def test_single_line_json(self):
        doc = """T.

Annotations:
    pattern: {"format": "regex", "minLength": 1}
"""
        result = parse_annotations_section(doc)
        line_no, parsed = result["pattern"]
        assert parsed == {"format": "regex", "minLength": 1}
        assert isinstance(line_no, int) and line_no > 0

    def test_multiline_json(self):
        doc = """T.

Annotations:
    opts: {
        "additionalProperties": false,
        "propertyNames": {"pattern": "^[a-z]+$"}
    }
"""
        result = parse_annotations_section(doc)
        _, parsed = result["opts"]
        assert parsed["additionalProperties"] is False
        assert parsed["propertyNames"] == {"pattern": "^[a-z]+$"}

    def test_strict_json_rejects_python_literals(self):
        doc = """T.

Annotations:
    x: {"flag": True}
"""
        with pytest.raises(ValueError, match="invalid JSON"):
            parse_annotations_section(doc)

    def test_strict_json_rejects_single_quotes(self):
        doc = """T.

Annotations:
    x: {'format': 'regex'}
"""
        with pytest.raises(ValueError, match="invalid JSON"):
            parse_annotations_section(doc)

    def test_section_end_at_dedent(self):
        doc = """T.

Annotations:
    x: {"minimum": 0}

Returns:
    nothing.
"""
        result = parse_annotations_section(doc)
        assert "x" in result
        _, parsed = result["x"]
        assert parsed["minimum"] == 0

    def test_no_annotations_section(self):
        doc = """T.

Args:
    x: number.
"""
        assert parse_annotations_section(doc) == {}

    def test_empty_docstring(self):
        assert parse_annotations_section("") == {}

    def test_line_number_tracks_original_doc(self):
        """line_no 是原文档的 1-based 行号。"""
        doc = """Line 1
Line 2

Annotations:
    x: {"key": "val"}
"""
        result = parse_annotations_section(doc)
        line_no, _ = result["x"]
        assert line_no == 5  # 原文档第5行


# ---------------------------------------------------------------------------
# extract_description
# ---------------------------------------------------------------------------


class TestExtractDescription:
    def test_single_line(self):
        assert extract_description("Run the thing.") == "Run the thing."

    def test_multiline_main(self):
        doc = """First line.

        Second paragraph still part of main.

        Args:
            x: ignored.
        """
        result = extract_description(doc)
        assert "First line." in result
        assert "Second paragraph" in result
        assert "Args" not in result

    def test_returns_header_terminates(self):
        doc = """Do stuff.

        Returns:
            A result.
        """
        assert extract_description(doc) == "Do stuff."

    def test_raises_header_terminates(self):
        doc = """Validate.

        Raises:
            ValueError: if bad.
        """
        assert extract_description(doc) == "Validate."

    def test_note_header_terminates(self):
        doc = """Summary.

        Note:
            Something important.
        """
        assert extract_description(doc) == "Summary."

    def test_annotations_header_terminates(self):
        doc = """Query.

        Annotations:
            x: {"minimum": 0}
        """
        assert extract_description(doc) == "Query."

    def test_empty_string(self):
        assert extract_description("") == ""

    def test_no_section_headers(self):
        """No section headers → entire doc is description."""
        doc = """This function does things.

It's very useful."""
        assert extract_description(doc) == "This function does things.\n\nIt's very useful."
