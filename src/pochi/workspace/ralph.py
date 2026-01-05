"""Ralph Wiggum loop implementation for worker topics.

Ralph Wiggum is an iterative prompting technique where Claude reviews
its own work and continues until satisfied or max iterations reached.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from ..logging import get_logger
from ..model import ResumeToken
from ..render import ExecProgressRenderer, prepare_telegram
from ..router import AutoRouter

if TYPE_CHECKING:
    from .config import WorkspaceConfig, FolderConfig
    from ..telegram import BotClient
    from ..runner import Runner

logger = get_logger(__name__)

# Prompt template for Ralph iterations
RALPH_ITERATION_PROMPT = """Review your previous work and continue the task.

If the task is complete and working correctly, respond with exactly:
RALPH_COMPLETE: <brief summary of what was accomplished>

If more work is needed, continue working on the task.

Original task: {original_prompt}

Current iteration: {iteration} of {max_iterations}
"""

RALPH_COMPLETE_PATTERN = re.compile(r"^RALPH_COMPLETE:\s*(.+)$", re.MULTILINE)

# Maximum retries for removing the cancel button
BUTTON_REMOVE_MAX_RETRIES = 3


def _cancel_keyboard(topic_id: int, loop_id: str) -> dict:
    """Build an inline keyboard with a Cancel button."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "❌ Cancel",
                    "callback_data": f"ralph:cancel:{topic_id}:{loop_id}",
                }
            ]
        ]
    }


def _empty_keyboard() -> dict:
    """Build an empty inline keyboard to remove buttons."""
    return {"inline_keyboard": []}


@dataclass
class RalphLoop:
    """Represents an active Ralph Wiggum loop."""

    folder_name: str
    topic_id: int
    original_prompt: str
    max_iterations: int
    loop_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    current_iteration: int = 0
    resume_token: ResumeToken | None = None
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    completed: bool = False
    summary: str | None = None
    button_message_id: int | None = None
    button_message_text: str | None = None
    button_message_entities: list[dict] | None = None


