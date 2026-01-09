"""Interactive onboarding for Pochi.

This module provides a guided setup wizard that helps users configure
Pochi when the config is missing or invalid.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import anyio
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

from .config import create_workspace
from .config_store import get_config_path
from .engines import list_backends
from .logging import get_logger
from .telegram import TelegramClient

logger = get_logger(__name__)
console = Console()


class OnboardingError(Exception):
    """Error during onboarding."""

    pass


async def validate_bot_token(token: str) -> dict[str, Any] | None:
    """Validate bot token by calling Telegram getMe API.

    Args:
        token: Telegram bot token

    Returns:
        Bot info dict if valid, None otherwise
    """
    bot = TelegramClient(token)
    try:
        return await bot.get_me()
    except Exception:
        return None
    finally:
        await bot.close()


async def validate_chat_access(token: str, chat_id: int) -> dict[str, Any] | None:
    """Validate bot can access a chat.

    Args:
        token: Telegram bot token
        chat_id: Chat/group ID to validate

    Returns:
        Chat info dict if accessible, None otherwise
    """
    bot = TelegramClient(token)
    try:
        return await bot.get_chat(chat_id)
    except Exception:
        return None
    finally:
        await bot.close()


async def detect_chat_from_message(
    token: str, timeout_seconds: int = 60
) -> tuple[int, str] | None:
    """Wait for a message to the bot to detect chat ID.

    Args:
        token: Telegram bot token
        timeout_seconds: How long to wait for a message

    Returns:
        Tuple of (chat_id, chat_title) if detected, None on timeout
    """
    bot = TelegramClient(token)
    try:
        # Get initial update_id to only look at new messages
        updates = await bot.get_updates(offset=None, timeout_s=0)
        last_update_id = 0
        if updates:
            last_update_id = max(u.get("update_id", 0) for u in updates) + 1

        # Poll for new messages
        deadline = anyio.current_time() + timeout_seconds
        while anyio.current_time() < deadline:
            remaining = int(deadline - anyio.current_time())
            if remaining <= 0:
                break

            updates = await bot.get_updates(
                offset=last_update_id,
                timeout_s=min(remaining, 10),
            )

            if updates:
                for update in updates:
                    last_update_id = update.get("update_id", 0) + 1
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    chat_type = chat.get("type", "")

                    # We want group or supergroup chats
                    if chat_id and chat_type in ("group", "supergroup"):
                        chat_title = chat.get("title", f"Group {chat_id}")
                        return (chat_id, chat_title)

        return None
    finally:
        await bot.close()


def show_welcome() -> None:
    """Display welcome banner."""
    panel = Panel(
        "Let's set up your Pochi workspace.",
        title="Welcome to Pochi!",
        border_style="cyan",
        padding=(1, 2),
        expand=False,
    )
    console.print()
    console.print(panel)
    console.print()


def show_available_engines() -> None:
    """Display available AI agent engines."""
    backends = list_backends()

    table = Table(title="Available Engines", show_header=True)
    table.add_column("Engine", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Install Command")

    for backend in backends:
        cmd = backend.cli_cmd or backend.id
        available = shutil.which(cmd) is not None

        status = "[green]installed[/green]" if available else "[dim]not found[/dim]"
        install = backend.install_cmd or "-" if not available else "-"

        table.add_row(backend.id, status, install)

    console.print(table)
    console.print()


def prompt_bot_token() -> str:
    """Prompt user for Telegram bot token with instructions."""
    console.print("[bold]Step 1:[/bold] Telegram Bot Token")
    console.print()
    console.print("To create a bot, talk to @BotFather on Telegram:")
    console.print("  1. Send /newbot")
    console.print("  2. Choose a name and username for your bot")
    console.print("  3. Copy the token BotFather gives you")
    console.print()

    token = Prompt.ask("[cyan]Enter your bot token[/cyan]")
    return token.strip()


def prompt_group_id(bot_username: str) -> int | None:
    """Prompt user for Telegram group ID.

    Args:
        bot_username: The bot's username for display

    Returns:
        Group ID if entered manually, None if user wants auto-detection
    """
    console.print()
    console.print("[bold]Step 2:[/bold] Telegram Group")
    console.print()
    console.print("Pochi needs a Telegram group to work in.")
    console.print("  1. Create a group (or use an existing one)")
    console.print(f"  2. Add @{bot_username} to the group")
    console.print(f"  3. Make @{bot_username} an admin (required for topics)")
    console.print()

    choice = Prompt.ask(
        "Do you want to enter the group ID manually or detect it?",
        choices=["manual", "detect"],
        default="detect",
    )

    if choice == "manual":
        group_id_str = Prompt.ask("[cyan]Enter your group ID[/cyan]")
        try:
            return int(group_id_str.strip())
        except ValueError:
            console.print("[red]Invalid group ID. Must be an integer.[/red]")
            return None
    else:
        return None


def show_config_preview(
    name: str, bot_token: str, chat_id: int, chat_title: str
) -> None:
    """Display a preview of the config to be created."""
    console.print()
    console.print("[bold]Configuration Preview:[/bold]")
    console.print()

    # Mask the token
    if len(bot_token) > 10:
        masked_token = bot_token[:5] + "..." + bot_token[-5:]
    else:
        masked_token = "***"

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    table.add_row("Workspace Name", name)
    table.add_row("Bot Token", masked_token)
    table.add_row("Group ID", str(chat_id))
    table.add_row("Group Name", chat_title)

    console.print(table)
    console.print()


def run_onboarding_sync(workspace_root: Path | None = None) -> Path | None:
    """Run interactive onboarding wizard (synchronous wrapper).

    Args:
        workspace_root: Directory to create workspace in (defaults to cwd)

    Returns:
        Path to created config file, or None if cancelled
    """
    return anyio.run(run_onboarding, workspace_root)


async def run_onboarding(workspace_root: Path | None = None) -> Path | None:
    """Run interactive onboarding wizard.

    Args:
        workspace_root: Directory to create workspace in (defaults to cwd)

    Returns:
        Path to created config file, or None if cancelled
    """
    if workspace_root is None:
        workspace_root = Path.cwd()

    # Check if config already exists
    config_path = get_config_path(workspace_root)
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if not Confirm.ask("Do you want to overwrite it?"):
            return None

    show_welcome()
    show_available_engines()

    # Step 1: Bot token
    bot_token = prompt_bot_token()
    if not bot_token:
        console.print("[red]Bot token is required.[/red]")
        return None

    console.print()
    console.print("Validating bot token...", end=" ")

    bot_info = await validate_bot_token(bot_token)
    if bot_info is None:
        console.print("[red]failed[/red]")
        console.print("[red]Invalid bot token. Please check and try again.[/red]")
        return None

    bot_username = bot_info.get("username", "bot")
    console.print(f"[green]connected to @{bot_username}[/green]")

    # Step 2: Group ID
    group_id = prompt_group_id(bot_username)

    if group_id is None:
        # Auto-detect by waiting for a message
        console.print()
        console.print(
            f"[cyan]Send any message to your group after adding @{bot_username}...[/cyan]"
        )
        console.print("[dim](Waiting up to 60 seconds)[/dim]")

        result = await detect_chat_from_message(bot_token, timeout_seconds=60)
        if result is None:
            console.print("[red]Timeout waiting for message. Please try again.[/red]")
            return None

        group_id, chat_title = result
        console.print(f"[green]Detected group: {chat_title} ({group_id})[/green]")
    else:
        # Validate manual group ID
        console.print()
        console.print("Validating group access...", end=" ")

        chat_info = await validate_chat_access(bot_token, group_id)
        if chat_info is None:
            console.print("[red]failed[/red]")
            console.print(
                f"[red]Cannot access group {group_id}. "
                f"Make sure @{bot_username} is added to the group.[/red]"
            )
            return None

        chat_title = chat_info.get("title", f"Group {group_id}")
        console.print(f"[green]access verified ({chat_title})[/green]")

    # Step 3: Workspace name
    workspace_name = workspace_root.name

    # Show preview and confirm
    show_config_preview(workspace_name, bot_token, group_id, chat_title)

    if not Confirm.ask("Create this configuration?"):
        console.print("[yellow]Setup cancelled.[/yellow]")
        return None

    # Create the workspace
    try:
        config = create_workspace(
            root=workspace_root,
            name=workspace_name,
            telegram_group_id=group_id,
            bot_token=bot_token,
        )
        console.print()
        console.print(f"[green]Configuration saved to {config.config_path()}[/green]")
        console.print()
        console.print("[bold]Next steps:[/bold]")
        console.print("  Run [cyan]pochi[/cyan] to start the bot")
        console.print()
        return config.config_path()
    except Exception as e:
        console.print(f"[red]Error creating configuration: {e}[/red]")
        logger.exception("onboarding.create_failed")
        return None
