"""Discord workspace bridge for message handling.

This module provides the Discord-specific bridge that handles:
- Auto-creating threads for new conversations
- Mapping threads to Claude sessions
- Processing messages through the runner
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import anyio

from ..bridge import (
    RunOutcome,
    _format_error,
    _is_cancel_command,
    _strip_engine_command,
    _strip_resume_lines,
    sync_resume_token,
    PROGRESS_EDIT_EVERY_S,
)
from ..chat import ChatUpdate, Destination, MessageRef
from ..discord import DiscordProvider, truncate_for_thread_name
from ..logging import bind_run_context, clear_context, get_logger
from ..model import ResumeToken
from ..render import ExecProgressRenderer, prepare_discord
from ..router import AutoRouter, RunnerUnavailableError
from ..runner import Runner
from .config import (
    WorkspaceConfig,
    save_workspace_config,
    update_folder_discord_channel_id,
)
from .discord_commands import register_commands
from .discord_router import DiscordRouteResult, DiscordWorkspaceRouter
from .orchestrator import prepend_orchestrator_context

logger = get_logger(__name__)


@dataclass(frozen=True)
class DiscordBridgeConfig:
    """Configuration for the Discord workspace bridge."""

    provider: DiscordProvider
    router: AutoRouter
    workspace: WorkspaceConfig
    discord_router: DiscordWorkspaceRouter
    startup_msg: str
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S


@dataclass
class DiscordRunningTask:
    """Tracks a running task in Discord."""

    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)
    resume: ResumeToken | None = None


class DiscordProgressEdits:
    """Handles progress message updates for Discord."""

    def __init__(
        self,
        provider: DiscordProvider,
        dest: Destination,
        progress_ref: MessageRef | None,
        renderer: ExecProgressRenderer,
        started_at: float,
        *,
        progress_edit_every: float = PROGRESS_EDIT_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._provider = provider
        self._dest = dest
        self._progress_ref = progress_ref
        self.renderer = renderer
        self._started_at = started_at
        self._progress_edit_every = progress_edit_every
        self._clock = clock
        self._sleep = sleep
        self._last_edit_at = started_at
        self._last_rendered: str | None = None
        self._cond = anyio.Condition()
        self._should_stop = False

    def notify(self) -> None:
        """Notify that there's an update to render."""
        pass  # We'll poll for updates

    async def run(self) -> None:
        """Run the progress update loop."""
        while not self._should_stop:
            await self._sleep(self._progress_edit_every)
            if self._should_stop:
                break
            await self._maybe_edit()

    async def _maybe_edit(self) -> None:
        """Maybe edit the progress message."""
        if self._progress_ref is None:
            return

        elapsed = self._clock() - self._started_at
        parts = self.renderer.render_progress_parts(elapsed)
        rendered, _ = prepare_discord(parts)

        if rendered == self._last_rendered:
            return

        success = await self._provider.edit_message(
            self._progress_ref,
            rendered,
            wait=False,
        )
        if success:
            self._last_rendered = rendered
            self._last_edit_at = self._clock()

    def stop(self) -> None:
        """Stop the progress update loop."""
        self._should_stop = True


