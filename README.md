# Tom

> **Triage. Orchestrate. Manage.**

```
This is Major Tom to Ground Control, I'm stepping through the door.
— David Bowie, Space Oddity
```

Tom monitors your GitHub repository, triages incoming issues, writes code, reviews pull requests, and merges them — autonomously. When something needs a human decision, it escalates and moves on.

## Prerequisites

- **Python 3.12+**
- **Git**
- **GitHub CLI (`gh`)** — installed and authenticated (`gh auth login`)
- **Claude Code CLI** — installed and in PATH (`npm install -g @anthropic-ai/claude-code`)

## Quick start

```bash
pip install tom

cd your-repo
tom init       # set up config and GitHub labels
tom doctor     # verify everything is ready
tom start      # launch Tom
```

See [Getting started](GETTING_STARTED.md) for the full setup guide and [Configuration](CONFIGURATION.md) for settings.

## Development

```bash
git clone <repo-url>
cd tom
uv sync
uv run pytest tests/ -v
uv run tom --help
```

## License

MIT
