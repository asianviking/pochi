# pochi

Multi-model AI agent Telegram bot for multi-folder workspaces.

Run AI coding agents (Claude Code, Codex, OpenCode, Pi, etc.) from Telegram with workspace-based organization. Each folder gets its own Telegram topic, enabling parallel work across multiple projects from a single bot.

## features

- **multi-engine**: supports multiple AI agent backends (Claude, Codex, OpenCode, Pi) via auto-discovery
- **workspace mode**: organize multiple folders under one Telegram group with topic-based isolation
- **stateless resume**: continue a thread in chat or pick up in the terminal with `claude --resume <token>`
- **progress updates**: real-time streaming of commands, tools, file changes, and elapsed time
- **ralph wiggum loops**: iterative Claude prompting where it reviews its own work until complete
- **folder management**: clone repos, create folders (with or without git), or add existing directories from Telegram
- **robust rendering**: markdown output with quality-of-life tweaks for Telegram

## requirements

- `uv` for installation (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- python 3.14+ (uv can install it: `uv python install 3.14`)
- `claude` CLI on PATH (`npm install -g @anthropic-ai/claude-code`)
- a Telegram bot token (from [@BotFather](https://t.me/BotFather))
- a Telegram group with forum/topics enabled

## install

```sh
uv python install 3.14
uv tool install -U pochi
# or try it with
uvx pochi@latest
```

## quick start

1. **create a Telegram bot** via [@BotFather](https://t.me/BotFather) and save the token

2. **create a Telegram group** with topics enabled:
   - create a new group
   - go to group settings → Topics → enable
   - add your bot to the group as admin

3. **get the group ID**: the easiest way is to add [@RawDataBot](https://t.me/RawDataBot) to the group temporarily, it will show the chat ID (remove it after)

4. **initialize a workspace**:
   ```sh
   mkdir my-workspace && cd my-workspace
   pochi init
   # or non-interactive:
   pochi init --bot-token "123:ABC" --group-id -100123456789
   ```

5. **run pochi**:
   ```sh
   pochi
   ```

6. **add folders** from the General topic in Telegram:
   ```
   /clone backend git@github.com:user/backend.git
   /create frontend
   /create knowledge-vault --no-git
   /add existing ~/dev/my-project
   ```

each folder gets its own topic. switch to a folder's topic to work on it.

## workspace config

config lives at `.pochi/workspace.toml`:

```toml
[workspace]
name = "my-workspace"
telegram_group_id = -100123456789
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
default_engine = "claude"  # Optional, defaults to "claude"

[folders.backend]
path = "backend"
topic_id = 123
origin = "git@github.com:user/backend.git"

[folders.frontend]
path = "frontend"
topic_id = 456

[folders.knowledge-vault]
path = "vault"
topic_id = 789
description = "Team knowledge base"

[workers.ralph]
enabled = false
default_max_iterations = 3

# Per-engine configuration sections
[claude]
model = "opus"
allowed_tools = ["Bash", "Read", "Edit", "Write"]
```

Folders can be git repos or plain directories. The type is auto-detected based on `.git` presence.

## commands

### CLI

```sh
pochi init [folder]     # initialize workspace (prompts for bot token + group ID)
pochi                   # run the bot
pochi info              # show workspace info
pochi --version         # show version
pochi --debug           # enable debug logging
pochi --no-final-notify # edit progress message instead of sending new final message
```

### Telegram (General topic)

| command | description |
|---------|-------------|
| `/clone <name> <url> [path]` | clone a git repo and create a topic |
| `/create <name> [--no-git]` | create a new folder (with git init by default) |
| `/add <name> <path>` | add an existing folder to the workspace |
| `/list` | list all folders in the workspace |
| `/remove <name>` | remove folder from workspace (keeps files) |
| `/status` | show workspace status |
| `/engine` | show current default engine and available engines |
| `/engine <name>` | set default engine for new conversations |
| `/help` | show available commands |

### Telegram (folder topics)

| command | description |
|---------|-------------|
| `/ralph <prompt> [--max-iterations N]` | start iterative Claude loop |
| `/cancel` | cancel the current run or ralph loop |

or just send a message to chat with your AI agent!

## ralph wiggum loops

ralph loops run Claude iteratively, asking it to review its own work after each iteration. Claude signals completion by responding with `RALPH_COMPLETE: <summary>`.

```
/ralph-loop implement user authentication with tests --max-iterations 5
```

enable always-on ralph mode in config:

```toml
[workers.ralph]
enabled = true
default_max_iterations = 3
```

## resume tokens

every response includes a resume line:

```
`claude --resume abc123`
```

- reply to a message with a resume line to continue that thread
- copy the command to resume interactively in your terminal
- resume tokens are scoped to topics: `topic:123:abc123`

## notes

- the bot only responds to messages in the configured group
- run only one pochi instance per bot token
- run `claude` once interactively in each folder to trust the directory

## acknowledgments

Built on top of [takopi](https://github.com/banteg/takopi).

## license

MIT
