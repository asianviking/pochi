"""Tests for pochi.runner module."""

from __future__ import annotations

import re
from typing import Any

import anyio
import pytest

from pochi.model import ActionEvent, EngineId, ResumeToken, StartedEvent
from pochi.runner import (
    BaseRunner,
    JsonlRunState,
    JsonlSubprocessRunner,
    ResumeTokenMixin,
    SessionLockMixin,
)


class MockResumeTokenRunner(ResumeTokenMixin):
    """A concrete class for testing ResumeTokenMixin."""

    def __init__(self, engine: str = "test") -> None:
        self.engine = EngineId(engine)
        engine_name = re.escape(engine)
        self.resume_re = re.compile(
            rf"(?im)^\s*`?{engine_name}\s+resume\s+(?P<token>[^`\s]+)`?\s*$"
        )


class MockSessionLockRunner(SessionLockMixin):
    """A concrete class for testing SessionLockMixin."""

    def __init__(self, engine: str = "test") -> None:
        self.engine = EngineId(engine)


class TestResumeTokenMixin:
    """Tests for ResumeTokenMixin class."""

    def test_format_resume(self) -> None:
        """Test format_resume generates correct format."""
        runner = MockResumeTokenRunner("claude")
        token = ResumeToken(engine=EngineId("claude"), value="session-123")
        result = runner.format_resume(token)
        assert result == "`claude resume session-123`"

    def test_format_resume_wrong_engine(self) -> None:
        """Test format_resume raises for wrong engine."""
        runner = MockResumeTokenRunner("claude")
        token = ResumeToken(engine=EngineId("codex"), value="session-123")
        with pytest.raises(RuntimeError, match="resume token is for engine"):
            runner.format_resume(token)

    def test_is_resume_line_true(self) -> None:
        """Test is_resume_line returns True for valid resume lines."""
        runner = MockResumeTokenRunner("claude")
        assert runner.is_resume_line("`claude resume abc123`") is True
        assert runner.is_resume_line("claude resume abc123") is True
        assert runner.is_resume_line("  `claude resume abc123`") is True

    def test_is_resume_line_false(self) -> None:
        """Test is_resume_line returns False for non-resume lines."""
        runner = MockResumeTokenRunner("claude")
        assert runner.is_resume_line("hello world") is False
        assert runner.is_resume_line("/claude something") is False
        assert runner.is_resume_line("codex resume abc123") is False

    def test_extract_resume_from_text(self) -> None:
        """Test extract_resume extracts token from text."""
        runner = MockResumeTokenRunner("claude")
        text = "Here is the resume command:\n`claude resume abc123`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.engine == EngineId("claude")
        assert token.value == "abc123"

    def test_extract_resume_last_match(self) -> None:
        """Test extract_resume returns last match."""
        runner = MockResumeTokenRunner("claude")
        text = "`claude resume first`\n`claude resume second`"
        token = runner.extract_resume(text)
        assert token is not None
        assert token.value == "second"

    def test_extract_resume_none_for_empty(self) -> None:
        """Test extract_resume returns None for empty text."""
        runner = MockResumeTokenRunner("claude")
        assert runner.extract_resume(None) is None
        assert runner.extract_resume("") is None

    def test_extract_resume_none_for_no_match(self) -> None:
        """Test extract_resume returns None when no match."""
        runner = MockResumeTokenRunner("claude")
        assert runner.extract_resume("just some text") is None


