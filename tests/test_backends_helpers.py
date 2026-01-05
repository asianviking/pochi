"""Tests for pochi.backends_helpers module."""

from __future__ import annotations

from pochi.backends_helpers import install_issue


class TestInstallIssue:
    """Tests for install_issue function."""

    def test_with_install_cmd(self) -> None:
        """Test install_issue with install command provided."""
        issue = install_issue("myengine", "pip install myengine")
        assert issue.title == "install myengine"
        assert len(issue.lines) == 1
        assert "pip install myengine" in issue.lines[0]

    def test_without_install_cmd(self) -> None:
        """Test install_issue without install command."""
        issue = install_issue("myengine", None)
        assert issue.title == "install myengine"
        assert len(issue.lines) == 1
        assert "See engine setup docs" in issue.lines[0]

    def test_different_commands(self) -> None:
        """Test install_issue with different commands."""
        # npm command
        npm_issue = install_issue("codex", "npm install -g codex")
        assert "npm install -g codex" in npm_issue.lines[0]

        # cargo command
        cargo_issue = install_issue("rust-agent", "cargo install rust-agent")
        assert "cargo install rust-agent" in cargo_issue.lines[0]
