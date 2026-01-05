"""Tests for Pi runner."""

from pathlib import Path

import pytest

from pochi.backends import EngineBackend
from pochi.config import ConfigError
from pochi.model import ResumeToken
from pochi.runners.pi import (
    BACKEND,
    ENGINE,
    PiRunner,
    PiStreamState,
    _assistant_error,
    _extract_text_blocks,
    _last_assistant_message,
    _tool_kind_and_title,
    build_runner,
    translate_pi_event,
)
from pochi.schemas import pi as pi_schema


class TestPiSchema:
    """Tests for Pi schema parsing."""

    def test_decode_agent_start(self) -> None:
        """Test decoding agent_start event."""
        data = b'{"type": "agent_start"}'
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.AgentStart)

    def test_decode_agent_end(self) -> None:
        """Test decoding agent_end event."""
        data = b'{"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}]}'
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.AgentEnd)
        assert len(event.messages) == 1

    def test_decode_message_end(self) -> None:
        """Test decoding message_end event."""
        data = (
            b'{"type": "message_end", "message": {"role": "assistant", "content": []}}'
        )
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.MessageEnd)
        assert event.message["role"] == "assistant"

    def test_decode_tool_execution_start(self) -> None:
        """Test decoding tool_execution_start event."""
        data = b'{"type": "tool_execution_start", "toolCallId": "call_1", "toolName": "bash", "args": {"command": "ls"}}'
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.ToolExecutionStart)
        assert event.toolCallId == "call_1"
        assert event.toolName == "bash"

    def test_decode_tool_execution_end(self) -> None:
        """Test decoding tool_execution_end event."""
        data = b'{"type": "tool_execution_end", "toolCallId": "call_1", "toolName": "bash", "result": "file1\\nfile2", "isError": false}'
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.ToolExecutionEnd)
        assert event.isError is False

    def test_decode_auto_retry_start(self) -> None:
        """Test decoding auto_retry_start event."""
        data = b'{"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3}'
        event = pi_schema.decode_event(data)
        assert isinstance(event, pi_schema.AutoRetryStart)
        assert event.attempt == 1


