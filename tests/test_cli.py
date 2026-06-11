import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from tom.cli import main


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """Create a temp git repo with a GitHub remote."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:test/repo.git"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


class TestInit:
    def test_creates_all_files(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0

        assert (git_repo / ".tom" / "settings.json").exists()
        settings = json.loads((git_repo / ".tom" / "settings.json").read_text())
        assert "id" in settings
        assert settings["patrol"]["interval"] == "30m"

        assert (git_repo / "CLAUDE.md").exists()
        assert (git_repo / "CONVENTIONS.md").exists()
        assert (git_repo / "docs" / "index.md").exists()

    def test_skips_existing_files(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        (git_repo / "CLAUDE.md").write_text("custom content")

        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert (git_repo / "CLAUDE.md").read_text() == "custom content"
        assert "already exists" in result.output

    def test_skips_existing_settings(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        tom_dir = git_repo / ".tom"
        tom_dir.mkdir()
        (tom_dir / "settings.json").write_text('{"id": "existing"}')

        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert json.loads((tom_dir / "settings.json").read_text())["id"] == "existing"

    def test_adds_worktrees_to_gitignore(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert ".worktrees/" in (git_repo / ".gitignore").read_text()

    def test_does_not_duplicate_gitignore_entry(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        (git_repo / ".gitignore").write_text(".worktrees/\n")

        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        content = (git_repo / ".gitignore").read_text()
        assert content.count(".worktrees/") == 1

    def test_fails_without_git(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code != 0
        assert "Not a git repository" in result.output

    def test_fails_without_remote(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code != 0
        assert "No remote" in result.output

    def test_settings_json_is_pretty_printed(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        runner = CliRunner()
        runner.invoke(main, ["init"])
        content = (git_repo / ".tom" / "settings.json").read_text()
        assert "\n  " in content  # indented


class TestDoctor:
    def test_passes_in_valid_project(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        # Set up a valid project
        runner = CliRunner()
        runner.invoke(main, ["init"])

        # Mock external checks
        monkeypatch.setattr("tom.cli.shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)

        # Mock the async GitHub checks by patching asyncio.run in doctor
        def mock_asyncio_run(coro):
            return True, None, []

        monkeypatch.setattr("tom.cli.asyncio.run", mock_asyncio_run)

        result = runner.invoke(main, ["doctor"])
        assert "pass: Git repository" in result.output
        assert "pass: GitHub remote" in result.output
        assert "pass: CLAUDE.md" in result.output
        assert "pass: CONVENTIONS.md" in result.output

    def test_fails_missing_settings(self, git_repo, monkeypatch):
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert "FAIL: .tom/settings.json" in result.output

    def test_fails_missing_claude_cli(self, git_repo, monkeypatch):
        monkeypatch.setattr("tom.cli.asyncio.run", lambda x: None)

        runner = CliRunner()
        runner.invoke(main, ["init"])

        monkeypatch.setattr("tom.cli.shutil.which", lambda cmd: None)
        monkeypatch.setattr("tom.cli.asyncio.run", lambda coro: (True, None, []))

        result = runner.invoke(main, ["doctor"])
        assert "FAIL: claude CLI" in result.output

    def test_fails_missing_project_files(self, git_repo, monkeypatch):
        tom_dir = git_repo / ".tom"
        tom_dir.mkdir()
        from tom.config import generate_default_settings
        (tom_dir / "settings.json").write_text(json.dumps(generate_default_settings()))

        monkeypatch.setattr("tom.cli.shutil.which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("tom.cli.asyncio.run", lambda coro: (True, None, []))

        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert "FAIL: CLAUDE.md" in result.output
        assert "FAIL: CONVENTIONS.md" in result.output
        assert "FAIL: docs/index.md" in result.output

    def test_exit_code_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code != 0
