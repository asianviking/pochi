"""Markdown formatting and presenter for agent progress."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .model import Action, ActionEvent, PochiEvent, StartedEvent
from .progress import ActionState, ProgressState
from .transport import RenderedMessage
from .utils.paths import relativize_path

STATUS = {"running": "▸", "update": "↻", "done": "✓", "fail": "✗"}
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_FILE_CHANGES_INLINE = 3


@dataclass(frozen=True, slots=True)
class MarkdownParts:
    """Structured markdown with header, body, and footer."""

    header: str
    body: str | None = None
    footer: str | None = None


def assemble_markdown_parts(parts: MarkdownParts) -> str:
    """Assemble markdown parts into a single string."""
    return "\n\n".join(
        chunk for chunk in (parts.header, parts.body, parts.footer) if chunk
    )


def format_changed_file_path(path: str, *, base_dir: Path | None = None) -> str:
    """Format a file path for display."""
    return f"`{relativize_path(path, base_dir=base_dir)}`"


def format_elapsed(elapsed_s: float) -> str:
    """Format elapsed time as human-readable string."""
    total = max(0, int(elapsed_s))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_header(
    elapsed_s: float, item: int | None, *, label: str, engine: str
) -> str:
    """Format the progress header line."""
    elapsed = format_elapsed(elapsed_s)
    parts = [label, engine]
    parts.append(elapsed)
    if item is not None:
        parts.append(f"step {item}")
    return HEADER_SEP.join(parts)


def shorten(text: str, width: int | None) -> str:
    """Shorten text to specified width with ellipsis."""
    if width is None:
        return text
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="…")


def action_status(action: Action, *, completed: bool, ok: bool | None = None) -> str:
    """Determine status symbol for an action."""
    if not completed:
        return STATUS["running"]
    if ok is not None:
        return STATUS["done"] if ok else STATUS["fail"]
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return STATUS["fail"]
    return STATUS["done"]


def action_suffix(action: Action) -> str:
    """Generate suffix for action (e.g., exit code)."""
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return f" (exit {exit_code})"
    return ""


def format_file_change_title(action: Action, *, command_width: int | None) -> str:
    """Format title for file change actions."""
    title = str(action.title or "")
    detail = action.detail or {}

    changes = detail.get("changes")
    if isinstance(changes, list) and changes:
        rendered: list[str] = []
        for raw in changes:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path")
            if not isinstance(path, str) or not path:
                continue
            kind = raw.get("kind")
            verb = kind if isinstance(kind, str) and kind else "update"
            rendered.append(f"{verb} {format_changed_file_path(path)}")

        if rendered:
            if len(rendered) > MAX_FILE_CHANGES_INLINE:
                remaining = len(rendered) - MAX_FILE_CHANGES_INLINE
                rendered = rendered[:MAX_FILE_CHANGES_INLINE] + [f"…({remaining} more)"]
            inline = shorten(", ".join(rendered), command_width)
            return f"files: {inline}"

    return f"files: {shorten(title, command_width)}"


def format_action_title(action: Action, *, command_width: int | None) -> str:
    """Format action title for display."""
    title = str(action.title or "")
    kind = action.kind
    if kind == "command":
        title = shorten(title, command_width)
        return f"`{title}`"
    if kind == "tool":
        title = shorten(title, command_width)
        return f"tool: {title}"
    if kind == "web_search":
        title = shorten(title, command_width)
        return f"searched: {title}"
    if kind == "subagent":
        title = shorten(title, command_width)
        return f"subagent: {title}"
    if kind == "file_change":
        return format_file_change_title(action, command_width=command_width)
    if kind in {"note", "warning"}:
        return shorten(title, command_width)
    return shorten(title, command_width)


def format_action_line(
    action: Action,
    phase: str,
    ok: bool | None,
    *,
    command_width: int | None,
) -> str:
    """Format a single action line for display."""
    if phase != "completed":
        status = STATUS["update"] if phase == "updated" else STATUS["running"]
        return f"{status} {format_action_title(action, command_width=command_width)}"
    status = action_status(action, completed=True, ok=ok)
    suffix = action_suffix(action)
    return (
        f"{status} {format_action_title(action, command_width=command_width)}{suffix}"
    )


def render_event_cli(event: PochiEvent) -> list[str]:
    """Render an event for CLI output."""
    match event:
        case StartedEvent(engine=engine):
            return [str(engine)]
        case ActionEvent() as action_event:
            action = action_event.action
            if action.kind == "turn":
                return []
            return [
                format_action_line(
                    action_event.action,
                    action_event.phase,
                    action_event.ok,
                    command_width=MAX_PROGRESS_CMD_LEN,
                )
            ]
        case _:
            return []


class MarkdownFormatter:
    """Formats progress state into markdown parts."""

    def __init__(
        self,
        *,
        max_actions: int = 5,
        command_width: int | None = MAX_PROGRESS_CMD_LEN,
    ) -> None:
        self.max_actions = max(0, int(max_actions))
        self.command_width = command_width

    def format_action_line(self, action_state: ActionState) -> str:
        """Format an action state into a display line."""
        return format_action_line(
            action_state.action,
            action_state.display_phase,
            action_state.ok,
            command_width=self.command_width,
        )

    def format_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> MarkdownParts:
        """Format progress state into markdown parts."""
        step = state.action_count or None
        header = format_header(
            elapsed_s,
            step,
            label=label,
            engine=state.engine,
        )
        lines = [self.format_action_line(a) for a in state.actions]
        body = HARD_BREAK.join(lines) if lines else None
        return MarkdownParts(header=header, body=body, footer=state.resume_line)

    def format_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> MarkdownParts:
        """Format final result into markdown parts."""
        step = state.action_count or None
        header = format_header(
            elapsed_s,
            step,
            label=status,
            engine=state.engine,
        )
        answer = (answer or "").strip()
        body = answer if answer else None
        return MarkdownParts(header=header, body=body, footer=state.resume_line)


class MarkdownPresenter:
    """Presenter that outputs markdown-formatted messages."""

    def __init__(
        self,
        formatter: MarkdownFormatter | None = None,
        *,
        prepare_output: Callable[[MarkdownParts], RenderedMessage] | None = None,
    ) -> None:
        self.formatter = formatter or MarkdownFormatter()
        self._prepare_output = prepare_output or self._default_prepare

    @staticmethod
    def _default_prepare(parts: MarkdownParts) -> RenderedMessage:
        """Default output preparation: just assemble markdown."""
        return RenderedMessage(text=assemble_markdown_parts(parts))

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        """Render progress for display."""
        parts = self.formatter.format_progress(state, elapsed_s=elapsed_s, label=label)
        return self._prepare_output(parts)

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        """Render final result for display."""
        parts = self.formatter.format_final(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        return self._prepare_output(parts)
