from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import typer

import tomllib
from typing import Any

from . import __version__
from .backends import EngineBackend
from .config import ConfigError
from .engines import get_engine_config, list_backends
from .logging import get_logger, setup_logging
from .router import AutoRouter, RunnerEntry
from .telegram import TelegramClient
from .workspace import (
    WorkspaceConfig,
    create_workspace,
    find_workspace_root,
    load_workspace_config,
)

logger = get_logger(__name__)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def _load_raw_config(config_path: Path) -> dict[str, Any]:
    """Load raw TOML config for engine-specific sections."""
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _build_runner_entry(
    backend: EngineBackend,
    raw_config: dict[str, Any],
    config_path: Path,
) -> RunnerEntry:
    """Build a RunnerEntry for a single backend."""
    engine_cfg = get_engine_config(raw_config, backend.id, config_path)
    cmd = backend.cli_cmd or backend.id

    # Check CLI availability
    if shutil.which(cmd) is None:
        # Return unavailable entry
        return RunnerEntry(
            engine=backend.id,
            runner=None,  # type: ignore[arg-type]
            available=False,
            issue=f"{cmd} not found on PATH",
        )

    try:
        runner = backend.build_runner(engine_cfg, config_path)
    except Exception as exc:
        return RunnerEntry(
            engine=backend.id,
            runner=None,  # type: ignore[arg-type]
            available=False,
            issue=f"Failed to build runner: {exc}",
        )

    return RunnerEntry(
        engine=backend.id,
        runner=runner,
        available=True,
        issue=None,
    )


def _build_router(
    workspace_config: WorkspaceConfig,
) -> tuple[AutoRouter, list[RunnerEntry], list[RunnerEntry]]:
    """Build a router with all available backends.

    Returns:
        Tuple of (router, available_entries, unavailable_entries)
    """
    config_path = workspace_config.config_path()
    raw_config = _load_raw_config(config_path)
    default_engine = workspace_config.default_engine

    backends = list_backends()
    if not backends:
        raise ConfigError("No engine backends found")

    entries: list[RunnerEntry] = []
    available: list[RunnerEntry] = []
    unavailable: list[RunnerEntry] = []

    for backend in backends:
        entry = _build_runner_entry(backend, raw_config, config_path)
        entries.append(entry)
        if entry.available:
            available.append(entry)
        else:
            unavailable.append(entry)

    if not available:
        issues = [f"  - {e.engine}: {e.issue}" for e in unavailable]
        raise ConfigError("No engines available:\n" + "\n".join(issues))

    # Check if default engine is available
    default_available = any(e.engine == default_engine for e in available)
    if not default_available:
        # Fall back to first available engine
        default_engine = available[0].engine

    # Only include available entries in router
    router = AutoRouter(entries=available, default_engine=default_engine)
    return router, available, unavailable


async def _validate_bot_token(bot_token: str) -> dict | None:
    """Validate bot token by calling getMe API."""
    bot = TelegramClient(bot_token)
    try:
        return await bot.get_me()
    finally:
        await bot.close()


async def _validate_group_access(bot_token: str, group_id: int) -> dict | None:
    """Validate bot can access the group."""
    bot = TelegramClient(bot_token)
    try:
        return await bot.get_chat(group_id)
    finally:
        await bot.close()


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Multi-model AI agent Telegram bot for multi-folder workspaces.",
)


@app.callback()
def app_main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    final_notify: bool = typer.Option(
        True,
        "--final-notify/--no-final-notify",
        help="Send the final response as a new message (not an edit).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        help="Log engine JSONL, Telegram requests, and rendered messages.",
    ),
) -> None:
    """Pochi CLI - Multi-model AI agent workspace automation."""
    if ctx.invoked_subcommand is None:
        # Default command: run workspace
        _run_workspace(final_notify=final_notify, debug=debug)
        raise typer.Exit()


def _run_workspace(*, final_notify: bool, debug: bool) -> None:
    """Run pochi in workspace mode."""
    from .workspace.bridge import WorkspaceBridgeConfig, run_workspace_loop
    from .workspace.manager import WorkspaceManager
    from .workspace.ralph import RalphManager
    from .workspace.router import WorkspaceRouter

    setup_logging(debug=debug)

    workspace_root = find_workspace_root()
    if workspace_root is None:
        typer.echo(
            "error: not in a workspace (no .pochi/workspace.toml found)", err=True
        )
        typer.echo("Run 'pochi init' to create a workspace here.", err=True)
        raise typer.Exit(code=1)

    workspace_config = load_workspace_config(workspace_root)
    if workspace_config is None:
        typer.echo("error: failed to load workspace config", err=True)
        raise typer.Exit(code=1)

    if not workspace_config.bot_token:
        typer.echo("error: bot_token not set in workspace config", err=True)
        raise typer.Exit(code=1)

    if not workspace_config.telegram_group_id:
        typer.echo("error: telegram_group_id not set in workspace config", err=True)
        raise typer.Exit(code=1)

    # Build router with all available engines
    try:
        router, available, unavailable = _build_router(workspace_config)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)

    # Create Telegram client
    bot = TelegramClient(workspace_config.bot_token)

    # Create workspace components
    workspace_router = WorkspaceRouter(workspace_config)
    workspace_manager = WorkspaceManager(workspace_config, bot)
    workspace_manager.set_router(workspace_router)

    # Create Ralph manager
    ralph_manager = RalphManager(workspace_config, bot, router)

    # Build startup message with engine info
    repo_count = len(workspace_config.folders)
    ralph_status = "enabled" if workspace_config.ralph.enabled else "on-demand"
    available_engines = [e.engine for e in available]
    unavailable_engines = [e.engine for e in unavailable]

    agents_line = ", ".join(f"`{e}`" for e in available_engines)
    if unavailable_engines:
        not_installed = ", ".join(f"`{e}`" for e in unavailable_engines)
        agents_line = f"{agents_line} (not installed: {not_installed})"

    startup_msg = (
        f"\N{DOG FACE} **pochi ready**\n\n"
        f"workspace: `{workspace_config.name}`  \n"
        f"repos: `{repo_count}`  \n"
        f"default: `{router.default_engine}`  \n"
        f"agents: {agents_line}  \n"
        f"ralph: `{ralph_status}`  \n"
        f"working in: `{workspace_root}`"
    )

    cfg = WorkspaceBridgeConfig(
        bot=bot,
        router=router,
        workspace=workspace_config,
        workspace_router=workspace_router,
        workspace_manager=workspace_manager,
        ralph_manager=ralph_manager,
        final_notify=final_notify,
        startup_msg=startup_msg,
    )

    try:
        anyio.run(run_workspace_loop, cfg)
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130)


