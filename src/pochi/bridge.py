"""Telegram bridge utilities for running runners and streaming progress."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

from .model import CompletedEvent, EngineId, PochiEvent, ResumeToken, StartedEvent
from .logging import bind_run_context, get_logger
from .render import (
    ExecProgressRenderer,
    prepare_telegram,
    render_event_cli,
)
from .router import AutoRouter
from .telegram import BotClient

if TYPE_CHECKING:
    from .runner import Runner


logger = get_logger(__name__)


def _log_runner_event(evt: PochiEvent) -> None:
    for line in render_event_cli(evt):
        logger.debug(
            "runner.event.cli",
            line=line,
            event_type=getattr(evt, "type", None),
            engine=getattr(evt, "engine", None),
        )


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _strip_engine_command(
    text: str, *, engine_ids: tuple[EngineId, ...]
) -> tuple[str, EngineId | None]:
    if not text:
        return text, None

    if not engine_ids:
        return text, None

    engine_map = {engine.lower(): engine for engine in engine_ids}
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return text, None

    line = lines[idx].lstrip()
    if not line.startswith("/"):
        return text, None

    parts = line.split(maxsplit=1)
    command = parts[0][1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    engine = engine_map.get(command.lower())
    if engine is None:
        return text, None

    remainder = parts[1] if len(parts) > 1 else ""
    if remainder:
        lines[idx] = remainder
    else:
        lines.pop(idx)
    return "\n".join(lines).strip(), engine


def _build_bot_commands(router: AutoRouter) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()

    # Add engine commands (e.g., /claude)
    for entry in router.available_entries:
        cmd = entry.engine.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"start {cmd}"})
        seen.add(cmd)

    # Add workspace commands
    workspace_commands = {
        "clone": "Clone a git repo and create a topic",
        "create": "Create a new empty repo with git init",
        "add": "Add an existing repo to the workspace",
        "list": "List all repos in the workspace",
        "remove": "Remove repo from workspace",
        "status": "Show workspace status",
        "help": "Show available commands",
        "ralph": "Run an iterative Claude loop",
    }
    for cmd, desc in workspace_commands.items():
        if cmd not in seen:
            commands.append({"command": cmd, "description": desc})
            seen.add(cmd)

    # Add cancel if not already present
    if "cancel" not in seen:
        commands.append({"command": "cancel", "description": "cancel current run"})

    return commands


async def _set_command_menu(cfg: BridgeConfig) -> None:
    commands = _build_bot_commands(cfg.router)
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:
        logger.info(
            "startup.command_menu.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return
    if not ok:
        logger.info("startup.command_menu.rejected")
        return
    logger.info(
        "startup.command_menu.updated",
        commands=[cmd["command"] for cmd in commands],
    )


def _strip_resume_lines(text: str, *, is_resume_line: Callable[[str], bool]) -> str:
    stripped_lines: list[str] = []
    for line in text.splitlines():
        if is_resume_line(line):
            continue
        stripped_lines.append(line)
    prompt = "\n".join(stripped_lines).strip()
    return prompt or "continue"


def _flatten_exception_group(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        flattened: list[BaseException] = []
        for exc in error.exceptions:
            flattened.extend(_flatten_exception_group(exc))
        return flattened
    return [error]


def _format_error(error: Exception) -> str:
    cancel_exc = anyio.get_cancelled_exc_class()
    flattened = [
        exc
        for exc in _flatten_exception_group(error)
        if not isinstance(exc, cancel_exc)
    ]
    if len(flattened) == 1:
        return str(flattened[0]) or flattened[0].__class__.__name__
    if not flattened:
        return str(error) or error.__class__.__name__
    messages = [str(exc) for exc in flattened if str(exc)]
    if not messages:
        return str(error) or error.__class__.__name__
    if len(messages) == 1:
        return messages[0]
    return "\n".join(messages)


PROGRESS_EDIT_EVERY_S = 2.0


class ProgressEdits:
    def __init__(
        self,
        *,
        bot: BotClient,
        chat_id: int,
        progress_id: int | None,
        renderer: ExecProgressRenderer,
        started_at: float,
        progress_edit_every: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        last_edit_at: float,
        last_rendered: str | None,
        reply_markup: dict | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.progress_id = progress_id
        self.renderer = renderer
        self.started_at = started_at
        self.progress_edit_every = progress_edit_every
        self.clock = clock
        self.sleep = sleep
        self.last_edit_at = last_edit_at
        self.last_rendered = last_rendered
        self.reply_markup = reply_markup
        self.event_seq = 0
        self.rendered_seq = 0
        self.signal_send, self.signal_recv = anyio.create_memory_object_stream(1)

    async def run(self) -> None:
        if self.progress_id is None:
            return
        while True:
            while self.rendered_seq == self.event_seq:
                try:
                    await self.signal_recv.receive()
                except anyio.EndOfStream:
                    return

            await self.sleep(
                max(
                    0.0,
                    self.last_edit_at + self.progress_edit_every - self.clock(),
                )
            )

            seq_at_render = self.event_seq
            now = self.clock()
            parts = self.renderer.render_progress_parts(now - self.started_at)
            rendered, entities = prepare_telegram(parts)
            if rendered != self.last_rendered:
                logger.debug(
                    "telegram.edit_message",
                    chat_id=self.chat_id,
                    message_id=self.progress_id,
                    rendered=rendered,
                )
                self.last_edit_at = now
                edited = await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.progress_id,
                    text=rendered,
                    entities=entities,
                    reply_markup=self.reply_markup,
                )
                if edited is not None:
                    self.last_rendered = rendered

            self.rendered_seq = seq_at_render

    async def on_event(self, evt: PochiEvent) -> None:
        if not self.renderer.note_event(evt):
            return
        if self.progress_id is None:
            return
        self.event_seq += 1
        try:
            self.signal_send.send_nowait(None)
        except anyio.WouldBlock:
            pass
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass


@dataclass(frozen=True)
class BridgeConfig:
    bot: BotClient
    router: AutoRouter
    chat_id: int
    final_notify: bool
    startup_msg: str
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S


@dataclass
class RunningTask:
    resume: ResumeToken | None = None
    resume_ready: anyio.Event = field(default_factory=anyio.Event)
    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)


async def _drain_backlog(cfg: BridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("startup.backlog.failed")
            return offset
        logger.debug("startup.backlog.updates", updates=updates)
        if not updates:
            if drained:
                logger.info("startup.backlog.drained", count=drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


@dataclass(slots=True)
class RunOutcome:
    cancelled: bool = False
    completed: CompletedEvent | None = None
    resume: ResumeToken | None = None


async def run_runner_with_cancel(
    runner: "Runner",
    *,
    prompt: str,
    resume_token: ResumeToken | None,
    edits: ProgressEdits,
    running_task: RunningTask | None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
) -> RunOutcome:
    outcome = RunOutcome()
    async with anyio.create_task_group() as tg:

        async def run_runner() -> None:
            try:
                async for evt in runner.run(prompt, resume_token):
                    _log_runner_event(evt)
                    if isinstance(evt, StartedEvent):
                        outcome.resume = evt.resume
                        bind_run_context(resume=evt.resume.value)
                        if running_task is not None and running_task.resume is None:
                            running_task.resume = evt.resume
                            running_task.resume_ready.set()
                            if on_thread_known is not None:
                                await on_thread_known(evt.resume, running_task.done)
                    elif isinstance(evt, CompletedEvent):
                        outcome.resume = evt.resume or outcome.resume
                        outcome.completed = evt
                    await edits.on_event(evt)
            finally:
                tg.cancel_scope.cancel()

        async def wait_cancel(task: RunningTask) -> None:
            await task.cancel_requested.wait()
            outcome.cancelled = True
            tg.cancel_scope.cancel()

        tg.start_soon(run_runner)
        if running_task is not None:
            tg.start_soon(wait_cancel, running_task)

    return outcome


def sync_resume_token(
    renderer: ExecProgressRenderer, resume: ResumeToken | None
) -> ResumeToken | None:
    resume = resume or renderer.resume_token
    renderer.resume_token = resume
    return resume
