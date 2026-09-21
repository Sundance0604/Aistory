# Current scraper audit

Audit date: 2026-09-21.

## Existing flow

- Authentication uses a visible Chromium/Chrome persistent context rooted at `browser_profile/`. `ensure_logged_in` checks `/api/auth/session` and waits for an interactive login when needed.
- `capture_auth` observes ChatGPT's own `/backend-api/conversations` request and retains `Authorization` and `chatgpt-account-id` headers. API calls execute inside the page with `fetch`, preserving session cookies and browser fingerprinting.
- Main conversations are paged through `/backend-api/conversations?offset=<n>&limit=100&order=updated`.
- Project conversations are discovered by paging `/backend-api/gizmos/snorlax/sidebar`, then each `/backend-api/gizmos/<project>/conversations` endpoint.
- Full JSON is fetched from `/backend-api/conversation/<conversation_id>`. The local, uncommitted direct-fetch repair is preserved.
- The exporter retries ordinary failures three times and applies escalating multi-minute cooldowns for HTTP 429/403. It also adds per-chat pacing and periodic long breaks.
- Raw output is one `conversation.json` per chat plus a rendered `conversation.md`; attachments and generated sandbox files are downloaded into `files/`.
- The script previously called `library_sweep` unconditionally after a normal export and from `--fix-files`. GPT Activity removes that default behavior and requires the explicit `--include-library` or `--include-files` opt-in.

## Reused and isolated behavior

GPT Activity reuses the persistent browser login, captured session headers, and in-page fetch pattern. ChatGPT Web access is isolated in `gpt_activity.sync.ChatGPTWebSource`; parsing, SQLite, analytics, API, and UI do not depend on endpoint details.

The 69 successfully downloaded JSON files under `complete_export/` are valid per-conversation responses with a `mapping` graph and `current_node`. They are imported locally so the user does not need to download them again.
