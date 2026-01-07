"""Telegram-specific presenter implementation."""

from __future__ import annotations

from ..markdown import MarkdownFormatter, MarkdownParts
from ..progress import ProgressState
from ..transport import RenderedMessage
from .render import prepare_telegram


class TelegramPresenter:
    """Presenter that formats messages for Telegram."""

    def __init__(
        self,
        formatter: MarkdownFormatter | None = None,
    ) -> None:
        self.formatter = formatter or MarkdownFormatter()

    def _prepare(self, parts: MarkdownParts) -> RenderedMessage:
        """Convert markdown parts to a Telegram-ready message."""
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        """Render progress for Telegram."""
        parts = self.formatter.format_progress(state, elapsed_s=elapsed_s, label=label)
        return self._prepare(parts)

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        """Render final result for Telegram."""
        parts = self.formatter.format_final(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        return self._prepare(parts)
