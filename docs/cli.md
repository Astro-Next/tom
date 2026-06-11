# CLI

Tom provides a command-line interface for setup, operation, and debugging.

## `tom init`

Scaffold a new Tom project in the current directory.

Must be run inside a git repository with a GitHub remote. Tom reads the remote `origin` URL to determine the owner and repo — no arguments are required. If there is no git repository or no GitHub remote, it exits with an error.

Creates:
- `.tom/settings.json` with defaults and a generated project ID
- `CLAUDE.md` (if not present) — template with placeholder instructions
- `CONVENTIONS.md` (if not present) — template for coding standards
- `docs/index.md` (if not present) — template knowledge base entry
- GitHub labels on the repository (the same set as `tom labels`)

Adds to `.gitignore`:
- `.worktrees/`

## `tom start`

Start Tom. Launches the patrol process and the retro process, each as an independent background process with its own lock file under `~/.tom/{project-id}/`.

- `tom start` — start both processes
- `tom start patrol` — start only the patrol process
- `tom start retro` — start only the retro process

For each process, if its lock file exists and the recorded PID is still running, `tom start` refuses to start it (use `--replace` to stop the existing one and start fresh). If the lock file exists but the process is dead, Tom reports the stale lock and asks you to remove it or pass `--replace`.

**Flags:**
- `--replace` — stop the existing process and start a new one
- `--run-now` — trigger an immediate cycle after starting

## `tom stop`

Stop Tom.

- `tom stop` — stop both processes
- `tom stop patrol` — stop only the patrol process
- `tom stop retro` — stop only the retro process

For each targeted process, Tom reads the PID from its lock file, sends SIGTERM, waits for graceful shutdown, and removes the lock file.

## `tom doctor`

Check that the project is correctly set up for Tom.

Checks:
- Git repository exists
- Git remote origin is configured and points to a GitHub repository
- `.tom/settings.json` exists and is valid
- `gh` CLI is installed and authenticated (`gh auth token` returns a valid token)
- GitHub API is reachable and token has correct permissions
- GitHub labels exist on the repository
- `claude` CLI is installed and accessible
- `CLAUDE.md` exists
- `CONVENTIONS.md` exists
- `docs/index.md` exists

Reports each check as pass/fail with actionable error messages.

## `tom labels`

Create or update GitHub labels on the repository.

Creates all labels Tom uses with their colors:
- `need-dev`, `in-dev`, `need-review`, `in-review` (workflow)
- `blocked` (escalation)
- `parent` (structural)
- `p0`, `p1`, `p2` (priority)
- `feature`, `bug` (type)

If a label already exists, updates its color and description to match.

**Flags:**
- `--clean-labels` — delete labels on the repository that are not managed by Tom

## `tom patrol`

Run a single patrol cycle and exit. Does not start the daemon.

Useful for testing and debugging. Executes the full patrol workflow (steps 1-7) once, prints the summary, and exits.

## `tom retro`

Run a single retro cycle and exit. Does not start the daemon.

Useful for testing and debugging. Executes the full retro workflow once, prints the summary, and exits.
