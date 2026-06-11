# Review Agent

The Review agent reviews a single pull request — it reads the code, runs tests, and forms a verdict. It returns a structured result with its decision and findings. It does not post reviews, merge PRs, or post comments — Tom handles all GitHub API calls and git operations.

## When invoked

Patrol step 4 (dispatch review) spawns a review agent for each `need-review` issue when review quota is available.

## Inputs

Tom gives the review agent the PR number, the linked issue number, and a git worktree already checked out on the PR's head branch. The agent reads everything else itself — the PR diff, review threads, the issue, and project context — using `gh` and its other tools. The exact prompt is in [Agent prompts — Review](../prompts.md#review-prompt).

## Process

### 1. Read the PR and issue

Read the PR (body, diff, files, existing review threads) and the issue it addresses with `gh`. If the issue references `Part of #N`, read the parent issue too.

If the issue or the PR references attachment URLs, read them from the local cache (see [Attachments](../attachments.md)), skipping anything missing.

### 2. Read project context

Before reviewing, read:
- **CLAUDE.md** — agent instructions and project-specific rules
- **CONVENTIONS.md** — coding patterns, naming conventions, project standards
- **docs/index.md** — project knowledge base entry point

Read the source files changed in the PR. Also read surrounding files to understand how changed code fits into the broader codebase.

### 3. Run tests

- Run the build/compile step if the project has one
- Run the full test suite
- Note any failures — **test failures are automatic blockers**

### 4. Review the code

Check for:

| Area | What to look for |
|------|-----------------|
| **Requirements** | Does it do what the issue asks? Anything missing? |
| **No hacks** | No temporary, hacky, or non-standard solutions |
| **Clean code** | No leftover files, dead code, unused imports |
| **Conventions** | Follows CONVENTIONS.md, matches existing patterns and project structure |
| **Reuse** | Uses existing components and utilities, no reinventing. Generalizes if similar code already exists |
| **Security** | No injection, no hardcoded secrets, input validated at boundaries |
| **Tests** | Changes have adequate test coverage (don't demand tests for trivial changes) |
| **Documentation** | docs/, CONVENTIONS.md, and CLAUDE.md are updated to reflect changes. New patterns/APIs are documented. Stale references are a blocker. |

## Output schema

The review agent returns structured JSON:

```json
{
  "status": "success | failure",
  "verdict": "approved | changes-requested",
  "comment": "## Summary\n...\n\n## Findings\n### [blocker] src/foo.ts:42 — ...\n...",
  "failureReason": null
}
```

`status` is `success` for a normal review — `verdict` and `comment` carry the result. It is `failure` only when the agent cannot review at all (for example the PR has no diff, the branch does not build for reasons unrelated to the change, or the linked issue is missing); then `failureReason` explains what blocked the review and the other fields are null. A normal review always renders a verdict — "the change is wrong" is `changes-requested` with a blocker finding, not a failure.

`comment` is the markdown review posted on the PR. It uses a `## Summary` section describing what was verified, plus a `## Findings` section with one `### [tag] file:line — short title` subsection per finding. The `## Findings` section is omitted on a clean approval with no findings.

**On `status: success` + `verdict: approved`**, Tom executes the full completion sequence:
1. Posts `comment` on the PR
2. Posts the `review result: approved` completion comment on the issue
3. Squash-merges the PR
4. Deletes the source branch
5. Verifies the issue was auto-closed (via `Closes #N` in PR body). If still open, closes it.
6. Removes workflow labels from the closed issue
7. Posts the `merged PR #N` comment on the issue

**On `status: success` + `verdict: changes-requested`**, Tom:
1. Posts `comment` on the PR
2. Posts the `review result: changes-requested` completion comment on the issue

**On `status: failure`**, Tom labels the issue `blocked` and posts a `Blocked:` comment with `failureReason`. This is an agent-declared failure — retrying would hit the same wall — so it escalates to a human immediately rather than counting as a failed dispatch. (A crashed or timed-out subprocess is different; that is the Tom-detected dispatch-failure path in [Patrol](../patrol.md), which retries. See [Architecture — Agent failure model](../architecture.md#agent-failure-model).)

**Approval criteria:** tests pass, requirements met, no blocker findings.

**Rejection criteria:** any `[blocker]` finding — logic errors, security issues, test failures, stale documentation.

Be specific in findings — say what's wrong and what to do instead. Don't nitpick to justify the review — if the PR is good, approve it.

## Constraints

- **No workflow actions.** The review agent reads code, runs tests, and returns a verdict. It never posts the review, merges, comments, or changes labels — Tom does that from the verdict.
- Never change labels
- Never create issues — note additional problems in findings
- Never push commits or modify the PR's code directly
- Bounded scope — review what the PR changes, nothing more
- Do not create or switch branches — the worktree is already on the correct branch
