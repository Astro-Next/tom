# Architecture

## Daemon model

Tom runs as two independent long-lived Python processes, each an `asyncio` event loop:

- **Patrol process** — runs every `patrol.interval`. Each cycle scans all open GitHub issues and advances them through the lifecycle.
- **Retro process** — runs on `retro.interval` at `retro.time`. Each cycle analyzes recently merged PRs and creates a retro issue for human review.

The two processes are independent: each can be started, stopped, and restarted on its own. `tom start` launches both; `tom start patrol` or `tom start retro` launches one. Each process writes its own lock file on startup and removes it on shutdown. See [CLI — `tom start` / `tom stop`](cli.md) for details.

Tom pulls state from GitHub on a timer; nothing pushes to Tom.

## Subprocess model

When Tom needs LLM judgment, it spawns a Claude Code subprocess using `claude -p` (print/headless mode) with `--output-format json` and `--json-schema` to enforce a specific response structure. Each agent type has its own JSON schema defining the expected output.

Every agent is a one-shot invocation: it receives a prompt, does its work (thinking, reading code, writing code), and returns a structured JSON result. Tom reads the `.structured_output` field from stdout and executes all subsequent actions (GitHub API calls, git operations, comments, label changes) in deterministic Python code.

**This is the core design principle: agents do the work, Tom owns the workflow.** Agents have full tools — they read issues and PRs via `gh`, grep and search the repo, run tests, and (for the dev agent) write code. What they never do is perform workflow side effects: creating issues, posting comments, changing labels, merging PRs, or pushing commits. Those are executed by Tom's Python code based on the agent's structured output, which keeps every state transition predictable, testable, and recoverable.

### Launching agents

Tom launches every agent the same way:

```
claude -p "<prompt>" \
  --output-format json \
  --json-schema <schema-for-this-agent> \
  --permission-mode bypassPermissions
```

- **`-p "<prompt>"`** — the agent prompt (see [Agent prompts](prompts.md)), with identifiers like the issue or PR number already substituted in.
- **`--output-format json` + `--json-schema`** — force the agent's final message to match the agent's schema. Tom reads `.structured_output` from stdout; if it does not validate, the run is treated as a failed dispatch.
- **`--permission-mode bypassPermissions`** — agents run unattended, so they must read, write, and run commands without interactive approval prompts. There is no human at the subprocess to confirm tool use.

**Working directory is how the agent lands in the right place — the prompt does not `cd`.** Tom sets the subprocess `cwd` via `asyncio.create_subprocess_exec(..., cwd=<dir>)`:

| Agent | `cwd` | Why |
|-------|-------|-----|
| **Dev** | the issue's worktree | Already checked out on the correct branch (new branch or existing PR branch), so edits land on the right branch without the agent touching git refs. |
| **Review** | the PR's worktree | Checked out on the PR head branch, so the agent reviews exactly the code under review. |
| **PM** | project root | Read-only triage — greps code and reads docs to judge scope. |
| **Analyst** | project root | Reads merged diffs and project context to find problems. |

The agent confirms its location (`git rev-parse --show-toplevel`) but never changes it. Because `cwd` is set at spawn, the branch guarantee for dev and review is structural, not something the agent has to arrange.

### Data flow to agents

Tom gives each agent the identifiers it needs — an issue number, a PR number, or the set of PR/issue numbers in a retro window — and the agent reads the detail itself with `gh` and its other tools. Tom does not pre-fetch and paste content into the prompt; it points the agent at what to read. Tom does download attachments to a local cache ahead of dispatch (agents read those from disk, see [Attachments](attachments.md)), and for dev and review agents it provides a git worktree, already on the correct branch, as the working directory.

### Agent output model

All four agents return structured JSON via `--json-schema`:

| Agent | Returns | Tom executes |
|-------|---------|-------------|
| **PM** | Triage decision, type, priority, children list | Label issues, create child issues, post comments |
| **Dev** | PR title, PR body, PR comment, or failure reason | Push, create/update PR, post completion comment `dev completed: PR #N` (or block on failure) |
| **Review** | Verdict, PR comment (summary + findings) | Post PR comment, squash-merge + delete branch + close issue (if approved), post completion comment `review result: approved` or `review result: changes-requested` |
| **Analyst** | Retro issue title and body, or a no-findings signal | Create retro issue with `blocked` label (only if there are findings) |

### Subprocess lifecycle

