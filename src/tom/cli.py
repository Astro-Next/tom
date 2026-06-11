import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import click

from tom import __version__
from tom.config import generate_default_settings, load_settings
from tom.models import Settings

_CLAUDE_MD_TEMPLATE = """\
# Agent Instructions

## Project Overview
<!-- Describe what this project does, its purpose, and key technologies. -->

## Build & Test
<!-- Commands agents need to run:
- Build: `npm run build`
- Test: `npm test`
- Lint: `npm run lint`
-->

## Key Files
<!-- Important entry points and files agents should know about. -->

## Project Context
- `docs/index.md` — project knowledge and documentation
- `CONVENTIONS.md` — coding patterns and standards
"""

_CONVENTIONS_MD_TEMPLATE = """\
# Conventions
<!-- Add conventions as the project evolves. Examples:

## Naming
- Components: PascalCase
- Functions: camelCase
- Files: kebab-case

## Patterns
- Use existing utilities before creating new ones
- Error handling: use Result type, not try/catch

## Testing
- Co-locate tests: foo.ts -> foo.test.ts
- Use integration tests for API endpoints
-->
"""

_DOCS_INDEX_TEMPLATE = """\
<!-- # Project Knowledge

Entry point for project documentation.

## Architecture
- [overview](architecture.md) -- system design and key decisions

## Guides
- [local setup](guides/setup.md) -- dev environment, dependencies, first run
-->
"""


def _require_settings() -> Settings:
    path = Path.cwd() / ".tom" / "settings.json"
    if not path.exists():
        raise click.ClickException("No .tom/settings.json found. Are you in a Tom-managed repository?")
    return load_settings(path)


@click.group()
@click.version_option(
    version=__version__,
    prog_name="Tom — Triage. Orchestrate. Manage.",
    message="%(prog)s\nversion %(version)s",
)
def main() -> None:
    pass


@main.command()
def retro() -> None:
    """Run a single retro cycle and exit."""
    asyncio.run(_retro())


async def _retro() -> None:
    from tom.github import GitHubClient
    from tom.retro import run_retro

    root = Path.cwd()
    settings = _require_settings()

    client = GitHubClient()
    try:
        issue = await run_retro(settings, str(root), client)
        if issue:
            click.echo(f"Retro issue created: #{issue['number']}")
        else:
            click.echo("Retro: nothing to report.")
    finally:
        await client.close()


@main.command()
@click.argument("process", required=False, type=click.Choice(["patrol", "retro"]))
@click.option("--replace", is_flag=True, help="Stop existing process and start fresh.")
@click.option("--run-now", is_flag=True, help="Trigger an immediate cycle after starting.")
def start(process: str | None, replace: bool, run_now: bool) -> None:
    """Start Tom. Launches patrol and retro as background processes."""
    from tom.log import setup_logging
    from tom.process import is_running, stop_process

    root = Path.cwd()
    settings = _require_settings()

    targets = [process] if process else ["patrol", "retro"]

    for target in targets:
        running, pid = is_running(settings.id, target)
        if running:
            if replace:
                click.echo(f"Stopping existing {target} (pid {pid})...")
                stop_process(settings.id, target)
            else:
                raise click.ClickException(
                    f"{target} is already running (pid {pid}). Use --replace to restart."
                )

    setup_logging(settings.id)

    for target in targets:
        pid = _spawn_background(settings, str(root), target, run_now)
        click.echo(f"Started {target} (pid {pid}).")


def _spawn_background(settings: Settings, project_root: str, target: str, run_now: bool) -> int:
    import sys
    pid = os.fork()
    if pid > 0:
        return pid

    os.setsid()
    sys.stdin.close()

    if target == "patrol":
        from tom.github import GitHubClient

        async def _run():
            client = GitHubClient()
            try:
                default_branch = await client.get_default_branch()
            finally:
                await client.close()

            from tom.process import run_patrol_loop
            await run_patrol_loop(settings, project_root, default_branch, run_now=run_now)

        asyncio.run(_run())
    else:
        from tom.process import run_retro_loop
        asyncio.run(run_retro_loop(settings, project_root, run_now=run_now))

    os._exit(0)