class TestSessionLockMixin:
    """Tests for SessionLockMixin class."""

    def test_lock_for_creates_lock(self) -> None:
        """Test lock_for creates a new lock."""
        runner = MockSessionLockRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        lock = runner.lock_for(token)
        assert isinstance(lock, anyio.Lock)

    def test_lock_for_returns_same_lock(self) -> None:
        """Test lock_for returns same lock for same token."""
        runner = MockSessionLockRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        lock1 = runner.lock_for(token)
        lock2 = runner.lock_for(token)
        assert lock1 is lock2

    def test_lock_for_different_tokens(self) -> None:
        """Test lock_for returns different locks for different tokens."""
        runner = MockSessionLockRunner("test")
        token1 = ResumeToken(engine=EngineId("test"), value="session-1")
        token2 = ResumeToken(engine=EngineId("test"), value="session-2")
        lock1 = runner.lock_for(token1)
        lock2 = runner.lock_for(token2)
        assert lock1 is not lock2

    def test_lock_for_initializes_session_locks(self) -> None:
        """Test lock_for initializes session_locks dict."""
        runner = MockSessionLockRunner("test")
        assert runner.session_locks is None
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        runner.lock_for(token)
        assert runner.session_locks is not None

    @pytest.mark.anyio
    async def test_run_with_resume_lock_no_resume(self) -> None:
        """Test run_with_resume_lock without resume token."""
        runner = MockSessionLockRunner("test")
        events_yielded = []

        async def run_fn(prompt, resume):
            events_yielded.append(("called", prompt, resume))
            yield "event1"
            yield "event2"

        results = []
        async for event in runner.run_with_resume_lock("test prompt", None, run_fn):
            results.append(event)

        assert results == ["event1", "event2"]
        assert events_yielded == [("called", "test prompt", None)]

    @pytest.mark.anyio
    async def test_run_with_resume_lock_with_resume(self) -> None:
        """Test run_with_resume_lock with resume token."""
        runner = MockSessionLockRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        events_yielded = []

        async def run_fn(prompt, resume):
            events_yielded.append(("called", prompt, resume))
            yield "event"

        results = []
        async for event in runner.run_with_resume_lock("test prompt", token, run_fn):
            results.append(event)

        assert results == ["event"]
        assert events_yielded == [("called", "test prompt", token)]

    @pytest.mark.anyio
    async def test_run_with_resume_lock_wrong_engine(self) -> None:
        """Test run_with_resume_lock raises for wrong engine."""
        runner = MockSessionLockRunner("test")
        wrong_token = ResumeToken(engine=EngineId("other"), value="session-1")

        async def run_fn(prompt, resume):
            yield "event"

        with pytest.raises(RuntimeError, match="resume token is for engine"):
            async for _ in runner.run_with_resume_lock("prompt", wrong_token, run_fn):
                pass

    @pytest.mark.anyio
    async def test_run_with_resume_lock_serializes(self) -> None:
        """Test that run_with_resume_lock serializes calls with same token."""
        runner = MockSessionLockRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        call_order = []

        async def run_fn(prompt, resume):
            call_order.append(f"start-{prompt}")
            await anyio.sleep(0.02)
            call_order.append(f"end-{prompt}")
            yield f"event-{prompt}"

        async def run_task(prompt):
            async for _ in runner.run_with_resume_lock(prompt, token, run_fn):
                pass

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_task, "first")
            await anyio.sleep(0.01)  # Let first task start
            tg.start_soon(run_task, "second")

        # Due to locking, second should wait for first to complete
        assert call_order == ["start-first", "end-first", "start-second", "end-second"]


