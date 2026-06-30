from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tom.models import Settings, PatrolSettings, RetroSettings, AgentSettings, DevSettings, ReviewSettings
from tom.patrol import (
    PatrolSummary,
    _comment_age_exceeds,
    _count_failed_dispatches,
    _find_comment,
    _find_last_comment,
    _parse_dependencies,
    _sort_by_priority,
    _step1_triage,
    _step2_check_review_progress,
    _step3_check_dev_progress,
    _step5_dispatch_dev,
    _step6_check_parents,
    _step7_cleanup,
)


def _make_settings(**overrides) -> Settings:
    return Settings(
        id="test-proj",
        patrol=PatrolSettings(),
        retro=RetroSettings(),
        agent=AgentSettings(),
        dev=DevSettings(),
        review=ReviewSettings(),
    )


def _make_issue(number: int, title: str, labels: list[str] | None = None, state: str = "open") -> dict:
    return {
        "number": number,
        "title": title,
        "body": f"Issue {number} body",
        "state": state,
        "labels": [{"name": l} for l in (labels or [])],
    }


class TestPatrolSummary:
    def test_format_empty(self):
        s = PatrolSummary()
        assert s.format() is None

    def test_format_single_item(self):
        s = PatrolSummary()
        s.triaged.append((42, "Add login"))
        text = s.format()
        assert "1 new issue triaged" in text
        assert "#42: Add login" in text

    def test_format_plural(self):
        s = PatrolSummary()
        s.dev_dispatched.append((1, "A"))
        s.dev_dispatched.append((2, "B"))
        text = s.format()
        assert "2 dev agents dispatched" in text

    def test_format_skips_zero_count(self):
        s = PatrolSummary()
        s.triaged.append((1, "A"))
        text = s.format()
        assert "blocked" not in text
        assert "retried" not in text

    def test_format_multiple_categories(self):
        s = PatrolSummary()
        s.triaged.append((1, "A"))
        s.blocked.append((2, "B"))
        text = s.format()
        assert "triaged" in text
        assert "blocked" in text


class TestHelpers:
    def test_find_comment(self):
        comments = [
            {"body": "dev completed: PR #10"},
            {"body": "Some other comment"},
        ]
        assert _find_comment(comments, "dev completed: PR #")["body"] == "dev completed: PR #10"
        assert _find_comment(comments, "review") is None

    def test_find_last_comment(self):
        comments = [
            {"body": "dispatched dev\nProcess: 1\nStarted: 2026-01-01 00:00 UTC"},
            {"body": "dispatched dev\nProcess: 2\nStarted: 2026-01-02 00:00 UTC"},
        ]
        result = _find_last_comment(comments, "dispatched dev")
        assert "Process: 2" in result["body"]

    def test_count_failed_dispatches_dev(self):
        comments = [
            {"body": "dispatched dev\n..."},
            {"body": "dev completed: PR #10"},
            {"body": "dispatched dev\n..."},
        ]
        assert _count_failed_dispatches(comments, "dev") == 1

    def test_count_failed_dispatches_review(self):
        comments = [
            {"body": "dispatched review for PR #10\n..."},
            {"body": "dispatched review for PR #10\n..."},
            {"body": "review approved"},
        ]
        assert _count_failed_dispatches(comments, "review") == 1

    def test_count_failed_dispatches_none(self):
        comments = [
            {"body": "dispatched dev\n..."},
            {"body": "dev completed: PR #10"},
        ]
        assert _count_failed_dispatches(comments, "dev") == 0

    def test_sort_by_priority(self):
        issues = [
            _make_issue(3, "C", ["need-dev", "p2"]),
            _make_issue(1, "A", ["need-dev", "p0"]),
            _make_issue(2, "B", ["need-dev", "p1"]),
            _make_issue(4, "D", ["need-dev"]),
        ]
        sorted_issues = _sort_by_priority(issues)
        assert [i["number"] for i in sorted_issues] == [1, 2, 3, 4]


