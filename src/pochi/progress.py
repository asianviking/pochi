"""Progress tracking for runner execution.

This module provides stateful progress tracking that converts runner events
into immutable progress snapshots, aligned with takopi's progress architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import Action, ActionEvent, ResumeToken, StartedEvent, PochiEvent


@dataclass(frozen=True, slots=True)
class ActionState:
    """Immutable state of a single action."""

    action: Action
    phase: str
    ok: bool | None
    display_phase: str
    completed: bool
    first_seen: int
    last_update: int


@dataclass(frozen=True, slots=True)
class ProgressState:
    """Immutable snapshot of progress state."""

    engine: str
    action_count: int
    actions: tuple[ActionState, ...]
    resume: ResumeToken | None
    resume_line: str | None


class ProgressTracker:
    """Stateful tracker that reduces events into progress snapshots.

    This class maintains mutable state internally but produces immutable
    ProgressState snapshots via the snapshot() method.
    """

    def __init__(self, *, engine: str) -> None:
        self.engine = engine
        self.resume: ResumeToken | None = None
        self.action_count = 0
        self._actions: dict[str, ActionState] = {}
        self._seq = 0

    def note_event(self, event: PochiEvent) -> bool:
        """Process an event and update internal state.

        Args:
            event: A Pochi event to process

        Returns:
            True if the event caused a state change, False otherwise
        """
        match event:
            case StartedEvent(resume=resume):
                self.resume = resume
                return True
            case ActionEvent(action=action, phase=phase, ok=ok):
                if action.kind == "turn":
                    return False
                action_id = str(action.id or "")
                if not action_id:
                    return False
                completed = phase == "completed"
                existing = self._actions.get(action_id)
                has_open = existing is not None and not existing.completed
                is_update = phase == "updated" or (phase == "started" and has_open)
                display_phase = "updated" if is_update and not completed else phase

                self._seq += 1
                seq = self._seq

                if existing is None:
                    self.action_count += 1
                    first_seen = seq
                else:
                    first_seen = existing.first_seen
                self._actions[action_id] = ActionState(
                    action=action,
                    phase=phase,
                    ok=ok,
                    display_phase=display_phase,
                    completed=completed,
                    first_seen=first_seen,
                    last_update=seq,
                )
                return True
            case _:
                return False

    def set_resume(self, resume: ResumeToken | None) -> None:
        """Set or update the resume token."""
        if resume is not None:
            self.resume = resume

    def snapshot(
        self,
        *,
        resume_formatter: Callable[[ResumeToken], str] | None = None,
    ) -> ProgressState:
        """Create an immutable snapshot of current progress state.

        Args:
            resume_formatter: Optional function to format the resume token

        Returns:
            Immutable ProgressState snapshot
        """
        resume_line: str | None = None
        if self.resume is not None and resume_formatter is not None:
            resume_line = resume_formatter(self.resume)
        actions = tuple(
            sorted(self._actions.values(), key=lambda item: item.first_seen)
        )
        return ProgressState(
            engine=self.engine,
            action_count=self.action_count,
            actions=actions,
            resume=self.resume,
            resume_line=resume_line,
        )


def sync_resume_token(
    tracker: ProgressTracker, resume: ResumeToken | None
) -> ResumeToken | None:
    """Sync resume token between tracker and an external source.

    Args:
        tracker: The progress tracker to sync
        resume: Resume token from external source (e.g., completed event)

    Returns:
        The resolved resume token (external takes precedence if set)
    """
    resume = resume or tracker.resume
    tracker.set_resume(resume)
    return resume
