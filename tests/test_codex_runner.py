"""Tests for Codex runner."""

from pathlib import Path

import pytest

from pochi.backends import EngineBackend
from pochi.config import ConfigError
from pochi.events import EventFactory
from pochi.model import ResumeToken
from pochi.runners.codex import (
    BACKEND,
    ENGINE,
    CodexRunState,
    CodexRunner,
    _format_change_summary,
    _parse_reconnect_message,
    _short_tool_name,
    _summarize_todo_list,
    _todo_title,
    _TodoSummary,
    build_runner,
    translate_codex_event,
)
from pochi.schemas import codex as codex_schema


class TestCodexSchema:
    """Tests for Codex schema parsing."""

    def test_decode_thread_started(self) -> None:
        """Test decoding thread.started event."""
        data = b'{"type": "thread.started", "thread_id": "thread_abc123"}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.ThreadStarted)
        assert event.thread_id == "thread_abc123"

    def test_decode_turn_started(self) -> None:
        """Test decoding turn.started event."""
        data = b'{"type": "turn.started"}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.TurnStarted)

    def test_decode_turn_completed(self) -> None:
        """Test decoding turn.completed event."""
        data = b'{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 200}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.TurnCompleted)
        assert event.usage.input_tokens == 100
        assert event.usage.cached_input_tokens == 50
        assert event.usage.output_tokens == 200

    def test_decode_turn_failed(self) -> None:
        """Test decoding turn.failed event."""
        data = b'{"type": "turn.failed", "error": {"message": "API error"}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.TurnFailed)
        assert event.error.message == "API error"

    def test_decode_stream_error(self) -> None:
        """Test decoding error event."""
        data = b'{"type": "error", "message": "Connection lost"}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.StreamError)
        assert event.message == "Connection lost"

    def test_decode_item_started_command(self) -> None:
        """Test decoding item.started with command_execution."""
        data = b'{"type": "item.started", "item": {"type": "command_execution", "id": "cmd_1", "command": "ls -la", "aggregated_output": "", "exit_code": null, "status": "in_progress"}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.ItemStarted)
        assert isinstance(event.item, codex_schema.CommandExecutionItem)
        assert event.item.command == "ls -la"
        assert event.item.status == "in_progress"

    def test_decode_item_completed_agent_message(self) -> None:
        """Test decoding item.completed with agent_message."""
        data = b'{"type": "item.completed", "item": {"type": "agent_message", "id": "msg_1", "text": "Hello world"}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.ItemCompleted)
        assert isinstance(event.item, codex_schema.AgentMessageItem)
        assert event.item.text == "Hello world"

    def test_decode_file_change_item(self) -> None:
        """Test decoding file_change item."""
        data = b'{"type": "item.completed", "item": {"type": "file_change", "id": "fc_1", "changes": [{"path": "src/main.py", "kind": "update"}], "status": "completed"}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.ItemCompleted)
        assert isinstance(event.item, codex_schema.FileChangeItem)
        assert len(event.item.changes) == 1
        assert event.item.changes[0].path == "src/main.py"

    def test_decode_todo_list_item(self) -> None:
        """Test decoding todo_list item."""
        data = b'{"type": "item.updated", "item": {"type": "todo_list", "id": "todo_1", "items": [{"text": "Task 1", "completed": true}, {"text": "Task 2", "completed": false}]}}'
        event = codex_schema.decode_event(data)
        assert isinstance(event, codex_schema.ItemUpdated)
        assert isinstance(event.item, codex_schema.TodoListItem)
        assert len(event.item.items) == 2
        assert event.item.items[0].completed is True