Subprocesses are spawned with `asyncio.create_subprocess_exec` and tracked as asyncio tasks. Tom does not block waiting for an agent to finish during patrol — it records that an agent was dispatched (via a tracking comment on the issue) and checks for completion on the next patrol cycle.

1. **Spawn** — launch `claude -p` with the agent's prompt, JSON schema, and the correct `cwd` (see [Launching agents](#launching-agents))
2. **Track** — store the process object keyed by issue number. Post a tracking comment on the GitHub issue with a process ID. Schedule a timeout timer via `call_later` to terminate the process after `agent.timeout`.
3. **Monitor** — on each patrol cycle, check if the process has exited. If still running, skip. If exited, read stdout and parse `.structured_output`.
4. **Act** — based on the structured output, execute the appropriate GitHub API calls and git operations.
5. **Timeout** — if a process has been running longer than a configurable timeout, terminate it and treat as a failed dispatch.
6. **Cleanup** — remove the worktree directory after the process exits. Each dispatch and each retry gets a fresh worktree; Tom never reuses one across attempts.

## Agent failure model

Every agent shares one output contract: it returns structured JSON describing a result, and Tom executes all side effects. An agent run ends in one of three ways, and only one of them retries.

**1. A usable result.** The agent returns valid JSON describing what it accomplished — a triage decision, a PR to create, a verdict, or a retro issue. Tom acts on it. This includes results that ask for a human without being failures:

- **PM `decision: "blocked"`** — the agent triaged the issue and decided it needs a human (requirements too vague to scope). A valid decision, not a crash.
- **Analyst `hasFindings: false`** — the agent analyzed the window and found nothing worth raising. A clean round, not a crash.

**2. An agent-declared failure.** The agent ran fine, understood the task well enough to know it cannot produce a normal result, and says so: `status: "failure"` with a `failureReason` (dev and review agents), or `decision: "blocked"` with a `reason` (PM). Retrying would hit the same wall — the same prompt against the same unchanged input fails the same way — so **Tom blocks the issue immediately, without retrying**, and posts the agent's reason in the `Blocked:` comment so the human sees exactly what the agent could not resolve.

**3. A Tom-detected failure.** The subprocess crashed, exited non-zero, exceeded `agent.timeout`, or returned output that does not validate against the schema. This bucket covers the common environment failures — a broken or unauthenticated `claude` install, an unreachable model server, a network timeout — all of which surface as a non-zero exit or a process that never responds. Tom cannot tell whether any work is salvageable or what the agent intended, so it treats the run as a failed dispatch: **retry up to `agent.maxRetries`, then block.** The block comment carries Tom's reason (what failed and how many attempts were made), since there is no agent reason to quote.

Tom is the layer that runs the subprocess, so it captures what the subprocess reported and writes it to `tom.log`: the exit code and a short tail of stderr (or "timed out" / "unparseable output"). The `Blocked:` comment summarizes it briefly and points to the log. Tom records what the subprocess surfaced — it does not attempt to diagnose *why* Claude Code failed beyond that.

**Every block carries a reason, from one of two sources:** the agent's reason (case 2) or Tom's own (case 3). The reason only reaches the human because Tom writes it into the issue comment — the agent's JSON is consumed by Tom and never posted on its own.

A malformed-JSON run (case 3) is worth one caveat: a dev agent might have written real code but botched its final JSON. Tom discards that run and the next retry starts from a fresh worktree, so the partial work is lost. This is an accepted tradeoff — Tom cannot trust or act on a result it cannot parse, and a clean retry is more predictable than salvaging an unverifiable working tree.

## Concurrency control

Tom enforces configurable caps on simultaneous agents:

- `dev.concurrent` — maximum number of dev agents running at once
- `review.concurrent` — maximum number of review agents running at once

Before dispatching a new agent, patrol checks the current count:

**How to count active agents:** Count issues with the `in-dev` label (for dev quota) or `in-review` label (for review quota) via the GitHub API. This works because Tom sets the label before spawning the subprocess, and removes it when the agent completes or fails. Labels are the source of truth, not in-memory process tracking — so the quota check survives a process restart.

**Quota is re-evaluated per issue, against live state.** A dispatch step reads its candidate issues fresh from the GitHub API at the moment it runs — it never reuses a list captured earlier in the cycle. Triage (step 1) writes its labels before the dispatch steps run, so the dispatch query sees newly triaged `need-dev` issues in the same cycle. Within a dispatch step, Tom sets the `in-dev` / `in-review` label before spawning each agent, then re-checks the count before the next issue. This keeps the cap exact even when a single cycle dispatches several agents.

