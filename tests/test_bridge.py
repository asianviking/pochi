"""Tests for pochi.bridge module."""

from __future__ import annotations

import pytest

from pochi.bridge import (
    _build_bot_commands,
    _is_cancel_command,
    _log_runner_event,
    _strip_engine_command,
)
from pochi.model import EngineId, ResumeToken
from pochi.events import EventFactory
from pochi.router import AutoRouter, RunnerEntry
from pochi.runners.mock import Return, ScriptRunner


def _make_script_runner(engine: str) -> ScriptRunner:
    """Create a ScriptRunner for testing."""
    return ScriptRunner([Return(answer="test")], engine=EngineId(engine))


def _make_router(*engines: str) -> AutoRouter:
    """Create an AutoRouter with the given engines."""
    entries = [
        RunnerEntry(engine=EngineId(e), runner=_make_script_runner(e)) for e in engines
    ]
    return AutoRouter(entries=entries, default_engine=engines[0])


class TestIsCancelCommand:
    """Tests for _is_cancel_command function."""

    def test_simple_cancel(self) -> None:
        assert _is_cancel_command("/cancel") is True

    def test_cancel_with_text(self) -> None:
        assert _is_cancel_command("/cancel now") is True

    def test_cancel_with_bot_mention(self) -> None:
        assert _is_cancel_command("/cancel@mybot") is True

    def test_cancel_with_bot_mention_and_text(self) -> None:
        assert _is_cancel_command("/cancel@mybot please") is True

    def test_cancelled_is_not_cancel(self) -> None:
        """Test that /cancelled is not treated as /cancel."""
        assert _is_cancel_command("/cancelled") is False

    def test_empty_string(self) -> None:
        assert _is_cancel_command("") is False

    def test_whitespace(self) -> None:
        assert _is_cancel_command("   ") is False

    def test_other_command(self) -> None:
        assert _is_cancel_command("/help") is False