class TestCodexHelpers:
    """Tests for Codex helper functions."""

    def test_parse_reconnect_message_valid(self) -> None:
        """Test parsing valid reconnect message."""
        result = _parse_reconnect_message("Reconnecting... 2/5")
        assert result == (2, 5)

    def test_parse_reconnect_message_invalid(self) -> None:
        """Test parsing invalid reconnect message."""
        result = _parse_reconnect_message("Some other message")
        assert result is None

    def test_short_tool_name_with_both(self) -> None:
        """Test short tool name with server and tool."""
        result = _short_tool_name("mcp", "read_file")
        assert result == "mcp.read_file"

    def test_short_tool_name_tool_only(self) -> None:
        """Test short tool name with tool only."""
        result = _short_tool_name(None, "read_file")
        assert result == "read_file"

    def test_short_tool_name_empty(self) -> None:
        """Test short tool name with no values."""
        result = _short_tool_name(None, None)
        assert result == "tool"

    def test_format_change_summary_with_paths(self) -> None:
        """Test formatting change summary with paths."""
        changes = [
            codex_schema.FileUpdateChange(path="src/main.py", kind="update"),
            codex_schema.FileUpdateChange(path="src/utils.py", kind="add"),
        ]
        result = _format_change_summary(changes)
        assert result == "src/main.py, src/utils.py"

    def test_format_change_summary_empty(self) -> None:
        """Test formatting change summary with no changes."""
        result = _format_change_summary([])
        assert result == "files"

    def test_summarize_todo_list(self) -> None:
        """Test summarizing todo list."""
        items = [
            codex_schema.TodoItem(text="Task 1", completed=True),
            codex_schema.TodoItem(text="Task 2", completed=False),
            codex_schema.TodoItem(text="Task 3", completed=False),
        ]
        summary = _summarize_todo_list(items)
        assert summary.done == 1
        assert summary.total == 3
        assert summary.next_text == "Task 2"

    def test_todo_title_with_next(self) -> None:
        """Test todo title with next task."""
        summary = _TodoSummary(done=1, total=3, next_text="Task 2")
        result = _todo_title(summary)
        assert result == "todo 1/3: Task 2"

    def test_todo_title_all_done(self) -> None:
        """Test todo title when all done."""
        summary = _TodoSummary(done=3, total=3, next_text=None)
        result = _todo_title(summary)
        assert result == "todo 3/3: done"


class TestCodexEventTranslation:
    """Tests for Codex event translation."""

    def test_translate_thread_started(self) -> None:
        """Test translating thread.started event."""
        event = codex_schema.ThreadStarted(thread_id="thread_abc123")
        factory = EventFactory(ENGINE)
        events = translate_codex_event(event, title="Codex", factory=factory)

        assert len(events) == 1
        assert events[0].resume.value == "thread_abc123"
        assert events[0].title == "Codex"

    def test_translate_item_started_command(self) -> None:
        """Test translating item.started command_execution."""
        item = codex_schema.CommandExecutionItem(
            id="cmd_1",
            command="ls -la",
            aggregated_output="",
            exit_code=None,
            status="in_progress",
        )
        event = codex_schema.ItemStarted(item=item)
        factory = EventFactory(ENGINE)
        events = translate_codex_event(event, title="Codex", factory=factory)

        assert len(events) == 1
        assert events[0].action.kind == "command"
        assert events[0].phase == "started"

    def test_translate_item_completed_command(self) -> None:
        """Test translating item.completed command_execution."""
        item = codex_schema.CommandExecutionItem(
            id="cmd_1",
            command="ls -la",
            aggregated_output="file1\nfile2",
            exit_code=0,
            status="completed",
        )
        event = codex_schema.ItemCompleted(item=item)
        factory = EventFactory(ENGINE)
        events = translate_codex_event(event, title="Codex", factory=factory)

        assert len(events) == 1
        assert events[0].action.kind == "command"
        assert events[0].phase == "completed"
        assert events[0].ok is True

    def test_translate_item_completed_file_change(self) -> None:
        """Test translating item.completed file_change."""
        item = codex_schema.FileChangeItem(
            id="fc_1",
            changes=[codex_schema.FileUpdateChange(path="src/main.py", kind="update")],
            status="completed",
        )
        event = codex_schema.ItemCompleted(item=item)
        factory = EventFactory(ENGINE)
        events = translate_codex_event(event, title="Codex", factory=factory)

        assert len(events) == 1
        assert events[0].action.kind == "file_change"
        assert events[0].ok is True


