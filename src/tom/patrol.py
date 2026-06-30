from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
from datetime import datetime
from pathlib import Path

import httpx

from tom.agents import AgentFailure, AgentSuccess, await_agent, run_agent, spawn_agent
from tom.attachments import download_attachments
from tom.config import Settings, parse_interval_seconds
from tom.github import GitHubClient
from tom.prompts import dev_prompt, pm_prompt, review_prompt
from tom.schemas import DEV_SCHEMA, PM_SCHEMA, REVIEW_SCHEMA
from tom.worktree import (
    _git,
    cleanup_worktree,
    create_dev_worktree,
    create_redispatch_worktree,
    create_review_worktree,
    fetch_origin,
)

_log = logging.getLogger("tom")

_WORKFLOW_LABELS = {"need-dev", "in-dev", "need-review", "in-review"}
_PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2}


class PatrolSummary:
    def __init__(self) -> None:
        self.triaged: list[tuple[int, str]] = []
        self.dev_dispatched: list[tuple[int, str]] = []
        self.review_dispatched: list[tuple[int, str]] = []
        self.completed: list[tuple[int, str]] = []
        self.retried: list[tuple[int, str]] = []
        self.blocked: list[tuple[int, str]] = []
        self.parents_completed: list[tuple[int, str]] = []

    def format(self) -> str | None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"Patrol {now}"]
        categories = [
            ("new issue", "new issues", "triaged", self.triaged),
            ("dev agent", "dev agents", "dispatched", self.dev_dispatched),
            ("review agent", "review agents", "dispatched", self.review_dispatched),
            ("issue", "issues", "completed", self.completed),
            ("agent", "agents", "retried", self.retried),
            ("item", "items", "blocked", self.blocked),
            ("parent", "parents", "completed", self.parents_completed),
        ]
        any_content = False
        for singular, plural, verb, items in categories:
            if not items:
                continue
            any_content = True
            noun = singular if len(items) == 1 else plural
            lines.append(f"- {len(items)} {noun} {verb}")
            for number, title in items:
                lines.append(f"  - #{number}: {title}")
        if not any_content:
            return None
        return "\n".join(lines)


ActiveProcess = tuple[asyncio.subprocess.Process, asyncio.TimerHandle]


async def run_patrol(
    settings: Settings,
    project_root: str,
    default_branch: str,
    client: GitHubClient,
    active_processes: dict[int, ActiveProcess],
) -> PatrolSummary:
    summary = PatrolSummary()

    await _step1_triage(settings, project_root, default_branch, client, summary)
    await _step2_check_review_progress(settings, project_root, client, active_processes, summary)
    await _step3_check_dev_progress(settings, project_root, client, active_processes, summary)
    await _step4_dispatch_review(settings, project_root, client, active_processes, summary)
    await _step5_dispatch_dev(settings, project_root, default_branch, client, active_processes, summary)
    await _step6_check_parents(client, summary)
    await _step7_cleanup(client)

    return summary


# --- Step 1: Triage ---

