# Implementation Plan

## Tech Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.12+ |
| Package manager | uv |
| Async HTTP | httpx |
| CLI | click |
| Scheduling | asyncio |
| LLM engine | Claude Code CLI (`claude -p`) |
| Config | JSON |
| Logging | logging + RotatingFileHandler |
| Testing | pytest + pytest-asyncio |

## File Structure

```
tom/
├── pyproject.toml
├── README.md
├── CLAUDE.md
├── CONVENTIONS.md
├── docs/
│   └── ...
├── src/
│   └── tom/
│       ├── __init__.py
│       ├── cli.py                 # click commands (init, start, stop, doctor, labels, patrol, retro)
│       ├── config.py              # load/validate .tom/settings.json, interval/time parsing
│       ├── github.py              # GitHub REST API client (httpx async, ETag caching, auth)
│       ├── notify.py              # Discord webhook
│       ├── patrol.py              # patrol loop (7 steps)
│       ├── retro.py               # retro loop
│       ├── agents.py              # spawn claude -p, parse structured output, timeout
│       ├── worktree.py            # git worktree create/cleanup/branch management
│       ├── attachments.py         # download + cache attachments
│       ├── labels.py              # label definitions + create/update on GitHub
│       ├── prompts.py             # prompt templates for PM, Dev, Review, Analyst
│       ├── schemas.py             # JSON schemas for agent output validation
│       ├── models.py              # dataclasses/TypedDicts (config, agent outputs, etc.)
│       ├── process.py             # daemon lifecycle (lock files, PID, SIGTERM, background)
│       └── log.py                 # logging setup (RotatingFileHandler, stderr in foreground)
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_github.py
│   ├── test_patrol.py
│   ├── test_retro.py
│   ├── test_agents.py
│   ├── test_worktree.py
│   ├── test_attachments.py
│   └── test_labels.py
└── .gitignore
```

## Phases

### Phase 1: Project skeleton + config + models

Set up the project foundation.

- `pyproject.toml` — uv project, dependencies (httpx, click), `[project.scripts] tom = "tom.cli:main"`, python 3.12+
- `src/tom/__init__.py` — version
- `src/tom/models.py` — all dataclasses/TypedDicts: `Settings`, `PatrolSettings`, `RetroSettings`, `DevSettings`, `ReviewSettings`, `NotifySettings`, agent output types (`PmResult`, `DevResult`, `ReviewResult`, `AnalystResult`, finding types)
- `src/tom/schemas.py` — JSON schemas matching each agent output type (used with `claude --json-schema`)
- `src/tom/config.py` — load `.tom/settings.json`, validate against schema, parse intervals (`30m`, `1h`, `2d`), parse time (`HH:MM`)
- `src/tom/log.py` — configure `RotatingFileHandler` to `~/.tom/{project-id}/logs/tom.log`, stderr handler for foreground mode
- `src/tom/cli.py` — click group with `tom --help`, no subcommands yet
- Tests for config loading, validation, interval parsing, model construction

**Milestone:** `uv run tom --help` works. Config loads and validates. All types defined and tested.

**Verify:**
- `uv run tom --help` prints the CLI help
- `uv run pytest tests/test_config.py` — all pass
- Load a valid settings.json → returns `Settings` dataclass with correct values
- Load invalid settings (bad interval format, missing fields, wrong types) → raises validation error
- Parse `"30m"` → 1800 seconds, `"2h"` → 7200, `"1d"` → 86400, `"22:00"` → valid time

### Phase 2: GitHub client + labels

- `src/tom/github.py` — async GitHub REST API client:
  - Auth: read token from `gh auth token`, retry with fresh token on 401
  - ETag caching: store ETags per URL, send `If-None-Match`, handle 304
  - API versioning: `X-GitHub-Api-Version` header
  - Methods: list issues (with label filters), get issue, create issue, close issue, add/remove labels, list comments, create comment, get PR, list PRs, create PR, update PR, create PR review, merge PR, delete branch
  - Parse git remote origin URL → owner/repo
