"""Workspace-aware bridge for multi-repo Telegram topics."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio

from ..bridge import (
    BridgeConfig,
    ProgressEdits,
    RunningTask,
    RunOutcome,
    _drain_backlog,
    _format_error,
    _is_cancel_command,
    _set_command_menu,
    _strip_engine_command,
    _strip_resume_lines,
    run_runner_with_cancel,
    sync_resume_token,
    PROGRESS_EDIT_EVERY_S,
)
from ..logging import bind_run_context, clear_context, get_logger
from ..model import ResumeToken
from ..render import ExecProgressRenderer, MarkdownParts, prepare_telegram
from ..router import AutoRouter, RunnerUnavailableError
from ..runner import Runner
from ..telegram import BotClient
from .commands import handle_slash_command
from .config import (
    WorkspaceConfig,
)
from .manager import WorkspaceManager
from .orchestrator import prepend_orchestrator_context
from .ralph import RalphManager, parse_ralph_command
from .router import WorkspaceRouter, is_general_slash_command

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkspaceBridgeConfig:
    """Configuration for the workspace bridge."""

    bot: BotClient
    router: AutoRouter
    workspace: WorkspaceConfig
    workspace_router: WorkspaceRouter
    workspace_manager: WorkspaceManager
    ralph_manager: RalphManager
    final_notify: bool
    startup_msg: str
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S


def _make_topic_resume_key(topic_id: int | None, resume: str) -> str:
    """Create a namespaced resume key for a topic."""
    prefix = f"topic:{topic_id}" if topic_id else "general"
    return f"{prefix}:{resume}"


def _parse_topic_resume_key(key: str) -> tuple[int | None, str]:
    """Parse a topic-namespaced resume key."""
    if key.startswith("topic:"):
        rest = key[6:]  # Remove "topic:"
        parts = rest.split(":", 1)
        if len(parts) == 2:
            try:
                topic_id = int(parts[0])
                return topic_id, parts[1]
            except ValueError:
                pass
    elif key.startswith("general:"):
        return None, key[8:]
    # Fallback - treat as general topic
    return None, key


async def _send_startup(cfg: WorkspaceBridgeConfig) -> None:
    """Send startup message to the General topic."""
    logger.debug("startup.message", text=cfg.startup_msg)
    sent = await cfg.workspace_manager.send_to_topic(
        None,  # General topic
        cfg.startup_msg,
        parse_mode="Markdown",
    )
    if sent is not None:
        logger.info("startup.sent", chat_id=cfg.workspace.telegram_group_id)


async def poll_workspace_updates(
    cfg: WorkspaceBridgeConfig,
) -> AsyncIterator[dict[str, Any]]:
    """Poll for updates, filtering to the workspace's Telegram group."""
    offset: int | None = None

    # Use a minimal bridge config for draining backlog
    drain_cfg = BridgeConfig(
        bot=cfg.bot,
        router=cfg.router,
        chat_id=cfg.workspace.telegram_group_id,
        final_notify=cfg.final_notify,
        startup_msg=cfg.startup_msg,
    )
    offset = await _drain_backlog(drain_cfg, offset)
    await _send_startup(cfg)

    # Process any pending topics at startup
    created = await cfg.workspace_manager.process_pending_topics()
    if created:
        for folder_name, topic_id in created:
            logger.info(
                "startup.topic_created",
                folder=folder_name,
                topic_id=topic_id,
            )

    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=50, allowed_updates=["message", "callback_query"]
        )
        if updates is None:
            logger.info("loop.get_updates.failed")
            await anyio.sleep(2)
            continue
        logger.debug("loop.updates", updates=updates)

        for upd in updates:
            offset = upd["update_id"] + 1

            # Handle callback queries (inline button presses)
            callback_query = upd.get("callback_query")
            if callback_query is not None:
                yield {"_type": "callback_query", "callback_query": callback_query}
                continue

            msg = upd.get("message")
            if msg is None:
                continue
            if "text" not in msg:
                continue
            # Filter to our workspace's group
            if msg["chat"]["id"] != cfg.workspace.telegram_group_id:
                continue
            yield {"_type": "message", "message": msg}