async def _step1_triage(
    settings: Settings,
    project_root: str,
    default_branch: str,
    client: GitHubClient,
    summary: PatrolSummary,
) -> None:
    issues = await client.list_issues()
    issues = _sort_by_priority(issues)
    unlabeled = [i["number"] for i in issues if not i.get("pull_request") and not ({l["name"] for l in i.get("labels", [])} & (_WORKFLOW_LABELS | {"blocked", "parent"}))]
    _log.debug("Step 1: found %d unlabeled issues: %s", len(unlabeled), unlabeled)
    for issue in issues:
        if issue.get("pull_request"):
            continue
        labels = {l["name"] for l in issue.get("labels", [])}
        if labels & (_WORKFLOW_LABELS | {"blocked", "parent"}):
            continue

        number = issue["number"]
        title = issue["title"]
        _log.info("Triaging issue #%d: %s", number, title)

        texts = [issue.get("body", "") or ""]
        comments = await client.list_comments(number)
        texts.extend(c.get("body", "") or "" for c in comments)
        await download_attachments(settings.id, number, texts, client._token)

        await _git(["checkout", default_branch], cwd=project_root)
        await _git(["pull", "--ff-only"], cwd=project_root)

        result = None
        for attempt in range(settings.agent.max_retries):
            try:
                proc = await spawn_agent(pm_prompt(number, settings.id), PM_SCHEMA, cwd=project_root)
            except FileNotFoundError:
                await client.add_labels(number, ["blocked"])
                await client.create_comment(number, "Blocked: claude CLI not found in PATH")
                summary.blocked.append((number, title))
                break

            if attempt == 0:
                await client.create_comment(number, f"triaging\nProcess: {proc.pid}")

            result = await await_agent(proc, timeout_str=settings.agent.timeout)

            if isinstance(result, AgentSuccess):
                break
            _log.warning("PM agent failed for #%d (attempt %d/%d): %s", number, attempt + 1, settings.agent.max_retries, result.reason)
        else:
            _log.error("PM agent failed for #%d after %d attempts", number, settings.agent.max_retries)
            await client.add_labels(number, ["blocked"])
            await client.create_comment(
                number,
                f"Blocked: PM agent failed after {settings.agent.max_retries} attempts — {result.reason}",
            )
            summary.blocked.append((number, title))
            continue

        if not isinstance(result, AgentSuccess):
            continue

        output = result.output
        decision = output.get("decision")

        if decision == "need-dev":
            await client.add_labels(number, ["need-dev", output["type"], output["priority"]])
            summary.triaged.append((number, title))

        elif decision == "parent":
            await client.add_labels(number, ["parent", output["type"], output["priority"]])
            children = output.get("children", [])
            child_numbers: list[int] = []
            child_bodies: list[str] = []
            children_lines = []
            for child_data in children:
                body_rest = f"## Description\n{child_data['description']}\n\n## Acceptance Criteria\n"
                for criterion in child_data.get("acceptanceCriteria", []):
                    body_rest += f"- [ ] {criterion}\n"
                body_rest += f"\n## Context\n{child_data['context']}"

                child_issue = await client.create_issue(
                    child_data["title"],
                    f"Part of #{number}\n\n{body_rest}",
                    labels=["need-dev", output["type"], child_data["priority"]],
                )
                child_number = child_issue["number"]
                child_numbers.append(child_number)
                child_bodies.append(body_rest)
                children_lines.append(
                    f"- [ ] {child_data['priority'].upper()}: #{child_number} — {child_data['title']}"
                )

            for index, child_data in enumerate(children):
                deps = [
                    child_numbers[d]
                    for d in child_data.get("dependsOn", [])
                    if 0 <= d < len(children) and d != index
                ]
                if deps:
                    dep_refs = ", ".join(f"#{n}" for n in deps)
                    await client.update_issue(
                        child_numbers[index],
                        body=f"Part of #{number}\n\nDepends on {dep_refs}\n\n{child_bodies[index]}",
                    )

            await client.create_comment(number, "## Children\n\n" + "\n".join(children_lines))
            summary.triaged.append((number, title))

        elif decision == "blocked":
            await client.add_labels(number, ["blocked"])
            await client.create_comment(number, f"Blocked: {output['reason']}")
            summary.blocked.append((number, title))

        else:
            _log.warning("Unknown PM decision for #%d: %s", number, decision)


# --- Step 2: Check review progress ---