class RalphManager:
    """Manages Ralph Wiggum loops for worker topics."""

    def __init__(
        self,
        workspace_config: "WorkspaceConfig",
        bot: "BotClient",
        router: AutoRouter,
    ) -> None:
        self.workspace = workspace_config
        self.bot = bot
        self.router = router
        self._active_loops: dict[int, RalphLoop] = {}  # topic_id -> loop

    def get_active_loop(self, topic_id: int) -> RalphLoop | None:
        """Get the active loop for a topic, if any."""
        return self._active_loops.get(topic_id)

    def has_active_loop(self, topic_id: int) -> bool:
        """Check if a topic has an active Ralph loop."""
        return topic_id in self._active_loops

    async def _remove_button(self, loop: RalphLoop) -> None:
        """Remove the Cancel button from the current button message with retry."""
        if loop.button_message_id is None or loop.button_message_text is None:
            return

        chat_id = self.workspace.telegram_group_id
        for attempt in range(BUTTON_REMOVE_MAX_RETRIES):
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=loop.button_message_id,
                    text=loop.button_message_text,
                    entities=loop.button_message_entities,
                    reply_markup=_empty_keyboard(),
                )
                loop.button_message_id = None
                loop.button_message_text = None
                loop.button_message_entities = None
                return
            except Exception as e:
                if attempt < BUTTON_REMOVE_MAX_RETRIES - 1:
                    await anyio.sleep(1 * (attempt + 1))  # backoff
                else:
                    logger.warning(
                        "ralph.button_remove_failed",
                        message_id=loop.button_message_id,
                        error=str(e),
                    )

    async def start_loop(
        self,
        *,
        folder: "FolderConfig",
        prompt: str,
        max_iterations: int | None,
        reply_to_message_id: int,
        runner: "Runner",
    ) -> None:
        """Start a new Ralph Wiggum loop for a folder topic."""
        if folder.topic_id is None:
            logger.error("ralph.start.no_topic", folder=folder.name)
            return

        topic_id = folder.topic_id

        # Check if there's already an active loop
        if self.has_active_loop(topic_id):
            await self.bot.send_message(
                chat_id=self.workspace.telegram_group_id,
                text="❌ A Ralph loop is already running in this topic.\n"
                "Use /cancel to stop it first.",
                message_thread_id=topic_id,
                reply_to_message_id=reply_to_message_id,
            )
            return

        # Use default max_iterations from config if not specified
        if max_iterations is None:
            max_iterations = self.workspace.ralph.default_max_iterations

        # Create the loop
        loop = RalphLoop(
            folder_name=folder.name,
            topic_id=topic_id,
            original_prompt=prompt,
            max_iterations=max_iterations,
        )
        self._active_loops[topic_id] = loop

        logger.info(
            "ralph.loop.started",
            folder=folder.name,
            topic_id=topic_id,
            max_iterations=max_iterations,
        )

        # Send start message
        await self.bot.send_message(
            chat_id=self.workspace.telegram_group_id,
            text=f"🔄 Starting Ralph loop ({max_iterations} max iterations)\n\n"
            f"Task: {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
            message_thread_id=topic_id,
            reply_to_message_id=reply_to_message_id,
        )

        # Run the loop
        try:
            await self._run_loop(loop, folder, runner)
        finally:
            self._active_loops.pop(topic_id, None)

    async def _run_loop(
        self,
        loop: RalphLoop,
        folder: "FolderConfig",
        runner: "Runner",
    ) -> None:
        """Run the Ralph loop iterations."""
        chat_id = self.workspace.telegram_group_id
        cwd = folder.absolute_path(self.workspace.root)

        try:
            while loop.current_iteration < loop.max_iterations:
                loop.current_iteration += 1

                # Remove button from previous iteration's message
                await self._remove_button(loop)

                # Check for cancellation
                if loop.cancel_requested.is_set():
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ Ralph loop cancelled at iteration {loop.current_iteration}",
                        message_thread_id=loop.topic_id,
                    )
                    return

                # Build prompt for this iteration
                if loop.current_iteration == 1:
                    prompt = loop.original_prompt
                else:
                    prompt = RALPH_ITERATION_PROMPT.format(
                        original_prompt=loop.original_prompt,
                        iteration=loop.current_iteration,
                        max_iterations=loop.max_iterations,
                    )

                # Send iteration start message
                iteration_msg = await self.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔄 Iteration {loop.current_iteration}/{loop.max_iterations}...",
                    message_thread_id=loop.topic_id,
                )
                if iteration_msg is None:
                    logger.error(
                        "ralph.iteration.send_failed", iteration=loop.current_iteration
                    )
                    return

                iteration_msg_id = int(iteration_msg["message_id"])

                # Run the Claude instance for this iteration
                answer = await self._run_iteration(
                    loop=loop,
                    prompt=prompt,
                    runner=runner,
                    cwd=cwd,
                    iteration_msg_id=iteration_msg_id,
                )

                if answer is None:
                    # Error or cancellation
                    return

                # Check if Claude signaled completion
                match = RALPH_COMPLETE_PATTERN.search(answer)
                if match:
                    loop.completed = True
                    loop.summary = match.group(1).strip()
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ Ralph loop completed after {loop.current_iteration} iterations\n\n"
                        f"Summary: {loop.summary}",
                        message_thread_id=loop.topic_id,
                    )
                    return

            # Max iterations reached
            await self.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Ralph loop reached max iterations ({loop.max_iterations})\n\n"
                "The task may not be fully complete. Review the output and continue manually if needed.",
                message_thread_id=loop.topic_id,
            )
        finally:
            # Always remove the button when the loop ends
            await self._remove_button(loop)

    async def _run_iteration(
        self,
        loop: RalphLoop,
        prompt: str,
        runner: "Runner",
        cwd: Path,
        iteration_msg_id: int,
    ) -> str | None:
        """Run a single iteration of the Ralph loop.

        Returns the answer text, or None if cancelled/error.
        """
        import os
        from ..bridge import (
            ProgressEdits,
            RunOutcome,
            run_runner_with_cancel,
            sync_resume_token,
            _format_error,
            PROGRESS_EDIT_EVERY_S,
        )
        import time

        chat_id = self.workspace.telegram_group_id
        original_cwd = os.getcwd()

        try:
            os.chdir(cwd)
        except Exception as e:
            await self.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Failed to change to folder directory: {e}",
                message_thread_id=loop.topic_id,
            )
            return None

        try:
            started_at = time.monotonic()
            progress_renderer = ExecProgressRenderer(
                max_actions=5,
                resume_formatter=runner.format_resume,
                engine=runner.engine,
            )

            # Send initial progress with cancel button
            initial_parts = progress_renderer.render_progress_parts(
                0.0, label="working"
            )
            initial_rendered, initial_entities = prepare_telegram(initial_parts)
            cancel_keyboard = _cancel_keyboard(loop.topic_id, loop.loop_id)
            progress_msg = await self.bot.send_message(
                chat_id=chat_id,
                text=initial_rendered,
                entities=initial_entities,
                message_thread_id=loop.topic_id,
                reply_to_message_id=iteration_msg_id,
                disable_notification=True,
                reply_markup=cancel_keyboard,
            )

            progress_id: int | None = None
            if progress_msg is not None:
                progress_id = int(progress_msg["message_id"])

            edits = ProgressEdits(
                bot=self.bot,
                chat_id=chat_id,
                progress_id=progress_id,
                renderer=progress_renderer,
                started_at=started_at,
                progress_edit_every=PROGRESS_EDIT_EVERY_S,
                clock=time.monotonic,
                sleep=anyio.sleep,
                last_edit_at=started_at,
                last_rendered=initial_rendered,
                reply_markup=cancel_keyboard,
            )

            # Create a fake running task for cancellation
            from ..bridge import RunningTask

            running_task = RunningTask()

            # Watch for cancellation
            async def check_cancel() -> None:
                while not loop.cancel_requested.is_set():
                    await anyio.sleep(0.5)
                running_task.cancel_requested.set()

            cancel_scope = anyio.CancelScope()
            edits_scope = anyio.CancelScope()
            outcome = RunOutcome()
            error: Exception | None = None

            async with anyio.create_task_group() as tg:

                async def run_edits() -> None:
                    try:
                        with edits_scope:
                            await edits.run()
                    except anyio.get_cancelled_exc_class():
                        pass

                async def run_cancel_watch() -> None:
                    with cancel_scope:
                        await check_cancel()

                tg.start_soon(run_edits)
                tg.start_soon(run_cancel_watch)

                try:
                    outcome = await run_runner_with_cancel(
                        runner,
                        prompt=prompt,
                        resume_token=loop.resume_token,
                        edits=edits,
                        running_task=running_task,
                        on_thread_known=None,
                    )
                    # Update resume token for next iteration
                    loop.resume_token = outcome.resume
                except Exception as exc:
                    error = exc
                finally:
                    cancel_scope.cancel()
                    edits_scope.cancel()

            elapsed = time.monotonic() - started_at

            if error is not None:
                sync_resume_token(progress_renderer, outcome.resume)
                err_body = _format_error(error)
                final_parts = progress_renderer.render_final_parts(
                    elapsed, err_body, status="error"
                )
                final_rendered, final_entities = prepare_telegram(final_parts)
                if progress_id is not None:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_id,
                        text=final_rendered,
                        entities=final_entities,
                    )
                return None

            if outcome.cancelled:
                sync_resume_token(progress_renderer, outcome.resume)
                final_parts = progress_renderer.render_final_parts(
                    elapsed, "Loop cancelled", status="cancelled"
                )
                final_rendered, final_entities = prepare_telegram(final_parts)
                if progress_id is not None:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_id,
                        text=final_rendered,
                        entities=final_entities,
                    )
                return None

            if outcome.completed is None:
                return None

            # Send final response
            completed = outcome.completed
            final_answer = completed.answer
            if completed.ok is False and completed.error:
                if final_answer.strip():
                    final_answer = f"{final_answer}\n\n{completed.error}"
                else:
                    final_answer = str(completed.error)

            sync_resume_token(progress_renderer, completed.resume or outcome.resume)
            final_parts = progress_renderer.render_final_parts(
                elapsed,
                final_answer,
                status="done" if completed.ok else "error",
            )
            final_rendered, final_entities = prepare_telegram(final_parts)

            if progress_id is not None:
                # Add Cancel button to the final message
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_id,
                    text=final_rendered,
                    entities=final_entities,
                    reply_markup=_cancel_keyboard(loop.topic_id, loop.loop_id),
                )
                # Track this message so we can remove the button later
                loop.button_message_id = progress_id
                loop.button_message_text = final_rendered
                loop.button_message_entities = final_entities

            return final_answer

        finally:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass

    def cancel_loop(self, topic_id: int) -> bool:
        """Cancel the active loop for a topic.

        Returns True if a loop was cancelled, False if no loop was active.
        """
        loop = self._active_loops.get(topic_id)
        if loop is None:
            return False

        loop.cancel_requested.set()
        return True


def parse_ralph_command(args: str) -> tuple[str, int | None]:
    """Parse /ralph-loop command arguments.

    Returns (prompt, max_iterations).
    """
    # Look for --max-iterations N or -n N
    max_iter_pattern = re.compile(r"(?:--max-iterations|-n)\s+(\d+)")
    match = max_iter_pattern.search(args)

    max_iterations: int | None = None
    prompt = args

    if match:
        max_iterations = int(match.group(1))
        # Remove the flag from the prompt
        prompt = max_iter_pattern.sub("", args).strip()

    return prompt, max_iterations
