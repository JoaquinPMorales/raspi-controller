# Plan & Next Steps: Phase 1 + Backups + Integration + Observability + Packaging

## Summary
This document captures guidelines, priorities, and small, testable todos for the five selected areas:
- Phase 1 (async helpers)
- Backups (incremental/restic/rsync modes)
- qBittorrent integration (watcher)
- Observability (structured logging & alerts)
- Packaging / Deploy (Docker + systemd)

Keep changes small and reversible. Each change should include tests and minimal behavior change unless explicitly intended.

---

## Conventions & High-level Guidelines
- Backwards compatibility: keep synchronous APIs and add "_async" variants or wrappers where needed.
- Threading model: use asyncio + ThreadPoolExecutor for blocking libs (paramiko, subprocess, rclone, dd). Prefer async libraries only after safe wrappers exist.
- Testing: add pytest + pytest-asyncio for async helpers. Mock external systems (SSH, HTTP, rclone) in unit tests.
- Configuration: keep secrets out of git. Support config.yaml and environment variable overrides for CI/containers.
- Logging: use a single logger module (get_logger) returning structured logs (JSON) by default; allow human-readable mode for interactive CLI.

---

## Phase 1 — Async foundation (Priority: High)
Objective: Add safe async wrappers to offload blocking calls to executor and demonstrate usage by updating two call sites.

Guidelines:
- Create `async_helpers.py` with:
  - async_run_cmd(command, timeout=None) -> (rc, stdout, stderr)
  - async_call(func, *args, **kwargs) -> await result
  - async_paramiko_exec(ssh, command) -> (exit_status, stdout, stderr)
- Do not replace core logic; call wrappers from new async functions only.
- Provide unit tests that verify correct return values, exception propagation and simple timeout behaviour.

Tasks (Phase 1):
- async-1: Add `async_helpers.py` (done)
- async-2: Add pytest-asyncio tests for async_helpers (done)
- async-3: Add `get_item_size_async` in `scanner.py` and `_run_sudo_command_async` in `updater.py` (done)

Acceptance criteria:
- Tests pass locally: `pytest -q`
- Synchronous behaviour unchanged for non-async callers
- Async variants are documented in code comments and README

Example usage:
```py
# from an async handler
size = await scanner.get_item_size_async('/mnt/storage/downloads/My Show S01')
# Run a blocking function
res = await async_helpers.async_call(blocking_func, arg1, arg2)
```

---

## Backups (Priority: High)
Objective: Add incremental and restic-backed backup options, keep full dd-image support.

Guidelines & choices:
- Support `backup.mode` in config.yaml: `full` (dd+gzip), `rsync` (snapshot via rsync+--link-dest), `restic` (recommended for efficient incremental + encryption).
- If `restic` used, require `RESTIC_PASSWORD` via environment and `restic init` step documented.
- Use `rclone` for cloud uploads; keep cloud upload optional and non-blocking.
- Keep status file JSON for last backup metadata and provide `get_status_text()` for Telegram/UI.

Implementation notes:
- Refactor `SystemBackup.create_backup` into small functions: `_create_full_image`, `_create_rsync_snapshot`, `_create_restic_snapshot`.
- Use subprocess-run with timeouts, and expose progress_callback for UI.
- Retention: add `backup.keep` or restic retention rules; remove local/remote older backups post-success.

Acceptance criteria:
- New `backup.mode` accepted and exercised via tests or manual dry-run
- Status file updated consistently
- Cloud upload still functional when enabled

Config example:
```yaml
backup:
  enabled: true
  mode: restic
  restic_repo: /mnt/storage/restic_repo
  cloud_enabled: true
  cloud_remote: "gdrive:Backups"
  auto_backup: true
```

---

## qBittorrent Integration (Priority: Medium-High)
Objective: Auto-trigger copy flow when downloads complete in qBittorrent.

Design & Guidelines:
- Implement `qbt_watcher.py` that polls qBittorrent Web API or accepts webhook callbacks.
- Default: polling interval (configurable) with exponential backoff on errors.
- Use torrent `hash` or `info_hash` as idempotency key and store recently-processed IDs in a small JSON file or SQLite table.
- When a completed torrent is seen, emit an event (e.g., asyncio.Queue) consumed by a worker that runs the same copy flow (dry-run -> confirm -> copy).
- Safety: always run copy in dry-run mode first; allow auto-apply via config flag.

