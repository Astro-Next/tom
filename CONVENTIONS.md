# Conventions

## Python

- Python 3.12+
- Type hints on all function signatures
- `async def` for anything that touches IO (GitHub API, subprocess, file download)

## Naming

- Files: `snake_case.py`
- Functions/methods: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private: prefix with `_`

## Data

- Dataclasses for structured data (config, agent outputs)
- TypedDict when you need dict compatibility (JSON serialization)
- No Pydantic — keep dependencies minimal

## Error handling

- Raise exceptions at boundaries (config validation, CLI input)
- Return `None` or result types internally — don't wrap everything in try/except
- Log and continue for non-fatal failures (notification failures, single attachment download)
- Let unexpected errors propagate — don't swallow tracebacks

## Async

- `asyncio` for concurrency — no threads
- `httpx.AsyncClient` for HTTP
- `asyncio.create_subprocess_exec` for subprocesses
- One shared `httpx.AsyncClient` per `GitHubClient` instance; short-lived clients are acceptable for isolated operations (notifications, attachment downloads)

## Testing

- `pytest` + `pytest-asyncio`
- Co-locate: `src/tom/config.py` → `tests/test_config.py`
- Mock external IO (httpx, subprocess), not internal functions
- No fixtures that hit real GitHub unless explicitly marked integration

## Project structure

- One module per concern — don't stuff unrelated logic into the same file
- No circular imports — if two modules need each other, extract the shared part
- CLI is the entry point; it calls into library code, never the reverse

## Dependencies

- `uv` for package management — `uv run`, `uv add`, no pip
- Minimal dependencies: httpx, click, pytest, pytest-asyncio
- No Pydantic, no ORM, no web framework

## CLI

- `click` for all commands
- Commands are thin — validate input, call library code, print output
- Exit code 0 on success, 1 on failure
- Errors print to stderr, results print to stdout

## Schemas

- JSON schemas passed to `claude --json-schema` must not use `oneOf`, `allOf`, or `anyOf` — the Claude API rejects them. Use a flat object with a discriminator field instead.

## Style

- No docstrings unless the function signature is genuinely unclear
- No comments unless the why is non-obvious
- Imports: stdlib → third-party → local, separated by blank lines

## Releasing

- Version lives in one place: `__version__` in `src/tom/__init__.py`. `pyproject.toml` derives it via `dynamic = ["version"]` — never put a version there
- Bump: edit `__version__`, then `uv sync --reinstall-package tom` so installed metadata rebuilds (plain `uv sync` reuses the cache — CLI reads source live, but `importlib.metadata` won't update without a rebuild)
- Tag the release commit: `git tag -a vX.Y.Z -m "Tom vX.Y.Z"`