class TestStep1Triage:
    @pytest.mark.asyncio
    async def test_triage_need_dev(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Add login")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"
        client.add_labels = AsyncMock()
        client.create_comment = AsyncMock()

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentSuccess
            mock_await.return_value = AgentSuccess(output={
                "decision": "need-dev",
                "type": "feature",
                "priority": "p1",
            })

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.add_labels.assert_called_with(42, ["need-dev", "feature", "p1"])
        assert len(summary.triaged) == 1
        # Verify tracking comment was posted
        tracking_calls = [c for c in client.create_comment.call_args_list if "triaging" in str(c)]
        assert len(tracking_calls) == 1

    @pytest.mark.asyncio
    async def test_triage_blocked(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Unclear issue")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentSuccess
            mock_await.return_value = AgentSuccess(output={
                "decision": "blocked",
                "reason": "Requirements are unclear",
            })

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.add_labels.assert_called_once_with(42, ["blocked"])
        assert len(summary.blocked) == 1

    @pytest.mark.asyncio
    async def test_triage_parent_feature(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Big feature")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"
        client.create_issue = AsyncMock(side_effect=[
            {"number": 43},
            {"number": 44},
        ])

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentSuccess
            mock_await.return_value = AgentSuccess(output={
                "decision": "parent",
                "type": "feature",
                "priority": "p1",
                "children": [
                    {
                        "title": "Part A",
                        "description": "First part",
                        "acceptanceCriteria": ["Does A"],
                        "context": "See foo.py",
                        "priority": "p0",
                    },
                    {
                        "title": "Part B",
                        "description": "Second part",
                        "acceptanceCriteria": ["Does B"],
                        "context": "See bar.py",
                        "priority": "p1",
                    },
                ],
            })

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.add_labels.assert_any_call(42, ["parent", "feature", "p1"])
        assert client.create_issue.call_count == 2
        child_labels = [c.kwargs["labels"] for c in client.create_issue.call_args_list]
        assert child_labels[0] == ["need-dev", "feature", "p0"]
        assert child_labels[1] == ["need-dev", "feature", "p1"]
        assert len(summary.triaged) == 1

    @pytest.mark.asyncio
    async def test_triage_parent_bug(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Big bug")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"
        client.create_issue = AsyncMock(side_effect=[
            {"number": 43},
        ])

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentSuccess
            mock_await.return_value = AgentSuccess(output={
                "decision": "parent",
                "type": "bug",
                "priority": "p0",
                "children": [
                    {
                        "title": "Fix part A",
                        "description": "First fix",
                        "acceptanceCriteria": ["Fixes A"],
                        "context": "See foo.py",
                        "priority": "p0",
                    },
                ],
            })

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.add_labels.assert_any_call(42, ["parent", "bug", "p0"])
        child_labels = client.create_issue.call_args.kwargs["labels"]
        assert child_labels == ["need-dev", "bug", "p0"]
        assert len(summary.triaged) == 1

    @pytest.mark.asyncio
    async def test_triage_parent_appends_depends_on(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Big feature")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"
        client.create_issue = AsyncMock(side_effect=[
            {"number": 43},
            {"number": 44},
        ])

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentSuccess
            mock_await.return_value = AgentSuccess(output={
                "decision": "parent",
                "type": "feature",
                "priority": "p1",
                "children": [
                    {"title": "A", "description": "d", "acceptanceCriteria": ["a"], "context": "c", "priority": "p0"},
                    {"title": "B", "description": "d", "acceptanceCriteria": ["a"], "context": "c", "priority": "p1", "dependsOn": [0]},
                ],
            })

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.update_issue.assert_called_once()
        assert client.update_issue.call_args.args[0] == 44
        assert "Depends on #43" in client.update_issue.call_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_skips_labeled_issues(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[
            _make_issue(1, "In dev", ["in-dev"]),
            _make_issue(2, "Blocked", ["blocked"]),
            _make_issue(3, "Parent", ["parent"]),
        ])

        with patch("tom.patrol.spawn_agent") as mock_spawn:
            await _step1_triage(settings, "/tmp/test", "main", client, summary)
            mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_triage_agent_failure(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Test")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentFailure
            mock_await.return_value = AgentFailure(reason="claude crashed")

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        client.add_labels.assert_called_once_with(42, ["blocked"])
        assert len(summary.blocked) == 1

    @pytest.mark.asyncio
    async def test_triage_retries_on_failure(self):
        settings = _make_settings()
        summary = PatrolSummary()

        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(42, "Test")])
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol._git", new_callable=AsyncMock), \
             patch("tom.patrol.spawn_agent") as mock_spawn, \
             patch("tom.patrol.await_agent") as mock_await:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            from tom.agents import AgentFailure, AgentSuccess
            mock_await.side_effect = [
                AgentFailure(reason="first attempt crashed"),
                AgentSuccess(output={"decision": "need-dev", "type": "bug", "priority": "p1"}),
            ]

            await _step1_triage(settings, "/tmp/test", "main", client, summary)

        assert mock_spawn.call_count == 2
        client.add_labels.assert_called_with(42, ["need-dev", "bug", "p1"])
        assert len(summary.triaged) == 1
        assert len(summary.blocked) == 0


class TestStep5DispatchDev:
    @pytest.mark.asyncio
    async def test_dispatch_dev_skips_unmet_dependency(self):
        settings = _make_settings()
        summary = PatrolSummary()

        child = _make_issue(44, "Dependent", ["need-dev"])
        child["body"] = "Part of #42\n\nDepends on #43\n\nwork"
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[child])
        client.get_issue = AsyncMock(return_value=_make_issue(43, "Dep", state="open"))

        await _step5_dispatch_dev(settings, "/tmp/test", "main", client, {}, summary)

        client.add_labels.assert_not_called()
        assert summary.dev_dispatched == []

    @pytest.mark.asyncio
    async def test_dispatch_dev_proceeds_when_dependency_closed(self):
        settings = _make_settings()
        summary = PatrolSummary()

        child = _make_issue(44, "Dependent", ["need-dev"])
        child["body"] = "Part of #42\n\nDepends on #43\n\nwork"
        client = AsyncMock()
        client.list_issues = AsyncMock(side_effect=[[child], []])
        client.get_issue = AsyncMock(return_value=_make_issue(43, "Dep", state="closed"))
        client.list_comments = AsyncMock(return_value=[])
        client._token = "fake-token"

        with patch("tom.patrol.download_attachments", new_callable=AsyncMock), \
             patch("tom.patrol.fetch_origin", new_callable=AsyncMock), \
             patch("tom.patrol.create_dev_worktree", new_callable=AsyncMock) as mock_wt, \
             patch("tom.patrol.spawn_agent") as mock_spawn:
            mock_wt.return_value = "/tmp/test/.worktrees/dev-44"
            mock_proc = AsyncMock()
            mock_proc.pid = 999
            mock_spawn.return_value = mock_proc
            await _step5_dispatch_dev(settings, "/tmp/test", "main", client, {}, summary)

        client.add_labels.assert_any_call(44, ["in-dev"])
        assert summary.dev_dispatched == [(44, "Dependent")]


class TestParseDependencies:
    def test_no_dependency(self):
        assert _parse_dependencies("Part of #42\n\nwork") == []

    def test_single(self):
        assert _parse_dependencies("Part of #42\n\nDepends on #43\n\nwork") == [43]

    def test_multiple(self):
        assert _parse_dependencies("Depends on #43, #44, #45") == [43, 44, 45]


class TestStep6Parents:
    @pytest.mark.asyncio
    async def test_closes_parent_when_all_children_closed(self):
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(10, "Parent", ["parent"])])
        client.list_comments = AsyncMock(return_value=[
            {"body": "## Children\n\n- [ ] P0: #11 — Part A\n- [ ] P1: #12 — Part B"},
        ])
        client.get_issue = AsyncMock(side_effect=[
            {"state": "closed"},
            {"state": "closed"},
        ])

        await _step6_check_parents(client, summary)

        client.close_issue.assert_called_once_with(10)
        assert len(summary.parents_completed) == 1

    @pytest.mark.asyncio
    async def test_does_not_close_parent_with_open_children(self):
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(10, "Parent", ["parent"])])
        client.list_comments = AsyncMock(return_value=[
            {"body": "## Children\n\n- [ ] P0: #11 — Part A\n- [ ] P1: #12 — Part B"},
        ])
        client.get_issue = AsyncMock(side_effect=[
            {"state": "closed"},
            {"state": "open"},
        ])

        await _step6_check_parents(client, summary)

        client.close_issue.assert_not_called()


