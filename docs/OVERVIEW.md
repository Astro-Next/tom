# Tom

> **Triage. Orchestrate. Manage.**

Tom is an autonomous software development daemon. It monitors a GitHub repository, triages incoming issues, dispatches AI agents to write code and review pull requests, and runs periodic retrospectives to improve project practices.

Tom runs as long-lived background processes on a local machine. It polls GitHub on a configurable interval, advances issues through a label-based state machine, and spawns one-shot Claude Code subprocesses to do the work that requires judgment — triaging issues, implementing features, reviewing PRs, and analyzing merged work.

Everything deterministic (label transitions, concurrency limits, retry counting, dispatch ordering, scheduling) is plain Python code. The LLM is only invoked for tasks that require reading, reasoning, and writing.

## How it works

Tom runs two independent processes, each an async loop:

- **Patrol** — scans all open issues, triages new ones, checks agent progress, dispatches dev and review agents, manages epics, and posts a summary.
- **Retro** — analyzes recently merged code for problems, risks, and stale or missing documentation, and creates a single follow-up issue for human review.

GitHub is the single source of truth. All state lives in issue labels and comments. If Tom restarts, it reconstructs everything from GitHub on the next patrol cycle. There is no database.

## Agents

Tom dispatches four types of agents, each a one-shot Claude Code subprocess:

| Agent | Job | Invoked by |
|-------|-----|------------|
| **PM** | Triage issues, assign labels, break epics into child issues | Patrol |
| **Dev** | Implement an issue — write code, tests, create a PR | Patrol |
| **Review** | Review a PR — approve and merge, or request changes | Patrol |
| **Analyst** | Analyze merged PRs, identify improvements, create follow-up issues | Retro |

## Tech stack

| Component | Tool |
|-----------|------|
| Language | Python 3.12+ |
| LLM engine | Claude Code CLI (`claude -p`) |
| GitHub API | REST API via `httpx` (async) |
| Scheduling | `asyncio` event loops |
| CLI | `click` |
| Config | JSON |

## Documentation

- [Architecture](docs/architecture.md) — daemon model, subprocess management, concurrency, state storage
- [Issue lifecycle](docs/issue-lifecycle.md) — state machine, labels, transitions, escalation, epics
- [Patrol](docs/patrol.md) — the patrol loop, step by step
- [Retro](docs/retro.md) — the retrospective loop
- [Attachments](docs/attachments.md) — how issue attachments are downloaded, cached, and read
- [PM agent](docs/agents/pm.md) — issue triage
- [Dev agent](docs/agents/dev.md) — code implementation
- [Review agent](docs/agents/review.md) — PR review and merge
- [Analyst agent](docs/agents/analyst.md) — retrospective analysis
- [Agent prompts](docs/prompts.md) — the exact prompt passed to each agent subprocess
- [Templates](docs/templates.md) — the project files created by `tom init`
- [Configuration](docs/configuration.md) — settings schema, project template
- [CLI](docs/cli.md) — commands reference
- [Getting started](docs/getting-started.md) — setup guide
