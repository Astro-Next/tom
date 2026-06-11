from __future__ import annotations

import logging
from datetime import datetime, timezone

from tom.agents import AgentSuccess, run_agent
from tom.config import Settings, parse_interval_seconds
from tom.github import GitHubClient
from tom.prompts import analyst_prompt
from tom.schemas import ANALYST_SCHEMA
from tom.worktree import _git

_log = logging.getLogger("tom")


async def run_retro(
    settings: Settings,
    project_root: str,
    client: GitHubClient,
) -> dict | None:
    """Run one retro cycle. Returns the created issue dict, or None if no findings."""

    lookback_seconds = parse_interval_seconds(settings.retro.interval)
    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(now.timestamp() - lookback_seconds, tz=timezone.utc)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    merged_prs = await _find_merged_prs(client, since_iso)
    closed_issues = await _find_closed_issues(client, since_iso)

    if not merged_prs and not closed_issues:
        _log.info("Retro: no merged PRs or closed issues in window")
        return None

    pr_numbers = ", ".join(f"#{pr['number']}" for pr in merged_prs) or "none"
    issue_numbers = ", ".join(f"#{i['number']}" for i in closed_issues) or "none"

    _log.info("Retro scope: PRs %s, issues %s", pr_numbers, issue_numbers)

    default_branch = await client.get_default_branch()
    await _git(["checkout", default_branch], cwd=project_root)
    await _git(["pull", "--ff-only"], cwd=project_root)

    result = None
    for attempt in range(settings.agent.max_retries):
        result = await run_agent(
            analyst_prompt(pr_numbers, issue_numbers, settings.id),
            ANALYST_SCHEMA,
            cwd=project_root,
            timeout_str=settings.agent.timeout,
        )

        if isinstance(result, AgentSuccess):
            break
        _log.warning("Analyst agent failed (attempt %d/%d): %s", attempt + 1, settings.agent.max_retries, result.reason)
    else:
        _log.error("Analyst agent failed after %d attempts", settings.agent.max_retries)
        return None

    output = result.output

    if not output.get("hasFindings"):
        _log.info("Retro: no findings this round")
        return None

    title = f"[Retro] {output.get('title', 'Retrospective findings')}"
    body = output.get("body", "")

    issue = await client.create_issue(title, body, labels=["blocked"])
    _log.info("Created retro issue #%d: %s", issue["number"], title)

    return issue


async def _find_merged_prs(client: GitHubClient, since: str) -> list[dict]:
    query = f"repo:{client.owner}/{client.repo} is:pr is:merged merged:>={since}"
    results = await client.search_issues(query)
    return results


async def _find_closed_issues(client: GitHubClient, since: str) -> list[dict]:
    query = f"repo:{client.owner}/{client.repo} is:issue is:closed closed:>={since}"
    results = await client.search_issues(query)
    return [i for i in results if "pull_request" not in i]