class TestStep7Cleanup:
    @pytest.mark.asyncio
    async def test_removes_stale_labels_from_closed_issues(self):
        client = AsyncMock()
        client.list_issues = AsyncMock(side_effect=[
            [_make_issue(1, "A", ["in-review"], state="closed")],
            [],
            [],
            [],
        ])

        await _step7_cleanup(client)

        client.remove_label.assert_called_with(1, "in-review")


def _dispatch_comment(prefix: str, *, age_minutes: float) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return {"body": f"{prefix}\nProcess: 1718", "created_at": ts.isoformat().replace("+00:00", "Z")}


class TestCommentAge:
    def test_stale_comment_exceeds(self):
        c = _dispatch_comment("dispatched dev", age_minutes=60)
        assert _comment_age_exceeds(c, 1800) is True

    def test_fresh_comment_within(self):
        c = _dispatch_comment("dispatched dev", age_minutes=1)
        assert _comment_age_exceeds(c, 1800) is False

    def test_missing_timestamp(self):
        assert _comment_age_exceeds({"body": "dispatched dev"}, 1800) is False

    def test_malformed_timestamp(self):
        assert _comment_age_exceeds({"created_at": "not-a-date"}, 1800) is False


class TestStep3Orphan:
    @pytest.mark.asyncio
    async def test_stale_orphan_blocks(self):
        settings = _make_settings()
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(13, "Stuck", ["in-dev"])])
        client.list_comments = AsyncMock(return_value=[_dispatch_comment("dispatched dev", age_minutes=120)])
        client.search_issues = AsyncMock(return_value=[])

        await _step3_check_dev_progress(settings, "/repo", client, {}, summary)

        client.remove_label.assert_any_call(13, "in-dev")
        client.add_labels.assert_any_call(13, ["blocked"])
        assert (13, "Stuck") in summary.blocked
        body = client.create_comment.call_args.args[1]
        assert body.startswith("Blocked: dev agent produced no result")

    @pytest.mark.asyncio
    async def test_fresh_orphan_skips(self):
        settings = _make_settings()
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(13, "Recent", ["in-dev"])])
        client.list_comments = AsyncMock(return_value=[_dispatch_comment("dispatched dev", age_minutes=1)])

        await _step3_check_dev_progress(settings, "/repo", client, {}, summary)

        client.add_labels.assert_not_called()
        assert summary.blocked == []

    @pytest.mark.asyncio
    async def test_probe_failure_still_blocks(self):
        settings = _make_settings()
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(13, "Stuck", ["in-dev"])])
        client.list_comments = AsyncMock(return_value=[_dispatch_comment("dispatched dev", age_minutes=120)])
        client.search_issues = AsyncMock(side_effect=RuntimeError("API down"))

        await _step3_check_dev_progress(settings, "/repo", client, {}, summary)

        client.add_labels.assert_any_call(13, ["blocked"])
        assert (13, "Stuck") in summary.blocked

    @pytest.mark.asyncio
    async def test_comment_failure_still_blocks(self):
        settings = _make_settings()
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(13, "Stuck", ["in-dev"])])
        client.list_comments = AsyncMock(return_value=[_dispatch_comment("dispatched dev", age_minutes=120)])
        client.search_issues = AsyncMock(return_value=[])
        client.create_comment = AsyncMock(side_effect=RuntimeError("rate limited"))

        await _step3_check_dev_progress(settings, "/repo", client, {}, summary)

        client.add_labels.assert_any_call(13, ["blocked"])
        assert (13, "Stuck") in summary.blocked


class TestStep2Orphan:
    @pytest.mark.asyncio
    async def test_stale_review_orphan_blocks(self):
        settings = _make_settings()
        summary = PatrolSummary()
        client = AsyncMock()
        client.list_issues = AsyncMock(return_value=[_make_issue(13, "Stuck", ["in-review"])])
        client.list_comments = AsyncMock(
            return_value=[_dispatch_comment("dispatched review for PR #20", age_minutes=120)]
        )
        client.search_issues = AsyncMock(return_value=[])

        await _step2_check_review_progress(settings, "/repo", client, {}, summary)

        client.remove_label.assert_any_call(13, "in-review")
        client.add_labels.assert_any_call(13, ["blocked"])
        assert (13, "Stuck") in summary.blocked