async def _step2_check_review_progress(
    settings: Settings,
    project_root: str,
    client: GitHubClient,
    active_processes: dict[int, ActiveProcess],
    summary: PatrolSummary,
) -> None:
    issues = await client.list_issues(labels="in-review")
    _log.debug("Step 2: found %d in-review issues: %s", len(issues), [i["number"] for i in issues if not i.get("pull_request")])
    for issue in issues:
        if issue.get("pull_request"):
            continue
        number = issue["number"]
        title = issue["title"]
        comments = await client.list_comments(number)

        last_dispatch = _find_last_comment(comments, "dispatched review for PR #")

        last_merged = _find_last_comment(comments, "merged PR #")
        if last_merged and (not last_dispatch or comments.index(last_merged) > comments.index(last_dispatch)):
            if issue["state"] == "open":
                await client.close_issue(number)
            await client.remove_label(number, "in-review")
            summary.completed.append((number, title))
            continue

        last_changes = _find_last_comment(comments, "review result: changes-requested")
        if last_changes and (not last_dispatch or comments.index(last_changes) > comments.index(last_dispatch)):
            await client.remove_label(number, "in-review")
            await client.add_labels(number, ["need-dev"])
            continue

        entry = active_processes.get(number)
        if entry is None:
            continue
        proc, timer = entry

        if proc.returncode is None:
            continue

        # Process exited — cancel timeout timer and read result
        timer.cancel()
        active_processes.pop(number, None)
        stdout = (await proc.stdout.read()).decode(errors="replace") if proc.stdout else ""
        stderr = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""

        pr_comment = _find_last_comment(comments, "dispatched review for PR #")
        pr_number = _extract_pr_number(pr_comment) if pr_comment else None
        if pr_number is not None:
            await cleanup_worktree(project_root, Path(project_root) / ".worktrees" / f"review-{pr_number}")

        if proc.returncode == -signal.SIGTERM:
            _log.warning("Review agent timed out for #%d", number)
            await _handle_dispatch_failure(
                number, title, comments, "review",
                settings.agent.max_retries,
                "Review agent timed out",
                client, summary,
            )
            continue

        if proc.returncode != 0:
            _log.error("Review agent crashed for #%d (exit %d)", number, proc.returncode)
            await _handle_dispatch_failure(
                number, title, comments, "review",
                settings.agent.max_retries,
                f"Review agent crashed (exit {proc.returncode})",
                client, summary,
            )
            continue

        from tom.agents import _parse_output
        result = _parse_output(stdout, stderr)

        if isinstance(result, AgentFailure):
            await _handle_dispatch_failure(
                number, title, comments, "review",
                settings.agent.max_retries,
                result.reason,
                client, summary,
            )
            continue

        output = result.output

        if output.get("status") == "failure":
            await client.remove_label(number, "in-review")
            await client.add_labels(number, ["blocked"])
            await client.create_comment(
                number,
                f"Blocked: {output.get('failureReason', 'Review agent declared failure')}",
            )
            summary.blocked.append((number, title))
            continue

        verdict = output.get("verdict")
        if pr_number is None:
            continue

        review_body = output.get("comment") or "No comment."

        try:
            if verdict == "approved":
                await client.create_comment(pr_number, review_body)
                await client.create_comment(number, "review result: approved")

                pr = await client.get_pr(pr_number)
                try:
                    await client.merge_pr(pr_number)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    detail = exc.response.text
                    _log.warning("Merge failed for PR #%d (issue #%d): %s", pr_number, number, detail)
                    await client.create_comment(pr_number, f"merge failed ({status}): {detail}")
                    await client.create_comment(number, f"merge failed: PR #{pr_number} — {status} {detail}")
                    await client.remove_label(number, "in-review")
                    if status in (405, 409):
                        await client.add_labels(number, ["need-dev"])
                    else:
                        await client.add_labels(number, ["blocked"])
                        summary.blocked.append((number, title))
                    continue

                if issue["state"] == "open":
                    await client.close_issue(number)
                await client.remove_label(number, "in-review")
                await client.delete_branch(pr["head"]["ref"])
                await client.create_comment(number, f"merged PR #{pr_number}")
                summary.completed.append((number, title))

            elif verdict == "changes-requested":
                await client.create_comment(pr_number, review_body)
                await client.remove_label(number, "in-review")
                await client.add_labels(number, ["need-dev"])
                await client.create_comment(number, "review result: changes-requested")
        except Exception as exc:
            detail = _exception_detail(exc)
            _log.exception("Applying review result failed for #%d", number)
            await client.remove_label(number, "in-review")
            await client.add_labels(number, ["blocked"])
            await _try_comment(client, number, f"Blocked: applying review result failed — {detail}")
            summary.blocked.append((number, title))
            continue