async def handle_callback_query(
    cfg: WorkspaceBridgeConfig,
    callback_query: dict[str, Any],
) -> None:
    """Handle a callback query (inline button press)."""
    data = callback_query.get("data", "")
    query_id = callback_query["id"]

    # Handle Ralph cancel button
    if data.startswith("ralph:cancel:"):
        parts = data.split(":")
        if len(parts) == 4:
            try:
                topic_id = int(parts[2])
                loop_id = parts[3]

                loop = cfg.ralph_manager.get_active_loop(topic_id)
                if loop and loop.loop_id == loop_id:
                    cfg.ralph_manager.cancel_loop(topic_id)
                    await cfg.bot.answer_callback_query(query_id, text="Loop cancelled")
                    # Remove the button from the message
                    msg = callback_query.get("message")
                    if msg:
                        msg_id = msg.get("message_id")
                        chat_id = msg.get("chat", {}).get("id")
                        if msg_id and chat_id:
                            try:
                                await cfg.bot.edit_message_reply_markup(
                                    chat_id=chat_id,
                                    message_id=msg_id,
                                    reply_markup={"inline_keyboard": []},
                                )
                            except Exception:
                                pass  # Best effort - button removal is not critical
                else:
                    await cfg.bot.answer_callback_query(query_id, text="No active loop")
                return
            except ValueError:
                pass

    # Unknown callback - just acknowledge it
    await cfg.bot.answer_callback_query(query_id)


async def handle_workspace_message(
    cfg: WorkspaceBridgeConfig,
    *,
    runner: Runner,
    chat_id: int,
    user_msg_id: int,
    text: str,
    message_thread_id: int | None,
    resume_token: ResumeToken | None,
    cwd: Path | None,
    running_tasks: dict[int, RunningTask] | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S,
) -> None:
    """Handle a message in a workspace topic."""
    logger.info(
        "handle.incoming",
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        message_thread_id=message_thread_id,
        resume=resume_token.value if resume_token else None,
        text=text,
        cwd=str(cwd) if cwd else None,
    )

    # Change to repo directory if specified
    original_cwd: str | None = None
    if cwd is not None:
        original_cwd = os.getcwd()
        try:
            os.chdir(cwd)
            logger.debug("handle.cwd_changed", cwd=str(cwd))
        except Exception as e:
            logger.error("handle.cwd_failed", cwd=str(cwd), error=str(e))
            await cfg.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Failed to change to repo directory: {e}",
                message_thread_id=message_thread_id,
                reply_to_message_id=user_msg_id,
            )
            return

    try:
        started_at = clock()
        is_resume_line = runner.is_resume_line
        runner_text = _strip_resume_lines(text, is_resume_line=is_resume_line)

        progress_renderer = ExecProgressRenderer(
            max_actions=5, resume_formatter=runner.format_resume, engine=runner.engine
        )

        # Send initial progress message to the topic
        initial_parts = progress_renderer.render_progress_parts(0.0, label="starting")
        initial_rendered, initial_entities = prepare_telegram(initial_parts)
        progress_msg = await cfg.bot.send_message(
            chat_id=chat_id,
            text=initial_rendered,
            entities=initial_entities,
            message_thread_id=message_thread_id,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
        )

        progress_id: int | None = None
        last_edit_at = 0.0
        last_rendered: str | None = None
        if progress_msg is not None:
            progress_id = int(progress_msg["message_id"])
            last_edit_at = clock()
            last_rendered = initial_rendered

        edits = ProgressEdits(
            bot=cfg.bot,
            chat_id=chat_id,
            progress_id=progress_id,
            renderer=progress_renderer,
            started_at=started_at,
            progress_edit_every=progress_edit_every,
            clock=clock,
            sleep=sleep,
            last_edit_at=last_edit_at,
            last_rendered=last_rendered,
        )

        running_task: RunningTask | None = None
        if running_tasks is not None and progress_id is not None:
            running_task = RunningTask()
            running_tasks[progress_id] = running_task

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
            if progress_id is not None:
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
                    and progress_id is not None
                ):
                    running_task.done.set()
                    running_tasks.pop(progress_id, None)
                if not outcome.cancelled and error is None:
                    await anyio.sleep(0)
                edits_scope.cancel()

        elapsed = clock() - started_at

        # Handle error
        if error is not None:
            sync_resume_token(progress_renderer, outcome.resume)
            err_body = _format_error(error)
            final_parts = progress_renderer.render_final_parts(
                elapsed, err_body, status="error"
            )
            await _send_topic_result(
                cfg,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                user_msg_id=user_msg_id,
                progress_id=progress_id,
                parts=final_parts,
                disable_notification=True,
                edit_message_id=progress_id,
            )
            return

        # Handle cancellation
        if outcome.cancelled:
            resume = sync_resume_token(progress_renderer, outcome.resume)
            logger.info(
                "handle.cancelled",
                resume=resume.value if resume else None,
                elapsed_s=elapsed,
            )
            final_parts = progress_renderer.render_progress_parts(
                elapsed, label="`cancelled`"
            )
            await _send_topic_result(
                cfg,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                user_msg_id=user_msg_id,
                progress_id=progress_id,
                parts=final_parts,
                disable_notification=True,
                edit_message_id=progress_id,
            )
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
            "runner.completed",
            ok=run_ok,
            error=run_error,
            answer_len=len(final_answer or ""),
            elapsed_s=round(elapsed, 2),
            action_count=progress_renderer.action_count,
            resume=resume_token.value if resume_token else None,
        )

        sync_resume_token(progress_renderer, completed.resume or outcome.resume)
        final_parts = progress_renderer.render_final_parts(
            elapsed, final_answer, status=status
        )

        edit_message_id = None if cfg.final_notify else progress_id
        await _send_topic_result(
            cfg,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            user_msg_id=user_msg_id,
            progress_id=progress_id,
            parts=final_parts,
            disable_notification=False,
            edit_message_id=edit_message_id,
        )

    finally:
        # Restore original working directory
        if original_cwd is not None:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass


async def _send_topic_result(
    cfg: WorkspaceBridgeConfig,
    *,
    chat_id: int,
    message_thread_id: int | None,
    user_msg_id: int,
    progress_id: int | None,
    parts: MarkdownParts,
    disable_notification: bool,
    edit_message_id: int | None,
) -> None:
    """Send or edit a result message in a topic."""
    rendered, entities = prepare_telegram(parts)

    if edit_message_id is not None:
        edited = await cfg.bot.edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=rendered,
            entities=entities,
        )
        if edited is not None:
            return

    # Send new message
    sent = await cfg.bot.send_message(
        chat_id=chat_id,
        text=rendered,
        entities=entities,
        message_thread_id=message_thread_id,
        reply_to_message_id=user_msg_id,
        disable_notification=disable_notification,
    )

    # Delete progress message if we sent a new one
    if sent is not None and progress_id is not None and edit_message_id is None:
        await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)


async def run_workspace_loop(
    cfg: WorkspaceBridgeConfig,
    poller: Callable[
        [WorkspaceBridgeConfig], AsyncIterator[dict[str, Any]]
    ] = poll_workspace_updates,
) -> None:
    """Main loop for workspace mode."""
    running_tasks: dict[int, RunningTask] = {}
    chat_id = cfg.workspace.telegram_group_id

    try:
        # Set bot command menu
        menu_cfg = BridgeConfig(
            bot=cfg.bot,
            router=cfg.router,
            chat_id=chat_id,
            final_notify=cfg.final_notify,
            startup_msg=cfg.startup_msg,
        )
        await _set_command_menu(menu_cfg)

        async with anyio.create_task_group() as tg:

            async def run_job(
                user_msg_id: int,
                text: str,
                message_thread_id: int | None,
                resume_token: ResumeToken | None,
                cwd: Path | None,
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
                        await cfg.bot.send_message(
                            chat_id=chat_id,
                            text=f"error: {exc}",
                            message_thread_id=message_thread_id,
                            reply_to_message_id=user_msg_id,
                        )
                        return

                    if not entry.available:
                        reason = entry.issue or "engine unavailable"
                        await cfg.bot.send_message(
                            chat_id=chat_id,
                            text=f"error: {reason}",
                            message_thread_id=message_thread_id,
                            reply_to_message_id=user_msg_id,
                        )
                        return

                    bind_run_context(
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        engine=entry.runner.engine,
                        resume=resume_token.value if resume_token else None,
                    )

                    await handle_workspace_message(
                        cfg,
                        runner=entry.runner,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=text,
                        message_thread_id=message_thread_id,
                        resume_token=resume_token,
                        cwd=cwd,
                        running_tasks=running_tasks,
                        progress_edit_every=cfg.progress_edit_every,
                    )
                except Exception as exc:
                    logger.exception(
                        "handle.worker_failed",
                        error=str(exc),
                        error_type=exc.__class__.__name__,
                    )
                finally:
                    clear_context()

            async for update in poller(cfg):
                # Handle callback queries (inline button presses)
                if update.get("_type") == "callback_query":
                    await handle_callback_query(cfg, update["callback_query"])
                    continue

                # Handle regular messages
                msg = update.get("message")
                if msg is None:
                    continue

                text = msg["text"]
                user_msg_id = msg["message_id"]
                message_thread_id = msg.get("message_thread_id")

                # Handle /cancel command
                if _is_cancel_command(text):
                    # First, try to cancel a running task if replying to it
                    reply = msg.get("reply_to_message")
                    if reply:
                        progress_id = reply.get("message_id")
                        if progress_id is not None:
                            running_task = running_tasks.get(int(progress_id))
                            if running_task is not None:
                                running_task.cancel_requested.set()
                                continue

                    # In worker topics, also try to cancel any active ralph loop
                    if message_thread_id is not None:
                        if cfg.ralph_manager.cancel_loop(message_thread_id):
                            await cfg.bot.send_message(
                                chat_id=chat_id,
                                text="⚠️ Cancelling Ralph loop...",
                                message_thread_id=message_thread_id,
                                reply_to_message_id=user_msg_id,
                            )
                            continue

                    await cfg.bot.send_message(
                        chat_id=chat_id,
                        text="No active run to cancel. Reply to a progress message to cancel it.",
                        message_thread_id=message_thread_id,
                        reply_to_message_id=user_msg_id,
                    )
                    continue

                # Route the message
                route = cfg.workspace_router.route(message_thread_id, text)

                # Handle General topic slash commands (Python-handled)
                if is_general_slash_command(route):
                    tg.start_soon(
                        handle_slash_command,
                        cfg.workspace_manager,
                        route,
                        user_msg_id,
                    )
                    continue

                # Handle Ralph commands in worker topics
                if (
                    cfg.workspace_router.is_ralph_command(route)
                    and route.folder is not None
                ):
                    prompt, max_iter = parse_ralph_command(route.command_args)
                    if not prompt.strip():
                        await cfg.bot.send_message(
                            chat_id=chat_id,
                            text="Usage: /ralph <task> [--max-iterations N]",
                            message_thread_id=message_thread_id,
                            reply_to_message_id=user_msg_id,
                        )
                        continue

                    # Get runner for ralph loop
                    try:
                        entry = cfg.router.entry_for_engine(None)
                    except Exception:
                        await cfg.bot.send_message(
                            chat_id=chat_id,
                            text="error: no engine available",
                            message_thread_id=message_thread_id,
                            reply_to_message_id=user_msg_id,
                        )
                        continue

                    tg.start_soon(
                        partial(
                            cfg.ralph_manager.start_loop,
                            folder=route.folder,
                            prompt=prompt,
                            max_iterations=max_iter,
                            reply_to_message_id=user_msg_id,
                            runner=entry.runner,
                        ),
                    )
                    continue

                # Reject non-ralph messages if ralph is active in this topic
                if route.folder is not None and route.folder.topic_id is not None:
                    if cfg.ralph_manager.has_active_loop(route.folder.topic_id):
                        await cfg.bot.send_message(
                            chat_id=chat_id,
                            text="❌ A Ralph loop is running. Use /cancel to stop it first.",
                            message_thread_id=message_thread_id,
                            reply_to_message_id=user_msg_id,
                        )
                        continue

                # Handle unbound topic
                if route.is_unbound_topic:
                    await cfg.workspace_manager.send_unbound_topic_error(
                        message_thread_id,  # type: ignore
                        user_msg_id,
                    )
                    continue

                # Strip engine commands
                text, engine_override = _strip_engine_command(
                    text, engine_ids=cfg.router.engine_ids
                )

                # Determine working directory
                cwd: Path | None = None
                if route.folder is not None:
                    # Worker topic - use repo directory
                    cwd = route.folder.absolute_path(cfg.workspace.root)
                elif route.is_general:
                    # Orchestrator in General topic - use workspace root
                    cwd = cfg.workspace.root

                # Resolve resume token from reply
                r = msg.get("reply_to_message") or {}
                resume_token = cfg.router.resolve_resume(text, r.get("text"))

                # Check if replying to a running task
                reply_id = r.get("message_id")
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(int(reply_id))
                    if running_task is not None:
                        # Wait for resume token from running task
                        if running_task.resume is not None:
                            resume_token = running_task.resume
                        else:
                            await cfg.bot.send_message(
                                chat_id=chat_id,
                                text="resume token not ready yet; try replying to the final message.",
                                message_thread_id=message_thread_id,
                                reply_to_message_id=user_msg_id,
                                disable_notification=True,
                            )
                            continue

                # Inject orchestrator context for new General topic messages
                job_text = text
                if route.is_general and resume_token is None:
                    job_text = prepend_orchestrator_context(cfg.workspace, text)

                # Start the job
                tg.start_soon(
                    run_job,
                    user_msg_id,
                    job_text,
                    message_thread_id,
                    resume_token,
                    cwd,
                    engine_override,
                )

    finally:
        await cfg.bot.close()
