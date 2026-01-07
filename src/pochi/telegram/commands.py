"""Telegram-specific command implementations.

These extend the base command handlers with Telegram-specific behavior
like topic creation and inline buttons.
"""

from __future__ import annotations

from ..commands import CommandContext


def format_telegram_help(context: CommandContext) -> str:
    """Format help text for Telegram."""
    if context == "general":
        return """📖 Pochi Workspace Commands

General Topic Commands:
  /clone <name> <git-url> [path]
    Clone a git repo and create a topic for it

  /create <name> [--no-git]
    Create a new folder (with git init by default)
    Use --no-git for non-repo folders

  /add <name> <path>
    Add an existing folder to the workspace

  /list
    List all folders in the workspace

  /remove <name>
    Remove a folder from workspace (doesn't delete files)

  /status
    Show workspace status

  /engine [name]
    Show or set the default engine

  /help
    Show this help message

Or just send a message to chat with the agent!
"""
    else:
        return """📖 Folder Commands

  /ralph <goal> [--max-iterations N]
    Start an iterative agent loop

  /cancel
    Cancel the current run or ralph loop

Or just send a message to chat with the agent!
"""


def make_ralph_cancel_button(topic_id: int, loop_id: str) -> dict:
    """Create an inline keyboard with a cancel button for Ralph."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "❌ Cancel Loop",
                    "callback_data": f"ralph:cancel:{topic_id}:{loop_id}",
                }
            ]
        ]
    }
