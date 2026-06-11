# Attachments

GitHub issue content (bodies and comments) may contain image or file attachment URLs — pasted screenshots, uploaded logs, diagrams, or external links. Agents need to read these to do their work, but agents never fetch URLs themselves. Tom downloads attachments to a local cache and agents read them from disk.

Tom owns the authenticated fetch and the cache; the agent reads files from disk. Keeping the download in Tom's deterministic Python code (see [Architecture — Subprocess model](architecture.md#subprocess-model)) means attachments are fetched once, with credentials, and every agent finds them at a predictable path.

## The cache

All attachments live under an ephemeral, project-scoped cache directory:

```
/tmp/tom-{project-id}/cache/{issue_number}/{hash}.{ext}
```

- **`{project-id}`** — the `id` field from `.tom/settings.json`.
- **`{issue_number}`** — the issue the attachment was found on. PR attachments are cached under the issue the PR addresses.
- **`{hash}`** — first 12 characters of the URL's SHA hash: `echo -n "<url>" | shasum | cut -c1-12`.
- **`{ext}`** — the file extension (see [Extension rules](#extension-rules)).

The cache is in `/tmp`, so it does not survive a reboot and is never committed. This is intentional: GitHub is the source of truth, and any lost file can be re-downloaded from its URL. Nothing depends on the cache persisting.

## Download

### Who downloads

**Tom downloads. Agents never download.** Tom runs the download in its own Python process, authenticated with `GITHUB_TOKEN`, before spawning any agent that will read the issue.

### When Tom downloads

Tom downloads attachments at the moment it reads issue content during a patrol step, immediately before invoking an agent that needs that content. Concretely, this happens in:

- **Triage (patrol step 1)** — before invoking the PM agent on a new issue.
- **Dev dispatch (patrol step 5)** — before invoking the Dev agent on a `need-dev` issue. For re-dispatches, the parent issue's attachments are fetched too if the issue body contains `Part of #N`.
- **Review dispatch (patrol step 4)** — before invoking the Review agent. Tom scans the issue it addresses (and its parent) **and the PR's own body and comments**, because reviewers often paste screenshots on the PR. PR attachments are cached under the same issue directory (the issue the PR addresses).

Tom downloads only the attachments belonging to the specific issue (and parent, when linked) being dispatched, plus the PR's own attachments at review dispatch — never a bulk sweep across all issues.

### How Tom downloads

1. **Scan content** — collect the issue body and all comment bodies (and, at review dispatch, the PR body and its comments), then extract attachment URLs:
   - **Image markup** — `<img src="https://...">` tags and `![alt](https://...)` markdown. These are always images.
   - **File links** — `[text](https://github.com/user-attachments/...)` markdown links pointing to GitHub attachments.
2. **Resolve each URL to a cache path** — compute the hash and extension, build the path under the issue's cache directory.
3. **Skip if already cached** — if the file exists, do nothing. Downloads are idempotent; re-running a step never re-fetches a file that is already on disk.
4. **Fetch** — issue an authenticated `GET` (Bearer `GITHUB_TOKEN`) via `httpx`, writing the body to the cache path.
5. **Discard empties** — if the download fails or produces a zero-byte file, delete the partial file. A missing cache entry is always treated as "not available" downstream.

### Extension rules

The extension is taken from the URL path (after stripping any query string). When the URL has no extension:

| Source | No extension → |
|--------|---------------|
| Image markup (`<img>` or `![]()`) | Default to `.png` — the markup guarantees it is an image. |
| File link (`[text](url)`) | **Skip the attachment.** Without an extension the file type is unknown, so it is not downloaded. |

## Access

Agents read attachments from the cache; they never compute URLs into network requests.

When an agent encounters an attachment URL in the issue content it was given, it resolves the URL to a local path using the same rules Tom used to write it:

1. Hash the URL: `echo -n "<url>" | shasum | cut -c1-12`.
2. Determine the extension (URL path, or `.png` for image markup with no extension; skip a bare file link with no extension).
3. Build the path: `/tmp/tom-{project-id}/cache/{issue_number}/{hash}.{ext}`.
4. If the file exists, read it. If not, skip it and continue — see [Missing attachments](#missing-attachments).

Because Tom and the agent compute the path identically, the agent always looks in the right place without being told where each file landed.

## Missing attachments

An attachment can be absent from the cache for several reasons: it had no extension on a bare file link (never downloaded), its download failed or returned zero bytes, or the URL is dead.

**Agents never trigger a download.** If a file an agent expected is not on disk, the agent skips it silently and proceeds with the rest of the issue content. Downloading is Tom's job, done before dispatch — the agent works with whatever the cache holds.

**Tom re-downloads only on its next dispatch of that issue.** Because Tom fetches at dispatch time and skips files already cached, a transiently failed download is naturally retried the next time that issue is dispatched (e.g. a `need-dev` issue picked up on a later patrol cycle, or a re-dispatch after review). Within a single cycle there is no retry — a failed fetch is left missing and the agent skips it. Permanent cases (no-extension file links, genuinely dead URLs) are simply never satisfied, which is correct: there is nothing valid to download.

### Retro does not download

The Retro loop reads many merged PRs and closed issues in a single cycle (see [Retro](retro.md)). It **reads from the cache only and never triggers downloads.** Re-fetching attachments across every issue and PR a retro touches would be a large, wasteful burst of network and disk activity for marginal benefit. Retro works with whatever patrol already cached during the normal lifecycle; any attachment not present is skipped. If an attachment matters for analysis, it was cached when the issue was dispatched during patrol.