- `src/tom/labels.py` — label definitions (name, color, description for all workflow/type/priority/structural labels), create-or-update via GitHub client
- `src/tom/cli.py` — add `tom labels` subcommand
- Tests with mocked httpx responses for all GitHub operations

**Milestone:** `tom labels` creates/updates labels on a real repo.

**Verify:**
- `uv run pytest tests/test_github.py tests/test_labels.py` — all pass
- Mock tests: every GitHub method returns expected data, handles 304/401/404 correctly
- ETag caching: second request sends `If-None-Match`, returns cached data on 304
- Auth retry: simulate 401 → re-read token → retry succeeds
- `uv run tom labels` against a test repo → labels appear on GitHub with correct names and colors
- Run `tom labels` again → no errors, labels unchanged (idempotent)

### Phase 3: `tom init` + `tom doctor`

- `src/tom/cli.py` — add `tom init`:
  - Verify git repo with GitHub remote
  - Create `.tom/settings.json` with defaults + generated project ID
  - Create `CLAUDE.md`, `CONVENTIONS.md`, `docs/index.md` from templates (if not present)
  - Append `.worktrees/` to `.gitignore`
  - Create GitHub labels (reuse labels.py)
- `src/tom/cli.py` — add `tom doctor`:
  - Check: git repo, remote origin, `.tom/settings.json` valid, `gh` installed + authenticated, GitHub API reachable + permissions, labels exist, `claude` CLI in PATH, `CLAUDE.md` exists, `CONVENTIONS.md` exists, `docs/index.md` exists
  - Report each as pass/fail with actionable message
- Tests for init (temp git repo), doctor checks

**Milestone:** `tom init` scaffolds a project. `tom doctor` validates setup.

**Verify:**
- `uv run pytest` — all pass (init and doctor tests)
- In a fresh temp git repo with a GitHub remote: `tom init` creates `.tom/settings.json`, `CLAUDE.md`, `CONVENTIONS.md`, `docs/index.md`, adds `.worktrees/` to `.gitignore`, creates labels
- Run `tom init` again → skips existing files, no duplicates
- `tom doctor` in a valid project → all checks pass
- `tom doctor` with missing `gh` → reports failure with actionable message
- `tom doctor` with missing `.tom/settings.json` → reports failure
- `tom doctor` with invalid config → reports what's wrong

### Phase 4: Attachments + agent subprocess

- `src/tom/attachments.py`:
  - Scan issue/PR body + comments for attachment URLs (image markup, file links)
  - Hash URL → `shasum` first 12 chars
  - Extension rules (from URL path, default `.png` for image markup, skip bare links)
  - Download with `GITHUB_TOKEN` auth via httpx to `/tmp/tom-{project-id}/cache/{issue_number}/{hash}.{ext}`
  - Skip if already cached, delete zero-byte failures
- `src/tom/agents.py`:
  - Launch `claude -p "<prompt>" --output-format json --json-schema <schema> --permission-mode bypassPermissions` via `asyncio.create_subprocess_exec`
  - Set `cwd` per agent type
  - Read stdout, parse `.structured_output` from JSON
  - Validate against schema
  - Timeout handling (configurable `agent.timeout`, terminate on exceed)
  - Return parsed result or failure reason (exit code, stderr tail)
- `src/tom/prompts.py` — all four prompt templates with `{placeholder}` substitution
- Tests with mock subprocess for agent launching, attachment URL extraction and caching

**Milestone:** Can spawn a claude subprocess, enforce timeout, parse structured JSON output. Attachments download and cache correctly.

**Verify:**
- `uv run pytest tests/test_agents.py tests/test_attachments.py` — all pass
- Attachment URL extraction: parse image markup (`<img>`, `![]()`), file links from sample issue bodies → correct URL list
- Hash + extension: known URL → expected hash and extension; image with no ext → `.png`; bare link with no ext → skipped
- Download: mock httpx → file written to correct cache path; already cached → no request; failed download → no file left on disk
- Agent launch: mock subprocess returning valid JSON → parsed `DevResult` with correct fields
- Agent timeout: mock subprocess hanging → terminated after timeout, returns failure
- Agent crash: mock subprocess exit code 1 → returns failure with stderr tail
- Agent bad JSON: mock subprocess returning garbage → returns parse failure
- Prompt templates: each template fills placeholders correctly (`{issue_number}`, `{pr_number}`, etc.)