class TestJsonlRunState:
    """Tests for JsonlRunState dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        state = JsonlRunState()
        assert state.note_seq == 0

    def test_custom_note_seq(self) -> None:
        """Test setting custom note_seq."""
        state = JsonlRunState(note_seq=5)
        assert state.note_seq == 5


class MockJsonlRunner(JsonlSubprocessRunner):
    """Mock implementation for testing JsonlSubprocessRunner."""

    logger: Any = None

    def __init__(self, engine: str = "test") -> None:
        self.engine = EngineId(engine)
        self.session_locks = None

    def command(self) -> str:
        return "test-cmd"

    def build_args(self, prompt, resume, *, state):  # type: ignore[override]
        return ["--arg1"]

    def translate(self, data, *, state, resume, found_session):  # type: ignore[override]
        return []


class TestJsonlSubprocessRunner:
    """Tests for JsonlSubprocessRunner class."""

    def test_tag_default(self) -> None:
        """Test tag returns engine name by default."""
        runner = MockJsonlRunner("myengine")
        assert runner.tag() == "myengine"

    def test_new_state(self) -> None:
        """Test new_state returns JsonlRunState."""
        runner = MockJsonlRunner()
        state = runner.new_state("prompt", None)
        assert isinstance(state, JsonlRunState)
        assert state.note_seq == 0

    def test_start_run_returns_none(self) -> None:
        """Test start_run returns None."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        result = runner.start_run("prompt", None, state=state)
        assert result is None

    def test_stdin_payload(self) -> None:
        """Test stdin_payload encodes prompt."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        payload = runner.stdin_payload("hello world", None, state=state)
        assert payload == b"hello world"

    def test_env_returns_none(self) -> None:
        """Test env returns None by default."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        result = runner.env(state=state)
        assert result is None

    def test_pipes_error_message(self) -> None:
        """Test pipes_error_message format."""
        runner = MockJsonlRunner("claude")
        msg = runner.pipes_error_message()
        assert "claude" in msg
        assert "pipes" in msg.lower()

    def test_next_note_id_increments(self) -> None:
        """Test next_note_id increments sequence."""
        runner = MockJsonlRunner("engine")
        state = JsonlRunState()
        id1 = runner.next_note_id(state)
        id2 = runner.next_note_id(state)
        assert id1 == "engine.note.1"
        assert id2 == "engine.note.2"
        assert state.note_seq == 2

    def test_note_event_creates_action_event(self) -> None:
        """Test note_event creates proper ActionEvent."""
        runner = MockJsonlRunner("test")
        state = JsonlRunState()
        event = runner.note_event("Test message", state=state)

        assert isinstance(event, ActionEvent)
        assert event.engine == EngineId("test")
        assert event.action.kind == "warning"
        assert event.action.title == "Test message"
        assert event.phase == "completed"
        assert event.ok is False
        assert event.level == "warning"

    def test_note_event_ok_changes_level(self) -> None:
        """Test note_event with ok=True changes level to info."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        event = runner.note_event("Success", state=state, ok=True)

        assert isinstance(event, ActionEvent)
        assert event.ok is True
        assert event.level == "info"

    def test_note_event_with_detail(self) -> None:
        """Test note_event includes detail."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        detail = {"key": "value", "num": 42}
        event = runner.note_event("Message", state=state, detail=detail)

        assert isinstance(event, ActionEvent)
        assert event.action.detail == detail

    def test_invalid_json_events(self) -> None:
        """Test invalid_json_events returns note event."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        events = runner.invalid_json_events(raw="raw", line="bad json", state=state)

        assert len(events) == 1
        assert isinstance(events[0], ActionEvent)
        assert events[0].message is not None and "invalid JSON" in events[0].message

    def test_decode_jsonl_valid(self) -> None:
        """Test decode_jsonl parses valid JSON."""
        runner = MockJsonlRunner()
        result = runner.decode_jsonl(line=b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_decode_jsonl_invalid(self) -> None:
        """Test decode_jsonl returns None for invalid JSON."""
        runner = MockJsonlRunner()
        result = runner.decode_jsonl(line=b"not json")
        assert result is None

    def test_decode_error_events(self) -> None:
        """Test decode_error_events returns note event."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        events = runner.decode_error_events(
            raw="raw", line="line", error=ValueError("test"), state=state
        )

        assert len(events) == 1
        assert isinstance(events[0], ActionEvent)
        assert "line" in events[0].action.detail

    def test_translate_error_events(self) -> None:
        """Test translate_error_events handles errors."""
        runner = MockJsonlRunner()
        state = JsonlRunState()
        data = {"type": "test_type", "item": {"type": "item_type"}}
        events = runner.translate_error_events(
            data=data, error=ValueError("oops"), state=state
        )

        assert len(events) == 1
        assert isinstance(events[0], ActionEvent)
        assert events[0].action.detail["type"] == "test_type"
        assert events[0].action.detail["item_type"] == "item_type"

    def test_process_error_events(self) -> None:
        """Test process_error_events generates error events."""
        from pochi.model import CompletedEvent

        runner = MockJsonlRunner()
        state = JsonlRunState()
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        events = runner.process_error_events(
            1, resume=token, found_session=None, state=state
        )

        assert len(events) == 2
        assert isinstance(events[0], ActionEvent)
        assert events[0].message is not None and "rc=1" in events[0].message

        assert isinstance(events[1], CompletedEvent)
        assert events[1].ok is False
        assert events[1].resume == token

    def test_stream_end_events(self) -> None:
        """Test stream_end_events generates completion event."""
        from pochi.model import CompletedEvent

        runner = MockJsonlRunner()
        state = JsonlRunState()
        events = runner.stream_end_events(resume=None, found_session=None, state=state)

        assert len(events) == 1

        assert isinstance(events[0], CompletedEvent)
        assert events[0].ok is False
        assert events[0].error is not None and "without a result" in events[0].error

    def test_handle_started_event_first_seen(self) -> None:
        """Test handle_started_event for first session."""
        runner = MockJsonlRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        started = StartedEvent(engine=EngineId("test"), resume=token, title="Running")

        found, emit = runner.handle_started_event(
            started, expected_session=None, found_session=None
        )

        assert found == token
        assert emit is True

    def test_handle_started_event_matches_expected(self) -> None:
        """Test handle_started_event when matching expected."""
        runner = MockJsonlRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        started = StartedEvent(engine=EngineId("test"), resume=token, title="Running")

        found, emit = runner.handle_started_event(
            started, expected_session=token, found_session=None
        )

        assert found == token
        assert emit is True

    def test_handle_started_event_duplicate(self) -> None:
        """Test handle_started_event for duplicate session."""
        runner = MockJsonlRunner("test")
        token = ResumeToken(engine=EngineId("test"), value="session-1")
        started = StartedEvent(engine=EngineId("test"), resume=token, title="Running")

        found, emit = runner.handle_started_event(
            started, expected_session=None, found_session=token
        )

        assert found == token
        assert emit is False

    def test_handle_started_event_wrong_engine(self) -> None:
        """Test handle_started_event raises for wrong engine."""
        runner = MockJsonlRunner("test")
        token = ResumeToken(engine=EngineId("other"), value="session-1")
        started = StartedEvent(engine=EngineId("other"), resume=token, title="Running")

        with pytest.raises(RuntimeError, match="emitted session token for engine"):
            runner.handle_started_event(
                started, expected_session=None, found_session=None
            )

    def test_handle_started_event_mismatched_expected(self) -> None:
        """Test handle_started_event raises for mismatched expected session."""
        runner = MockJsonlRunner("test")
        expected = ResumeToken(engine=EngineId("test"), value="expected-1")
        actual = ResumeToken(engine=EngineId("test"), value="actual-1")
        started = StartedEvent(engine=EngineId("test"), resume=actual, title="Running")

        with pytest.raises(RuntimeError, match="but expected"):
            runner.handle_started_event(
                started, expected_session=expected, found_session=None
            )

    def test_handle_started_event_mismatched_found(self) -> None:
        """Test handle_started_event raises when session differs from found."""
        runner = MockJsonlRunner("test")
        found = ResumeToken(engine=EngineId("test"), value="found-1")
        actual = ResumeToken(engine=EngineId("test"), value="actual-1")
        started = StartedEvent(engine=EngineId("test"), resume=actual, title="Running")

        with pytest.raises(RuntimeError, match="but expected"):
            runner.handle_started_event(
                started, expected_session=None, found_session=found
            )

    def test_get_logger_uses_instance_logger(self) -> None:
        """Test get_logger uses instance logger if available."""
        import logging

        runner = MockJsonlRunner()
        runner.logger = logging.getLogger("custom.logger")
        assert runner.get_logger() == runner.logger

    def test_next_note_id_without_note_seq(self) -> None:
        """Test next_note_id raises when state has no note_seq."""

        class BadState:
            pass

        runner = MockJsonlRunner()
        state = BadState()

        with pytest.raises(RuntimeError, match="note_seq"):
            runner.next_note_id(state)


class TestBaseRunner:
    """Tests for BaseRunner class."""

    def test_run_returns_run_locked(self) -> None:
        """Test run delegates to run_locked."""

        class TestRunner(BaseRunner):
            def __init__(self):
                self.engine = EngineId("test")

            async def run_impl(self, prompt, resume):
                yield StartedEvent(
                    engine=self.engine,
                    resume=ResumeToken(engine=self.engine, value="s1"),
                    title="Test",
                )

        runner = TestRunner()
        result = runner.run("prompt", None)
        # Should return an async iterator
        assert hasattr(result, "__anext__")

    @pytest.mark.anyio
    async def test_run_locked_without_resume(self) -> None:
        """Test run_locked emits events properly."""

        class TestRunner(BaseRunner):
            def __init__(self):
                self.engine = EngineId("test")
                self.session_locks = None

            async def run_impl(self, prompt, resume):
                yield StartedEvent(
                    engine=self.engine,
                    resume=ResumeToken(engine=self.engine, value="s1"),
                    title="Test",
                )

        runner = TestRunner()
        events = []
        async for evt in runner.run_locked("test prompt", None):
            events.append(evt)

        assert len(events) == 1
        assert isinstance(events[0], StartedEvent)
