# Issue Lifecycle

Tom manages issues through a label-based state machine. Labels encode the current lifecycle stage. All transitions are performed by Tom's patrol loop — agents never change labels.

## Labels

### Workflow labels (managed by Tom)

These are mutually exclusive — an issue has at most one workflow label at a time.

| Label | Meaning |
|-------|---------|
| `need-dev` | Ready for a dev agent to pick up |
| `in-dev` | A dev agent is actively working on this |
| `need-review` | A PR exists and is ready for review |
| `in-review` | A review agent is actively reviewing the PR |
| `blocked` | Needs human intervention — patrol skips this issue entirely |

### Structural labels (managed by Tom)

| Label | Meaning |
|-------|---------|
| `parent` | Multi-PR issue broken into child issues |

### Type labels (set by PM agent during triage)

| Label | Meaning |
|-------|---------|
| `feature` | New functionality |
| `bug` | Defect fix |

### Priority labels (set by PM agent during triage)

| Label | Meaning |
|-------|---------|
| `p0` | Critical — dispatched first |
| `p1` | High |
| `p2` | Normal |

Priority affects dispatch ordering: `p0` issues are dispatched before `p1`, which are dispatched before `p2`, which are dispatched before unlabeled issues.

### Completion

Completion is represented by GitHub's closed issue state, not a label. An issue is closed when its PR is merged (via GitHub's `Closes #N` auto-close) or when patrol closes a parent whose children are all done.

## State machine

### Task lifecycle

```
(open, no labels)
    │ PM agent triages
    ▼
need-dev
    │ patrol dispatches dev agent
    ▼
in-dev
    │ dev agent completes, PR created
    ▼
need-review
    │ patrol dispatches review agent
    ▼
in-review
    ├── closed (review approves, PR merged)
    └── need-dev (review requests changes → re-dispatch dev)

Any state → blocked (max retries hit, or requirements unclear)
blocked → (human removes label) → re-enters workflow at previous state
```

### Transition table

| From | To | Trigger | Who |
|------|----|---------|-----|
| (open, no workflow label) | `need-dev` | PM agent triages issue as simple task | Patrol |
| (open, no workflow label) | `parent` | PM agent triages issue as multi-PR | Patrol |
| `need-dev` | `in-dev` | Dev agent dispatched | Patrol |
| `in-dev` | `need-review` | Dev agent returned success, Tom posted `dev completed: PR #N` | Patrol |
| `in-dev` | `need-dev` | Dev dispatch crashed/timed out, retries remaining | Patrol |
| `in-dev` | `blocked` | Dev returned `status: failure` (immediate), or dispatch failed at max retries | Patrol |
| `need-review` | `in-review` | Review agent dispatched | Patrol |
| `in-review` | closed | Review agent returned approved, Tom merged PR | Patrol |
| `in-review` | `need-dev` | Review agent returned changes-requested | Patrol |
| `in-review` | `need-review` | Review dispatch crashed/timed out, retries remaining | Patrol |
| `in-review` | `blocked` | Review returned `status: failure` (immediate), or dispatch failed at max retries | Patrol |
| (open, no workflow label) | `blocked` | PM returned `decision: blocked` (requirements too vague) | Patrol |
| `blocked` | (re-enters workflow) | Human removes `blocked` label | Human |
| `parent` | closed | All child issues are closed | Patrol |

### Label swap rules

When transitioning between workflow labels, Tom always removes the old label and adds the new one in the same operation. An issue never has two workflow labels simultaneously.

## Escalation and blocking

An issue is labeled `blocked` in two situations, which differ in whether they retry first (see [Architecture — Agent failure model](architecture.md#agent-failure-model)):

1. **Agent-declared failure (no retry).** An agent ran successfully but reported it cannot proceed — the PM returns `decision: "blocked"`, or a dev or review agent returns `status: "failure"`. Retrying the same prompt against the same input would fail identically, so Tom blocks immediately. This covers unclear requirements, a decision no one has made, and external dependencies — whatever the agent named in its reason.
2. **Tom-detected failure, retries exhausted.** A dev or review subprocess crashed, timed out, or returned unparseable output `agent.maxRetries` times. Each is a failed dispatch; at the limit, Tom blocks. Normal review cycles (changes-requested followed by re-dev) are not failed dispatches.

When adding `blocked`, Tom posts a `Blocked:` comment with the reason: the agent's own reason for case 1, or Tom's summary (what failed and the attempt count) for case 2. The reason reaches the human only because Tom writes it into the comment.

**Resolving blocked issues:** A human removes the `blocked` label. On the next patrol, Tom reads the latest comments for context (the human may have added clarifying instructions) and re-enters the issue into the workflow. If the issue had a workflow label before being blocked, it returns to that state. If not, it goes through triage again.

## Retry counting

Tom counts retries by reading issue comments. A dispatch is a comment matching `dispatched dev` or `dispatched review`. A successful completion is a comment matching `dev completed: PR #N` or `review result: approved`/`review result: changes-requested`.

**Failed dispatch count** = number of dispatch comments that are not followed by a matching completion comment.

Only Tom-detected failures (crash, timeout, unparseable output) count toward this total. An agent-declared `status: "failure"` does not — it blocks immediately, so it never reaches a retry. If the failed dispatch count reaches `agent.maxRetries` (from config), the issue is escalated to `blocked`.

## Parent issue management

### Creation

When the PM agent triages an issue as requiring multiple PRs, it:
1. Labels the parent issue `parent`, type, and priority
2. Creates child issues, each representing one independently mergeable unit of work. Children inherit the parent's type, so each child gets the parent's type label, its own priority label, and `need-dev`
3. Posts a comment on the parent issue listing all children:

```
## Children

- [ ] P0: #101 — Add user model
- [ ] P1: #102 — Add API endpoints
- [ ] P2: #103 — Add frontend form
```

### Child issue format

Each child issue body starts with `Part of #N` (referencing the parent issue number), followed by a description, acceptance criteria, and context.

### Linking

GitHub lacks native issue hierarchy. Tom uses text references:
- Child issue body: `Part of #{parent_number}`
- Epic comment: numbered list of `#{child_number}` references

### Auto-close

Each patrol cycle, Tom checks every open parent: if all child issues listed in the `## Children` comment are closed, Tom closes the parent.

### Label cleanup

When an issue is closed (by PR merge or parent auto-close), patrol removes any remaining workflow labels (`in-review`, `in-dev`, etc.) to keep the label state clean.
