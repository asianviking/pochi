"""Tests for pochi.events module."""

from __future__ import annotations

import pytest

from pochi.events import EventFactory
from pochi.model import EngineId, ResumeToken


@pytest.fixture
def engine_id() -> EngineId:
    """Create an engine ID for testing."""
    return EngineId("test-engine")


@pytest.fixture
def resume_token(engine_id: EngineId) -> ResumeToken:
    """Create a resume token for testing."""
    return ResumeToken(engine=engine_id, value="session-123")


class TestEventFactory:
    """Tests for EventFactory class."""

    def test_create_factory(self, engine_id: EngineId) -> None:
        """Test creating an EventFactory."""
        factory = EventFactory(engine_id)
        assert factory.engine == engine_id
        assert factory.resume is None

    def test_started_sets_resume(
        self, engine_id: EngineId, resume_token: ResumeToken
    ) -> None:
        """Test that started() sets the resume token."""
        factory = EventFactory(engine_id)
        event = factory.started(resume_token, title="Test Run")
        assert factory.resume == resume_token
        assert event.engine == engine_id
        assert event.resume == resume_token
        assert event.title == "Test Run"

    def test_started_with_meta(
        self, engine_id: EngineId, resume_token: ResumeToken
    ) -> None:
        """Test started() with metadata."""
        factory = EventFactory(engine_id)
        event = factory.started(resume_token, meta={"model": "claude-opus"})
        assert event.meta == {"model": "claude-opus"}

    def test_started_raises_for_wrong_engine(self, engine_id: EngineId) -> None:
        """Test that started() raises for wrong engine in token."""
        factory = EventFactory(engine_id)
        wrong_token = ResumeToken(engine=EngineId("other-engine"), value="session-123")
        with pytest.raises(RuntimeError, match="resume token is for engine"):
            factory.started(wrong_token)

    def test_started_raises_for_token_mismatch(
        self, engine_id: EngineId, resume_token: ResumeToken
    ) -> None:
        """Test that started() raises for token value mismatch."""
        factory = EventFactory(engine_id)
        factory.started(resume_token)
        different_token = ResumeToken(engine=engine_id, value="different-session")
        with pytest.raises(RuntimeError, match="resume token mismatch"):
            factory.started(different_token)

    def test_action(self, engine_id: EngineId) -> None:
        """Test creating an action event."""
        factory = EventFactory(engine_id)
        event = factory.action(
            phase="started",
            action_id="action-1",
            kind="tool",
            title="Running command",
            detail={"cmd": "ls"},
        )
        assert event.engine == engine_id
        assert event.action.id == "action-1"
        assert event.action.kind == "tool"
        assert event.action.title == "Running command"
        assert event.action.detail == {"cmd": "ls"}
        assert event.phase == "started"

    def test_action_started(self, engine_id: EngineId) -> None:
        """Test action_started helper."""
        factory = EventFactory(engine_id)
        event = factory.action_started(
            action_id="action-2",
            kind="file_change",
            title="Reading file",
        )
        assert event.phase == "started"
        assert event.action.id == "action-2"

    def test_action_updated(self, engine_id: EngineId) -> None:
        """Test action_updated helper."""
        factory = EventFactory(engine_id)
        event = factory.action_updated(
            action_id="action-3",
            kind="file_change",
            title="Writing file",
        )
        assert event.phase == "updated"
        assert event.action.id == "action-3"

    def test_action_completed(self, engine_id: EngineId) -> None:
        """Test action_completed helper."""
        factory = EventFactory(engine_id)
        event = factory.action_completed(
            action_id="action-4",
            kind="command",
            title="Run command",
            ok=True,
            message="Success",
            level="info",
        )
        assert event.phase == "completed"
        assert event.ok is True
        assert event.message == "Success"
        assert event.level == "info"

    def test_action_completed_failure(self, engine_id: EngineId) -> None:
        """Test action_completed with failure."""
        factory = EventFactory(engine_id)
        event = factory.action_completed(
            action_id="action-5",
            kind="command",
            title="Run command",
            ok=False,
            message="Command failed",
            level="error",
        )
        assert event.ok is False
        assert event.level == "error"

    def test_completed(self, engine_id: EngineId, resume_token: ResumeToken) -> None:
        """Test creating a completed event."""
        factory = EventFactory(engine_id)
        factory.started(resume_token)
        event = factory.completed(
            ok=True,
            answer="Done!",
            usage={"tokens": 100},
        )
        assert event.engine == engine_id
        assert event.ok is True
        assert event.answer == "Done!"
        assert event.resume == resume_token
        assert event.usage == {"tokens": 100}

    def test_completed_with_explicit_resume(
        self, engine_id: EngineId, resume_token: ResumeToken
    ) -> None:
        """Test completed with explicit resume token."""
        factory = EventFactory(engine_id)
        other_token = ResumeToken(engine=engine_id, value="other-session")
        event = factory.completed(
            ok=True,
            answer="Done!",
            resume=other_token,
        )
        assert event.resume == other_token

    def test_completed_with_error(self, engine_id: EngineId) -> None:
        """Test completed with error."""
        factory = EventFactory(engine_id)
        event = factory.completed(
            ok=False,
            answer="",
            error="Something went wrong",
        )
        assert event.ok is False
        assert event.error == "Something went wrong"

    def test_completed_ok_helper(
        self, engine_id: EngineId, resume_token: ResumeToken
    ) -> None:
        """Test completed_ok helper."""
        factory = EventFactory(engine_id)
        factory.started(resume_token)
        event = factory.completed_ok(answer="Success!")
        assert event.ok is True
        assert event.answer == "Success!"

    def test_completed_error_helper(self, engine_id: EngineId) -> None:
        """Test completed_error helper."""
        factory = EventFactory(engine_id)
        event = factory.completed_error(error="Failure!")
        assert event.ok is False
        assert event.error == "Failure!"
        assert event.answer == ""

    def test_completed_error_with_answer(self, engine_id: EngineId) -> None:
        """Test completed_error with partial answer."""
        factory = EventFactory(engine_id)
        event = factory.completed_error(error="Failure!", answer="Partial result")
        assert event.ok is False
        assert event.error == "Failure!"
        assert event.answer == "Partial result"
