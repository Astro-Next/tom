from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

import httpx

_API_VERSION = "2022-11-28"
_log = logging.getLogger("tom")


def _parse_github_remote() -> tuple[str, str]:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    )
    url = result.stdout.strip()
    # SSH: git@github.com:owner/repo.git
    if url.startswith("git@"):
        path = url.split(":", 1)[1]
    # HTTPS: https://github.com/owner/repo.git
    elif "github.com" in url:
        path = url.split("github.com/", 1)[1]
    else:
        raise ValueError(f"Unsupported remote URL format: {url}")
    path = path.removesuffix(".git")
    parts = path.split("/")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse owner/repo from remote URL: {url}")
    return parts[0], parts[1]


def _read_gh_token() -> str:
    result = subprocess.run(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gh auth token returned empty — run 'gh auth login'")
    return token


class GitHubClient:
    def __init__(self, owner: str | None = None, repo: str | None = None) -> None:
        if owner and repo:
            self._owner = owner
            self._repo = repo
        else:
            self._owner, self._repo = _parse_github_remote()
        self._token = _read_gh_token()
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=30.0,
        )
        self._etags: dict[str, str] = {}
        self._etag_cache: dict[str, Any] = {}

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def repo(self) -> str:
        return self._repo

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        use_etag: bool = False,
    ) -> httpx.Response:
        url = path
        headers = self._headers()

        if use_etag and url in self._etags:
            headers["If-None-Match"] = self._etags[url]

        resp = await self._client.request(
            method, url, headers=headers, json=json, params=params,
        )

        # Retry once with fresh token on 401
        if resp.status_code == 401:
            _log.warning("GitHub API 401 — refreshing token")
            self._token = _read_gh_token()
            headers = self._headers()
            if use_etag and url in self._etags:
                headers["If-None-Match"] = self._etags[url]
            resp = await self._client.request(
                method, url, headers=headers, json=json, params=params,
            )

        if use_etag and resp.status_code == 304:
            return resp

        if use_etag and "etag" in resp.headers:
            self._etags[url] = resp.headers["etag"]
            self._etag_cache[url] = resp.json()

        resp.raise_for_status()
        return resp

    async def _get_json(self, path: str, *, params: dict[str, str] | None = None, use_etag: bool = False) -> Any:
        resp = await self._request("GET", path, params=params, use_etag=use_etag)
        if resp.status_code == 304:
            return self._etag_cache[path]
        return resp.json()

    def _repo_path(self, suffix: str) -> str:
        return f"/repos/{self._owner}/{self._repo}{suffix}"

    # --- Repository ---

    async def get_repo(self) -> dict:
        return await self._get_json(self._repo_path(""))

    async def get_default_branch(self) -> str:
        repo = await self.get_repo()
        return repo["default_branch"]

    # --- Issues ---

    async def list_issues(
        self,
        *,
        state: str = "open",
        labels: str | None = None,
        per_page: int = 100,
    ) -> list[dict]:
        params: dict[str, str] = {"state": state, "per_page": str(per_page)}
        if labels:
            params["labels"] = labels
        return await self._get_json(self._repo_path("/issues"), params=params)

    async def get_issue(self, number: int) -> dict:
        return await self._get_json(self._repo_path(f"/issues/{number}"))

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict:
        data: dict[str, Any] = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
        resp = await self._request("POST", self._repo_path("/issues"), json=data)
        return resp.json()

    async def update_issue(self, number: int, *, body: str) -> dict:
        resp = await self._request("PATCH", self._repo_path(f"/issues/{number}"), json={"body": body})
        return resp.json()

    async def close_issue(self, number: int) -> dict:
        resp = await self._request("PATCH", self._repo_path(f"/issues/{number}"), json={"state": "closed"})
        return resp.json()

    async def add_labels(self, issue_number: int, labels: list[str]) -> list[dict]:
        resp = await self._request(
            "POST", self._repo_path(f"/issues/{issue_number}/labels"), json={"labels": labels},
        )
        return resp.json()

    async def remove_label(self, issue_number: int, label: str) -> None:
        try:
            await self._request("DELETE", self._repo_path(f"/issues/{issue_number}/labels/{label}"))
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise

    # --- Comments ---

    async def list_comments(self, issue_number: int) -> list[dict]:
        return await self._get_json(self._repo_path(f"/issues/{issue_number}/comments"), use_etag=True)

    async def create_comment(self, issue_number: int, body: str) -> dict:
        resp = await self._request(
            "POST", self._repo_path(f"/issues/{issue_number}/comments"), json={"body": body},
        )
        return resp.json()

    # --- Pull Requests ---

    async def get_pr(self, number: int) -> dict:
        return await self._get_json(self._repo_path(f"/pulls/{number}"))

    async def list_prs(self, *, state: str = "open", head: str | None = None) -> list[dict]:
        params: dict[str, str] = {"state": state, "per_page": "100"}
        if head:
            params["head"] = f"{self._owner}:{head}"
        return await self._get_json(self._repo_path("/pulls"), params=params)

    async def create_pr(self, title: str, body: str, head: str, base: str) -> dict:
        resp = await self._request(
            "POST",
            self._repo_path("/pulls"),
            json={"title": title, "body": body, "head": head, "base": base},
        )
        return resp.json()

    async def update_pr(self, number: int, *, title: str | None = None, body: str | None = None) -> dict:
        data: dict[str, str] = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        resp = await self._request("PATCH", self._repo_path(f"/pulls/{number}"), json=data)
        return resp.json()

    async def merge_pr(self, number: int, *, merge_method: str = "squash") -> dict:
        resp = await self._request(
            "PUT", self._repo_path(f"/pulls/{number}/merge"), json={"merge_method": merge_method},
        )
        return resp.json()

    async def delete_branch(self, branch: str) -> None:
        try:
            await self._request("DELETE", self._repo_path(f"/git/refs/heads/{branch}"))
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 422:
                raise

    # --- Labels ---

    async def list_labels(self) -> list[dict]:
        return await self._get_json(self._repo_path("/labels"), params={"per_page": "100"})

    async def create_label(self, name: str, color: str, description: str = "") -> dict:
        resp = await self._request(
            "POST",
            self._repo_path("/labels"),
            json={"name": name, "color": color, "description": description},
        )
        return resp.json()

    async def update_label(self, name: str, *, color: str, description: str = "") -> dict:
        resp = await self._request(
            "PATCH",
            self._repo_path(f"/labels/{name}"),
            json={"color": color, "description": description},
        )
        return resp.json()

    async def delete_label(self, name: str) -> None:
        try:
            await self._request("DELETE", self._repo_path(f"/labels/{name}"))
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise

    # --- Search (for PRs by issue reference) ---

    async def search_issues(self, query: str) -> list[dict]:
        resp = await self._get_json("/search/issues", params={"q": query})
        return resp.get("items", [])

    # --- Lifecycle ---

    async def close(self) -> None:
        await self._client.aclose()
