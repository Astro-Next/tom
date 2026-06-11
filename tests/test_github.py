import pytest

from tom.github import _parse_github_remote


class TestParseGitHubRemote:
    def test_https(self, monkeypatch):
        monkeypatch.setattr(
            "tom.github.subprocess.run",
            lambda *a, **kw: type("R", (), {"stdout": "https://github.com/owner/repo.git\n"})(),
        )
        assert _parse_github_remote() == ("owner", "repo")

    def test_https_no_git_suffix(self, monkeypatch):
        monkeypatch.setattr(
            "tom.github.subprocess.run",
            lambda *a, **kw: type("R", (), {"stdout": "https://github.com/owner/repo\n"})(),
        )
        assert _parse_github_remote() == ("owner", "repo")

    def test_ssh(self, monkeypatch):
        monkeypatch.setattr(
            "tom.github.subprocess.run",
            lambda *a, **kw: type("R", (), {"stdout": "git@github.com:owner/repo.git\n"})(),
        )
        assert _parse_github_remote() == ("owner", "repo")

    def test_ssh_no_git_suffix(self, monkeypatch):
        monkeypatch.setattr(
            "tom.github.subprocess.run",
            lambda *a, **kw: type("R", (), {"stdout": "git@github.com:owner/repo\n"})(),
        )
        assert _parse_github_remote() == ("owner", "repo")

    def test_unsupported_url(self, monkeypatch):
        monkeypatch.setattr(
            "tom.github.subprocess.run",
            lambda *a, **kw: type("R", (), {"stdout": "https://gitlab.com/owner/repo.git\n"})(),
        )
        with pytest.raises(ValueError, match="Unsupported remote URL"):
            _parse_github_remote()


class TestGitHubClientEtag:
    @pytest.mark.asyncio
    async def test_etag_caching(self, monkeypatch):
        import httpx
        from unittest.mock import AsyncMock

        from tom.github import GitHubClient

        monkeypatch.setattr("tom.github._parse_github_remote", lambda: ("owner", "repo"))
        monkeypatch.setattr("tom.github._read_gh_token", lambda: "fake-token")

        client = GitHubClient()

        first_resp = httpx.Response(
            200,
            json=[{"id": 1, "body": "test"}],
            headers={"etag": '"abc123"'},
            request=httpx.Request("GET", "https://api.github.com/repos/owner/repo/issues/1/comments"),
        )
        second_resp = httpx.Response(
            304,
            request=httpx.Request("GET", "https://api.github.com/repos/owner/repo/issues/1/comments"),
        )

        call_count = 0

        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_resp
            return second_resp

        client._client.request = mock_request

        result1 = await client.list_comments(1)
        assert result1 == [{"id": 1, "body": "test"}]

        result2 = await client.list_comments(1)
        assert result2 == [{"id": 1, "body": "test"}]

        await client.close()


class TestGitHubClientAuthRetry:
    @pytest.mark.asyncio
    async def test_retries_on_401(self, monkeypatch):
        import httpx

        from tom.github import GitHubClient

        monkeypatch.setattr("tom.github._parse_github_remote", lambda: ("owner", "repo"))

        token_calls = []

        def mock_read_token():
            token_calls.append(1)
            return f"token-{len(token_calls)}"

        monkeypatch.setattr("tom.github._read_gh_token", mock_read_token)

        client = GitHubClient()

        call_count = 0

        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    401,
                    request=httpx.Request("GET", "https://api.github.com/repos/owner/repo"),
                )
            return httpx.Response(
                200,
                json={"default_branch": "main"},
                request=httpx.Request("GET", "https://api.github.com/repos/owner/repo"),
            )

        client._client.request = mock_request

        repo = await client.get_repo()
        assert repo["default_branch"] == "main"
        assert len(token_calls) == 2  # initial + retry

        await client.close()
