from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

_log = logging.getLogger("tom")

_IMG_TAG_RE = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_MD_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
_FILE_LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\((https://github\.com/[^)]*user-attachments[^)]+)\)')


def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _extension_from_url(url: str) -> str | None:
    path = urlparse(url).path
    if "." in path.split("/")[-1]:
        return "." + path.split("/")[-1].rsplit(".", 1)[1]
    return None


def extract_attachment_urls(text: str) -> list[tuple[str, str]]:
    """Extract attachment URLs from issue/PR body text.

    Returns list of (url, extension) tuples. Skips bare file links with no extension.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for url in _IMG_TAG_RE.findall(text):
        if url not in seen:
            seen.add(url)
            ext = _extension_from_url(url) or ".png"
            results.append((url, ext))

    for url in _IMG_MD_RE.findall(text):
        if url not in seen:
            seen.add(url)
            ext = _extension_from_url(url) or ".png"
            results.append((url, ext))

    for _, url in _FILE_LINK_RE.findall(text):
        if url not in seen:
            seen.add(url)
            ext = _extension_from_url(url)
            if ext is None:
                continue
            results.append((url, ext))

    return results


def cache_path(project_id: str, issue_number: int, url: str, ext: str) -> Path:
    return Path(f"/tmp/tom-{project_id}/cache/{issue_number}/{_hash_url(url)}{ext}")


async def download_attachments(
    project_id: str,
    issue_number: int,
    texts: list[str],
    token: str,
) -> list[Path]:
    """Download attachments from issue/PR text content to local cache.

    Returns list of paths that were downloaded (or already existed).
    """
    all_urls: list[tuple[str, str]] = []
    for text in texts:
        all_urls.extend(extract_attachment_urls(text))

    if not all_urls:
        return []

    downloaded: list[Path] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for url, ext in all_urls:
            path = cache_path(project_id, issue_number, url, ext)

            if path.exists() and path.stat().st_size > 0:
                downloaded.append(path)
                continue

            path.parent.mkdir(parents=True, exist_ok=True)

            try:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True)
                resp.raise_for_status()
                if len(resp.content) == 0:
                    _log.warning("Empty response for attachment: %s", url)
                    continue
                path.write_bytes(resp.content)
                downloaded.append(path)
                _log.debug("Cached attachment: %s -> %s", url, path)
            except Exception:
                _log.warning("Failed to download attachment: %s", url, exc_info=True)
                if path.exists():
                    path.unlink()

    return downloaded