@main.command()
@click.argument("process", required=False, type=click.Choice(["patrol", "retro"]))
def stop(process: str | None) -> None:
    """Stop Tom."""
    from tom.process import is_running, stop_process

    root = Path.cwd()
    settings = _require_settings()

    targets = [process] if process else ["patrol", "retro"]

    for target in targets:
        running, pid = is_running(settings.id, target)
        if running:
            stop_process(settings.id, target)
            click.echo(f"Stopped {target} (pid {pid}).")
        else:
            click.echo(f"{target} is not running.")


@main.command()
def status() -> None:
    """Show Tom's running status."""
    from tom.process import is_running

    settings = _require_settings()

    patrol_running, patrol_pid = is_running(settings.id, "patrol")
    retro_running, retro_pid = is_running(settings.id, "retro")

    if patrol_running:
        click.echo(f"patrol: running (pid {patrol_pid}) — interval {settings.patrol.interval}")
    else:
        click.echo("patrol: stopped")

    if retro_running:
        click.echo(f"retro:  running (pid {retro_pid}) — interval {settings.retro.interval} at {settings.retro.time}")
    else:
        click.echo("retro:  stopped")


@main.command()
def patrol() -> None:
    """Run a single patrol cycle and exit."""
    asyncio.run(_patrol())


async def _patrol() -> None:
    from tom.github import GitHubClient
    from tom.patrol import run_patrol

    root = Path.cwd()
    settings = _require_settings()

    client = GitHubClient()
    try:
        default_branch = await client.get_default_branch()
        active_processes: dict[int, asyncio.subprocess.Process] = {}

        summary = await run_patrol(settings, str(root), default_branch, client, active_processes)

        text = summary.format()
        if text:
            click.echo(text)
        else:
            click.echo("Patrol: nothing to report.")
    finally:
        await client.close()


@main.command()
@click.option("--clean-labels", is_flag=True, help="Delete labels not managed by Tom.")
def labels(clean_labels: bool) -> None:
    """Create or update GitHub labels on the repository."""
    asyncio.run(_labels(clean=clean_labels))


async def _labels(*, clean: bool = False) -> None:
    from tom.github import GitHubClient
    from tom.labels import sync_labels

    client = GitHubClient()
    try:
        await sync_labels(client, clean=clean)
        click.echo("Labels synced.")
    finally:
        await client.close()


@main.command()
@click.option("--clean-labels", is_flag=True, help="Delete labels not managed by Tom.")
def init(clean_labels: bool) -> None:
    """Scaffold a new Tom project in the current directory."""
    root = Path.cwd()

    if not (root / ".git").is_dir():
        raise click.ClickException("Not a git repository. Run 'git init' first.")

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
        url = result.stdout.strip()
        if "github.com" not in url:
            raise click.ClickException(f"Remote origin is not a GitHub URL: {url}")
    except subprocess.CalledProcessError:
        raise click.ClickException("No remote 'origin' configured. Add a GitHub remote first.")

    tom_dir = root / ".tom"
    tom_dir.mkdir(exist_ok=True)
    settings_path = tom_dir / "settings.json"
    if settings_path.exists():
        click.echo(".tom/settings.json already exists -- skipping.")
    else:
        data = generate_default_settings()
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
        click.echo(f"Created .tom/settings.json (id: {data['id']})")

    _write_template(root / "CLAUDE.md", _CLAUDE_MD_TEMPLATE)
    _write_template(root / "CONVENTIONS.md", _CONVENTIONS_MD_TEMPLATE)
    docs_dir = root / "docs"
    docs_dir.mkdir(exist_ok=True)
    _write_template(docs_dir / "index.md", _DOCS_INDEX_TEMPLATE)

    gitignore = root / ".gitignore"
    entry = ".worktrees/"
    if gitignore.exists():
        content = gitignore.read_text()
        if entry not in content:
            with gitignore.open("a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{entry}\n")
            click.echo("Added .worktrees/ to .gitignore")
    else:
        gitignore.write_text(f"{entry}\n")
        click.echo("Created .gitignore with .worktrees/")

    click.echo("Syncing GitHub labels...")
    asyncio.run(_labels_sync(clean=clean_labels))

    click.echo("Done. Edit CLAUDE.md, CONVENTIONS.md, and docs/index.md to configure your project.")


