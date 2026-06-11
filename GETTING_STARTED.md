# Getting Started

## Install

```bash
uv sync
```

## Initialize

Navigate to your git repository (must have a GitHub remote) and run:

```bash
tom init
```

This creates:

- `.tom/settings.json` — configuration with defaults
- `CLAUDE.md` — agent instructions (edit this)
- `CONVENTIONS.md` — coding standards (edit this)
- `docs/index.md` — project knowledge base (edit this)
- GitHub labels on the repository

## Configure your project

Edit the three project files — these are what agents read to understand your codebase:

- **CLAUDE.md** — build commands, test commands, key files, project-specific rules
- **CONVENTIONS.md** — naming conventions, patterns, style rules
- **docs/index.md** — architecture, domain knowledge, key decisions

The more complete these files are, the better agents perform.

## Verify

```bash
tom doctor
```

Checks git, GitHub remote, config, `gh` auth, labels, `claude` CLI, and project files. Fix any failures before starting.

## Start

```bash
tom start
```

Launches patrol and retro as background processes. Patrol runs every 30 minutes, retro runs daily at 22:00 (configurable in `.tom/settings.json`).

Start a single process:

```bash
tom start patrol
tom start retro
```

Trigger an immediate cycle:

```bash
tom start --run-now
```

## Check status

```bash
tom status
```

## Test with a single cycle

```bash
tom patrol    # run one patrol cycle and exit
tom retro     # run one retro cycle and exit
```

## Stop

```bash
tom stop
```

Stop a single process:

```bash
tom stop patrol
tom stop retro
```

## CLI reference

| Command | Description |
|---------|-------------|
| `tom init` | Scaffold a new project |
| `tom doctor` | Verify setup |
| `tom labels` | Create or update GitHub labels (`--clean-labels` to remove unmanaged labels) |
| `tom start` | Start background processes (`--replace` to restart, `--run-now` for immediate cycle) |
| `tom stop` | Stop background processes |
| `tom status` | Show running status |
| `tom patrol` | Run one patrol cycle |
| `tom retro` | Run one retro cycle |
