# Pochi

A multi-model AI agent Telegram bot for multi-folder workspaces. Bridge AI coding agents (Claude Code, Codex, OpenCode, Pi, etc.) to Telegram and interact with agents from your phone or any Telegram client.

## Features

- **Workspace-first design** - One Telegram group maps to one workspace, each folder gets its own topic
- **Full context & resume** - Resume tokens let you continue conversations seamlessly
- **Ralph Wiggum loops** - Iterative prompting where Claude reviews its own work until satisfied
- **Git-integrated** - Clone repos, create projects, manage multiple codebases from Telegram

## Installation

```sh
pip install pochi
# or
uv add pochi
```

Requires Python 3.14+ and [Claude Code](https://claude.ai/code) CLI.

## Quick Start

1. Create a Telegram bot via [@BotFather](https://t.me/botfather)
2. Create a Telegram group with topics enabled
3. Add your bot to the group as admin
4. Initialize the workspace:

```sh
pochi init
# or create in a new directory
pochi init my-workspace
```

5. Run pochi:

```sh
pochi
```

## Workspace Commands

In the **General** topic:

| Command | Description |
|---------|-------------|
| `/clone <name> <git-url>` | Clone a repo and create a topic |
| `/create <name>` | Create a new folder with git init |
| `/add <name> <path>` | Add an existing folder |
| `/list` | List all folders |
| `/remove <name>` | Remove folder from workspace |
| `/status` | Show workspace status |
| `/help` | Show help |

In **folder topics**:

| Command | Description |
|---------|-------------|
| `/ralph <prompt>` | Start an iterative Claude loop |
| `/cancel` | Cancel current run or Ralph loop |

Or just send a message to chat with Claude!

## Configuration

Config lives in `.pochi/workspace.toml`:

```toml
[workspace]
name = "my-workspace"
telegram_group_id = -1001234567890
bot_token = "123456:ABC..."

[folders.backend]
path = "backend"
topic_id = 123

[folders.frontend]
path = "frontend"
topic_id = 456

[workers.ralph]
enabled = false
default_max_iterations = 3
```

## Resume Tokens

Every response includes a resume token for conversation continuity:

```
`claude --resume abc123`
```

Reply to any message or include the token to continue that conversation.

## Acknowledgments

Built on top of [takopi](https://github.com/banteg/takopi).

## License

MIT
