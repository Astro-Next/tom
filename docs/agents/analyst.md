# Analyst Agent

The Analyst agent performs retrospective analysis of recently merged work. It is a one-shot Claude Code subprocess invoked by the retro workflow. It returns the content for a single retro issue — it does not create issues, post comments, or make any GitHub API calls.

## When invoked

The retro loop invokes the Analyst agent once per cycle.

## Inputs

Tom determines which work falls in the retro window (it owns the time filtering) and gives the Analyst the **PR and issue numbers in scope**. The agent reads each one's detail itself — diffs, bodies, review threads, comments — using `gh`, along with project context and source. The exact prompt is in [Agent prompts — Analyst](../prompts.md#analyst-prompt).

- Merged PRs in scope — read each with `gh pr view` / `gh pr diff`
- Closed issues in scope — read each with `gh issue view`
- Project context — CLAUDE.md, CONVENTIONS.md, docs/index.md, and the surrounding source as needed
- Cached attachments — read from the cache only, if present; retro does not download (see [Attachments](../attachments.md))

## What to find

The Analyst hunts for things a human should act on: problems, risks, and stale or missing knowledge.

### 1. Code problems and risk

Read the **merged diffs** — this is the primary input, not the review threads. The most important findings are the ones review did *not* catch, because anything a reviewer flagged was likely fixed before merge.

Look for:
- Quality issues that slipped through — hacks, duplication, weak error handling, missing tests on risky paths, security smells that were approved anyway
- Risky changes — broad diffs, changes to sensitive areas (auth, payments, migrations, data handling), or code merged with unresolved `[question]` review findings

Use review threads as a **cross-reference**, not the target: they tell you what was already discussed, which helps you focus on what was missed.

### 2. Recurring problems

Look across multiple PRs for patterns that point to a systemic gap — for example, the same kind of `[blocker]` or `[suggestion]` showing up in review thread after review thread, or the same mistake reappearing in diffs. A one-off is not a finding; a repeated pattern is.

### 3. Knowledge freshness

Keep the knowledge base honest about the code as it now stands. Check both directions:
- **Drift** — docs, CONVENTIONS.md, or docs/index.md describe behavior the merged code no longer has
- **Gaps** — recent PRs introduced a new pattern, component, API, or decision that the knowledge base never captured. Newly merged code that nothing documents yet is a finding, even when nothing is contradictory.

## Output schema

The Analyst returns structured JSON — the content for one retro issue.

```json
{
  "hasFindings": true,
  "title": "Short descriptive title for the retro issue, no prefix",
  "body": "Full markdown — every finding as its own section, each with evidence and a proposed action"
}
```

A round produces **one** issue covering all findings, so the `body` must include every finding — not just the first. Each finding is its own section, so the human and the PM agent that later triages this issue can act on them independently. `title` summarizes the round as a whole; Tom prepends the `[Retro]` prefix, so the agent must not add it.

Example `body`:

```markdown
## Finding 1: OAuth token refresh silently swallows errors

**Observed:** The refresh path catches and discards the failure, so an expired token looks like a successful no-op.
**Evidence:** PR #41 (`src/auth/refresh.ts`), PR #45.
**Proposed action:** Surface the error to the caller and add a test for the expired-token path.

## Finding 2: `docs/index.md` no longer describes the caching layer

**Observed:** PR #44 added a request cache, but the knowledge base still says responses are uncached.
**Evidence:** PR #44.
**Proposed action:** Document the cache in `docs/index.md` and note the invalidation rules in CONVENTIONS.md.
```

When a round surfaces nothing worth raising, return `hasFindings: false` with `title` and `body` null.

| `hasFindings` | Tom does |
|---------------|----------|
| `true` | Creates one retro issue titled `[Retro] <title>` with `body`, labeled `blocked` |
| `false` | Nothing — no issue, no notification. Logs that the round was clean and exits. |

A created retro issue follows the standard lifecycle: a human reviews it, removes `blocked`, and the PM agent triages it on the next patrol.

## Constraints

- **No workflow actions.** The Analyst reads and returns the issue content. It never creates the issue or sends the notification — Tom does that from the returned result.
- **Read the diffs, not just the comments.** The point is to catch what review missed.
- Evidence-based — every finding must reference the specific PRs or issues it came from.
- Actionable — every finding must propose a concrete change or a decision for the human, not just observe a problem.
- No metrics — do not report review-round counts, retry counts, or other operational numbers. Retro surfaces problems, not dashboards.