# --- Step 3: Check dev progress ---

async def _step3_check_dev_progress(
    settings: Settings,
    project_root: str,
    client: GitHubClient,
    active_processes: dict[int, ActiveProcess],
    summary: PatrolSummary,
) -> None:
    issues = await client.list_issues(labels="in-dev")
    _log.debug("Step 3: found %d in-dev issues: %s", len(issues), [i["number"] for i in issues if not i.get("pull_request")])
    for issue in issues:
        if issue.get("pull_request"):
            continue
        number = issue["number"]
        title = issue["title"]
        comments = await client.list_comments(number)

        last_dispatch = _find_last_comment(comments, "dispatched dev")
        last_completion = _find_last_comment(comments, "dev completed: PR #")
        if last_completion and (not last_dispatch or comments.index(last_completion) > comments.index(last_dispatch)):
            _log.info("Step 3: #%d already completed, advancing to need-review", number)
            await client.remove_label(number, "in-dev")
            await client.add_labels(number, ["need-review"])
            continue

        entry = active_processes.get(number)
        if entry is None:
            continue
        proc, timer = entry

        if proc.returncode is None:
            continue

        # Process exited — cancel timeout timer and read result
        timer.cancel()
        active_processes.pop(number, None)
        worktree_dir = Path(project_root) / ".worktrees" / f"dev-{number}"
        stdout = (await proc.stdout.read()).decode(errors="replace") if proc.stdout else ""
        stderr = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""

        if proc.returncode == -signal.SIGTERM:
            _log.warning("Dev agent timed out for #%d", number)
            await cleanup_worktree(project_root, worktree_dir)
            await _handle_dispatch_failure(
                number, title, comments, "dev",
                settings.agent.max_retries,
                "Dev agent timed out",
                client, summary,
            )
            continue

        if proc.returncode != 0:
            _log.error("Dev agent crashed for #%d (exit %d)", number, proc.returncode)
            await cleanup_worktree(project_root, worktree_dir)
            await _handle_dispatch_failure(
                number, title, comments, "dev",
                settings.agent.max_retries,
                f"Dev agent crashed (exit {proc.returncode})",
                client, summary,
            )
            continue

        from tom.agents import _parse_output
        result = _parse_output(stdout, stderr)

        if isinstance(result, AgentFailure):
            await cleanup_worktree(project_root, worktree_dir)
            await _handle_dispatch_failure(
                number, title, comments, "dev",
                settings.agent.max_retries,
                result.reason,
                client, summary,
            )
            continue

        output = result.output

        if output.get("status") == "failure":
            await cleanup_worktree(project_root, worktree_dir)
            await client.remove_label(number, "in-dev")
            await client.add_labels(number, ["blocked"])
            await client.create_comment(
                number,
                f"Blocked: {output.get('failureReason', 'Dev agent declared failure')}",
            )
            summary.blocked.append((number, title))
            continue

        # Success — push, create/update PR
        try:
            branch_name = await _git(["branch", "--show-current"], cwd=str(worktree_dir))
            await _git(["push", "origin", branch_name], cwd=str(worktree_dir))
        except RuntimeError as exc:
            _log.error("Commit/push failed for #%d: %s", number, exc)
            await cleanup_worktree(project_root, worktree_dir)
            await client.remove_label(number, "in-dev")
            await client.add_labels(number, ["blocked"])
            await client.create_comment(number, f"Blocked: push failed — {exc}")
            summary.blocked.append((number, title))
            continue

        await cleanup_worktree(project_root, worktree_dir)
        if branch_name:
            try:
                await _git(["branch", "-D", branch_name], cwd=project_root)
            except RuntimeError:
                pass

        pr_comment = _find_last_comment(comments, "dev completed: PR #")
        try:
            if pr_comment:
                existing_pr = _extract_pr_number(pr_comment)
                if existing_pr:
                    await client.update_pr(existing_pr, body=output.get("prBody", ""))
                    if output.get("comment"):
                        await client.create_comment(existing_pr, output["comment"])
                    await client.create_comment(number, f"dev completed: PR #{existing_pr}")
            else:
                default_branch = await client.get_default_branch()
                pr = await client.create_pr(
                    output.get("prTitle", f"Fix #{number}"),
                    output.get("prBody", f"Closes #{number}"),
                    branch_name,
                    default_branch,
                )
                if output.get("comment"):
                    await client.create_comment(pr["number"], output["comment"])
                await client.create_comment(number, f"dev completed: PR #{pr['number']}")
        except Exception as exc:
            detail = _exception_detail(exc)
            _log.exception("PR creation failed for #%d", number)
            await client.remove_label(number, "in-dev")
            await client.add_labels(number, ["blocked"])
            await _try_comment(client, number, f"Blocked: PR creation failed — {detail}")
            summary.blocked.append((number, title))
            continue

        await client.remove_label(number, "in-dev")
        await client.add_labels(number, ["need-review"])