Endpoints (qBittorrent):
- `POST /api/v2/auth/login` for login
- `GET /api/v2/torrents/info?filter=completed` to enumerate completed torrents

Acceptance criteria:
- Watcher finds a completed download and enqueues it
- No duplicate processing of the same torrent
- Unit tests mock qBittorrent endpoints

---

## Observability (Priority: Medium)
Objective: Provide structured logs, alerts on failures, and log rotation.

Guidelines:
- Create `logger.py` exposing get_logger(name) configured from config.yaml: level, format (json/human), file path, rotation.
- Use standard library `logging` + `RotatingFileHandler`/`TimedRotatingFileHandler`. Optionally add `python-json-logger` when available.
- Replace prints with logger calls incrementally. Keep rich/console output for interactive CLI flows.
- Alerts: add a small `alerts.py` that can send critical messages via Telegram (reuse existing bot token) or email (optional).
- Include operation IDs and timestamps in logs for correlation. Use `extra={'op_id': op_id}` when available.

Acceptance criteria:
- Logs emitted in structured format to file
- Optional Telegram alerts sent for critical failures in tests (mocked)

---

## Packaging & Deploy (Priority: Medium)
Objective: Add Dockerfile for Pi-friendly builds and improve systemd service example.

Guidelines:
- Provide a multi-stage Dockerfile with a slim Python base. Use platform build with `docker buildx` to produce arm/amd64 images.
- Do not bake secrets into the image. Use volume mounts or environment variables for config.yaml and rclone/restic credentials.
- Provide `docker-compose.yml` example for local testing (optional).
- Systemd: provide a robust template using `EnvironmentFile` and variables for `PROJECT_DIR`, `USER`, and `PATH`. Document steps to enable the service.

Acceptance criteria:
- Dockerfile present and documented build steps (README)
- systemd unit docs added and tested on a Pi (manual validation)

Example commands:
```bash
# build for multiple platforms (requires buildx)
docker buildx build --platform linux/arm/v7,linux/arm64,linux/amd64 -t myorg/raspi-controller:latest . --push

# systemd (example)
sudo cp /path/to/raspi-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-controller
```

---

## Testing, CI & Quality
- Add GitHub Actions workflow to run `pytest`, format check (`black --check`), and static checks (`flake8`/`mypy`) on PRs.
- Avoid running integration tests that need secrets in CI; mark them with `@pytest.mark.integration` and run manually.
- Use small unit tests for status-file behavior, the watcher (mocked HTTP), and async_helpers.

---

## Security Notes
- Prefer SSH key auth. If sudo_password is required, prefer storing it in environment variables or a secrets manager for production.
- Never commit `config.yaml` or tokens. Ensure `.gitignore` includes config.yaml (already in project).
- For restic, prefer encrypted repositories with `RESTIC_PASSWORD` stored securely.

---

## Prioritized Next Steps (short term)
1. Finish Phase 1 PR: async_helpers, tests, scanner/updater async variants (small, done/draft). (async-1..3)
2. Backup refactor: add restic/rsync modes and keep dd mode; unit tests for status handling. (backup-1..3)
3. qBittorrent watcher: implement polling + idempotency + test harness. (qb-1..2)
4. Observability: add logger module and replace core prints in a small PR. (obs-1)
5. Packaging: add Dockerfile and systemd template; document build steps. (pkg-1..2)

Each item should be delivered as a small PR with a single feature and tests.

---

## How to validate locally
- Install dev deps: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
- Run tests: `pytest -q`
- Lint/format: `black .` and `flake8`

---

If this plan looks good, proceed by opening the Phase 1 PR (async helpers + tests). After merge, continue with Backup and qBittorrent watcher in separate PRs.

## Progress update (automated)

- Phase 1 (async helpers): DONE — async_helpers.py added; tests passing.
- Backups: DONE — multi-mode backup support (full/rsync/restic) added; tests passing.
- Observability: DONE — logger.py (structured logging), alerts.py (Telegram alerts) added; tests passing.
- Op ID & global exception handling: DONE — operation IDs injected via LoggerAdapter; sys.excepthook and asyncio exception handler configured to alert on unhandled errors.

Next steps:
- Replace prints with get_logger across core modules (scanner, copier, updater, telegram_bot) incrementally.
- Add op_id propagation to Telegram messages and long-running tasks so UI messages include correlation IDs.
- Add GitHub Actions workflow for tests and linting.
- Implement qBittorrent watcher (next feature PR).


