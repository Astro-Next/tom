from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tom.models import Settings, PatrolSettings, RetroSettings, AgentSettings, DevSettings, ReviewSettings
from tom.retro import run_retro


def _make_settings(**overrides) -> Settings:
    return Settings(
        id="test-proj",
        patrol=PatrolSettings(),
        retro=RetroSettings(interval="1d", time="22:00"),
        agent=AgentSettings(),
        dev=DevSettings(),
        review=ReviewSettings(),
    )


class TestRunRetro:
    @pytest.mark.asyncio
    async def test_creates_issue_when_findings(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(side_effect=[
            [{"number": 10, "title": "PR 10"}],
            [{"number": 42, "title": "Issue 42"}],
        ])
        client.create_issue = AsyncMock(return_value={"number": 99, "title": "[Retro] Test findings"})

        with patch("tom.retro.run_agent") as mock_agent, \
             patch("tom.retro._git", new_callable=AsyncMock):
            from tom.agents import AgentSuccess
            mock_agent.return_value = AgentSuccess(output={
                "hasFindings": True,
                "title": "Test findings",
                "body": "## Finding 1: Something\n\n**Observed:** ...",
            })

            result = await run_retro(settings, "/tmp/test", client)

        assert result is not None
        assert result["number"] == 99
        client.create_issue.assert_called_once()
        call_args = client.create_issue.call_args
        assert call_args[0][0] == "[Retro] Test findings"
        assert call_args[1]["labels"] == ["blocked"]

    @pytest.mark.asyncio
    async def test_no_issue_when_no_findings(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(side_effect=[
            [{"number": 10}],
            [{"number": 42}],
        ])

        with patch("tom.retro.run_agent") as mock_agent, \
             patch("tom.retro._git", new_callable=AsyncMock):
            from tom.agents import AgentSuccess
            mock_agent.return_value = AgentSuccess(output={
                "hasFindings": False,
                "title": None,
                "body": None,
            })

            result = await run_retro(settings, "/tmp/test", client)

        assert result is None
        client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_issue_when_nothing_in_scope(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(return_value=[])

        result = await run_retro(settings, "/tmp/test", client)

        assert result is None
        client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_issue_on_agent_failure(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(side_effect=[
            [{"number": 10}],
            [{"number": 42}],
        ])

        with patch("tom.retro.run_agent") as mock_agent, \
             patch("tom.retro._git", new_callable=AsyncMock):
            from tom.agents import AgentFailure
            mock_agent.return_value = AgentFailure(reason="crashed")

            result = await run_retro(settings, "/tmp/test", client)

        assert result is None
        assert mock_agent.call_count == settings.agent.max_retries
        client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(side_effect=[
            [{"number": 10}],
            [{"number": 42}],
        ])
        client.create_issue = AsyncMock(return_value={"number": 99, "title": "[Retro] Findings"})

        with patch("tom.retro.run_agent") as mock_agent, \
             patch("tom.retro._git", new_callable=AsyncMock):
            from tom.agents import AgentFailure, AgentSuccess
            mock_agent.side_effect = [
                AgentFailure(reason="first attempt crashed"),
                AgentSuccess(output={
                    "hasFindings": True,
                    "title": "Findings",
                    "body": "## Finding 1\n\n...",
                }),
            ]

            result = await run_retro(settings, "/tmp/test", client)

        assert result is not None
        assert result["number"] == 99
        assert mock_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_filters_prs_from_closed_issues(self):
        settings = _make_settings()
        client = AsyncMock()
        client.owner = "test"
        client.repo = "repo"
        client.search_issues = AsyncMock(side_effect=[
            [{"number": 10}],
            [
                {"number": 42, "title": "Real issue"},
                {"number": 43, "title": "PR", "pull_request": {"url": "..."}},
            ],
        ])

        with patch("tom.retro.run_agent") as mock_agent, \
             patch("tom.retro._git", new_callable=AsyncMock):
            from tom.agents import AgentSuccess
            mock_agent.return_value = AgentSuccess(output={
                "hasFindings": False, "title": None, "body": None,
            })

            await run_retro(settings, "/tmp/test", client)

        call_args = mock_agent.call_args
        prompt = call_args[0][0]
        assert "#42" in prompt
        assert "#43" not in prompt
