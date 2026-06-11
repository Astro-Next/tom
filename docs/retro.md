# Retro

Retro is Tom's retrospective loop — it runs on a separate schedule from patrol and analyzes recently merged work to identify improvements.

Retro is a workflow that invokes the Analyst agent (a Claude Code subprocess) to produce a comprehensive analysis, then creates a single retro issue for human review.

## When it runs

Retro runs once per `retro.interval` at `retro.time`. It analyzes work from the lookback window — the same length as `retro.interval`, so a `1d` interval looks back 24 hours and a `2d` interval looks back 48 hours. This guarantees no merged work falls through the gap between runs.

## Workflow

### 1. Identify scope

Tom determines which work falls in the retro window — it owns the time filtering. The lookback window equals `retro.interval`.

- **Merged PRs** merged within the window
- **Closed issues** closed within the window

Tom hands these PR and issue **numbers** to the Analyst. The agent reads each one's detail itself (diffs, bodies, review threads, comments) via `gh`, so Tom does not pre-fetch that content.

Attachments: the Analyst reads them from the cache if already present. Retro does **not** download — it reads only what patrol cached during the normal lifecycle, and skips anything missing. See [Attachments — Retro does not download](attachments.md#retro-does-not-download).

### 2. Invoke Analyst agent

Tom spawns a Claude Code subprocess with all the gathered data and asks it to produce a comprehensive retrospective analysis. See [Analyst agent](agents/analyst.md) for the full analysis process.

The Analyst inspects the merged diffs (using review threads as a cross-reference) and returns a single decision: either the content for one retro issue, or a signal that the round surfaced nothing. It hunts for code problems and risk, recurring problems across PRs, and knowledge freshness (drift and gaps). See [Analyst agent](agents/analyst.md) for what it checks and the output schema.

### 3. Create retro issue

The Analyst returns `hasFindings`. If it is `false`, the round was clean — **Tom creates no issue and sends no notification.** It logs the clean round and the cycle ends.

If `hasFindings` is `true`, Tom creates a single GitHub issue:

**Issue title:** `[Retro] <title>` — Tom prepends the `[Retro]` prefix to the Analyst's `title`.

**Issue body:** the Analyst's `body` — all findings, each with evidence and a proposed action or decision for the human.

**Labels:** `blocked` — the retro issue requires human review before any action is taken.

The retro issue then follows the standard lifecycle: a human reviews it, decides what's actionable, and removes the `blocked` label. On the next patrol, the PM agent reads the retro issue and triages it — creating child issues for actionable findings (as a parent), dispatching dev directly (if it's a single change), or re-blocking it if nothing needs action.

### 4. Log result

Tom logs the retro result to `tom.log`: the issue number if one was created, or a clean-round entry if not.

If the round was clean (`hasFindings` false), no notification is sent — the channel only shows real retro output.
