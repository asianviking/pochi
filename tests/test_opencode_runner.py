"""Tests for OpenCode runner."""

from pathlib import Path

import pytest

from pochi.backends import EngineBackend
from pochi.config import ConfigError
from pochi.model import ResumeToken
from pochi.runners.opencode import (
    BACKEND,
    ENGINE,
    OpenCodeRunner,
    OpenCodeStreamState,
    _extract_tool_action,
    _tool_kind_and_title,
    build_runner,
    translate_opencode_event,
)
from pochi.schemas import opencode as opencode_schema


class TestOpenCodeSchema:
    """Tests for OpenCode schema parsing."""

    def test_decode_step_start(self) -> None:
        """Test decoding step_start event."""
        data = b'{"type": "step_start", "sessionID": "ses_abc123", "timestamp": 1234567890}'
        event = opencode_schema.decode_event(data)
        assert isinstance(event, opencode_schema.StepStart)
        assert event.sessionID == "ses_abc123"

    def test_decode_step_finish(self) -> None:
        """Test decoding step_finish event."""
        data = b'{"type": "step_finish", "sessionID": "ses_abc123", "part": {"reason": "stop"}}'
        event = opencode_schema.decode_event(data)
        assert isinstance(event, opencode_schema.StepFinish)
        assert event.part is not None
        assert event.part["reason"] == "stop"

    def test_decode_tool_use(self) -> None:
        """Test decoding tool_use event."""
        data = b'{"type": "tool_use", "sessionID": "ses_abc123", "part": {"tool": "bash", "callID": "call_1", "state": {"input": {"command": "ls"}}}}'
        event = opencode_schema.decode_event(data)
        assert isinstance(event, opencode_schema.ToolUse)
        assert event.part is not None
        assert event.part["tool"] == "bash"

    def test_decode_text(self) -> None:
        """Test decoding text event."""
        data = b'{"type": "text", "sessionID": "ses_abc123", "part": {"text": "Hello world"}}'
        event = opencode_schema.decode_event(data)
        assert isinstance(event, opencode_schema.Text)
        assert event.part is not None
        assert event.part["text"] == "Hello world"

    def test_decode_error(self) -> None:
        """Test decoding error event."""
        data = b'{"type": "error", "sessionID": "ses_abc123", "error": "API error", "message": "Rate limited"}'
        event = opencode_schema.decode_event(data)
        assert isinstance(event, opencode_schema.Error)
        assert event.message == "Rate limited"


class TestOpenCodeHelpers:
    """Tests for OpenCode helper functions."""

    def test_tool_kind_bash(self) -> None:
        """Test tool kind for bash."""
        kind, title = _tool_kind_and_title("bash", {"command": "ls -la"})
        assert kind == "command"
        assert "ls" in title

    def test_tool_kind_edit(self) -> None:
        """Test tool kind for edit."""
        kind, title = _tool_kind_and_title("edit", {"file_path": "/src/main.py"})
        assert kind == "file_change"

    def test_tool_kind_read(self) -> None:
        """Test tool kind for read."""
        kind, title = _tool_kind_and_title("read", {"file_path": "/src/main.py"})
        assert kind == "tool"
        assert "read" in title

    def test_tool_kind_grep(self) -> None:
        """Test tool kind for grep."""
        kind, title = _tool_kind_and_title("grep", {"pattern": "TODO"})
        assert kind == "tool"
        assert "grep" in title

    def test_tool_kind_websearch(self) -> None:
        """Test tool kind for websearch."""
        kind, title = _tool_kind_and_title("websearch", {"query": "python async"})
        assert kind == "web_search"

    def test_tool_kind_unknown(self) -> None:
        """Test tool kind for unknown tool."""
        kind, title = _tool_kind_and_title("custom_tool", {})
        assert kind == "tool"
        assert title == "custom_tool"

    def test_extract_tool_action(self) -> None:
        """Test extracting tool action."""
        part = {
            "callID": "call_123",
            "tool": "bash",
            "state": {"input": {"command": "ls"}, "status": "pending"},
        }
        action = _extract_tool_action(part)
        assert action is not None
        assert action.id == "call_123"
        assert action.kind == "command"

    def test_extract_tool_action_no_id(self) -> None:
        """Test extracting tool action without ID."""
        part = {"tool": "bash", "state": {"input": {"command": "ls"}}}
        action = _extract_tool_action(part)
        assert action is None


