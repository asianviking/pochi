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
    _drain_backlog,
    _is_cancel_command,
    _set_command_menu,
    _strip_engine_command,
    PROGRESS_EDIT_EVERY_S,
)
from ..logging import bind_run_context, clear_context, get_logger
from ..model import ResumeToken
from ..router import AutoRouter, RunnerUnavailableError
from ..runner import Runner
from ..runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTasks,
    handle_message,
)
from ..telegram import BotClient, TelegramPresenter, TelegramTransport
from ..transport import MessageRef
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
    running_tasks: RunningTasks | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Handle a message in a workspace topic.

    Uses the new Transport/Presenter architecture for platform-agnostic
    message handling.
    """
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
        # Create Transport and Presenter for this topic
        transport = TelegramTransport(cfg.bot, message_thread_id=message_thread_id)
        presenter = TelegramPresenter()

        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=cfg.final_notify,
        )

        incoming = IncomingMessage(
            channel_id=chat_id,
            message_id=user_msg_id,
            text=text,
        )

        await handle_message(
            exec_cfg,
            runner=runner,
            incoming=incoming,
            resume_token=resume_token,
            strip_resume_line=cfg.router.is_resume_line,
            running_tasks=running_tasks,
            on_thread_known=on_thread_known,
            clock=clock,
        )

    finally:
        # Restore original working directory
        if original_cwd is not None:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass


async def run_workspace_loop(
    cfg: WorkspaceBridgeConfig,
    poller: Callable[
        [WorkspaceBridgeConfig], AsyncIterator[dict[str, Any]]
    ] = poll_workspace_updates,
) -> None:
    """Main loop for workspace mode."""
    running_tasks: RunningTasks = {}
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
                            progress_ref = MessageRef(
                                channel_id=chat_id,
                                message_id=int(progress_id),
                            )
                            running_task = running_tasks.get(progress_ref)
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
                    reply_ref = MessageRef(channel_id=chat_id, message_id=int(reply_id))
                    running_task = running_tasks.get(reply_ref)
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