# --- Step 4: Dispatch review ---

async def _step4_dispatch_review(
    settings: Settings,
    project_root: str,
    client: GitHubClient,
    active_processes: dict[int, ActiveProcess],
    summary: PatrolSummary,
) -> None:
    issues = await client.list_issues(labels="need-review")
    issues = _sort_by_priority(issues)
    _log.debug("Step 4: found %d need-review issues: %s", len(issues), [i["number"] for i in issues if not i.get("pull_request")])
    for issue in issues:
        if issue.get("pull_request"):
            continue

        in_review = await client.list_issues(labels="in-review")
        review_count = sum(1 for i in in_review if not i.get("pull_request"))
        if review_count >= settings.review.concurrent:
            break

        number = issue["number"]
        title = issue["title"]
        comments = await client.list_comments(number)

        pr_comment = _find_last_comment(comments, "dev completed: PR #")
        if not pr_comment:
            continue
        pr_number = _extract_pr_number(pr_comment)
        if not pr_number:
            continue

        await client.remove_label(number, "need-review")
        await client.add_labels(number, ["in-review"])

        try:
            pr = await client.get_pr(pr_number)
            head_branch = pr["head"]["ref"]
            await fetch_origin(project_root)
            worktree_dir = await create_review_worktree(project_root, pr_number, head_branch)

            texts = [issue.get("body", "") or ""]
            texts.extend(c.get("body", "") or "" for c in comments)
            pr_texts = [pr.get("body", "") or ""]
            pr_comments = await client.list_comments(pr_number)
            pr_texts.extend(c.get("body", "") or "" for c in pr_comments)
            texts.extend(pr_texts)
            await download_attachments(settings.id, number, texts, client._token)

            proc = await spawn_agent(
                review_prompt(pr_number, number, settings.id),
                REVIEW_SCHEMA,
                cwd=str(worktree_dir),
            )
            timeout_secs = parse_interval_seconds(settings.agent.timeout)
            loop = asyncio.get_running_loop()
            timer = loop.call_later(timeout_secs, proc.terminate)
            active_processes[number] = (proc, timer)
            _log.info("Review agent dispatched for #%d (pid %d, timeout %s)", number, proc.pid, settings.agent.timeout)

            await client.create_comment(
                number,
                f"dispatched review for PR #{pr_number}\nProcess: {proc.pid}",
            )
            summary.review_dispatched.append((number, title))

        except Exception:
            _log.exception("Error dispatching review for #%d", number)
            await cleanup_worktree(project_root, Path(project_root) / ".worktrees" / f"review-{pr_number}")