async def _labels_sync(*, clean: bool = False) -> None:
    from tom.github import GitHubClient
    from tom.labels import sync_labels

    client = GitHubClient()
    try:
        await sync_labels(client, clean=clean)
    finally:
        await client.close()


def _write_template(path: Path, content: str) -> None:
    if path.exists():
        click.echo(f"{path.name} already exists -- skipping.")
    else:
        path.write_text(content)
        click.echo(f"Created {path.name}")


@main.command()
def doctor() -> None:
    """Check that the project is correctly set up for Tom."""
    root = Path.cwd()
    passed = 0
    failed = 0

    def check(name: str, ok: bool, fail_msg: str) -> None:
        nonlocal passed, failed
        if ok:
            click.echo(f"  pass: {name}")
            passed += 1
        else:
            click.echo(f"  FAIL: {name} -- {fail_msg}")
            failed += 1

    click.echo("Checking project setup...\n")

    check("Git repository", (root / ".git").is_dir(), "Not a git repo. Run 'git init'.")

    has_remote = False
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
        url = result.stdout.strip()
        has_remote = "github.com" in url
        check("GitHub remote", has_remote, f"Remote origin is not GitHub: {url}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        check("GitHub remote", False, "No remote 'origin' configured.")

    settings_path = root / ".tom" / "settings.json"
    settings_valid = False
    if settings_path.exists():
        try:
            load_settings(settings_path)
            settings_valid = True
        except (ValueError, json.JSONDecodeError) as e:
            check(".tom/settings.json", False, f"Invalid: {e}")
    if settings_valid:
        check(".tom/settings.json", True, "")
    elif not settings_path.exists():
        check(".tom/settings.json", False, "Not found. Run 'tom init'.")

    gh_ok = False
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        gh_ok = bool(result.stdout.strip())
        check("gh CLI authenticated", gh_ok, "gh auth token returned empty. Run 'gh auth login'.")
    except FileNotFoundError:
        check("gh CLI installed", False, "gh not found in PATH. Install GitHub CLI.")
    except subprocess.CalledProcessError:
        check("gh CLI authenticated", False, "gh auth token failed. Run 'gh auth login'.")

    if gh_ok and has_remote and settings_valid:
        try:
            from tom.github import GitHubClient
            from tom.labels import LABELS

            async def _check_api_and_labels():
                client = GitHubClient()
                try:
                    await client.get_repo()
                    api_ok = True
                    api_err = None
                except Exception as e:
                    api_ok = False
                    api_err = str(e)
                    await client.close()
                    return api_ok, api_err, None

                try:
                    existing = await client.list_labels()
                    names = {l["name"] for l in existing}
                    missing = [l.name for l in LABELS if l.name not in names]
                except Exception as e:
                    missing = None
                finally:
                    await client.close()
                return api_ok, api_err, missing

            api_ok, api_err, missing = asyncio.run(_check_api_and_labels())
            check("GitHub API reachable", api_ok, f"API error: {api_err}")
            if api_ok and missing is not None:
                check("GitHub labels", len(missing) == 0, f"Missing: {', '.join(missing)}. Run 'tom labels'.")
        except Exception as e:
            check("GitHub API", False, str(e))

    claude_ok = shutil.which("claude") is not None
    check("claude CLI", claude_ok, "claude not found in PATH. Install Claude Code CLI.")

    check("CLAUDE.md", (root / "CLAUDE.md").exists(), "Not found. Run 'tom init'.")
    check("CONVENTIONS.md", (root / "CONVENTIONS.md").exists(), "Not found. Run 'tom init'.")
    check("docs/index.md", (root / "docs" / "index.md").exists(), "Not found. Run 'tom init'.")

    click.echo(f"\n{passed} passed, {failed} failed")
    if failed > 0:
        raise SystemExit(1)
