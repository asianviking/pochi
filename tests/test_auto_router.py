from pochi.model import ResumeToken
from pochi.router import AutoRouter, RunnerEntry
from pochi.runners.claude import ClaudeRunner


def _router() -> tuple[AutoRouter, ClaudeRunner]:
    claude = ClaudeRunner(claude_cmd="claude")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=claude.engine, runner=claude),
        ],
        default_engine=claude.engine,
    )
    return router, claude


def test_router_resolves_text_before_reply() -> None:
    router, _claude = _router()
    token = router.resolve_resume("`claude --resume abc`", "`claude --resume def`")

    assert token == ResumeToken(engine="claude", value="abc")


def test_router_resolves_reply_text_when_text_missing() -> None:
    router, _claude = _router()

    token = router.resolve_resume(None, "`claude --resume xyz`")

    assert token == ResumeToken(engine="claude", value="xyz")


def test_router_is_resume_line() -> None:
    router, _claude = _router()

    assert router.is_resume_line("claude --resume abc")
    assert router.is_resume_line("`claude --resume def`")