# --- Step 5: Dispatch dev ---

async def _step5_dispatch_dev(
    settings: Settings,
    project_root: str,
    default_branch: str,
    client: GitHubClient,
    active_processes: dict[int, ActiveProcess],
    summary: PatrolSummary,
) -> None:
    issues = await client.list_issues(labels="need-dev")
    issues = [i for i in issues if not i.get("pull_request")]
    issues = _sort_by_priority(issues)
    _log.debug("Step 5: found %d need-dev issues: %s", len(issues), [i["number"] for i in issues])

    for issue in issues:
        in_dev = await client.list_issues(labels="in-dev")
        dev_count = sum(1 for i in in_dev if not i.get("pull_request"))
        if dev_count >= settings.dev.concurrent:
            break

        number = issue["number"]
        title = issue["title"]
        comments = await client.list_comments(number)

        deps = _parse_dependencies(issue.get("body", "") or "")
        if deps:
            blocked_dep = False
            for dep_number in deps:
                dep = await client.get_issue(dep_number)
                if dep["state"] != "closed":
                    blocked_dep = True
                    break
            if blocked_dep:
                _log.debug("Step 5: #%d waiting on unmet dependency, skipping", number)
                continue

        await client.remove_label(number, "need-dev")
        await client.add_labels(number, ["in-dev"])

        try:
            texts = [issue.get("body", "") or ""]
            texts.extend(c.get("body", "") or "" for c in comments)

            body = issue.get("body", "") or ""
            parent_match = re.search(r"Part of #(\d+)", body)
            if parent_match:
                parent_number = int(parent_match.group(1))
                parent = await client.get_issue(parent_number)
                parent_comments = await client.list_comments(parent_number)
                texts.append(parent.get("body", "") or "")
                texts.extend(c.get("body", "") or "" for c in parent_comments)

            await download_attachments(settings.id, number, texts, client._token)

            pr_comment = _find_last_comment(comments, "dev completed: PR #")
            if pr_comment:
                existing_pr = _extract_pr_number(pr_comment)
                if existing_pr:
                    pr = await client.get_pr(existing_pr)
                    head_branch = pr["head"]["ref"]
                    await fetch_origin(project_root)
                    worktree_dir = await create_redispatch_worktree(project_root, number, head_branch)
                else:
                    await fetch_origin(project_root)
                    worktree_dir = await create_dev_worktree(project_root, number, title, default_branch)
            else:
                await fetch_origin(project_root)
                worktree_dir = await create_dev_worktree(project_root, number, title, default_branch)

            proc = await spawn_agent(
                dev_prompt(number, settings.id),
                DEV_SCHEMA,
                cwd=str(worktree_dir),
            )
            timeout_secs = parse_interval_seconds(settings.agent.timeout)
            loop = asyncio.get_running_loop()
            timer = loop.call_later(timeout_secs, proc.terminate)
            active_processes[number] = (proc, timer)
            _log.info("Dev agent dispatched for #%d (pid %d, timeout %s)", number, proc.pid, settings.agent.timeout)

            await client.create_comment(
                number,
                f"dispatched dev\nProcess: {proc.pid}",
            )
            summary.dev_dispatched.append((number, title))

        except Exception as exc:
            _log.exception("Error dispatching dev for #%d", number)
            await cleanup_worktree(project_root, Path(project_root) / ".worktrees" / f"dev-{number}")
            await client.remove_label(number, "in-dev")
            await client.add_labels(number, ["blocked"])
            await client.create_comment(number, f"Blocked: dev dispatch failed — {exc}")
            summary.blocked.append((number, title))


# --- Step 6: Check parents ---

