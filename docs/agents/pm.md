# PM Agent

The PM agent triages new issues during patrol. It is a one-shot Claude Code subprocess that reads an issue, decides how to handle it, and returns a structured triage decision. It performs no workflow actions — labeling, creating child issues, and commenting are all done by Tom from the returned decision.

## When invoked

Patrol step 1 (triage new issues) invokes the PM agent for each open issue that has no workflow labels, no `blocked` label, and no `parent` label.

## Inputs

Tom gives the PM agent the issue number. The agent reads everything else itself — the issue and its comments, cached attachments, and project context — using `gh` and its other tools. It runs in the project root (read-only judgment; it writes no code). The exact prompt is in [Agent prompts — PM](../prompts.md#pm-prompt).

## Process

1. **Read the issue** — read the issue and its comments with `gh issue view`, and any attachments from the local cache (see [Attachments](../attachments.md)). If the issue is returning from `blocked`, its latest comments may hold human instructions.
2. **Read project context** — read CONVENTIONS.md, docs/index.md, and relevant source so you can judge scope accurately. Search the codebase as needed.
3. **Decide scope** — choose one outcome:
   - **Single PR (need-dev)** — one coherent, independently mergeable change. The default; prefer it whenever the work reasonably fits one PR.
   - **Parent (multiple PRs)** — too large or spans too many independent components for one PR. Break into non-overlapping children, each a single mergeable PR. Works for both features and bugs.
   - **Blocked** — requirements too vague to assess scope, or blocked on a decision no one has made.

## Output schema

The PM agent returns structured JSON matching a predefined schema. Three possible decisions:

### Simple issue (one PR)

```json
{
  "decision": "need-dev",
  "type": "feature | bug",
  "priority": "p0 | p1 | p2"
}
```

Tom then: applies labels `need-dev`, type, and priority.

### Parent (multiple PRs)

Children inherit the parent's type — a `bug` parent produces `bug` children, a `feature` parent produces `feature` children.

```json
{
  "decision": "parent",
  "type": "feature | bug",
  "priority": "p0 | p1 | p2",
  "children": [
    {
      "title": "...",
      "description": "...",
      "acceptanceCriteria": ["...", "..."],
      "context": "...",
      "priority": "p0 | p1 | p2",
      "dependsOn": [0, 1]
    }
  ]
}
```

Tom then:
1. Labels the parent issue `parent`, type, and priority
2. Creates each child issue via GitHub API with body format:

   ```
   Part of #<parent_number>

   Depends on #<sibling>, ...

   ## Description
   <description>

   ## Acceptance Criteria
   - [ ] <criterion 1>
   - [ ] <criterion 2>

   ## Context
   <context>
   ```

   Each child gets labels: `need-dev`, the parent's type, and the child's priority. `dependsOn` holds sibling indices, which don't have numbers until creation — so Tom creates all children first, then appends the resolved `Depends on #N, ...` line to those that declared one. Children with no dependencies omit the line.
3. Posts a comment on the parent issue:

   ```
   ## Children

   - [ ] P0: #<child_1> — <title>
   - [ ] P1: #<child_2> — <title>
   ```

### Blocked

```json
{
  "decision": "blocked",
  "reason": "explanation of what is unclear or ambiguous"
}
```

Tom then: labels the issue `blocked` and posts a comment with the reason.

## Constraints

- **No workflow actions.** The PM agent reads and returns a decision. It never changes labels, creates issues, or comments — Tom does that from the returned decision.
- **Don't force splitting.** If an issue is one PR of work, triage it as simple — don't create unnecessary parent issues.
- **Don't create overlapping children.** Each child should touch distinct files/components.
- **Order dependent children with dependsOn.** When one child needs another's merged code, set its sibling index in `dependsOn`; patrol won't dispatch it until the dependency is closed. Leave it empty otherwise.
- **Don't create children for things already done.** Check existing issues and PRs before proposing children.
- **Return `blocked` when in doubt.** If the requirements are too vague to assess scope, escalate rather than guess.