### Phase 5: Git worktree management

- `src/tom/worktree.py`:
  - `git fetch origin` before any worktree creation
  - Resolve default branch from GitHub API (cached at startup)
  - Create worktree: new branch `dev/{issue_number}-{slug}` from `origin/{default_branch}` at `.worktrees/dev-{issue_number}`
  - Create worktree for re-dispatch: fetch PR head branch, worktree on existing branch
  - Create review worktree: fetch PR head branch at `.worktrees/review-{pr_number}`
  - Cleanup: remove worktree directory + `git worktree prune`
  - Slug generation from issue title (kebab-case, truncated)
- Tests with temp git repos

**Milestone:** Worktrees created on correct branches, cleaned up after use.

**Verify:**
- `uv run pytest tests/test_worktree.py` — all pass
- New dev worktree: creates `.worktrees/dev-{issue_number}` on branch `dev/{issue_number}-{slug}` from `origin/{default_branch}`
- Re-dispatch worktree: creates worktree on existing PR head branch (not a new branch)
- Review worktree: creates `.worktrees/review-{pr_number}` on PR head branch
- Cleanup: worktree directory removed, `git worktree list` no longer shows it
- Slug generation: `"Add OAuth login"` → `add-oauth-login`, long titles truncated
- Fetch: `git fetch origin` called before worktree creation

### Phase 6: Patrol loop

- `src/tom/patrol.py` — all 7 steps:
  1. **Triage** — find unlabeled issues, download attachments, spawn PM agent, apply labels/create children/block based on result
  2. **Check review progress** — find `in-review` issues, read completion comments, handle approved/changes-requested/failure/crash/timeout
  3. **Check dev progress** — find `in-dev` issues, read completion comments, handle PR-created/failure/crash/timeout, commit+push+create-PR on success
  4. **Dispatch review** — find `need-review` issues, check quota, find PR from comments, swap labels, create worktree, spawn review agent, post tracking comment
  5. **Dispatch dev** — find `need-dev` issues sorted by priority, check quota, determine new-vs-redispatch, swap labels, create worktree, spawn dev agent, post tracking comment
  6. **Check parents** — find `parent` issues, read children comment, close if all children closed
  7. **Cleanup + summary** — remove stale labels from closed issues, compose summary, notify
  - Fresh state per step (re-query GitHub each step)
  - Retry counting from issue comments
  - Concurrency control via label counting
  - Comment conventions (`dispatched dev`, `dev completed: PR #N`, `review result: approved`, etc.)
- `src/tom/cli.py` — add `tom patrol` (single cycle, foreground)
- Tests for each step with mocked GitHub responses and mock agent results

**Milestone:** `tom patrol` runs a full cycle — triages issues, dispatches agents, processes results, posts comments, sends summary.

**Verify:**
- `uv run pytest tests/test_patrol.py` — all pass
- Step 1 (triage): mock unlabeled issue + mock PM agent returning `need-dev` → issue gets `need-dev`, type, priority labels
- Step 1 (parent): mock PM returning `parent` with children → parent labeled `parent`, child issues created with correct body format, children comment posted
- Step 1 (blocked): mock PM returning `blocked` → issue labeled `blocked`, comment posted with reason
- Step 2 (review approved): mock `review result: approved` comment → issue closed, labels cleaned
- Step 2 (review changes-requested): mock `review result: changes-requested` → `in-review` removed, `need-dev` added
- Step 2 (review timeout): mock running process past `agent.timeout` → terminated, retry or block
- Step 3 (dev complete): mock `dev completed: PR #N` comment → `in-dev` removed, `need-review` added
- Step 3 (dev failure): mock agent `status: failure` → blocked immediately, no retry
- Step 3 (dev crash + retries exhausted): → blocked with Tom's reason
- Step 4 (dispatch review): mock `need-review` issue → label swapped, worktree created, agent spawned, tracking comment posted
- Step 4 (quota full): mock `review.concurrent` reached → issue stays `need-review`
- Step 5 (dispatch dev): issues dispatched in priority order (p0 → p1 → p2 → unlabeled)
- Step 5 (new vs re-dispatch): no `dev completed: PR` comment → new branch; existing comment → PR branch
- Step 6 (parent close): all children closed → parent closed
- Step 7 (summary): correct format — skip zero-count categories, pluralize, list issues
- Fresh state: each step queries GitHub fresh, not a stale list from cycle start
- `tom patrol` runs end-to-end against mocked GitHub without errors