async def _step6_check_parents(client: GitHubClient, summary: PatrolSummary) -> None:
    issues = await client.list_issues(labels="parent")
    _log.debug("Step 6: found %d parent issues: %s", len(issues), [i["number"] for i in issues if not i.get("pull_request")])
    for issue in issues:
        if issue.get("pull_request"):
            continue
        number = issue["number"]
        title = issue["title"]
        comments = await client.list_comments(number)

        children_comment = _find_comment(comments, "## Children")
        if not children_comment:
            continue

        child_numbers = re.findall(r"#(\d+)", children_comment["body"])
        if not child_numbers:
            continue

        all_closed = True
        for child_num_str in child_numbers:
            child = await client.get_issue(int(child_num_str))
            if child["state"] != "closed":
                all_closed = False
                break

        if all_closed:
            await client.close_issue(number)
            summary.parents_completed.append((number, title))


# --- Step 7: Cleanup + summary ---

async def _step7_cleanup(client: GitHubClient) -> None:
    for label in ["in-review", "in-dev", "need-review", "need-dev"]:
        issues = await client.list_issues(state="closed", labels=label)
        for issue in issues:
            if issue.get("pull_request"):
                continue
            await client.remove_label(issue["number"], label)
            _log.debug("Removed stale label '%s' from closed #%d", label, issue["number"])


# --- Helpers ---

def _find_comment(comments: list[dict], prefix: str) -> dict | None:
    for c in comments:
        if (c.get("body") or "").startswith(prefix):
            return c
    return None


def _find_last_comment(comments: list[dict], prefix: str) -> dict | None:
    for c in reversed(comments):
        if (c.get("body") or "").startswith(prefix):
            return c
    return None


def _extract_pr_number(comment: dict) -> int | None:
    match = re.search(r"#(\d+)", comment.get("body", ""))
    return int(match.group(1)) if match else None


def _exception_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} {exc.response.text}"
    return str(exc)


async def _try_comment(client: GitHubClient, number: int, body: str) -> None:
    try:
        await client.create_comment(number, body)
    except Exception:
        _log.exception("Failed to post comment on #%d (continuing)", number)


def _parse_dependencies(body: str) -> list[int]:
    match = re.search(r"Depends on ([#\d,\s]+)", body)
    if not match:
        return []
    return [int(n) for n in re.findall(r"#(\d+)", match.group(1))]


def _count_failed_dispatches(comments: list[dict], agent_type: str) -> int:
    if agent_type == "dev":
        dispatch_prefix = "dispatched dev"
        completion_prefix = "dev completed: PR #"
    else:
        dispatch_prefix = "dispatched review"
        completion_prefix = "review "

    dispatch_count = 0
    completion_count = 0
    for c in comments:
        body = c.get("body", "") or ""
        if body.startswith(dispatch_prefix):
            dispatch_count += 1
        if body.startswith(completion_prefix):
            completion_count += 1

    return dispatch_count - completion_count


async def _handle_dispatch_failure(
    number: int,
    title: str,
    comments: list[dict],
    agent_type: str,
    max_retries: int,
    reason: str,
    client: GitHubClient,
    summary: PatrolSummary,
) -> None:
    failed_count = _count_failed_dispatches(comments, agent_type)
    label_in = f"in-{agent_type}" if agent_type == "dev" else "in-review"
    label_need = f"need-{agent_type}" if agent_type == "dev" else "need-review"

    if failed_count + 1 >= max_retries:
        await client.remove_label(number, label_in)
        await client.add_labels(number, ["blocked"])
        await client.create_comment(
            number,
            f"Blocked: {reason} ({failed_count + 1} attempts). See tom.log.",
        )
        summary.blocked.append((number, title))
    else:
        await client.remove_label(number, label_in)
        await client.add_labels(number, [label_need])
        summary.retried.append((number, title))


def _sort_by_priority(issues: list[dict]) -> list[dict]:
    def priority_key(issue: dict) -> tuple[int, int]:
        labels = {l["name"] for l in issue.get("labels", [])}
        for p, order in _PRIORITY_ORDER.items():
            if p in labels:
                return (order, issue["number"])
        return (len(_PRIORITY_ORDER), issue["number"])

    return sorted(issues, key=priority_key)
