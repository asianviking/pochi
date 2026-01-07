"""Progress tracking for agent execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .model import Action, ActionEvent, PochiEvent, ResumeToken, StartedEvent


@dataclass(frozen=True, slots=True)
class ActionState:
    """Snapshot of an action's state."""

    action: Action
    phase: str
    ok: bool | None
    display_phase: str
    completed: bool
    first_seen: int
    last_update: int


@dataclass(frozen=True, slots=True)
class ProgressState:
    """Immutable snapshot of progress for rendering."""

    engine: str
    action_count: int
    actions: tuple[ActionState, ...]
    resume: ResumeToken | None
    resume_line: str | None


class ProgressTracker:
    """Mutable tracker that reduces events into progress snapshots."""

    def __init__(self, engine: str, *, max_actions: int = 5) -> None:
        self.engine = engine
        self.max_actions = max(0, int(max_actions))
        self._resume: ResumeToken | None = None
        self._action_count = 0
        self._actions: dict[str, ActionState] = {}
        self._seq = 0

    def note_event(self, event: PochiEvent) -> bool:
        """Process an event, returns True if progress changed."""
        match event:
            case StartedEvent(resume=resume):
                self._resume = resume
                return True
            case ActionEvent(action=action, phase=phase, ok=ok):
                if action.kind == "turn":
                    return False
                action_id = str(action.id or "")
                if not action_id:
                    return False
                self._seq += 1
                existing = self._actions.get(action_id)
                completed = phase == "completed"
                is_update = existing is not None and not existing.completed
                display_phase = "updated" if is_update and not completed else phase

                if existing is None:
                    self._action_count += 1
                    first_seen = self._seq
                else:
                    first_seen = existing.first_seen

                self._actions[action_id] = ActionState(
                    action=action,
                    phase=phase,
                    ok=ok,
                    display_phase=display_phase,
                    completed=completed,
                    first_seen=first_seen,
                    last_update=self._seq,
                )
                return True
            case _:
                return False

    def set_resume(self, token: ResumeToken | None) -> None:
        """Update the resume token."""
        if token is not None:
            self._resume = token

    @property
    def resume(self) -> ResumeToken | None:
        """Current resume token."""
        return self._resume

    def snapshot(
        self, *, resume_formatter: Callable[[ResumeToken], str] | None = None
    ) -> ProgressState:
        """Create an immutable snapshot of current progress."""
        # Get recent actions, sorted by first_seen descending (newest first)
        sorted_actions = sorted(
            self._actions.values(),
            key=lambda a: a.first_seen,
            reverse=True,
        )
        # Take most recent, then reverse to show oldest first
        recent = tuple(reversed(sorted_actions[: self.max_actions]))

        resume_line: str | None = None
        if self._resume is not None and resume_formatter is not None:
            resume_line = resume_formatter(self._resume)

        return ProgressState(
            engine=self.engine,
            action_count=self._action_count,
            actions=recent,
            resume=self._resume,
            resume_line=resume_line,
        )
