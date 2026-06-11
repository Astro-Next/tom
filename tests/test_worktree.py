import asyncio
import subprocess
from pathlib import Path

import pytest

from tom.worktree import (
    _slugify,
    cleanup_worktree,
    create_dev_worktree,
    create_redispatch_worktree,
    create_review_worktree,
)


class TestSlugify:
    def test_simple(self):
        assert _slugify("Add OAuth login") == "add-oauth-login"

    def test_special_chars(self):
        assert _slugify("Fix bug #42: crash on startup!") == "fix-bug-42-crash-on-startup"

    def test_truncates_long_titles(self):
        title = "This is a very long issue title that should be truncated to fit"
        slug = _slugify(title)
        assert len(slug) <= 40
        assert not slug.endswith("-")

    def test_empty_title(self):
        assert _slugify("") == "issue"

    def test_only_special_chars(self):
        assert _slugify("!@#$%") == "issue"

    def test_unicode(self):
        result = _slugify("Fix l'erreur dans le code")
        assert "fix" in result
        assert "erreur" in result


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare 'origin' repo and a local clone with an initial commit."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], capture_output=True, check=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(origin), str(local)], capture_output=True, check=True)

    # Create initial commit on main
    (local / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], cwd=local, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com", "commit", "-m", "init"],
        cwd=local, capture_output=True, check=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=local, capture_output=True, check=True)

    return local


class TestCreateDevWorktree:
    @pytest.mark.asyncio
    async def test_creates_worktree_and_branch(self, git_repo):
        wt = await create_dev_worktree(git_repo, 42, "Add OAuth login", "main")

        assert wt.exists()
        assert wt == git_repo / ".worktrees" / "dev-42"

        # Check branch name
        result = subprocess.run(
            ["git", "branch", "--show-current"], cwd=wt, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "dev/42-add-oauth-login"

    @pytest.mark.asyncio
    async def test_recreates_if_exists(self, git_repo):
        wt1 = await create_dev_worktree(git_repo, 42, "First", "main")
        assert wt1.exists()

        wt2 = await create_dev_worktree(git_repo, 42, "Second", "main")
        assert wt2.exists()


class TestCreateRedispatchWorktree:
    @pytest.mark.asyncio
    async def test_creates_on_existing_branch(self, git_repo):
        # Create a branch on origin to simulate an existing PR branch
        subprocess.run(
            ["git", "branch", "dev/42-fix-bug"],
            cwd=git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "dev/42-fix-bug"],
            cwd=git_repo, capture_output=True, check=True,
        )

        wt = await create_redispatch_worktree(git_repo, 42, "dev/42-fix-bug")

        assert wt.exists()
        assert wt == git_repo / ".worktrees" / "dev-42"


class TestCreateReviewWorktree:
    @pytest.mark.asyncio
    async def test_creates_on_pr_branch(self, git_repo):
        subprocess.run(
            ["git", "branch", "dev/10-feature"],
            cwd=git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "dev/10-feature"],
            cwd=git_repo, capture_output=True, check=True,
        )

        wt = await create_review_worktree(git_repo, 10, "dev/10-feature")

        assert wt.exists()
        assert wt == git_repo / ".worktrees" / "review-10"


class TestCleanupWorktree:
    @pytest.mark.asyncio
    async def test_removes_worktree(self, git_repo):
        wt = await create_dev_worktree(git_repo, 42, "test", "main")
        assert wt.exists()

        await cleanup_worktree(git_repo, wt)
        assert not wt.exists()

    @pytest.mark.asyncio
    async def test_noop_if_not_exists(self, git_repo):
        fake = git_repo / ".worktrees" / "nonexistent"
        await cleanup_worktree(git_repo, fake)
