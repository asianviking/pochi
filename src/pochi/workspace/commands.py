"""Slash command handlers for the General topic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..engines import list_backend_ids
from ..logging import get_logger
from .config import save_workspace_config

if TYPE_CHECKING:
    from .manager import WorkspaceManager
    from .router import RouteResult

logger = get_logger(__name__)


async def handle_slash_command(
    manager: "WorkspaceManager",
    route: "RouteResult",
    reply_to_message_id: int,
) -> None:
    """Handle a slash command in the General topic."""
    command = route.command
    args = route.command_args

    handlers = {
        "clone": _handle_clone,
        "create": _handle_create,
        "add": _handle_add,
        "list": _handle_list,
        "remove": _handle_remove,
        "status": _handle_status,
        "engine": _handle_engine,
        "help": _handle_help,
    }

    handler = handlers.get(command)
    if handler is None:
        # Unknown command - let it fall through to orchestrator
        return

    try:
        await handler(manager, args, reply_to_message_id)
    except Exception as e:
        logger.exception(
            "workspace.command.error",
            command=command,
            error=str(e),
        )
        await manager.send_to_topic(
            None,  # General topic
            f"❌ Error executing /{command}: {e}",
            reply_to_message_id=reply_to_message_id,
        )


async def _handle_clone(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /clone <name> <git-url> [path]"""
    parts = args.split()
    if len(parts) < 2:
        await manager.send_to_topic(
            None,
            "Usage: /clone <name> <git-url> [path]\n\n"
            "Example: /clone backend git@github.com:user/backend.git",
            reply_to_message_id=reply_to_message_id,
        )
        return

    name = parts[0]
    git_url = parts[1]
    path = parts[2] if len(parts) > 2 else name

    # Check if folder name already exists
    if name in manager.config.folders:
        await manager.send_to_topic(
            None,
            f"❌ Folder '{name}' already exists in workspace.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Send progress message
    await manager.send_to_topic(
        None,
        f"🔄 Cloning {git_url}...",
        reply_to_message_id=reply_to_message_id,
    )

    # Clone the repo
    target_path = manager.config.root / path
    try:
        result = subprocess.run(
            ["git", "clone", git_url, str(target_path)],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for large repos
        )
        if result.returncode != 0:
            await manager.send_to_topic(
                None,
                f"❌ Clone failed:\n```\n{result.stderr}\n```",
                reply_to_message_id=reply_to_message_id,
            )
            return
    except subprocess.TimeoutExpired:
        await manager.send_to_topic(
            None,
            "❌ Clone timed out (5 minute limit).",
            reply_to_message_id=reply_to_message_id,
        )
        return
    except Exception as e:
        await manager.send_to_topic(
            None,
            f"❌ Clone failed: {e}",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Add folder and create topic
    folder, topic_id = await manager.add_folder(
        name=name,
        path=path,
        origin=git_url,
        create_topic=True,
    )

    if topic_id is not None:
        await manager.send_to_topic(
            None,
            f"✓ Cloned {name} to {path}\n"
            f"✓ Created topic #{name}\n\n"
            f"Switch to #{name} to start working on it.",
            reply_to_message_id=reply_to_message_id,
        )
    else:
        await manager.send_to_topic(
            None,
            f"✓ Cloned {name} to {path}\n"
            f"⚠️ Failed to create topic. Try /add {name} {path} manually.",
            reply_to_message_id=reply_to_message_id,
        )


async def _handle_create(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /create <name> [--no-git]"""
    parts = args.split()
    if len(parts) < 1 or not parts[0]:
        await manager.send_to_topic(
            None,
            "Usage: /create <name> [--no-git]\n\n"
            "Example: /create auth-service\n"
            "Example: /create knowledge-vault --no-git",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Parse --no-git flag
    no_git = "--no-git" in parts
    name_parts = [p for p in parts if p != "--no-git"]
    if not name_parts:
        await manager.send_to_topic(
            None,
            "Usage: /create <name> [--no-git]",
            reply_to_message_id=reply_to_message_id,
        )
        return

    name = name_parts[0]

    # Check if folder name already exists
    if name in manager.config.folders:
        await manager.send_to_topic(
            None,
            f"❌ Folder '{name}' already exists in workspace.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Create the directory
    target_path = manager.config.root / name
    try:
        target_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await manager.send_to_topic(
            None,
            f"❌ Failed to create directory: {e}",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Initialize git repo (unless --no-git)
    git_initialized = False
    if not no_git:
        try:
            result = subprocess.run(
                ["git", "init"],
                cwd=str(target_path),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                await manager.send_to_topic(
                    None,
                    f"❌ git init failed:\n```\n{result.stderr}\n```",
                    reply_to_message_id=reply_to_message_id,
                )
                return
            git_initialized = True
        except Exception as e:
            await manager.send_to_topic(
                None,
                f"❌ git init failed: {e}",
                reply_to_message_id=reply_to_message_id,
            )
            return

    # Add folder and create topic
    folder, topic_id = await manager.add_folder(
        name=name,
        path=name,
        create_topic=True,
    )

    if topic_id is not None:
        git_line = "✓ Initialized git repository\n" if git_initialized else ""
        await manager.send_to_topic(
            None,
            f"✓ Created {name} at {target_path}\n"
            f"{git_line}"
            f"✓ Created topic #{name}\n\n"
            f"Switch to #{name} to start working on it.",
            reply_to_message_id=reply_to_message_id,
        )
    else:
        git_line = "✓ Initialized git repository\n" if git_initialized else ""
        await manager.send_to_topic(
            None,
            f"✓ Created {name} at {target_path}\n"
            f"{git_line}"
            f"⚠️ Failed to create topic. Try /add {name} {name} manually.",
            reply_to_message_id=reply_to_message_id,
        )


async def _handle_add(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /add <name> <path>"""
    parts = args.split()
    if len(parts) < 2:
        await manager.send_to_topic(
            None,
            "Usage: /add <name> <path>\n\nExample: /add frontend ~/dev/my-frontend",
            reply_to_message_id=reply_to_message_id,
        )
        return

    name = parts[0]
    path = parts[1]

    # Check if folder name already exists
    if name in manager.config.folders:
        await manager.send_to_topic(
            None,
            f"❌ Folder '{name}' already exists in workspace.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Resolve the path
    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.exists():
        await manager.send_to_topic(
            None,
            f"❌ Path does not exist: {resolved_path}",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Make path relative to workspace if possible
    try:
        relative_path = resolved_path.relative_to(manager.config.root)
        path_str = str(relative_path)
    except ValueError:
        # Path is outside workspace, use absolute
        path_str = str(resolved_path)

    # Add folder and create topic
    folder, topic_id = await manager.add_folder(
        name=name,
        path=path_str,
        create_topic=True,
    )

    if topic_id is not None:
        await manager.send_to_topic(
            None,
            f"✓ Added {name} at {path_str}\n"
            f"✓ Created topic #{name}\n\n"
            f"Switch to #{name} to start working on it.",
            reply_to_message_id=reply_to_message_id,
        )
    else:
        await manager.send_to_topic(
            None,
            f"✓ Added {name} at {path_str}\n⚠️ Failed to create topic.",
            reply_to_message_id=reply_to_message_id,
        )


async def _handle_list(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /list"""
    folders = manager.config.folders
    if not folders:
        await manager.send_to_topic(
            None,
            "No folders in workspace yet.\n\nUse /clone, /create, or /add to add one.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    lines = [f"📁 Workspace: {manager.config.name}\n"]
    for name, folder in folders.items():
        topic_status = f"#{name}" if folder.topic_id else "⚠️ no topic"
        is_git = folder.is_git_repo(manager.config.root)
        type_indicator = " (git)" if is_git else ""
        origin_info = f" from {folder.origin}" if folder.origin else ""
        lines.append(f"• {name}{type_indicator} - {topic_status}{origin_info}")
        lines.append(f"  Path: {folder.path}")
        if folder.description:
            lines.append(f"  {folder.description}")

    await manager.send_to_topic(
        None,
        "\n".join(lines),
        reply_to_message_id=reply_to_message_id,
    )


async def _handle_remove(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /remove <name>"""
    parts = args.split()
    if len(parts) < 1 or not parts[0]:
        await manager.send_to_topic(
            None,
            "Usage: /remove <name>\n\n"
            "This removes the folder from the workspace config but does NOT delete files.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    name = parts[0]

    if name not in manager.config.folders:
        await manager.send_to_topic(
            None,
            f"❌ Folder '{name}' not found in workspace.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    folder = manager.config.folders[name]
    topic_id = folder.topic_id

    # Remove from config
    del manager.config.folders[name]
    save_workspace_config(manager.config)

    # Try to close the topic (archive it)
    topic_closed = False
    if topic_id is not None:
        topic_closed = await manager.bot.close_forum_topic(
            manager.config.telegram_group_id,
            topic_id,
        )

    if topic_closed:
        await manager.send_to_topic(
            None,
            f"✓ Removed {name} from workspace\n"
            f"✓ Archived topic #{name}\n\n"
            f"Files at {folder.path} were NOT deleted.",
            reply_to_message_id=reply_to_message_id,
        )
    else:
        await manager.send_to_topic(
            None,
            f"✓ Removed {name} from workspace\n"
            f"⚠️ Could not archive topic\n\n"
            f"Files at {folder.path} were NOT deleted.",
            reply_to_message_id=reply_to_message_id,
        )


async def _handle_status(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /status"""
    config = manager.config
    available_engines = list_backend_ids()

    lines = [
        f"📊 Workspace Status: {config.name}",
        "",
        f"Telegram Group: {config.telegram_group_id}",
        f"Folders: {len(config.folders)}",
        "",
        "Engines:",
        f"  Default: {config.default_engine}",
        f"  Available: {', '.join(available_engines)}",
        "",
        "Ralph Wiggum:",
        f"  Enabled: {'Yes' if config.ralph.enabled else 'No'}",
        f"  Max iterations: {config.ralph.default_max_iterations}",
    ]

    pending = config.get_pending_topics()
    if pending:
        lines.append("")
        lines.append(f"⚠️ Pending topics: {', '.join(f.name for f in pending)}")

    await manager.send_to_topic(
        None,
        "\n".join(lines),
        reply_to_message_id=reply_to_message_id,
    )


async def _handle_engine(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /engine [name]

    Without args: Show current default engine and available engines
    With args: Set default engine for new conversations
    """
    config = manager.config
    available_engines = list_backend_ids()

    if not args.strip():
        # Show current engine status
        lines = [
            "🔧 Engine Configuration",
            "",
            f"Default: `{config.default_engine}`",
            f"Available: {', '.join(f'`{e}`' for e in available_engines)}",
            "",
            "To change the default engine:",
            f"  /engine <name>  (e.g., /engine {available_engines[0] if available_engines else 'claude'})",
        ]
        await manager.send_to_topic(
            None,
            "\n".join(lines),
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Set new default engine
    new_engine = args.strip().lower()

    if new_engine not in available_engines:
        await manager.send_to_topic(
            None,
            f"❌ Unknown engine: `{new_engine}`\n\n"
            f"Available engines: {', '.join(f'`{e}`' for e in available_engines)}",
            reply_to_message_id=reply_to_message_id,
        )
        return

    if new_engine == config.default_engine:
        await manager.send_to_topic(
            None,
            f"`{new_engine}` is already the default engine.",
            reply_to_message_id=reply_to_message_id,
        )
        return

    # Update config
    old_engine = config.default_engine
    config.default_engine = new_engine
    save_workspace_config(config)

    await manager.send_to_topic(
        None,
        f"✓ Default engine changed: `{old_engine}` → `{new_engine}`\n\n"
        f"New conversations will use `{new_engine}` by default.",
        reply_to_message_id=reply_to_message_id,
    )


async def _handle_help(
    manager: "WorkspaceManager",
    args: str,
    reply_to_message_id: int,
) -> None:
    """Handle /help"""
    help_text = """📖 Pochi Workspace Commands

General Topic Commands:
  /clone <name> <git-url> [path]
    Clone a git repo and create a topic for it

  /create <name> [--no-git]
    Create a new folder (with git init by default)
    Use --no-git for non-repo folders like knowledge vaults

  /add <name> <path>
    Add an existing folder to the workspace

  /list
    List all folders in the workspace

  /remove <name>
    Remove a folder from workspace (doesn't delete files)

  /status
    Show workspace status

  /engine [name]
    Show or set the default engine (claude, codex, etc.)

  /help
    Show this help message

Worker Topic Commands:
  /ralph <prompt> [--max-iterations N]
    Run an iterative agent loop

  /cancel
    Cancel the current run or ralph loop

Or just send a message to chat with the agent!
"""
    await manager.send_to_topic(
        None,
        help_text,
        reply_to_message_id=reply_to_message_id,
    )