class TestCodexRunner:
    """Tests for CodexRunner class."""

    def test_backend_properties(self) -> None:
        """Test backend is correctly configured."""
        assert isinstance(BACKEND, EngineBackend)
        assert BACKEND.id == "codex"
        assert BACKEND.install_cmd == "npm install -g @openai/codex"

    def test_runner_engine(self) -> None:
        """Test runner engine ID."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        assert runner.engine == ENGINE

    def test_runner_format_resume(self) -> None:
        """Test resume token formatting."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        token = ResumeToken(engine=ENGINE, value="thread_abc123")
        result = runner.format_resume(token)
        assert result == "`codex resume thread_abc123`"

    def test_runner_extract_resume(self) -> None:
        """Test resume token extraction."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        text = "`codex resume thread_abc123`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.engine == ENGINE
        assert token.value == "thread_abc123"

    def test_runner_extract_resume_none(self) -> None:
        """Test resume token extraction returns None for no match."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        token = runner.extract_resume("No resume token here")
        assert token is None

    def test_runner_build_args_new_session(self) -> None:
        """Test building args for new session."""
        runner = CodexRunner(
            codex_cmd="codex", extra_args=["-c", "notify=[]"], title="Codex"
        )
        state = CodexRunState(factory=EventFactory(ENGINE))
        args = runner.build_args("hello", None, state=state)
        assert args == ["-c", "notify=[]", "exec", "--json", "-"]

    def test_runner_build_args_resume(self) -> None:
        """Test building args for resume session."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        state = CodexRunState(factory=EventFactory(ENGINE))
        token = ResumeToken(engine=ENGINE, value="thread_abc123")
        args = runner.build_args("hello", token, state=state)
        assert args == ["exec", "--json", "resume", "thread_abc123", "-"]

    def test_runner_new_state(self) -> None:
        """Test creating new state."""
        runner = CodexRunner(codex_cmd="codex", extra_args=[], title="Codex")
        state = runner.new_state("hello", None)
        assert isinstance(state, CodexRunState)
        assert state.factory.engine == ENGINE


class TestCodexBuildRunner:
    """Tests for build_runner function."""

    def test_build_runner_default_config(self) -> None:
        """Test building runner with default config."""
        runner = build_runner({}, Path("/test/workspace.toml"))
        assert isinstance(runner, CodexRunner)
        assert runner.extra_args == ["-c", "notify=[]"]

    def test_build_runner_with_profile(self) -> None:
        """Test building runner with profile."""
        runner = build_runner({"profile": "myprofile"}, Path("/test/workspace.toml"))
        assert isinstance(runner, CodexRunner)
        assert "--profile" in runner.extra_args
        assert "myprofile" in runner.extra_args
        assert runner.session_title == "myprofile"

    def test_build_runner_with_extra_args(self) -> None:
        """Test building runner with custom extra_args."""
        runner = build_runner(
            {"extra_args": ["--verbose"]}, Path("/test/workspace.toml")
        )
        assert isinstance(runner, CodexRunner)
        assert runner.extra_args == ["--verbose"]

    def test_build_runner_invalid_extra_args(self) -> None:
        """Test building runner with invalid extra_args."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"extra_args": "not a list"}, Path("/test/workspace.toml"))
        assert "extra_args" in str(exc_info.value)

    def test_build_runner_invalid_profile(self) -> None:
        """Test building runner with invalid profile."""
        with pytest.raises(ConfigError) as exc_info:
            build_runner({"profile": 123}, Path("/test/workspace.toml"))
        assert "profile" in str(exc_info.value)
