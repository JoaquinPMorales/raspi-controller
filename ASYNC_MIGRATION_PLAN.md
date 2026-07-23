# Async Migration Plan — raspi-controller

## Summary
This plan documents a conservative, incremental approach to introduce asynchronous patterns into the raspi-controller project. The goal is to improve responsiveness (especially for bot & network-bound operations), make the code more testable, and avoid blocking the event loop, while minimizing risk.

## Goals
- Prevent long blocking operations from stalling interactive components (Telegram bot, CLI). 
- Provide clear, incremental steps (safe quick wins first). 
- Keep behaviour unchanged unless intentionally migrating to async libraries.
- Add tests for any new helper code.

## Constraints & Decisions
- Keep local config.yaml as the secrets source (already ignored by git).
- Prefer conservative changes initially: wrap blocking ops with asyncio.to_thread / run_in_executor.
- Make heavy lifts (asyncssh migration, full async refactor) optional later.

## High-level approach
1. Add small async-friendly utility wrappers that offload blocking calls to a ThreadPoolExecutor (to_thread). Use these wrappers consistently for subprocess/paramiko/rclone/dd calls.
2. Replace direct synchronous HTTP calls (TMDB/Jellyfin) with async HTTP clients (aiohttp or httpx.AsyncClient) where beneficial; run concurrent lookups with asyncio.gather + Semaphore.
3. Migrate telegram_bot to async handlers (python-telegram-bot v20) but offload heavy ops to the executor until full async conversions are ready.
4. Optionally migrate SSH-dependent modules (scanner, updater) to asyncssh for end-to-end async flows.

## Detailed steps (priority order)

### Phase 1 — Safe, small changes (low risk, quick wins)
Status: mostly complete. The async helper foundation exists, tests were added, and async variants were added for key SSH-backed calls. Some synchronous call paths still remain in place for compatibility and should be migrated only where they are used from async flows.

- Create a new module `async_helpers.py` providing:
  - async_run_cmd(cmd, /, timeout=None) -> (returncode, stdout, stderr)
  - async_call_func(fn, *args, **kwargs) -> await result  (simple wrapper to_thread)
  - async_paramiko_exec(ssh_client, command) -> stream or collect stdout/stderr via to_thread
- Replace the most obvious blocking call sites to use these helpers:
  - scanner.get_item_size, calculate_items_size (calls `du`) — offload to async wrapper when used from async contexts
  - backup.create_backup/upload_to_cloud — offload subprocess calls
  - updater._run_sudo_command/_run_command — offload streaming reads
- Add a short test suite for async_helpers (pytest + pytest-asyncio). Validate the wrappers handle timeouts and propagate exceptions.

Deliverables: `async_helpers.py`, unit tests, minor call-site edits.

### Phase 2 — HTTP concurrency (medium risk)
Status: partially complete. Jellyfin has an async refresh path using `httpx.AsyncClient`, but TMDB lookups in `scanner.py` remain synchronous and do not yet use concurrency limits. TMDB work is intentionally deferred for now.

- Replace urllib.request usages with an async http client:
  - TMDB lookups: use httpx.AsyncClient with a Semaphore to limit concurrency and retries.
  - Jellyfin refresh: use async POST (or keep SSH fallback via async wrapper).
- Update FolderScanner._get_year_from_tmdb to be async (or provide an async variant) and enable concurrent lookups when scanning multiple folders.

Deliverables: async HTTP helpers, TMDB concurrency, config for concurrency limits.

### Phase 3 — Telegram bot migration (medium risk)
Status: complete for the current migration scope. The project already uses `python-telegram-bot>=20.0`, handlers are `async def`, blocking work is routed through the shared async helper, long-running bot operations are concurrency-limited, and cancellation/tests cover the migrated paths.

- Upgrade python-telegram-bot to v20 (async-based). Convert handlers to async def.
  - Keep heavy operations (SSH, rclone, dd, rsync) executed via async_helpers.async_call_func (run_in_executor).
  - Ensure handlers support cancellation and report progress back to the user.

Deliverables: updated `telegram_bot.py` or new `telegram_async.py`, migration notes, tests for handler dispatch (unit-level, mocking executor).

### Phase 4 — Optional full-SSH async migration (higher risk)
Status: not started.

- Replace paramiko with asyncssh in scanner/updater. This requires rewriting connect/exec/sftp logic to async APIs.
- Benefits: true non-blocking SSH and sftp operations; easier concurrency.
- Mitigations: keep a compatibility layer so code can run with either paramiko (sync) or asyncssh (async) until fully migrated.

Deliverables: `ssh_backends` abstraction, asyncssh implementation, migration guide.

## Testing
- Add pytest and pytest-asyncio dev deps.
- Unit tests for scanner._parse_show_info (already good candidate). Add tests for async_helpers behaviour (timeouts, exceptions). 
- Integration smoke tests (dry-run) for the telegram bot using mocked executor.

## Backwards compatibility
- Main entrypoint (main.py) remains synchronous; where needed, use asyncio.run to run async flows.
- All changes begin with wrappers — behaviour remains identical unless an explicit async library migration occurs.

## Security & Secrets
- No change to secret handling: continue using local config.yaml (ignored by git). Tests or CI should NOT require secrets; use fixtures or environment overrides.

## Todos (actionable)
- [x] Create `docs/async-migration.md` (this plan) — created in session-state.
- [x] Add `async_helpers.py` with to_thread wrappers and tests. (Phase 1) — implemented (async_helpers.py + tests).
- [x] Replace top blocking call sites to use async_helpers (scanner.get_item_size_async, updater._run_sudo_command_async) and add tests. (Phase 1) — implemented; a few synchronous call-sites remain (calculate_items_size, update_system streams).
- [x] Add async Jellyfin refresh via `httpx.AsyncClient`. (Phase 2) — implemented in `jellyfin.py` with tests.
- [ ] Migrate TMDB lookups in scanner._get_year_from_tmdb to an async HTTP client (`httpx.AsyncClient`) and add concurrency control with a semaphore. (Phase 2, deferred)
- [x] Upgrade & migrate Telegram bot to async handlers. (Phase 3) — `python-telegram-bot>=20.0` is in requirements and handlers are `async def`.
- [x] Finish Telegram bot async migration cleanup: extend test coverage, improve cancellation handling, add a long-running operation gate, and replace remaining blocking paths where they still affect async flows. (Phase 3)
- [ ] (Optional) Implement asyncssh backend and switch scanner/updater to full async. (Phase 4)

## Risks & Mitigations
- Risk: subtle behaviour changes with threading or concurrency. Mitigation: keep synchronous fallback and run integration smoke tests.
- Risk: new dependency versions (python-telegram-bot, httpx, asyncssh). Mitigation: pin in requirements and test locally before deploy.

## Next immediate step (recommended)
No immediate Telegram bot async follow-up is required. Remaining async work is optional (`asyncssh`) or deferred (TMDB HTTP migration).

---

Current focus should be any explicitly chosen deferred work (TMDB HTTP migration) or the optional deeper SSH migration (`asyncssh`).