class TestStripEngineCommand:
    """Tests for _strip_engine_command function."""

    def test_strips_engine_command_inline(self) -> None:
        text, engine = _strip_engine_command(
            "/claude do something", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == "do something"

    def test_strips_engine_command_with_newline(self) -> None:
        text, engine = _strip_engine_command(
            "/claude\nHello world", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == "Hello world"

    def test_strips_engine_command_with_bot_suffix(self) -> None:
        text, engine = _strip_engine_command(
            "/claude@mybot hello", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == "hello"

    def test_ignores_unknown_engine(self) -> None:
        text, engine = _strip_engine_command(
            "/unknown hello", engine_ids=(EngineId("claude"),)
        )
        assert engine is None
        assert text == "/unknown hello"

    def test_only_first_non_empty_line(self) -> None:
        """Test that engine command must be on first non-empty line."""
        text, engine = _strip_engine_command(
            "hello\n/claude hi", engine_ids=(EngineId("claude"),)
        )
        assert engine is None
        assert text == "hello\n/claude hi"

    def test_empty_string(self) -> None:
        text, engine = _strip_engine_command("", engine_ids=(EngineId("claude"),))
        assert engine is None
        assert text == ""

    def test_no_engine_ids(self) -> None:
        text, engine = _strip_engine_command("/claude hello", engine_ids=())
        assert engine is None
        assert text == "/claude hello"

    def test_case_insensitive(self) -> None:
        text, engine = _strip_engine_command(
            "/CLAUDE hello", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == "hello"

    def test_multiple_engines(self) -> None:
        engines = (EngineId("claude"), EngineId("codex"))

        text1, engine1 = _strip_engine_command("/claude hi", engine_ids=engines)
        assert engine1 == EngineId("claude")

        text2, engine2 = _strip_engine_command("/codex hi", engine_ids=engines)
        assert engine2 == EngineId("codex")

    def test_command_only_no_text(self) -> None:
        text, engine = _strip_engine_command(
            "/claude", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == ""

    def test_leading_whitespace_lines(self) -> None:
        text, engine = _strip_engine_command(
            "\n\n/claude hello", engine_ids=(EngineId("claude"),)
        )
        assert engine == EngineId("claude")
        assert text == "hello"

    def test_not_slash_command(self) -> None:
        text, engine = _strip_engine_command(
            "just text", engine_ids=(EngineId("claude"),)
        )
        assert engine is None
        assert text == "just text"


class TestBuildBotCommands:
    """Tests for _build_bot_commands function."""

    def test_includes_engine_commands(self) -> None:
        router = _make_router("claude")
        commands = _build_bot_commands(router)
        assert any(cmd["command"] == "claude" for cmd in commands)

    def test_includes_cancel(self) -> None:
        router = _make_router("claude")
        commands = _build_bot_commands(router)
        assert any(cmd["command"] == "cancel" for cmd in commands)

    def test_includes_workspace_commands(self) -> None:
        router = _make_router("claude")
        commands = _build_bot_commands(router)
        command_names = [cmd["command"] for cmd in commands]
        assert "clone" in command_names
        assert "create" in command_names
        assert "list" in command_names
        assert "help" in command_names

    def test_multiple_engines(self) -> None:
        router = _make_router("claude", "codex")
        commands = _build_bot_commands(router)
        command_names = [cmd["command"] for cmd in commands]
        assert "claude" in command_names
        assert "codex" in command_names


class TestLogRunnerEvent:
    """Tests for _log_runner_event function."""

    def test_logs_started_event(self) -> None:
        """Test that started events can be logged without error."""
        factory = EventFactory(EngineId("claude"))
        token = ResumeToken(engine=EngineId("claude"), value="test-session")
        event = factory.started(token, title="Test Run")
        # Should not raise
        _log_runner_event(event)

    def test_logs_action_event(self) -> None:
        """Test that action events can be logged without error."""
        factory = EventFactory(EngineId("claude"))
        event = factory.action_started(
            action_id="1",
            kind="tool",
            title="Running command",
        )
        # Should not raise
        _log_runner_event(event)

    def test_logs_completed_event(self) -> None:
        """Test that completed events can be logged without error."""
        factory = EventFactory(EngineId("claude"))
        event = factory.completed_ok(answer="Done!")
        # Should not raise
        _log_runner_event(event)


class TestStripResumeLines:
    """Tests for _strip_resume_lines function."""

    def test_strips_resume_lines(self) -> None:
        from pochi.bridge import _strip_resume_lines

        def is_resume(line: str) -> bool:
            return "resume" in line.lower()

        text = "hello\nclaude resume abc123\nworld"
        result = _strip_resume_lines(text, is_resume_line=is_resume)
        assert result == "hello\nworld"

    def test_returns_continue_for_empty_result(self) -> None:
        from pochi.bridge import _strip_resume_lines

        def is_resume(line: str) -> bool:
            return True  # All lines are resume lines

        text = "claude resume abc123"
        result = _strip_resume_lines(text, is_resume_line=is_resume)
        assert result == "continue"

    def test_preserves_non_resume_lines(self) -> None:
        from pochi.bridge import _strip_resume_lines

        def is_resume(line: str) -> bool:
            return "resume" in line

        text = "first line\nsecond line\nthird line"
        result = _strip_resume_lines(text, is_resume_line=is_resume)
        assert result == "first line\nsecond line\nthird line"


class TestFlattenExceptionGroup:
    """Tests for _flatten_exception_group function."""

    def test_single_exception(self) -> None:
        from pochi.bridge import _flatten_exception_group

        exc = ValueError("test error")
        result = _flatten_exception_group(exc)
        assert result == [exc]

    def test_exception_group(self) -> None:
        from pochi.bridge import _flatten_exception_group

        exc1 = ValueError("error1")
        exc2 = TypeError("error2")
        group = ExceptionGroup("group", [exc1, exc2])
        result = _flatten_exception_group(group)
        assert exc1 in result
        assert exc2 in result
        assert len(result) == 2

    def test_nested_exception_group(self) -> None:
        from pochi.bridge import _flatten_exception_group

        exc1 = ValueError("error1")
        exc2 = TypeError("error2")
        inner = ExceptionGroup("inner", [exc1])
        outer = ExceptionGroup("outer", [inner, exc2])
        result = _flatten_exception_group(outer)
        assert exc1 in result
        assert exc2 in result
        assert len(result) == 2


class TestFormatError:
    """Tests for _format_error function."""

    @pytest.mark.anyio
    async def test_single_error(self) -> None:
        from pochi.bridge import _format_error

        exc = ValueError("test error")
        result = _format_error(exc)
        assert result == "test error"

    @pytest.mark.anyio
    async def test_empty_message_uses_class_name(self) -> None:
        from pochi.bridge import _format_error

        exc = ValueError()
        result = _format_error(exc)
        assert result == "ValueError"

    @pytest.mark.anyio
    async def test_exception_group_single_message(self) -> None:
        from pochi.bridge import _format_error

        exc1 = ValueError("only error")
        group = ExceptionGroup("group", [exc1])
        result = _format_error(group)
        assert result == "only error"

    @pytest.mark.anyio
    async def test_exception_group_multiple_messages(self) -> None:
        from pochi.bridge import _format_error

        exc1 = ValueError("error1")
        exc2 = TypeError("error2")
        group = ExceptionGroup("group", [exc1, exc2])
        result = _format_error(group)
        assert "error1" in result
        assert "error2" in result


class TestSyncResumeToken:
    """Tests for sync_resume_token function."""

    def test_returns_provided_token(self) -> None:
        from pochi.bridge import sync_resume_token
        from pochi.render import ExecProgressRenderer

        renderer = ExecProgressRenderer(engine=EngineId("claude"))
        token = ResumeToken(engine=EngineId("claude"), value="test-123")

        result = sync_resume_token(renderer, token)
        assert result == token
        assert renderer.resume_token == token

    def test_uses_renderer_token_when_none_provided(self) -> None:
        from pochi.bridge import sync_resume_token
        from pochi.render import ExecProgressRenderer

        token = ResumeToken(engine=EngineId("claude"), value="existing-123")
        renderer = ExecProgressRenderer(engine=EngineId("claude"))
        renderer.resume_token = token

        result = sync_resume_token(renderer, None)
        assert result == token

    def test_returns_none_when_no_token(self) -> None:
        from pochi.bridge import sync_resume_token
        from pochi.render import ExecProgressRenderer

        renderer = ExecProgressRenderer(engine=EngineId("claude"))
        result = sync_resume_token(renderer, None)
        assert result is None


class TestBridgeConfig:
    """Tests for BridgeConfig dataclass."""

    def test_creates_bridge_config(self) -> None:
        from pochi.bridge import BridgeConfig, PROGRESS_EDIT_EVERY_S

        router = _make_router("claude")
        config = BridgeConfig(
            bot=None,  # type: ignore
            router=router,
            chat_id=123,
            final_notify=True,
            startup_msg="Hello!",
        )
        assert config.chat_id == 123
        assert config.final_notify is True
        assert config.startup_msg == "Hello!"
        assert config.progress_edit_every == PROGRESS_EDIT_EVERY_S

    def test_custom_progress_edit_every(self) -> None:
        from pochi.bridge import BridgeConfig

        router = _make_router("claude")
        config = BridgeConfig(
            bot=None,  # type: ignore
            router=router,
            chat_id=123,
            final_notify=False,
            startup_msg="Hi",
            progress_edit_every=5.0,
        )
        assert config.progress_edit_every == 5.0


class TestRunningTask:
    """Tests for RunningTask dataclass."""

    def test_creates_running_task(self) -> None:
        from pochi.bridge import RunningTask

        task = RunningTask()
        assert task.resume is None
        assert not task.resume_ready.is_set()
        assert not task.cancel_requested.is_set()
        assert not task.done.is_set()

    def test_running_task_events(self) -> None:
        from pochi.bridge import RunningTask

        task = RunningTask()
        token = ResumeToken(engine=EngineId("claude"), value="test")

        task.resume = token
        task.resume_ready.set()
        task.cancel_requested.set()
        task.done.set()

        assert task.resume == token
        assert task.resume_ready.is_set()
        assert task.cancel_requested.is_set()
        assert task.done.is_set()


class TestRunOutcome:
    """Tests for RunOutcome dataclass."""

    def test_creates_run_outcome(self) -> None:
        from pochi.bridge import RunOutcome

        outcome = RunOutcome()
        assert outcome.cancelled is False
        assert outcome.completed is None
        assert outcome.resume is None

    def test_run_outcome_with_values(self) -> None:
        from pochi.bridge import RunOutcome

        token = ResumeToken(engine=EngineId("claude"), value="test")
        factory = EventFactory(EngineId("claude"))
        completed = factory.completed_ok(answer="Done")

        outcome = RunOutcome(
            cancelled=True,
            completed=completed,
            resume=token,
        )
        assert outcome.cancelled is True
        assert outcome.completed == completed
        assert outcome.resume == token
