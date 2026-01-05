from pochi.bridge import _build_bot_commands, _strip_engine_command
from pochi.model import EngineId, ResumeToken
from pochi.render import MarkdownParts, prepare_telegram
from pochi.router import AutoRouter, RunnerEntry
from pochi.runners.claude import ClaudeRunner
from pochi.runners.mock import Return, ScriptRunner

CLAUDE_ENGINE = EngineId("claude")


def _make_router(runner) -> AutoRouter:
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


def test_claude_extract_resume_finds_command() -> None:
    session_id = "abc123"
    runner = ClaudeRunner(claude_cmd="claude")
    text = f"`claude --resume {session_id}`"

    assert runner.extract_resume(text) == ResumeToken(
        engine=CLAUDE_ENGINE, value=session_id
    )


def test_claude_extract_resume_accepts_plain_line() -> None:
    session_id = "abc123"
    runner = ClaudeRunner(claude_cmd="claude")
    text = f"claude --resume {session_id}"

    assert runner.extract_resume(text) == ResumeToken(
        engine=CLAUDE_ENGINE, value=session_id
    )


def test_prepare_telegram_trims_body_preserves_footer() -> None:
    body_limit = 3500
    parts = MarkdownParts(
        header="header",
        body="x" * (body_limit + 100),
        footer="footer",
    )

    rendered, _ = prepare_telegram(parts)

    chunks = [chunk for chunk in rendered.split("\n\n") if chunk]
    assert chunks[0] == "header"
    assert chunks[-1].rstrip() == "footer"
    assert len(chunks[1]) == body_limit
    assert chunks[1].endswith("…")


def test_prepare_telegram_preserves_entities_on_truncate() -> None:
    body_limit = 3500
    parts = MarkdownParts(
        header="h",
        body="**bold** " + ("x" * (body_limit + 100)),
    )

    _, entities = prepare_telegram(parts)

    assert any(e.get("type") == "bold" for e in entities)


def test_strip_engine_command_inline() -> None:
    text, engine = _strip_engine_command("/claude do it", engine_ids=("claude",))
    assert engine == "claude"
    assert text == "do it"


def test_strip_engine_command_newline() -> None:
    text, engine = _strip_engine_command("/claude\nhello", engine_ids=("claude",))
    assert engine == "claude"
    assert text == "hello"


def test_strip_engine_command_ignores_unknown() -> None:
    text, engine = _strip_engine_command("/unknown hi", engine_ids=("claude",))
    assert engine is None
    assert text == "/unknown hi"


def test_strip_engine_command_bot_suffix() -> None:
    text, engine = _strip_engine_command(
        "/claude@bunny_agent_bot hi", engine_ids=("claude",)
    )
    assert engine == "claude"
    assert text == "hi"


def test_strip_engine_command_only_first_non_empty_line() -> None:
    text, engine = _strip_engine_command("hello\n/claude hi", engine_ids=("claude",))
    assert engine is None
    assert text == "hello\n/claude hi"


def test_build_bot_commands_includes_cancel_and_engine() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CLAUDE_ENGINE, resume_value="sid"
    )
    router = _make_router(runner)
    commands = _build_bot_commands(router)

    assert {"command": "cancel", "description": "cancel current run"} in commands
    assert any(cmd["command"] == "claude" for cmd in commands)


def test_cancel_command_accepts_extra_text() -> None:
    from pochi.bridge import _is_cancel_command

    assert _is_cancel_command("/cancel now") is True
    assert _is_cancel_command("/cancel@pochi please") is True
    assert _is_cancel_command("/cancelled") is False