@app.command("init", help="Initialize a workspace in current dir or [FOLDER].")
def init_command(
    folder: str = typer.Argument(
        None,
        help="Folder name to create workspace in (defaults to current directory)",
    ),
    name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="Name for the workspace (defaults to folder name)",
    ),
    bot_token: str = typer.Option(
        None,
        "--bot-token",
        "-t",
        help="Telegram bot token (will prompt if not provided)",
    ),
    group_id: int = typer.Option(
        None,
        "--group-id",
        "-g",
        help="Telegram group ID (will prompt if not provided)",
    ),
) -> None:
    """Initialize a new workspace.

    If FOLDER is provided, creates a new directory with that name.
    Otherwise, initializes the workspace in the current directory.
    """
    cwd = Path.cwd()

    if folder:
        # Create in subfolder
        workspace_dir = cwd / folder
        workspace_name = name or folder

        if workspace_dir.exists():
            existing_config = load_workspace_config(workspace_dir)
            if existing_config is not None:
                typer.echo(
                    f"error: workspace already exists at {workspace_dir}", err=True
                )
                raise typer.Exit(code=1)

        workspace_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Initialize in current directory
        workspace_dir = cwd
        workspace_name = name or cwd.name

        existing_config = load_workspace_config(workspace_dir)
        if existing_config is not None:
            typer.echo(f"error: workspace already exists at {workspace_dir}", err=True)
            raise typer.Exit(code=1)

    # Prompt for missing values
    if bot_token is None:
        bot_token = typer.prompt("Telegram bot token")
    if group_id is None:
        group_id_str = typer.prompt("Telegram group ID")
        try:
            group_id = int(group_id_str)
        except ValueError:
            typer.echo("error: group ID must be an integer", err=True)
            raise typer.Exit(code=1)

    # Validate bot token
    typer.echo("Validating...")
    try:
        bot_info = anyio.run(_validate_bot_token, bot_token)
    except Exception as e:
        typer.echo(f"error: failed to validate bot token: {e}", err=True)
        raise typer.Exit(code=1)

    if bot_info is None:
        typer.echo("error: invalid bot token", err=True)
        raise typer.Exit(code=1)

    bot_username = bot_info.get("username", "bot")
    typer.echo(f"✓ Connected to @{bot_username}")

    # Validate group access
    try:
        chat_info = anyio.run(_validate_group_access, bot_token, group_id)
    except Exception as e:
        typer.echo(f"error: failed to access group: {e}", err=True)
        raise typer.Exit(code=1)

    if chat_info is None:
        typer.echo(
            f"error: bot cannot access group {group_id}. "
            "Make sure the bot is added to the group.",
            err=True,
        )
        raise typer.Exit(code=1)

    chat_title = chat_info.get("title", f"group {group_id}")
    typer.echo(f"✓ Access verified for '{chat_title}'")

    # Create workspace config
    config = create_workspace(
        root=workspace_dir,
        name=workspace_name,
        telegram_group_id=group_id,
        bot_token=bot_token,
    )

    if folder:
        typer.echo(f"✓ Created workspace '{workspace_name}' at {workspace_dir}")
    else:
        typer.echo(f"✓ Initialized workspace '{workspace_name}' in {workspace_dir}")
    typer.echo(f"✓ Config saved to {config.config_path()}")
    typer.echo("")
    typer.echo("Next steps:")
    if folder:
        typer.echo(f"  cd {folder}")
    typer.echo("  pochi")


@app.command("info")
def info_command() -> None:
    """Show information about the current workspace."""
    workspace_root = find_workspace_root()
    if workspace_root is None:
        typer.echo(
            "error: not in a workspace (no .pochi/workspace.toml found)", err=True
        )
        raise typer.Exit(code=1)

    config = load_workspace_config(workspace_root)
    if config is None:
        typer.echo("error: failed to load workspace config", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Workspace: {config.name}")
    typer.echo(f"Root: {config.root}")
    typer.echo(f"Telegram Group: {config.telegram_group_id}")
    typer.echo(f"Folders: {len(config.folders)}")
    typer.echo("")

    if config.folders:
        typer.echo("Folders:")
        for folder_name, folder in config.folders.items():
            git_suffix = " (git)" if folder.is_git_repo(config.root) else ""
            topic_status = (
                f"topic #{folder.topic_id}" if folder.topic_id else "no topic"
            )
            if folder.pending_topic:
                topic_status = "pending topic"
            typer.echo(f"  • {folder_name}{git_suffix} ({topic_status})")
            typer.echo(f"    Path: {folder.path}")
    else:
        typer.echo("No folders configured yet.")
        typer.echo("Use /clone, /create, or /add in Telegram to add folders.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
