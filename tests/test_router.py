"""Tests for pochi.router module."""

from __future__ import annotations

import pytest

from pochi.model import EngineId, ResumeToken
from pochi.router import AutoRouter, RunnerEntry, RunnerUnavailableError
from pochi.runners.mock import Return, ScriptRunner


def _make_script_runner(
    engine: str,
    resume_value: str = "test-session",
) -> ScriptRunner:
    """Create a ScriptRunner for testing."""
    return ScriptRunner(
        [Return(answer="test")],
        engine=EngineId(engine),
        resume_value=resume_value,
    )


def _make_entry(
    engine: str,
    available: bool = True,
    issue: str | None = None,
    resume_value: str = "test-session",
) -> RunnerEntry:
    """Create a RunnerEntry for testing."""
    return RunnerEntry(
        engine=EngineId(engine),
        runner=_make_script_runner(engine, resume_value),
        available=available,
        issue=issue,
    )


class TestRunnerUnavailableError:
    """Tests for RunnerUnavailableError exception."""

    def test_creates_message_without_issue(self) -> None:
        error = RunnerUnavailableError(EngineId("claude"))
        assert "claude" in str(error)
        assert "unavailable" in str(error)
        assert error.engine == EngineId("claude")
        assert error.issue is None

    def test_creates_message_with_issue(self) -> None:
        error = RunnerUnavailableError(EngineId("claude"), "not installed")
        assert "claude" in str(error)
        assert "not installed" in str(error)
        assert error.issue == "not installed"


class TestRunnerEntry:
    """Tests for RunnerEntry dataclass."""

    def test_creates_entry(self) -> None:
        runner = _make_script_runner("claude")
        entry = RunnerEntry(
            engine=EngineId("claude"),
            runner=runner,
            available=True,
            issue=None,
        )
        assert entry.engine == EngineId("claude")
        assert entry.available is True
        assert entry.issue is None

    def test_creates_unavailable_entry(self) -> None:
        runner = _make_script_runner("claude")
        entry = RunnerEntry(
            engine=EngineId("claude"),
            runner=runner,
            available=False,
            issue="not found",
        )
        assert entry.available is False
        assert entry.issue == "not found"


class TestAutoRouter:
    """Tests for AutoRouter class."""

    def test_creates_router_with_single_entry(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        assert router.default_engine == EngineId("claude")
        assert len(router.entries) == 1

    def test_creates_router_with_multiple_entries(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("codex"),
        ]
        router = AutoRouter(entries=entries, default_engine="claude")
        assert len(router.entries) == 2
        assert router.default_engine == EngineId("claude")

    def test_raises_for_empty_entries(self) -> None:
        with pytest.raises(ValueError, match="at least one runner"):
            AutoRouter(entries=[], default_engine="claude")

    def test_raises_for_duplicate_engine(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("claude"),
        ]
        with pytest.raises(ValueError, match="duplicate"):
            AutoRouter(entries=entries, default_engine="claude")

    def test_raises_for_invalid_default(self) -> None:
        entry = _make_entry("claude")
        with pytest.raises(ValueError, match="not configured"):
            AutoRouter(entries=[entry], default_engine="codex")

    def test_engine_ids_property(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("codex"),
        ]
        router = AutoRouter(entries=entries, default_engine="claude")
        assert router.engine_ids == (EngineId("claude"), EngineId("codex"))

    def test_available_entries_property(self) -> None:
        entries = [
            _make_entry("claude", available=True),
            _make_entry("codex", available=False, issue="not found"),
        ]
        router = AutoRouter(entries=entries, default_engine="claude")
        available = router.available_entries
        assert len(available) == 1
        assert available[0].engine == EngineId("claude")

    def test_default_entry_property(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("codex"),
        ]
        router = AutoRouter(entries=entries, default_engine="codex")
        assert router.default_entry.engine == EngineId("codex")

    def test_entry_for_engine_with_none(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        result = router.entry_for_engine(None)
        assert result.engine == EngineId("claude")

    def test_entry_for_engine_specific(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("codex"),
        ]
        router = AutoRouter(entries=entries, default_engine="claude")
        result = router.entry_for_engine(EngineId("codex"))
        assert result.engine == EngineId("codex")

    def test_entry_for_engine_raises_for_unknown(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        with pytest.raises(RunnerUnavailableError, match="not configured"):
            router.entry_for_engine(EngineId("unknown"))

    def test_entry_for_with_none_resume(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        result = router.entry_for(None)
        assert result.engine == EngineId("claude")

    def test_entry_for_with_resume_token(self) -> None:
        entries = [
            _make_entry("claude"),
            _make_entry("codex"),
        ]
        router = AutoRouter(entries=entries, default_engine="claude")
        token = ResumeToken(engine=EngineId("codex"), value="session-123")
        result = router.entry_for(token)
        assert result.engine == EngineId("codex")

    def test_runner_for_returns_runner(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        runner = router.runner_for(None)
        assert runner.engine == EngineId("claude")

    def test_runner_for_raises_when_unavailable(self) -> None:
        entry = _make_entry("claude", available=False, issue="broken")
        router = AutoRouter(entries=[entry], default_engine="claude")
        with pytest.raises(RunnerUnavailableError, match="broken"):
            router.runner_for(None)

    def test_format_resume(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        token = ResumeToken(engine=EngineId("claude"), value="session-123")
        result = router.format_resume(token)
        assert "session-123" in result

    def test_extract_resume_returns_none_for_empty(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        assert router.extract_resume(None) is None
        assert router.extract_resume("") is None

    def test_extract_resume_finds_token(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        text = entry.runner.format_resume(
            ResumeToken(engine=EngineId("claude"), value="abc123")
        )
        token = router.extract_resume(text)
        assert token is not None
        assert token.engine == EngineId("claude")

    def test_resolve_resume_from_text(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        text = entry.runner.format_resume(
            ResumeToken(engine=EngineId("claude"), value="abc123")
        )
        token = router.resolve_resume(text, None)
        assert token is not None

    def test_resolve_resume_from_reply_text(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        reply_text = entry.runner.format_resume(
            ResumeToken(engine=EngineId("claude"), value="abc123")
        )
        token = router.resolve_resume("hello", reply_text)
        assert token is not None

    def test_resolve_resume_returns_none_when_not_found(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        token = router.resolve_resume("hello", "world")
        assert token is None

    def test_is_resume_line(self) -> None:
        entry = _make_entry("claude")
        router = AutoRouter(entries=[entry], default_engine="claude")
        # ScriptRunner uses format: {engine} resume <value>
        assert router.is_resume_line("`claude resume test-session`") is True
        assert router.is_resume_line("random text") is False
