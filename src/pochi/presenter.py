"""Presenter protocol for rendering progress and final messages."""

from __future__ import annotations

from typing import Protocol

from .progress import ProgressState
from .transport import RenderedMessage


class Presenter(Protocol):
    """Protocol for rendering agent progress to display."""

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        """Render progress during agent execution."""
        ...

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        """Render final result after agent completion."""
        ...
