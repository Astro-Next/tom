# Patrol

Patrol is Tom's heartbeat — a recurring loop that scans all open GitHub issues and advances them through the lifecycle. It runs on a configurable interval.

Each patrol cycle executes the following steps in order. Steps are sequential and idempotent: running patrol twice with no external changes produces no new side effects.

All steps skip issues labeled `blocked` — those need human intervention first.

## Fresh state per step

Each step reads its candidate issues fresh from the GitHub API when the step runs — a step never reuses a list captured at the top of the cycle. This matters in two ways:

- **Triage feeds dispatch in the same cycle.** Step 1 writes `need-dev` labels; steps 4 and 5 then query those labels live, so newly triaged issues are dispatched without waiting a cycle.
- **Quota stays exact.** Tom sets the `in-dev` / `in-review` label before spawning each agent and re-checks the count before the next issue, so a step that dispatches several agents never exceeds `dev.concurrent` or `review.concurrent`.

A dev or review agent may run longer than one patrol interval. That is expected: a long-running agent keeps its `in-dev` / `in-review` label, so each cycle sees it still running and skips it (within `agent.timeout`). For example, a `2h` agent simply spans several `30m` cycles until it finishes or times out.

## Step 1: Triage new issues

Find all open issues that have no workflow labels (`need-dev`, `in-dev`, `need-review`, `in-review`), no `blocked` label, and no `parent` label.

For each unlabeled issue, invoke the PM agent to triage it. See [PM agent](agents/pm.md) for the full triage process.

The PM agent reads the issue, reads project context files (CLAUDE.md, CONVENTIONS.md, docs/), and decides:
- **Simple issue** (one PR of work) → PM labels it with type (`feature`/`bug`), priority (`p0`/`p1`/`p2`), and `need-dev`
- **Parent (multiple PRs)** → PM labels it `parent`, creates child issues each labeled `need-dev`. A child that needs another's merged code gets a `Depends on #a, #b` line in its body so step 5 can defer it
- **Unclear** → PM labels it `blocked` with a comment explaining what's ambiguous

If the PM agent crashes, times out, or returns unparseable output, Tom retries immediately (up to `agent.maxRetries` total attempts). Unlike dev/review, PM runs synchronously and blocks triage of the current issue, so retries happen in a tight loop within the same patrol cycle — there is no label-swap or waiting for the next cycle. If all attempts fail, the issue is labeled `blocked`.