class TestOpenCodeEventTranslation:
    """Tests for OpenCode event translation."""

    def test_translate_step_start_emits_started(self) -> None:
        """Test step_start emits started event."""
        event = opencode_schema.StepStart(sessionID="ses_abc123")
        state = OpenCodeStreamState()
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 1
        assert state.emitted_started is True

    def test_translate_step_start_no_duplicate(self) -> None:
        """Test step_start doesn't emit duplicate started event."""
        event = opencode_schema.StepStart(sessionID="ses_abc123")
        state = OpenCodeStreamState()
        state.emitted_started = True
        state.session_id = "ses_abc123"
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 0

    def test_translate_text_accumulates(self) -> None:
        """Test text events accumulate."""
        state = OpenCodeStreamState()
        state.session_id = "ses_abc123"

        event1 = opencode_schema.Text(part={"text": "Hello "})
        translate_opencode_event(event1, title="opencode", state=state)
        assert state.last_text == "Hello "

        event2 = opencode_schema.Text(part={"text": "world"})
        translate_opencode_event(event2, title="opencode", state=state)
        assert state.last_text == "Hello world"

    def test_translate_step_finish_stop(self) -> None:
        """Test step_finish with stop emits completed."""
        state = OpenCodeStreamState()
        state.session_id = "ses_abc123"
        state.last_text = "Final answer"

        event = opencode_schema.StepFinish(part={"reason": "stop"})
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 1
        assert events[0].ok is True
        assert events[0].answer == "Final answer"

    def test_translate_error(self) -> None:
        """Test error event emits completed with error."""
        state = OpenCodeStreamState()
        state.session_id = "ses_abc123"

        event = opencode_schema.Error(message="API error")
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 1
        assert events[0].ok is False
        assert events[0].error is not None
        assert "API error" in events[0].error

    def test_translate_tool_use_started(self) -> None:
        """Test tool_use pending emits action started."""
        state = OpenCodeStreamState()
        state.session_id = "ses_abc123"

        event = opencode_schema.ToolUse(
            part={
                "callID": "call_1",
                "tool": "bash",
                "state": {"input": {"command": "ls"}, "status": "pending"},
            }
        )
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 1
        assert events[0].phase == "started"
        assert events[0].action.kind == "command"

    def test_translate_tool_use_completed(self) -> None:
        """Test tool_use completed emits action completed."""
        state = OpenCodeStreamState()
        state.session_id = "ses_abc123"

        event = opencode_schema.ToolUse(
            part={
                "callID": "call_1",
                "tool": "bash",
                "state": {
                    "input": {"command": "ls"},
                    "status": "completed",
                    "output": "file1\nfile2",
                    "metadata": {"exit": 0},
                },
            }
        )
        events = translate_opencode_event(event, title="opencode", state=state)

        assert len(events) == 1
        assert events[0].phase == "completed"
        assert events[0].ok is True


class TestOpenCodeRunner:
    """Tests for OpenCodeRunner class."""

    def test_backend_properties(self) -> None:
        """Test backend is correctly configured."""
        assert isinstance(BACKEND, EngineBackend)
        assert BACKEND.id == "opencode"
        assert BACKEND.install_cmd is not None
        assert "opencode-ai" in BACKEND.install_cmd

    def test_runner_engine(self) -> None:
        """Test runner engine ID."""
        runner = OpenCodeRunner()
        assert runner.engine == ENGINE

    def test_runner_format_resume(self) -> None:
        """Test resume token formatting."""
        runner = OpenCodeRunner()
        token = ResumeToken(engine=ENGINE, value="ses_abc123")
        result = runner.format_resume(token)
        assert result == "`opencode --session ses_abc123`"

    def test_runner_extract_resume(self) -> None:
        """Test resume token extraction."""
        runner = OpenCodeRunner()
        text = "`opencode --session ses_abc123`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.engine == ENGINE
        assert token.value == "ses_abc123"

    def test_runner_extract_resume_with_run(self) -> None:
        """Test resume token extraction with run command."""
        runner = OpenCodeRunner()
        text = "`opencode run --session ses_abc123`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.value == "ses_abc123"

    def test_runner_extract_resume_short_flag(self) -> None:
        """Test resume token extraction with short flag."""
        runner = OpenCodeRunner()
        text = "`opencode -s ses_abc123`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.value == "ses_abc123"

    def test_runner_extract_resume_none(self) -> None:
        """Test resume token extraction returns None for no match."""
        runner = OpenCodeRunner()
        token = runner.extract_resume("No resume token here")
        assert token is None

    def test_runner_build_args_new_session(self) -> None:
        """Test building args for new session."""
        runner = OpenCodeRunner()
        state = OpenCodeStreamState()
        args = runner.build_args("hello", None, state=state)
        assert "run" in args
        assert "--format" in args
        assert "json" in args
        assert "--" in args
        assert "hello" in args

    def test_runner_build_args_resume(self) -> None:
        """Test building args for resume session."""
        runner = OpenCodeRunner()
        state = OpenCodeStreamState()
        token = ResumeToken(engine=ENGINE, value="ses_abc123")
        args = runner.build_args("hello", token, state=state)
        assert "--session" in args
        assert "ses_abc123" in args

    def test_runner_build_args_with_model(self) -> None:
        """Test building args with model."""
        runner = OpenCodeRunner(model="gpt-4")
        state = OpenCodeStreamState()
        args = runner.build_args("hello", None, state=state)
        assert "--model" in args
        assert "gpt-4" in args

    def test_runner_new_state(self) -> None:
        """Test creating new state."""
        runner = OpenCodeRunner()
        state = runner.new_state("hello", None)
        assert isinstance(state, OpenCodeStreamState)


class TestOpenCodeBuildRunner:
    """Tests for build_runner function."""

    def test_build_runner_default_config(self) -> None:
        """Test building runner with default config."""
        runner = build_runner({}, Path("/test/workspace.toml"))
        assert isinstance(runner, OpenCodeRunner)
        assert runner.model is None

    def test_build_runner_with_model(self) -> None:
        """Test building runner with model."""
        runner = build_runner({"model": "gpt-4"}, Path("/test/workspace.toml"))
        assert isinstance(runner, OpenCodeRunner)
        assert runner.model == "gpt-4"
        assert runner.session_title == "gpt-4"

    def test_build_runner_invalid_model(self) -> None:
        """Test building runner with invalid model."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"model": 123}, Path("/test/workspace.toml"))
        assert "model" in str(exc_info.value)
