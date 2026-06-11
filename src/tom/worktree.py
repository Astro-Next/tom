from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

_log = logging.getLogger("tom")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 40


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "issue"


async def _git(args: list[str], cwd: str | Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode(errors='replace').strip()}")
    return stdout.decode(errors="replace").strip()


async def fetch_origin(project_root: str | Path) -> None:
    await _git(["fetch", "origin"], cwd=project_root)


async def create_dev_worktree(
    project_root: str | Path,
    issue_number: int,
    issue_title: str,
    default_branch: str,
) -> Path:
    root = Path(project_root)
    worktree_dir = root / ".worktrees" / f"dev-{issue_number}"
    branch = f"dev/{issue_number}-{_slugify(issue_title)}"

    if worktree_dir.exists():
        await cleanup_worktree(project_root, worktree_dir)

    await _git(
        ["worktree", "add", "-b", branch, str(worktree_dir), f"origin/{default_branch}"],
        cwd=project_root,
    )
    _log.info("Created dev worktree: %s on branch %s", worktree_dir, branch)
    return worktree_dir


async def create_redispatch_worktree(
    project_root: str | Path,
    issue_number: int,
    pr_head_branch: str,
) -> Path:
    root = Path(project_root)
    worktree_dir = root / ".worktrees" / f"dev-{issue_number}"

    if worktree_dir.exists():
        await cleanup_worktree(project_root, worktree_dir)

    await _git(["fetch", "origin", pr_head_branch], cwd=project_root)
    await _git(
        ["worktree", "add", "-B", pr_head_branch, str(worktree_dir), f"origin/{pr_head_branch}"],
        cwd=project_root,
    )
    _log.info("Created redispatch worktree: %s on branch %s", worktree_dir, pr_head_branch)
    return worktree_dir


async def create_review_worktree(
    project_root: str | Path,
    pr_number: int,
    pr_head_branch: str,
) -> Path:
    root = Path(project_root)
    worktree_dir = root / ".worktrees" / f"review-{pr_number}"

    if worktree_dir.exists():
        await cleanup_worktree(project_root, worktree_dir)

    await _git(["fetch", "origin", pr_head_branch], cwd=project_root)
    await _git(
        ["worktree", "add", str(worktree_dir), f"origin/{pr_head_branch}"],
        cwd=project_root,
    )
    _log.info("Created review worktree: %s on branch %s", worktree_dir, pr_head_branch)
    return worktree_dir


async def cleanup_worktree(project_root: str | Path, worktree_dir: Path) -> None:
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir)
        _log.debug("Removed worktree directory: %s", worktree_dir)
    await _git(["worktree", "prune"], cwd=project_root)
    _log.debug("Pruned worktrees")