Triage runs before the dispatch steps, and the dispatch steps query GitHub fresh (see [below](#fresh-state-per-step)). So an issue triaged to `need-dev` here is picked up by step 5 in the **same cycle** when dev quota is free — a simple `p0` issue can go from open to `in-dev` in a single patrol. No separate fast-path is needed; the step order produces it.

## Step 2: Check review progress

Query the GitHub API for all open issues labeled `in-review`.

For each issue, read the issue comments looking for the review agent's completion signal:

- **`merged PR #N`** — the PR was merged. If the issue is still open (auto-close didn't fire), close it. Remove the `in-review` label.
- **`review result: changes-requested`** — remove `in-review`, add `need-dev`. The issue will be re-dispatched to dev in step 4 of this cycle or the next.
- **No completion comment found, a subprocess is tracked** — check if the review subprocess is still running:
  - **Still running, within `agent.timeout`** → skip, check next cycle
  - **Still running, exceeded `agent.timeout`** → terminate the process, treat as a Tom-detected failure (same logic as "crashed" below)
  - **Exited with `status: failure`** → the agent ran but could not review (for example the PR is empty or the linked issue is missing). Remove `in-review`, add `blocked`, post a `Blocked:` comment with the agent's `failureReason`. This does **not** count as a failed dispatch — it is an agent-declared failure that retrying would not fix (see [Architecture — Agent failure model](architecture.md#agent-failure-model)).
  - **Exited successfully, but applying the result fails** → the agent returned a verdict, but a GitHub API call while posting the review, merging, or closing errors. Remove `in-review`, add `blocked`, post a `Blocked:` comment with the API error detail. This blocks immediately rather than retrying. (A merge rejected with 405/409 is handled separately: the issue returns to `need-dev` for another dev pass — see [`merge failed`](#comment-conventions).)
  - **Crashed, timed out, or returned unparseable output** → Tom-detected failure. Count total failed review dispatches for this issue (dispatch comments not followed by a completion comment). If under `agent.maxRetries`, remove `in-review`, add `need-review` (will be re-dispatched). If at limit, add `blocked`, post a `Blocked:` comment with Tom's reason (what failed, attempt count; details in tom.log).
- **No completion comment found, no subprocess tracked** — the dispatch was interrupted (typically a daemon restart that killed the agent). If the `dispatched review for PR #N` comment is older than `agent.timeout`, remove `in-review`, add `blocked`, post a `Blocked:` comment with a best-effort state report (whether an open PR references the issue, whether the local worktree is present). This blocks immediately and does not retry. If the dispatch is still within `agent.timeout`, skip and re-check next cycle.

## Step 3: Check dev progress

Query the GitHub API for all open issues labeled `in-dev`.

For each issue, read the issue comments looking for the dev agent's completion signal:

- **`dev completed: PR #N`** — remove `in-dev`, add `need-review`.
- **No completion comment found, a subprocess is tracked** — check the dev subprocess:
  - **Still running, within `agent.timeout`** → skip, check next cycle
  - **Still running, exceeded `agent.timeout`** → terminate the process, treat as a Tom-detected failure (same logic as "crashed" below)
  - **Exited with `status: failure`** → the agent read the issue but could not proceed (ambiguous requirements). Remove `in-dev`, add `blocked`, post a `Blocked:` comment with the agent's `failureReason`. This does **not** count as a failed dispatch — it is an agent-declared failure that retrying would not fix (see [Architecture — Agent failure model](architecture.md#agent-failure-model)).
  - **Exited successfully, but finalizing the result fails** → the agent succeeded, but a GitHub API call while pushing or creating/updating the PR errors (for example a 422 when the branch has no diff). Remove `in-dev`, add `blocked`, post a `Blocked:` comment with the API error detail. The finalize is not safely repeatable, so this blocks immediately rather than retrying.
  - **Crashed, timed out, or returned unparseable output** → Tom-detected failure. Count failed dev dispatches. If under `agent.maxRetries`, remove `in-dev`, add `need-dev`. If at limit, add `blocked`, post a `Blocked:` comment with Tom's reason (what failed, attempt count; details in tom.log).
- **No completion comment found, no subprocess tracked** — the dispatch was interrupted (typically a daemon restart that killed the agent). If the `dispatched dev` comment is older than `agent.timeout`, the agent is gone: remove `in-dev`, add `blocked`, post a `Blocked:` comment with a best-effort state report (whether an open PR references the issue, whether the local worktree is present) so a human can recover. This blocks immediately and does not retry. If the dispatch is still within `agent.timeout`, skip and re-check next cycle.

## Step 4: Dispatch review agents

Query the GitHub API for all open issues labeled `need-review`.

For each issue:

1. **Check review quota:** count open issues labeled `in-review`. If the count is at or above `review.concurrent`, skip — the issue stays in `need-review` for the next cycle.

2. **Find the PR:** read the issue comments for the most recent `dev completed: PR #N` comment. Extract the PR number.

3. **Swap labels:** remove `need-review`, add `in-review`.

4. **Spawn review agent:** create a git worktree on the PR's head branch, invoke Claude Code with the review prompt. See [Review agent](agents/review.md) for the full process.

5. **Post tracking comment on the issue:**
   ```
   dispatched review for PR #<pr_number>
   Process: <pid>
   ```

## Step 5: Dispatch dev agents

Query the GitHub API for all open issues labeled `need-dev`.

**Sort by priority:** `p0` first, then `p1`, then `p2`, then issues with no priority label. Within a tier, dispatch in ascending issue number (oldest first) so ordering is deterministic.

For each issue, in priority order:

1. **Check dependencies:** if the body has a `Depends on #a, #b` line, query each referenced issue. If any is not closed, skip — the child stays `need-dev` and is re-checked next cycle. A skipped child consumes no quota.

2. **Check dev quota:** count open issues labeled `in-dev`. If at or above `dev.concurrent`, stop dispatching — remaining issues stay in `need-dev`.

3. **Read context:** read the issue comments. The issue may be returning from `blocked` (human added instructions) or from a review cycle (changes requested). The latest comments provide context for the dev agent.

4. **Swap labels:** remove `need-dev`, add `in-dev`.

5. **Set up the worktree:** decide new work vs. re-dispatch from the issue comments.
   - **No `dev completed: PR #N` comment** → new work. Create branch `dev/{issue_number}-{slug}` from `origin/{default-branch}` and a worktree at `.worktrees/dev-{issue_number}`.
   - **A `dev completed: PR #N` comment exists** → re-dispatch. Take the PR number from the most recent such comment, query the PR for its head branch, fetch it, and create the worktree on that branch — no new branch.

6. **Spawn dev agent:** invoke Claude Code with the dev prompt in the worktree. See [Dev agent](agents/dev.md) for the full process.

7. **Post tracking comment on the issue:**
   ```
   dispatched dev
   Process: <pid>
   ```

## Step 6: Check parents

Query the GitHub API for all open issues labeled `parent`.

For each parent:
1. Find the comment starting with `## Children`
2. Extract child issue numbers from the list
3. Query the GitHub API for each child's state
4. If all children are closed → close the parent

## Step 7: Cleanup and summary

**Label cleanup:** query for closed issues that still have workflow labels (`in-review`, `in-dev`, etc.). Remove the stale labels.

**Compose patrol summary.** Each category is a section. Under each, list the affected issues as `#<id>: <title>`. Format rules:

- **Skip zero-count categories.** A category line only appears if at least one issue falls into it. A cycle that dispatched two dev agents but retried nothing shows the dispatch section and omits the retry section entirely.
- **Pluralize the count noun.** `1 dev agent dispatched`, `2 dev agents dispatched`. Same for issue/issues, item/items, parent/parents, etc.
- **List each issue on its own line** as `#<id>: <title>`, indented under the category.

Example for a cycle that triaged two issues, dispatched one dev agent, completed one issue, and blocked one:

```
Patrol 2026-06-02 14:30

- 2 new issues triaged
  - #42: Add OAuth login
  - #43: Fix pagination bug
- 1 dev agent dispatched
  - #42: Add OAuth login
- 1 issue completed
  - #38: Update API docs
- 1 item blocked
  - #40: Migrate to new payment provider
```

Categories, in order: new issues triaged, dev agents dispatched, review agents dispatched, issues completed, agents retried, items blocked, parents completed.

The patrol summary is logged to `tom.log`. All-quiet cycles (every category empty) are logged but produce a minimal entry.

## Attachment handling during patrol

Before dispatching any agent that reads issue content (triage, dev, review), Tom downloads the relevant attachments to a local cache so the agent can read them from disk. For triage and dev this is the issue body and comments (and the parent issue when the body contains `Part of #N`). For review it is the same issue content **plus the PR's own body and comments**, since reviewers often paste screenshots on the PR itself. See [Attachments](attachments.md) for the full download, cache, and access model.

## Comment conventions

Agents return structured JSON; Tom posts the comments and acts on the results.

| Comment | Posted when | Purpose |
|---------|------------|---------|
| `triaging` | Step 1 | Tracks PM agent dispatch. Includes process ID. |
| `dispatched dev` | Step 5 | Tracks dev agent dispatch. Includes process ID. |
| `dispatched review for PR #N` | Step 4 | Tracks review agent dispatch. Includes process ID. |
| `Blocked: ...` | Steps 1, 2, or 3 | Explains why an issue was escalated and what the human needs to decide. The reason is the agent's (an agent-declared `failure`/`blocked`), Tom's own (retries exhausted on a crash/timeout/parse failure), a GitHub API error detail (finalizing a successful agent's result failed), or a state report (the dispatch was orphaned past `agent.timeout`). See [Architecture — Agent failure model](architecture.md#agent-failure-model). |
| `dev completed: PR #N` | Step 3, after dev agent returns success | Signals dev completion. Posted by Tom after committing, pushing, and creating/updating the PR. |
| `review result: approved` | Step 2, after review agent returns approved | Signals review approval. Posted by Tom after posting the review on the PR. |
| `merged PR #N` | Step 2, after successful merge | Confirms the PR was merged. Posted by Tom after merging, deleting the branch, and closing the issue. |
| `review result: changes-requested` | Step 2, after review agent returns changes-requested | Signals review rejection. Posted by Tom after posting the review on the PR. |
| `merge failed ...` | Step 2, when merge fails | Posted on both the PR and the issue with the HTTP status and error detail. |