### Phase 7: Retro loop

- `src/tom/retro.py`:
  1. **Scope** — query merged PRs and closed issues within the lookback window (`retro.interval`)
  2. **Dispatch analyst** — spawn analyst agent with PR/issue numbers
  3. **Create retro issue** — if `hasFindings`, create issue with `[Retro]` prefix, `blocked` label
  4. **Notify** — post to Discord if issue was created
- `src/tom/cli.py` — add `tom retro` (single cycle, foreground)
- Tests with mocked GitHub and mock analyst output

**Milestone:** `tom retro` analyzes recent work and creates a retro issue when findings exist.

**Verify:**
- `uv run pytest tests/test_retro.py` — all pass
- Scope: mock merged PRs and closed issues within window → correct numbers passed to analyst
- Scope: PRs/issues outside window → excluded
- Findings: mock analyst returning `hasFindings: true` → issue created with `[Retro]` prefix, `blocked` label, correct body
- No findings: mock analyst returning `hasFindings: false` → no issue created, no notification
- Notification: mock Discord webhook → correct embed format sent
- `tom retro` runs end-to-end against mocked GitHub without errors

### Phase 8: Daemon lifecycle + notifications

- `src/tom/notify.py`:
  - Send Discord embed via webhook URL (httpx POST)
  - Patrol summary formatting (categories, issue lists, pluralization)
  - Retro notification formatting
  - Silent skip when no webhook configured, log + continue on failure
- `src/tom/process.py`:
  - Lock files: `~/.tom/{project-id}/patrol.lock`, `retro.lock` (write PID on start, remove on stop)
  - Stale lock detection (PID alive check)
  - SIGTERM handler: cancel pending tasks, wait for running agents, remove lock
  - Background process spawning
- `src/tom/cli.py` — add remaining commands:
  - `tom start` / `tom start patrol` / `tom start retro` — launch background processes, `--replace`, `--run-now`
  - `tom stop` / `tom stop patrol` / `tom stop retro` — read PID from lock, send SIGTERM, wait, remove lock
- Tests for lock file management, signal handling, notification formatting

**Milestone:** `tom start` launches patrol + retro as background daemons. `tom stop` shuts them down. Discord notifications work. Tom is fully operational.

**Verify:**
- `uv run pytest` — all tests pass across all modules
- Lock files: start → lock file created with PID; stop → lock file removed; start while running → refuses (without `--replace`)
- Stale lock: kill process manually → `tom start` detects dead PID, reports stale lock
- `--replace`: stop existing + start new, clean handoff
- `--run-now`: immediate cycle fires after start
- SIGTERM: send SIGTERM → graceful shutdown (waits for agents, removes lock)
- Notification (patrol): mock webhook → correct embed with summary categories, issue lists, pluralization
- Notification (retro): mock webhook → correct embed with retro issue link
- Notification (no webhook): no webhook configured → skipped silently, no error
- Notification (failure): webhook returns 500 → logged, cycle continues
- `tom start` → both processes running (check lock files), `tom stop` → both stopped
- `tom start patrol` → only patrol lock exists, `tom stop patrol` → only patrol stopped
- Full integration: `tom start --run-now` on a test repo with a new issue → issue triaged, dev dispatched, PR created, review dispatched, merged (or blocked with reason)
