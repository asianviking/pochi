"""Tests for pochi.logging module."""

from __future__ import annotations

import io

import pytest

from pochi.logging import (
    SafeWriter,
    _level_value,
    _redact_text,
    _redact_value,
    _truthy,
    get_logger,
    suppress_logs,
)


class TestTruthy:
    """Tests for _truthy function."""

    def test_none_is_false(self) -> None:
        assert _truthy(None) is False

    def test_empty_string_is_false(self) -> None:
        assert _truthy("") is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "Yes", "on"])
    def test_truthy_values(self, value: str) -> None:
        assert _truthy(value) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "invalid"])
    def test_falsy_values(self, value: str) -> None:
        assert _truthy(value) is False

    def test_whitespace_is_trimmed(self) -> None:
        assert _truthy("  true  ") is True
        assert _truthy("  1  ") is True


class TestLevelValue:
    """Tests for _level_value function."""

    def test_default_for_none(self) -> None:
        assert _level_value(None) == 20  # info

    def test_default_for_empty(self) -> None:
        assert _level_value("") == 20  # info

    def test_custom_default(self) -> None:
        assert _level_value(None, default="warning") == 30
        assert _level_value("", default="debug") == 10

    def test_valid_levels(self) -> None:
        assert _level_value("debug") == 10
        assert _level_value("info") == 20
        assert _level_value("warning") == 30
        assert _level_value("error") == 40
        assert _level_value("critical") == 50

    def test_case_insensitive(self) -> None:
        assert _level_value("DEBUG") == 10
        assert _level_value("INFO") == 20
        assert _level_value("Warning") == 30

    def test_invalid_level_uses_default(self) -> None:
        assert _level_value("invalid") == 20  # info
        assert _level_value("invalid", default="warning") == 30


class TestRedactText:
    """Tests for _redact_text function."""

    def test_redacts_bot_token(self) -> None:
        text = "bot123456789:ABC-abc_123"
        result = _redact_text(text)
        assert result == "bot[REDACTED]"

    def test_redacts_bare_token(self) -> None:
        text = "Using token 123456789:ABCdefGHI_123456"
        result = _redact_text(text)
        assert "ABCdefGHI" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_leaves_safe_text_alone(self) -> None:
        text = "Hello world, this is safe text"
        result = _redact_text(text)
        assert result == text

    def test_redacts_multiple_tokens(self) -> None:
        text = "bot111:AAA-bbb_ccc and bot222:XXX-yyy_zzz"
        result = _redact_text(text)
        assert "AAA" not in result
        assert "XXX" not in result


class TestRedactValue:
    """Tests for _redact_value function."""

    def test_redacts_string(self) -> None:
        result = _redact_value("bot123:ABC-def_ghi", {})
        assert "ABC" not in result

    def test_redacts_bytes(self) -> None:
        result = _redact_value(b"bot123:ABC-def_ghi", {})
        assert "ABC" not in result

    def test_redacts_dict_values(self) -> None:
        data = {"token": "bot123:ABC-def_ghi", "safe": "hello"}
        result = _redact_value(data, {})
        assert "ABC" not in str(result)
        assert result["safe"] == "hello"

    def test_redacts_list_values(self) -> None:
        data = ["bot123:ABC-def_ghi", "safe"]
        result = _redact_value(data, {})
        assert "ABC" not in str(result)
        assert "safe" in result

    def test_redacts_tuple_values(self) -> None:
        data = ("bot123:ABC-def_ghi", "safe")
        result = _redact_value(data, {})
        assert "ABC" not in str(result)
        assert isinstance(result, tuple)

    def test_redacts_set_values(self) -> None:
        data = {"bot123:ABC-def_ghi", "safe"}
        result = _redact_value(data, {})
        assert "ABC" not in str(result)
        assert isinstance(result, set)

    def test_returns_other_types_unchanged(self) -> None:
        assert _redact_value(123, {}) == 123
        assert _redact_value(12.5, {}) == 12.5
        assert _redact_value(True, {}) is True
        assert _redact_value(None, {}) is None


class TestSafeWriter:
    """Tests for SafeWriter class."""

    def test_write_to_stream(self) -> None:
        stream = io.StringIO()
        writer = SafeWriter(stream)
        result = writer.write("hello")
        assert result == 5
        assert stream.getvalue() == "hello"

    def test_flush_stream(self) -> None:
        stream = io.StringIO()
        writer = SafeWriter(stream)
        writer.write("hello")
        writer.flush()
        # Should not raise

    def test_isatty_returns_false_for_stringio(self) -> None:
        stream = io.StringIO()
        writer = SafeWriter(stream)
        assert writer.isatty() is False

    def test_handles_closed_stream(self) -> None:
        stream = io.StringIO()
        writer = SafeWriter(stream)
        writer._closed = True
        # Should return 0 when closed
        assert writer.write("hello") == 0
        # Should not raise
        writer.flush()


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_with_name(self) -> None:
        logger = get_logger("test.module")
        assert logger is not None

    def test_get_logger_without_name(self) -> None:
        logger = get_logger()
        assert logger is not None


class TestSuppressLogs:
    """Tests for suppress_logs context manager."""

    def test_context_manager_runs(self) -> None:
        with suppress_logs():
            pass  # Should not raise

    def test_context_manager_with_level(self) -> None:
        with suppress_logs(level="error"):
            pass  # Should not raise
