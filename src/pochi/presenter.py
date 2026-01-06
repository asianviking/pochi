"""Presenter protocol for rendering progress state.

This module defines the Presenter protocol that converts ProgressState
into RenderedMessage outputs, aligned with takopi's presenter architecture.
"""

from __future__ import annotations

from typing import Protocol

from .progress import ProgressState
from .transport import RenderedMessage


class Presenter(Protocol):
    """Protocol for rendering progress state into messages.

    Implementations handle format-specific rendering (Telegram, Discord, CLI, etc.).
    """

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        """Render a progress update message.

        Args:
            state: The current progress state snapshot
            elapsed_s: Seconds elapsed since run started
            label: Status label (e.g., "working", "starting", "cancelled")

        Returns:
            RenderedMessage ready for delivery
        """
        ...

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        """Render a final result message.

        Args:
            state: The final progress state snapshot
            elapsed_s: Total seconds elapsed
            status: Final status (e.g., "done", "error", "cancelled")
            answer: The agent's final answer text

        Returns:
            RenderedMessage ready for delivery
        """
        ...
