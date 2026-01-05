"""Discord slash command registration and handling.

This module provides native Discord slash commands for workspace management,
offering better UX with autocomplete, validation, and ephemeral responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ..logging import get_logger
from .config import WorkspaceConfig, save_workspace_config

if TYPE_CHECKING:
    from ..discord import DiscordProvider

logger = get_logger(__name__)


def register_commands(provider: DiscordProvider, config: WorkspaceConfig) -> None:
    """Register Discord slash commands for workspace management.

    Args:
        provider: The Discord provider to register commands with
        config: The workspace configuration
    """
    tree = provider.command_tree
    guild = discord.Object(id=provider.guild_id)

    @tree.command(
        name="list", description="List all folders in the workspace", guild=guild
    )
    async def list_folders(interaction: discord.Interaction) -> None:
        """List all folders in the workspace."""
        if not config.folders:
            await interaction.response.send_message(
                "No folders configured. Use `/add` or `/clone` to add folders.",
                ephemeral=True,
            )
            return

        lines = ["**Workspace Folders**\n"]
        for name, folder in config.folders.items():
            status = ""
            if folder.pending_channel:
                status = " (pending channel)"
            elif folder.discord_channel_id:
                status = f" <#{folder.discord_channel_id}>"
            lines.append(f"• **{name}**: `{folder.path}`{status}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @tree.command(name="status", description="Show workspace status", guild=guild)
    async def status(interaction: discord.Interaction) -> None:
        """Show workspace status."""
        lines = [f"**Workspace: {config.name}**\n"]
        lines.append(f"Default engine: `{config.default_engine}`")
        lines.append(f"Folders: {len(config.folders)}")

        if config.discord:
            lines.append(f"Discord guild: `{config.discord.guild_id}`")
            lines.append(f"Category: `{config.discord.category_id}`")
            if config.discord.admin_user:
                lines.append(f"Admin: <@{config.discord.admin_user}>")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @tree.command(
        name="engine", description="Show or set the default engine", guild=guild
    )
    @app_commands.describe(name="Engine name to set as default (optional)")
    async def engine(interaction: discord.Interaction, name: str | None = None) -> None:
        """Show or set the default engine."""
        if name is None:
            await interaction.response.send_message(
                f"Current default engine: `{config.default_engine}`",
                ephemeral=True,
            )
            return

        # Set the engine
        config.default_engine = name
        save_workspace_config(config)
        await interaction.response.send_message(
            f"Default engine set to: `{name}`",
            ephemeral=True,
        )

    @tree.command(name="users", description="List authorized users", guild=guild)
    async def users(interaction: discord.Interaction) -> None:
        """List authorized users."""
        if config.discord is None:
            await interaction.response.send_message(
                "Discord not configured.",
                ephemeral=True,
            )
            return

        lines = ["**Authorized Users**\n"]

        if config.discord.admin_user:
            lines.append(f"**Admin:** <@{config.discord.admin_user}>")
        else:
            lines.append("**Admin:** (not set - first user to interact becomes admin)")

        if config.discord.allowed_users:
            guests = ", ".join(f"<@{uid}>" for uid in config.discord.allowed_users)
            lines.append(f"**Guests:** {guests}")
        else:
            lines.append("**Guests:** (none)")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @tree.command(
        name="adduser", description="Add a guest user (admin only)", guild=guild
    )
    @app_commands.describe(user="User to add as guest")
    async def adduser(interaction: discord.Interaction, user: discord.User) -> None:
        """Add a guest user."""
        if config.discord is None:
            await interaction.response.send_message(
                "Discord not configured.",
                ephemeral=True,
            )
            return

        # Check admin
        if not config.discord.is_admin(interaction.user.id):
            await interaction.response.send_message(
                "Only the admin can add users.",
                ephemeral=True,
            )
            return

        # Add the user
        if not config.add_guest(user.id, platform="discord"):
            await interaction.response.send_message(
                f"{user.mention} is already a guest or admin.",
                ephemeral=True,
            )
            return

        save_workspace_config(config)
        await interaction.response.send_message(
            f"Added {user.mention} as guest.",
            ephemeral=True,
        )

    @tree.command(
        name="removeuser", description="Remove a guest user (admin only)", guild=guild
    )
    @app_commands.describe(user="User to remove from guests")
    async def removeuser(interaction: discord.Interaction, user: discord.User) -> None:
        """Remove a guest user."""
        if config.discord is None:
            await interaction.response.send_message(
                "Discord not configured.",
                ephemeral=True,
            )
            return

        # Check admin
        if not config.discord.is_admin(interaction.user.id):
            await interaction.response.send_message(
                "Only the admin can remove users.",
                ephemeral=True,
            )
            return

        # Remove the user
        if not config.remove_guest(user.id, platform="discord"):
            await interaction.response.send_message(
                f"{user.mention} is not a guest.",
                ephemeral=True,
            )
            return

        save_workspace_config(config)
        await interaction.response.send_message(
            f"Removed {user.mention} from guests.",
            ephemeral=True,
        )

    @tree.command(name="help", description="Show available commands", guild=guild)
    async def help_cmd(interaction: discord.Interaction) -> None:
        """Show available commands."""
        lines = [
            "**Pochi Commands**\n",
            "**Workspace Management**",
            "• `/list` - List all folders",
            "• `/status` - Show workspace status",
            "• `/engine [name]` - Show or set default engine",
            "",
            "**User Management**",
            "• `/users` - List authorized users",
            "• `/adduser <user>` - Add a guest user (admin only)",
            "• `/removeuser <user>` - Remove a guest user (admin only)",
            "",
            "**Session Management**",
            "• `/resume <token>` - Resume a session from another platform",
            "",
            "**Tips**",
            "• Send a message in a folder channel to start a conversation",
            "• The bot will create a thread for each conversation",
            "• Each thread maintains its own Claude session",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @tree.command(
        name="resume",
        description="Resume a session from another platform",
        guild=guild,
    )
    @app_commands.describe(
        token="Resume token from Telegram or terminal",
        name="Optional name for the new thread",
    )
    async def resume(
        interaction: discord.Interaction,
        token: str,
        name: str | None = None,
    ) -> None:
        """Resume a session from another platform."""
        # Defer since we'll create a thread
        await interaction.response.defer(ephemeral=True)

        # Get the channel
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "This command can only be used in text channels.",
                ephemeral=True,
            )
            return

        # Create thread name
        thread_name = name or f"Resumed: {token[:20]}..."

        try:
            # Create a new thread
            thread = await channel.create_thread(
                name=thread_name[:100],  # Discord limit
                type=discord.ChannelType.public_thread,
            )

            # Store the resume token for this thread
            from ..model import ResumeToken

            provider.set_thread_session(
                thread.id,
                resume_token=ResumeToken(value=token, engine="claude"),
            )

            await interaction.followup.send(
                f"Created thread {thread.mention} for resumed session.",
                ephemeral=True,
            )

            # Send an initial message in the thread
            await thread.send(
                f"Session resumed from token: `{token[:30]}...`\n\n"
                "Send a message to continue the conversation."
            )

        except discord.HTTPException as e:
            logger.error(
                "discord.resume_failed",
                error=str(e),
                token=token[:20],
            )
            await interaction.followup.send(
                f"Failed to create thread: {e}",
                ephemeral=True,
            )

    logger.info(
        "discord.commands_registered",
        count=7,
        guild_id=provider.guild_id,
    )
