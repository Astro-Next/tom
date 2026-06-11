# Dev Agent

The Dev agent implements a single GitHub issue — it reads the issue, writes code, writes tests, verifies its work in a git worktree, and commits. It returns a structured result describing what changed. It does not push, create PRs, or post comments — Tom handles those.

## When invoked

Patrol step 5 (dispatch dev) spawns a dev agent for each `need-dev` issue when dev quota is available. Because step 5 queries `need-dev` fresh, an issue triaged in step 1 of the same cycle is eligible for dispatch in that cycle.

## Inputs

Tom gives the dev agent the issue number and a git worktree already checked out on the correct branch (a new branch for a new issue, the existing PR branch for a re-dispatch). The agent reads everything else itself — the issue, the repo, project context, and review feedback — using `gh` and its other tools. The exact prompt is in [Agent prompts — Dev](../prompts.md#dev-prompt).

## Process

### 1. Read the issue

Read the issue and its comments with `gh issue view`. If the issue references `Part of #N`, read the parent issue for context.

If the issue references attachment URLs, read them from the local cache (see [Attachments](../attachments.md)). Tom downloads them before dispatch; the agent only reads from disk and skips anything missing.

### 2. New issue or re-dispatch

Check for an existing open PR (`gh pr list --search "Closes #N"`). If one exists with review feedback, this is a re-dispatch: read the **PR body** to understand what was built and why, then read the review threads for what to change.

### 3. Read project context

Before writing any code, read:
- **CLAUDE.md** — agent instructions and project-specific rules
- **CONVENTIONS.md** — coding patterns, naming conventions, project standards
- **docs/index.md** — project knowledge base entry point

If the issue references specific areas, read the relevant source code and documentation. Search the codebase to understand how existing code works.

### 4. Plan implementation

Before writing code:
- Identify which files need to change
- Understand existing patterns in those files and nearby code
- Figure out the detailed implementation approach
- Consider edge cases and how existing tests cover related functionality

On a re-dispatch, plan against each review finding:
- `[blocker]` — implement the fix
- `[suggestion]` — use judgment; explain in the output if you decline
- `[question]` — answer it in the output

### 5. Write code

Rules:
- Follow CONVENTIONS.md strictly — match existing patterns, naming, and style
- Do not use temporary, hacky, or non-standard solutions
- Do not overdesign
- Reuse existing components, UI patterns, utilities, and code
- Add or update tests for the changes
- Do not modify files unrelated to this issue or the review feedback

### 6. Update documentation

If changes affect existing documentation:
- Update docs/ pages that reference changed behavior, APIs, or patterns
- Add new patterns or conventions to CONVENTIONS.md
- Update CLAUDE.md if agent instructions need to reflect the changes

### 7. Verify

- Self-review the code for mistakes, missed requirements, style violations
- Run the build/compile step if the project has one
- Run the test suite and ensure all tests pass
- Fix any failures before returning the result

## Output schema

The dev agent returns structured JSON:

```json
{
  "status": "success | failure",
  "prTitle": "Add user authentication",
  "prBody": "## Summary\n...\n\n## Approach\n...\n\n## Changes\n- ...\n\nCloses #42",
  "comment": "## Summary\n...\n\n## Review responses\n### [blocker] ...\n...",
  "failureReason": null
}
```

`prBody` must explain the work in depth — problem, why, approach, and changes — so a future session (a re-dispatch, the reviewer, or a human) understands it without re-deriving it. The PR body is the durable record of intent between otherwise stateless dispatches. Structure: `## Summary`, `## Approach`, `## Changes`, then `Closes #N`.

On a re-dispatch, the agent **preserves the existing PR body and appends** a `## Revision <n> — addressing review` section rather than rewriting it. The original Summary/Approach stay intact; if the approach genuinely changed, the revision section says so. `prBody` carries the full combined body, which Tom writes over the PR.

`comment` is a markdown comment posted on the PR after the round. It uses a `## Summary` section describing what the round did, plus a `## Review responses` section on re-dispatch with one `### [tag] short title` subsection per finding answered. The `## Review responses` section is omitted on a new issue or a merge-conflict resolution.

**On success**, Tom:
1. Pushes the branch (the agent has already committed)
2. Creates a new PR with `prTitle` and `prBody` (or updates the existing one for re-dispatches)
3. Posts `comment` as a PR comment
4. Posts the `dev completed: PR #N` completion comment on the issue

**On failure**, Tom labels the issue `blocked` and posts a `Blocked:` comment with `failureReason`. Failure means the agent read the issue and could not proceed — the requirements are too ambiguous to implement. Retrying would hit the same wall, so this escalates to `blocked` immediately rather than counting as a failed dispatch. (A crashed or timed-out subprocess is different — that is the Tom-detected dispatch-failure path in [Patrol](../patrol.md), which does retry. See [Architecture — Agent failure model](../architecture.md#agent-failure-model).)

## Branching strategy

Tom sets up the branch and worktree before spawning the agent, choosing based on whether the issue already has a `dev completed: PR #N` comment:

- **New issue** (no `dev completed: PR #N` comment): branch `dev/{issue_number}-{slug}` created from `origin/{default-branch}`, worktree at `.worktrees/dev-{issue_number}`. Slug is a short kebab-case version of the issue title.
- **Re-dispatch** (a `dev completed: PR #N` comment exists): Tom takes the PR number from the most recent such comment, reads the PR's head branch, fetches it, and creates the worktree on that branch — so the agent continues the existing PR rather than starting a new branch.

Each dispatch and each retry gets a fresh worktree, removed after the subprocess exits. Tom never carries a worktree across attempts; a retry recreates it from the branch above.

## Constraints

- **No workflow actions.** The dev agent writes code and commits in the worktree, then returns a result. It never pushes, creates or updates the PR, or comments — Tom does that from the result.
- Never change labels
- Never create issues — note additional work in `prBody`
- Bounded scope — implement what the issue asks, nothing more
- Do not create or switch branches — the worktree is already on the correct branch
