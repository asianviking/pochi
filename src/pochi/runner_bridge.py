"""Transport-agnostic runner bridge for message handling."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

from .logging import bind_run_context, get_logger
from .markdown import render_event_cli
from .model import CompletedEvent, PochiEvent, ResumeToken, StartedEvent
from .presenter import Presenter
from .progress import ProgressTracker
from .transport import ChannelId, MessageRef, RenderedMessage, SendOptions, Transport

if TYPE_CHECKING:
    from .runner import Runner

logger = get_logger(__name__)

PROGRESS_EDIT_EVERY_S = 2.0


def _log_runner_event(evt: PochiEvent) -> None:
    """Log a runner event for CLI output."""
    for line in render_event_cli(evt):
        logger.debug(
            "runner.event.cli",
            line=line,
            event_type=getattr(evt, "type", None),
            engine=getattr(evt, "engine", None),
        )


def _strip_resume_lines(text: str, *, is_resume_line: Callable[[str], bool]) -> str:
    """Strip resume token lines from prompt text."""
    stripped_lines: list[str] = []
    for line in text.splitlines():
        if is_resume_line(line):
            continue
        stripped_lines.append(line)
    prompt = "\n".join(stripped_lines).strip()
    return prompt or "continue"


def _flatten_exception_group(error: BaseException) -> list[BaseException]:
    """Flatten an exception group into a list of exceptions."""
    if isinstance(error, BaseExceptionGroup):
        flattened: list[BaseException] = []
        for exc in error.exceptions:
            flattened.extend(_flatten_exception_group(exc))
        return flattened
    return [error]


def _format_error(error: Exception) -> str:
    """Format an error for display, filtering cancellation exceptions."""
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


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """An incoming message to handle."""

    channel_id: ChannelId
    message_id: int
    text: str
    reply_to_message_id: int | None = None
    reply_to_text: str | None = None


@dataclass(frozen=True, slots=True)
class ExecBridgeConfig:
    """Configuration for the execution bridge."""

    transport: Transport
    presenter: Presenter
    final_notify: bool = True
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S


@dataclass
class RunningTask:
    """Tracks an active runner execution."""

    resume: ResumeToken | None = None
    resume_ready: anyio.Event = field(default_factory=anyio.Event)
    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)


@dataclass(slots=True)
class ProgressMessageState:
    """State of a progress message."""

    ref: MessageRef | None
    last_rendered: RenderedMessage | None
    last_edit_at: float


class ProgressEdits:
    """Manages throttled progress message updates."""

    def __init__(
        self,
        *,
        transport: Transport,
        presenter: Presenter,
        tracker: ProgressTracker,
        progress_ref: MessageRef | None,
        started_at: float,
        progress_edit_every: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        last_edit_at: float,
        last_rendered: RenderedMessage | None,
        reply_markup: dict | None = None,
    ) -> None:
        self.transport = transport
        self.presenter = presenter
        self.tracker = tracker
        self.progress_ref = progress_ref
        self.started_at = started_at
        self.progress_edit_every = progress_edit_every
        self.clock = clock
        self.sleep = sleep
        self.last_edit_at = last_edit_at
        self.last_rendered = last_rendered
        self.reply_markup = reply_markup
        self.event_seq = 0
        self.rendered_seq = 0
        self.signal_send, self.signal_recv = anyio.create_memory_object_stream[None](1)

    async def run(self) -> None:
        """Run the progress update loop."""
        if self.progress_ref is None:
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
            elapsed = now - self.started_at
            state = self.tracker.snapshot()
            rendered = self.presenter.render_progress(state, elapsed_s=elapsed)

            if self.reply_markup:
                rendered = RenderedMessage(
                    text=rendered.text,
                    extra={**rendered.extra, "reply_markup": self.reply_markup},
                )

            if rendered.text != (
                self.last_rendered.text if self.last_rendered else None
            ):
                logger.debug(
                    "progress.edit",
                    channel_id=self.progress_ref.channel_id,
                    message_id=self.progress_ref.message_id,
                )
                self.last_edit_at = now
                edited = await self.transport.edit(
                    self.progress_ref,
                    rendered,
                    wait=False,
                )
                if edited is not None:
                    self.last_rendered = rendered

            self.rendered_seq = seq_at_render

    async def on_event(self, evt: PochiEvent) -> None:
        """Handle a runner event."""
        if not self.tracker.note_event(evt):
            return
        if self.progress_ref is None:
            return
        self.event_seq += 1
        try:
            self.signal_send.send_nowait(None)
        except anyio.WouldBlock:
            pass
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass


@dataclass(slots=True)
class RunOutcome:
    """Outcome of a runner execution."""

    cancelled: bool = False
    completed: CompletedEvent | None = None
    resume: ResumeToken | None = None


async def send_or_edit_message(
    transport: Transport,
    channel_id: ChannelId,
    message: RenderedMessage,
    *,
    reply_to: MessageRef | None = None,
    replace: MessageRef | None = None,
    notify: bool = True,
) -> MessageRef | None:
    """Send a new message or edit an existing one."""
    if replace is not None:
        edited = await transport.edit(replace, message)
        if edited is not None:
            return edited
    # Fall through to send if edit failed or no replace specified
    return await transport.send(
        channel_id,
        message,
        SendOptions(reply_to=reply_to, notify=notify),
    )


async def send_initial_progress(
    transport: Transport,
    presenter: Presenter,
    tracker: ProgressTracker,
    channel_id: ChannelId,
    *,
    reply_to: MessageRef | None = None,
) -> MessageRef | None:
    """Send the initial progress message."""
    state = tracker.snapshot()
    rendered = presenter.render_progress(state, elapsed_s=0.0, label="starting")
    return await transport.send(
        channel_id,
        rendered,
        SendOptions(reply_to=reply_to, notify=False),
    )


async def run_runner_with_cancel(
    runner: "Runner",
    *,
    prompt: str,
    resume_token: ResumeToken | None,
    edits: ProgressEdits,
    running_task: RunningTask | None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
) -> RunOutcome:
    """Run a runner with cancellation support."""
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
    tracker: ProgressTracker, resume: ResumeToken | None
) -> ResumeToken | None:
    """Sync resume token between tracker and outcome."""
    resume = resume or tracker.resume
    tracker.set_resume(resume)
    return resume


async def send_result_message(
    cfg: ExecBridgeConfig,
    channel_id: ChannelId,
    tracker: ProgressTracker,
    *,
    elapsed_s: float,
    status: str,
    answer: str,
    reply_to: MessageRef | None = None,
    progress_ref: MessageRef | None = None,
) -> MessageRef | None:
    """Send the final result message."""
    state = tracker.snapshot()
    rendered = cfg.presenter.render_final(
        state, elapsed_s=elapsed_s, status=status, answer=answer
    )

    if cfg.final_notify:
        # Send as new message, delete progress message
        result = await cfg.transport.send(
            channel_id,
            rendered,
            SendOptions(reply_to=reply_to, notify=True),
        )
        if result is not None and progress_ref is not None:
            await cfg.transport.delete(progress_ref)
        return result
    else:
        # Edit the progress message in place
        return await send_or_edit_message(
            cfg.transport,
            channel_id,
            rendered,
            replace=progress_ref,
            notify=False,
        )


async def handle_message(
    cfg: ExecBridgeConfig,
    runner: "Runner",
    message: IncomingMessage,
    *,
    resume_token: ResumeToken | None = None,
    running_tasks: dict[int, RunningTask] | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    reply_markup: dict | None = None,
) -> RunOutcome:
    """Handle an incoming message by running the agent.

    This is the main entry point for transport-agnostic message handling.
    """
    logger.info(
        "handle.incoming",
        channel_id=message.channel_id,
        message_id=message.message_id,
        resume=resume_token.value if resume_token else None,
        text=message.text[:100] + "..." if len(message.text) > 100 else message.text,
    )

    started_at = clock()
    is_resume_line = runner.is_resume_line
    runner_text = _strip_resume_lines(message.text, is_resume_line=is_resume_line)

    tracker = ProgressTracker(
        runner.engine,
        max_actions=5,
    )

    # Send initial progress message
    reply_to = MessageRef(
        channel_id=message.channel_id,
        message_id=message.message_id,
    )
    progress_ref = await send_initial_progress(
        cfg.transport,
        cfg.presenter,
        tracker,
        message.channel_id,
        reply_to=reply_to,
    )

    last_edit_at = clock() if progress_ref else 0.0
    initial_rendered = cfg.presenter.render_progress(
        tracker.snapshot(), elapsed_s=0.0, label="starting"
    )

    edits = ProgressEdits(
        transport=cfg.transport,
        presenter=cfg.presenter,
        tracker=tracker,
        progress_ref=progress_ref,
        started_at=started_at,
        progress_edit_every=cfg.progress_edit_every,
        clock=clock,
        sleep=sleep,
        last_edit_at=last_edit_at,
        last_rendered=initial_rendered if progress_ref else None,
        reply_markup=reply_markup,
    )

    running_task: RunningTask | None = None
    if running_tasks is not None and progress_ref is not None:
        running_task = RunningTask()
        running_tasks[int(progress_ref.message_id)] = running_task

    cancel_exc_type = anyio.get_cancelled_exc_class()
    edits_scope = anyio.CancelScope()

    async def run_edits() -> None:
        try:
            with edits_scope:
                await edits.run()
        except cancel_exc_type:
            return

    outcome = RunOutcome()
    error: Exception | None = None

    async with anyio.create_task_group() as tg:
        if progress_ref is not None:
            tg.start_soon(run_edits)

        try:
            outcome = await run_runner_with_cancel(
                runner,
                prompt=runner_text,
                resume_token=resume_token,
                edits=edits,
                running_task=running_task,
                on_thread_known=on_thread_known,
            )
        except Exception as exc:
            error = exc
            logger.exception(
                "handle.runner_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
        finally:
            if (
                running_task is not None
                and running_tasks is not None
                and progress_ref is not None
            ):
                running_task.done.set()
                running_tasks.pop(int(progress_ref.message_id), None)
            if not outcome.cancelled and error is None:
                await anyio.sleep(0)
            edits_scope.cancel()

    elapsed = clock() - started_at

    # Handle error
    if error is not None:
        sync_resume_token(tracker, outcome.resume)
        err_body = _format_error(error)
        await send_result_message(
            cfg,
            message.channel_id,
            tracker,
            elapsed_s=elapsed,
            status="error",
            answer=err_body,
            reply_to=reply_to,
            progress_ref=progress_ref,
        )
        return outcome

    # Handle cancellation
    if outcome.cancelled:
        sync_resume_token(tracker, outcome.resume)
        logger.info(
            "handle.cancelled",
            resume=tracker.resume.value if tracker.resume else None,
            elapsed_s=elapsed,
        )
        # Edit progress message to show cancelled
        state = tracker.snapshot()
        rendered = cfg.presenter.render_progress(
            state, elapsed_s=elapsed, label="`cancelled`"
        )
        if progress_ref:
            await cfg.transport.edit(progress_ref, rendered)
        return outcome

    # Handle completion
    if outcome.completed is None:
        raise RuntimeError("runner finished without a completed event")

    completed = outcome.completed
    run_ok = completed.ok
    run_error = completed.error

    final_answer = completed.answer
    if run_ok is False and run_error:
        if final_answer.strip():
            final_answer = f"{final_answer}\n\n{run_error}"
        else:
            final_answer = str(run_error)

    status = (
        "error" if run_ok is False else ("done" if final_answer.strip() else "error")
    )

    sync_resume_token(tracker, completed.resume or outcome.resume)
    logger.info(
        "runner.completed",
        ok=run_ok,
        error=run_error,
        answer_len=len(final_answer or ""),
        elapsed_s=round(elapsed, 2),
        action_count=tracker._action_count,
        resume=tracker.resume.value if tracker.resume else None,
    )

    await send_result_message(
        cfg,
        message.channel_id,
        tracker,
        elapsed_s=elapsed,
        status=status,
        answer=final_answer,
        reply_to=reply_to,
        progress_ref=progress_ref,
    )

    return outcome
