# Agent Instructions

## Project Overview

Tom is an autonomous software development daemon. It monitors a GitHub repository, triages issues, dispatches AI agents to write code and review PRs, and runs retrospectives. Python 3.12+, asyncio, httpx, click.

## Build & Test

- Install: `uv sync`
- Test: `uv run pytest tests/ -v`
- Run CLI: `uv run tom --help`

## Key Files

- `src/tom/cli.py` — CLI entry point (init, start, stop, doctor, labels, patrol, retro)
- `src/tom/config.py` — settings loading and validation
- `src/tom/models.py` — config data types
- `src/tom/github.py` — GitHub REST API client
- `src/tom/patrol.py` — patrol loop (7 steps)
- `src/tom/retro.py` — retro loop
- `src/tom/agents.py` — Claude Code subprocess launcher and output parser
- `src/tom/prompts.py` — prompt templates for all 4 agent types
- `src/tom/schemas.py` — JSON schemas for agent outputs
- `src/tom/worktree.py` — git worktree lifecycle
- `src/tom/attachments.py` — attachment download and cache
- `src/tom/labels.py` — GitHub label definitions and sync
- `src/tom/process.py` — daemon lifecycle (lock files, PID, signals)
- `src/tom/log.py` — logging setup

## Project Context

- `docs/index.md` — project knowledge and documentation
- `CONVENTIONS.md` — coding patterns and standards
