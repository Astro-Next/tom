# Getting Started

## Prerequisites

- **Python 3.12+** — Tom requires Python 3.12 or later for `asyncio.TaskGroup`
- **Git** — for worktree management
- **GitHub CLI (`gh`)** — must be installed and authenticated with `gh auth login`. Tom reads the token from `gh` for API access, and agents use `gh` to read issues and PRs.
- **Claude Code CLI** — the `claude` command must be available in PATH. Install via `npm install -g @anthropic-ai/claude-code` or use the standalone installer. Tom assumes `claude` is already installed and authenticated — verify with `claude -p "hello"` before starting.

## Installation

```bash
pip install tom
```

Or for development:

```bash
git clone <tom-repo-url>
cd tom
pip install -e .
```

## Initialize a project

Navigate to your git repository and run:

```bash
tom init
```

Tom reads the `origin` remote to determine the GitHub owner and repo, so no arguments are needed. This creates:
- `.tom/settings.json` — configuration file with defaults
- `CLAUDE.md` — template for agent instructions (edit this)
- `CONVENTIONS.md` — template for coding standards (edit this)
- `docs/index.md` — template for project knowledge base (edit this)
- GitHub labels on the repository (workflow, priority, type, structural)

## Configure

Edit `.tom/settings.json` to adjust patrol/retro schedules, concurrency limits, and retry settings. See [Configuration](configuration.md) for the full schema.

**Edit project files** — these are what agents read to understand your codebase:

- **CLAUDE.md** — project-specific rules for agents (build commands, test commands, special constraints)
- **CONVENTIONS.md** — your coding patterns, naming conventions, style rules. Agents follow these when writing and reviewing code.
- **docs/index.md** — project overview, architecture, key decisions. Helps agents understand the domain.

The more complete these files are, the better agents perform. See [Templates](templates.md) for the default content of each file and what to fill in.

## Re-sync GitHub labels (optional)

`tom init` already created the labels. If you ever need to recreate or update them (for example after editing a label by hand), run:

```bash
tom labels
```

This creates or updates all labels Tom uses (workflow, priority, type, structural).

## Verify setup

```bash
tom doctor
```

This checks everything: config, token, labels, CLI tools, project files. Fix any failures before starting.

## Start Tom

```bash
tom start
```

Tom now runs the patrol and retro processes in the background. They will:
- Run patrol every 30 minutes (or your configured interval)
- Run retro daily at 22:00 (or your configured time)

You can start just one process with `tom start patrol` or `tom start retro`.

To trigger an immediate first run:

```bash
tom start --run-now
```

## Test with a single cycle

Before starting the background processes, you can test with one-shot commands:

```bash
tom patrol    # run one patrol cycle
tom retro     # run one retro cycle
```

## Create your first issue

1. Open a GitHub issue on your repository with a clear title and description
2. Wait for the next patrol cycle (or run `tom patrol`)
3. Watch Tom:
   - Triage the issue (PM agent decides, Tom labels it)
   - Dispatch a dev agent (creates a branch, writes code, opens a PR)
   - Dispatch a review agent (reviews the PR, approves or requests changes)
   - If approved: PR is merged, issue is closed

## Stop Tom

```bash
tom stop
```

This sends SIGTERM to each process, which shuts down gracefully: it cancels pending tasks, waits for running agents to finish, and removes the lock file. Use `tom stop patrol` or `tom stop retro` to stop just one.