If the quota is full, the issue stays in `need-dev` or `need-review` and is picked up on the next patrol cycle. There is no separate queue; the label is the queue.

## Git worktree isolation

Each agent subprocess runs in its own git worktree so multiple agents can work on different branches simultaneously without conflicts.

**Worktree directory:** `.worktrees/` in the project root.

Before creating any worktree, Tom runs `git fetch origin` to ensure it has the latest remote state. Worktrees branch from `origin/{default-branch}`, not the local checkout — this avoids stale code without needing a `git pull` (which could cause merge conflicts on the main working tree). Tom resolves the default branch name once at startup from the GitHub API (`default_branch`) and caches it.

**Dev agent worktree setup:**
- New issue: create branch `dev/{issue_number}-{slug}` from `origin/{default-branch}`, create worktree at `.worktrees/dev-{issue_number}`
- Re-dispatch (addressing review feedback): fetch and create worktree on the existing PR branch

**Review agent worktree setup:**
- Fetch and create worktree on the PR's head branch at `.worktrees/review-{pr_number}`

**Cleanup:** After a subprocess exits, Tom removes the worktree directory and prunes the git worktree list.

## GitHub as state store

Tom uses GitHub as its only persistent store — all state lives in issues, their labels, and their comments. Everything is reconstructable from GitHub:

- **Issue labels** encode the current lifecycle stage (`need-dev`, `in-dev`, `need-review`, `in-review`, `blocked`, `parent`)
- **Issue comments** encode tracking and completion data (all posted by Tom, not by agents). Comments use plain-text prefixes like `dispatched dev`, `dev completed: PR #N`, `review result: approved`, `Blocked: ...`. See [Patrol — Comment conventions](patrol.md#comment-conventions) for the full list.
- **PR body** links back to the issue via `Closes #N`

This design means: if Tom crashes and restarts, the next patrol cycle reads GitHub and picks up exactly where things left off. No state is lost.

## Attachment handling

Issue bodies and comments may contain image or file attachment URLs (GitHub-hosted or external). Tom downloads these to a local cache (`/tmp/tom-{project-id}/cache/{issue_number}/`) before invoking any agent that needs them, and agents read the cached files from disk rather than fetching URLs. This keeps network fetches in Tom's deterministic code, consistent with the agents-think-Tom-acts model.

See [Attachments](attachments.md) for the full model: who downloads, when, how files are cached and accessed, and how missing attachments are handled.

## Logging

Tom logs its own runtime operations (process startup and shutdown, subprocess spawns, subprocess failures with exit code and stderr tail, errors, API issues) to `~/.tom/{project-id}/logs/tom.log`. This is the runtime log of the daemon itself. Python's `logging` module with `RotatingFileHandler` handles log rotation.

When running in foreground mode (`tom patrol`, `tom retro`), logs are also written to stderr for interactive debugging.

## File locations

Tom's files are split across three locations:

**In the repo `.tom/` (committed to git):**
- `.tom/settings.json` — project configuration

**In `~/.tom/{project-id}/` (user-local runtime):**
- `patrol.lock` — patrol process lock file (holds the running PID)
- `retro.lock` — retro process lock file (holds the running PID)
- `logs/tom.log` — runtime logs

**In `/tmp/tom-{project-id}/` (ephemeral):**
- `cache/{issue_number}/{hash}.{ext}` — attachment cache

**In project root (gitignored):**
- `.worktrees/` — agent git worktrees, cleaned up after each agent

## GitHub REST API

Tom communicates with GitHub exclusively through the REST API via `httpx` (async HTTP client). It does not use the `gh` CLI.

**Repository identification:** Tom parses the git remote origin URL at startup to extract the owner and repo name. These are cached in memory for API calls — no config needed.

**Authentication:** Tom reads the GitHub token from `gh auth token` at startup and caches it in memory for API calls. If an API call fails, Tom re-reads the token from `gh auth token` and retries once — this picks up a refreshed credential without a restart. If the retry also fails, Tom logs the error and moves on.

**Rate limit awareness:** The REST API allows 5,000 requests/hour for PATs. Tom uses conditional requests (ETag / `If-None-Match` headers) — when data hasn't changed since the last poll, GitHub returns 304 and the request does not count against the rate limit. For a daemon polling every 30 minutes, most calls return 304.

**API versioning:** Tom sends the `X-GitHub-Api-Version` header to pin to a stable API version, protecting against breaking changes.