async def handle_discord_message(
    cfg: DiscordBridgeConfig,
    *,
    runner: Runner,
    update: ChatUpdate,
    route: DiscordRouteResult,
    resume_token: ResumeToken | None,
    running_tasks: dict[int, DiscordRunningTask] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> None:
    """Handle a message in Discord.

    Args:
        cfg: Bridge configuration
        runner: The runner to use
        update: The chat update
        route: Routing result
        resume_token: Optional resume token
        running_tasks: Dict of running tasks by thread ID
        clock: Time function
        sleep: Sleep function
    """
    text = update.text
    message_id = update.message_id
    channel_id = update.channel_id
    thread_id = update.thread_id

    # Determine working directory
    cwd: Path | None = None
    if route.folder is not None:
        cwd = route.folder.absolute_path(cfg.workspace.root)
    elif route.is_general:
        cwd = cfg.workspace.root

    # If not in a thread, create one
    if not route.is_thread:
        thread_name = truncate_for_thread_name(text)
        new_thread_id = await cfg.provider.create_thread(
            channel_id, message_id, thread_name
        )
        if new_thread_id is None:
            logger.error(
                "discord.create_thread_failed",
                channel_id=channel_id,
                message_id=message_id,
            )
            return
        thread_id = new_thread_id
        logger.info(
            "discord.thread_created",
            thread_id=thread_id,
            name=thread_name,
        )

    # Build destination for replies
    dest = Destination(
        channel_id=channel_id,
        thread_id=thread_id,
        reply_to=message_id if route.is_thread else None,
    )

    logger.info(
        "discord.handle.incoming",
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_id,
        resume=resume_token.value if resume_token else None,
        text=text[:100],
        cwd=str(cwd) if cwd else None,
    )

    # Change to working directory
    original_cwd: str | None = None
    if cwd is not None:
        original_cwd = os.getcwd()
        try:
            os.chdir(cwd)
        except Exception as e:
            logger.error("discord.cwd_failed", cwd=str(cwd), error=str(e))
            await cfg.provider.send_message(dest, f"Failed to change to directory: {e}")
            return

    try:
        started_at = clock()
        is_resume_line = runner.is_resume_line
        runner_text = _strip_resume_lines(text, is_resume_line=is_resume_line)

        progress_renderer = ExecProgressRenderer(
            max_actions=5, resume_formatter=runner.format_resume, engine=runner.engine
        )

        # Send initial progress message
        initial_parts = progress_renderer.render_progress_parts(0.0, label="starting")
        initial_rendered, _ = prepare_discord(initial_parts)
        progress_ref = await cfg.provider.send_message(
            dest, initial_rendered, disable_notification=True
        )

        edits = DiscordProgressEdits(
            provider=cfg.provider,
            dest=dest,
            progress_ref=progress_ref,
            renderer=progress_renderer,
            started_at=started_at,
            progress_edit_every=cfg.progress_edit_every,
            clock=clock,
            sleep=sleep,
        )

        running_task: DiscordRunningTask | None = None
        if running_tasks is not None and thread_id is not None:
            running_task = DiscordRunningTask()
            running_tasks[thread_id] = running_task

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

        # Convert to bridge-compatible ProgressEdits for run_runner_with_cancel
        # For now, run the runner directly
        async with anyio.create_task_group() as tg:
            if progress_ref is not None:
                tg.start_soon(run_edits)

            try:
                async for event in runner.run(runner_text, resume_token):
                    from ..events import StartedEvent, ActionEvent, CompletedEvent

                    if isinstance(event, StartedEvent):
                        outcome.resume = event.resume
                        progress_renderer.set_resume_token(event.resume)
                        if running_task is not None:
                            running_task.resume = event.resume
                        # Store thread session
                        if thread_id is not None:
                            cfg.provider.set_thread_session(thread_id, event.resume)
                    elif isinstance(event, ActionEvent):
                        progress_renderer.add_action(event)
                    elif isinstance(event, CompletedEvent):
                        outcome.completed = event
                        outcome.resume = event.resume or outcome.resume

            except Exception as exc:
                error = exc
                logger.exception(
                    "discord.runner_failed",
                    error=str(exc),
                )
            finally:
                if running_task is not None and running_tasks is not None:
                    running_task.done.set()
                    if thread_id is not None:
                        running_tasks.pop(thread_id, None)
                edits.stop()
                edits_scope.cancel()

        elapsed = clock() - started_at

        # Handle error
        if error is not None:
            sync_resume_token(progress_renderer, outcome.resume)
            err_body = _format_error(error)
            final_parts = progress_renderer.render_final_parts(
                elapsed, err_body, status="error"
            )
            final_rendered, _ = prepare_discord(final_parts)
            if progress_ref is not None:
                await cfg.provider.edit_message(progress_ref, final_rendered)
            else:
                await cfg.provider.send_message(dest, final_rendered)
            return

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
            "error"
            if run_ok is False
            else ("done" if final_answer.strip() else "error")
        )
        resume_token = completed.resume or outcome.resume
        logger.info(
            "discord.runner.completed",
            ok=run_ok,
            error=run_error,
            answer_len=len(final_answer or ""),
            elapsed_s=round(elapsed, 2),
            resume=resume_token.value if resume_token else None,
        )

        sync_resume_token(progress_renderer, resume_token)
        final_parts = progress_renderer.render_final_parts(
            elapsed, final_answer, status=status
        )
        final_rendered, _ = prepare_discord(final_parts)

        if progress_ref is not None:
            await cfg.provider.edit_message(progress_ref, final_rendered)
        else:
            await cfg.provider.send_message(dest, final_rendered)

    finally:
        if original_cwd is not None:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass


async def run_discord_loop(cfg: DiscordBridgeConfig) -> None:
    """Main loop for Discord workspace mode.

    Args:
        cfg: Bridge configuration
    """
    running_tasks: dict[int, DiscordRunningTask] = {}

    try:
        # Start the Discord client
        await cfg.provider.start()

        # Register slash commands
        register_commands(cfg.provider, cfg.workspace)

        # Sync commands to guild
        await cfg.provider.sync_commands()

        # Process any pending channels
        pending = cfg.workspace.get_pending_channels()
        for folder in pending:
            channel = await cfg.provider.create_channel(
                folder.name,
                topic=folder.description,
            )
            if channel is not None:
                update_folder_discord_channel_id(cfg.workspace, folder.name, channel.id)
                logger.info(
                    "discord.channel_created",
                    folder=folder.name,
                    channel_id=channel.id,
                )

        # Send startup message to general channel if configured
        # (Skip for now - general channel handling TBD)

        async with anyio.create_task_group() as tg:

            async def run_job(
                update: ChatUpdate,
                route: DiscordRouteResult,
                resume_token: ResumeToken | None,
                engine_override: str | None = None,
            ) -> None:
                try:
                    try:
                        entry = (
                            cfg.router.entry_for_engine(engine_override)
                            if resume_token is None
                            else cfg.router.entry_for(resume_token)
                        )
                    except RunnerUnavailableError as exc:
                        dest = Destination(
                            channel_id=update.channel_id,
                            thread_id=update.thread_id,
                        )
                        await cfg.provider.send_message(dest, f"Error: {exc}")
                        return

                    if not entry.available:
                        reason = entry.issue or "engine unavailable"
                        dest = Destination(
                            channel_id=update.channel_id,
                            thread_id=update.thread_id,
                        )
                        await cfg.provider.send_message(dest, f"Error: {reason}")
                        return

                    bind_run_context(
                        chat_id=update.channel_id,
                        user_msg_id=update.message_id,
                        engine=entry.runner.engine,
                        resume=resume_token.value if resume_token else None,
                    )

                    await handle_discord_message(
                        cfg,
                        runner=entry.runner,
                        update=update,
                        route=route,
                        resume_token=resume_token,
                        running_tasks=running_tasks,
                    )
                except Exception as exc:
                    logger.exception(
                        "discord.job_failed",
                        error=str(exc),
                    )
                finally:
                    clear_context()

            async for update in cfg.provider.get_updates():
                if update.update_type != "message":
                    continue

                text = update.text
                user_id = update.user_id

                # Check authorization
                if cfg.workspace.discord is not None:
                    # Bootstrap: first user becomes admin
                    if cfg.workspace.discord.admin_user is None:
                        cfg.workspace.discord.admin_user = user_id
                        save_workspace_config(cfg.workspace)
                        logger.info(
                            "discord.admin_bootstrap",
                            user_id=user_id,
                        )

                    if not cfg.workspace.discord.is_authorized(user_id):
                        logger.debug(
                            "discord.unauthorized",
                            user_id=user_id,
                        )
                        continue

                # Handle /cancel
                if _is_cancel_command(text):
                    if update.thread_id is not None:
                        running_task = running_tasks.get(update.thread_id)
                        if running_task is not None:
                            running_task.cancel_requested.set()
                            dest = Destination(
                                channel_id=update.channel_id,
                                thread_id=update.thread_id,
                            )
                            await cfg.provider.send_message(
                                dest, "Cancellation requested."
                            )
                    continue

                # Route the message
                route = cfg.discord_router.route(update)

                # Strip engine commands
                text, engine_override = _strip_engine_command(
                    text, engine_ids=cfg.router.engine_ids
                )
                update = ChatUpdate(
                    platform=update.platform,
                    update_type=update.update_type,
                    channel_id=update.channel_id,
                    thread_id=update.thread_id,
                    message_id=update.message_id,
                    text=text,
                    user_id=update.user_id,
                    reply_to_message_id=update.reply_to_message_id,
                    reply_to_text=update.reply_to_text,
                    raw=update.raw,
                )

                # Get resume token from thread session
                resume_token: ResumeToken | None = None
                if update.thread_id is not None:
                    session = cfg.provider.get_thread_session(update.thread_id)
                    if session is not None:
                        resume_token = session.resume_token

                # Inject orchestrator context for general channel
                if route.is_general and resume_token is None:
                    text = prepend_orchestrator_context(cfg.workspace, text)
                    update = ChatUpdate(
                        platform=update.platform,
                        update_type=update.update_type,
                        channel_id=update.channel_id,
                        thread_id=update.thread_id,
                        message_id=update.message_id,
                        text=text,
                        user_id=update.user_id,
                        reply_to_message_id=update.reply_to_message_id,
                        reply_to_text=update.reply_to_text,
                        raw=update.raw,
                    )

                # Start the job
                tg.start_soon(run_job, update, route, resume_token, engine_override)

    finally:
        await cfg.provider.close()