class TestPiHelpers:
    """Tests for Pi helper functions."""

    def test_extract_text_blocks(self) -> None:
        """Test extracting text blocks."""
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ]
        result = _extract_text_blocks(content)
        assert result == "Hello world"

    def test_extract_text_blocks_empty(self) -> None:
        """Test extracting text blocks from empty list."""
        result = _extract_text_blocks([])
        assert result is None

    def test_extract_text_blocks_no_text(self) -> None:
        """Test extracting text blocks with no text type."""
        content = [{"type": "tool_use", "id": "123"}]
        result = _extract_text_blocks(content)
        assert result is None

    def test_assistant_error_none(self) -> None:
        """Test assistant error with normal message."""
        message = {"stopReason": "end_turn"}
        result = _assistant_error(message)
        assert result is None

    def test_assistant_error_with_error(self) -> None:
        """Test assistant error with error stop reason."""
        message = {"stopReason": "error", "errorMessage": "API failed"}
        result = _assistant_error(message)
        assert result == "API failed"

    def test_assistant_error_aborted(self) -> None:
        """Test assistant error with aborted stop reason."""
        message = {"stopReason": "aborted"}
        result = _assistant_error(message)
        assert result == "pi run aborted"

    def test_tool_kind_bash(self) -> None:
        """Test tool kind for bash."""
        kind, title = _tool_kind_and_title("bash", {"command": "ls -la"})
        assert kind == "command"
        assert "ls" in title

    def test_tool_kind_edit(self) -> None:
        """Test tool kind for edit."""
        kind, title = _tool_kind_and_title("edit", {"path": "/src/main.py"})
        assert kind == "file_change"

    def test_tool_kind_read(self) -> None:
        """Test tool kind for read."""
        kind, title = _tool_kind_and_title("read", {"path": "/src/main.py"})
        assert kind == "tool"
        assert "read" in title

    def test_tool_kind_grep(self) -> None:
        """Test tool kind for grep."""
        kind, title = _tool_kind_and_title("grep", {"pattern": "TODO"})
        assert kind == "tool"
        assert "grep" in title

    def test_tool_kind_find(self) -> None:
        """Test tool kind for find."""
        kind, title = _tool_kind_and_title("find", {"pattern": "*.py"})
        assert kind == "tool"
        assert "find" in title

    def test_tool_kind_ls(self) -> None:
        """Test tool kind for ls."""
        kind, title = _tool_kind_and_title("ls", {"path": "/src"})
        assert kind == "tool"
        assert "ls" in title

    def test_tool_kind_unknown(self) -> None:
        """Test tool kind for unknown tool."""
        kind, title = _tool_kind_and_title("custom_tool", {})
        assert kind == "tool"
        assert title == "custom_tool"

    def test_last_assistant_message(self) -> None:
        """Test getting last assistant message."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            {"role": "user", "content": "Bye"},
            {"role": "assistant", "content": [{"type": "text", "text": "Goodbye"}]},
        ]
        result = _last_assistant_message(messages)
        assert result is not None
        assert result["content"][0]["text"] == "Goodbye"

    def test_last_assistant_message_none(self) -> None:
        """Test getting last assistant message with no assistant."""
        messages = [{"role": "user", "content": "Hello"}]
        result = _last_assistant_message(messages)
        assert result is None


class TestPiEventTranslation:
    """Tests for Pi event translation."""

    def test_translate_emits_started_first(self) -> None:
        """Test that first event emits started."""
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)

        event = pi_schema.AgentStart()
        events = translate_pi_event(event, title="pi", meta=None, state=state)

        assert len(events) == 1
        assert state.started is True

    def test_translate_tool_execution_start(self) -> None:
        """Test translating tool_execution_start."""
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        state.started = True

        event = pi_schema.ToolExecutionStart(
            toolCallId="call_1", toolName="bash", args={"command": "ls"}
        )
        events = translate_pi_event(event, title="pi", meta=None, state=state)

        assert len(events) == 1
        assert events[0].action.kind == "command"
        assert events[0].phase == "started"
        assert "call_1" in state.pending_actions

    def test_translate_tool_execution_end(self) -> None:
        """Test translating tool_execution_end."""
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        state.started = True

        event = pi_schema.ToolExecutionEnd(
            toolCallId="call_1", toolName="bash", result="file1\nfile2", isError=False
        )
        events = translate_pi_event(event, title="pi", meta=None, state=state)

        assert len(events) == 1
        assert events[0].phase == "completed"
        assert events[0].ok is True

    def test_translate_agent_end(self) -> None:
        """Test translating agent_end."""
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        state.started = True

        event = pi_schema.AgentEnd(
            messages=[
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done!"}],
                    "stopReason": "end_turn",
                }
            ]
        )
        events = translate_pi_event(event, title="pi", meta=None, state=state)

        assert len(events) == 1
        assert events[0].ok is True
        assert events[0].answer == "Done!"


class TestPiRunner:
    """Tests for PiRunner class."""

    def test_backend_properties(self) -> None:
        """Test backend is correctly configured."""
        assert isinstance(BACKEND, EngineBackend)
        assert BACKEND.id == "pi"
        assert BACKEND.install_cmd is not None
        assert "pi-coding-agent" in BACKEND.install_cmd
        assert BACKEND.cli_cmd == "pi"

    def test_runner_engine(self) -> None:
        """Test runner engine ID."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        assert runner.engine == ENGINE

    def test_runner_format_resume(self) -> None:
        """Test resume token formatting."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        result = runner.format_resume(token)
        assert "`pi --session" in result

    def test_runner_format_resume_with_spaces(self) -> None:
        """Test resume token formatting with spaces in path."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        token = ResumeToken(engine=ENGINE, value="/path/with spaces/session.jsonl")
        result = runner.format_resume(token)
        assert '"' in result  # Should be quoted

    def test_runner_extract_resume(self) -> None:
        """Test resume token extraction."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        text = "`pi --session /path/to/session.jsonl`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.engine == ENGINE
        assert token.value == "/path/to/session.jsonl"

    def test_runner_extract_resume_quoted(self) -> None:
        """Test resume token extraction with quoted path."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        text = '`pi --session "/path/with spaces/session.jsonl"`'
        token = runner.extract_resume(text)
        assert token is not None
        assert token.value == "/path/with spaces/session.jsonl"

    def test_runner_extract_resume_none(self) -> None:
        """Test resume token extraction returns None for no match."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        token = runner.extract_resume("No resume token here")
        assert token is None

    def test_runner_command(self) -> None:
        """Test runner command."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        assert runner.command() == "pi"

    def test_runner_build_args(self) -> None:
        """Test building args."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        args = runner.build_args("hello", None, state=state)
        assert "--print" in args
        assert "--mode" in args
        assert "json" in args
        assert "--session" in args

    def test_runner_build_args_with_model(self) -> None:
        """Test building args with model."""
        runner = PiRunner(
            extra_args=[], model="claude-opus-4-5-20251101", provider=None
        )
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        args = runner.build_args("hello", None, state=state)
        assert "--model" in args
        assert "claude-opus-4-5-20251101" in args

    def test_runner_build_args_with_provider(self) -> None:
        """Test building args with provider."""
        runner = PiRunner(extra_args=[], model=None, provider="anthropic")
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = PiStreamState(resume=token)
        args = runner.build_args("hello", None, state=state)
        assert "--provider" in args
        assert "anthropic" in args

    def test_runner_sanitize_prompt(self) -> None:
        """Test prompt sanitization."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        # Prompt starting with dash should be prefixed with space
        assert runner._sanitize_prompt("-p test") == " -p test"
        assert runner._sanitize_prompt("normal prompt") == "normal prompt"

    def test_runner_new_state(self) -> None:
        """Test creating new state."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        state = runner.new_state("hello", None)
        assert isinstance(state, PiStreamState)
        assert state.resume.engine == ENGINE

    def test_runner_new_state_with_resume(self) -> None:
        """Test creating new state with resume token."""
        runner = PiRunner(extra_args=[], model=None, provider=None)
        token = ResumeToken(engine=ENGINE, value="/path/to/session.jsonl")
        state = runner.new_state("hello", token)
        assert state.resume == token


class TestPiBuildRunner:
    """Tests for build_runner function."""

    def test_build_runner_default_config(self) -> None:
        """Test building runner with default config."""
        runner = build_runner({}, Path("/test/workspace.toml"))
        assert isinstance(runner, PiRunner)
        assert runner.model is None
        assert runner.provider is None
        assert runner.extra_args == []

    def test_build_runner_with_model(self) -> None:
        """Test building runner with model."""
        runner = build_runner(
            {"model": "claude-opus-4-5-20251101"}, Path("/test/workspace.toml")
        )
        assert isinstance(runner, PiRunner)
        assert runner.model == "claude-opus-4-5-20251101"

    def test_build_runner_with_provider(self) -> None:
        """Test building runner with provider."""
        runner = build_runner({"provider": "anthropic"}, Path("/test/workspace.toml"))
        assert isinstance(runner, PiRunner)
        assert runner.provider == "anthropic"

    def test_build_runner_with_extra_args(self) -> None:
        """Test building runner with extra_args."""
        runner = build_runner(
            {"extra_args": ["--verbose"]}, Path("/test/workspace.toml")
        )
        assert isinstance(runner, PiRunner)
        assert runner.extra_args == ["--verbose"]

    def test_build_runner_invalid_model(self) -> None:
        """Test building runner with invalid model."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"model": 123}, Path("/test/workspace.toml"))
        assert "model" in str(exc_info.value)

    def test_build_runner_invalid_provider(self) -> None:
        """Test building runner with invalid provider."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"provider": 123}, Path("/test/workspace.toml"))
        assert "provider" in str(exc_info.value)

    def test_build_runner_invalid_extra_args(self) -> None:
        """Test building runner with invalid extra_args."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"extra_args": "not a list"}, Path("/test/workspace.toml"))
        assert "extra_args" in str(exc_info.value)
