# Copilot instructions — raspi-controller

Purpose: help Copilot sessions quickly understand how to build, test, run, and make safe changes in this repository.

---

## Quick commands (env)
- Create venv & install deps:
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt

- Run tests (all):
  pytest -q

- Run a single test (example):
  pytest tests/test_logger.py::test_json_formatter_outputs_json
  or use pattern matching: pytest -k <pattern>

- Run a single test file:
  pytest tests/test_logger.py

- Run CLI (interactive):
  source venv/bin/activate
  python main.py

- Run Telegram bot (requires valid config.yaml with telegram.token):
  source venv/bin/activate
  python telegram_bot.py

- Lint/format (NOT configured in repo):
  The project plan mentions `black` and `flake8`. If you add them, use:
    black . && flake8 .
  (No repo config found; add config files if enabling.)

---

## High-level architecture (big picture)
- Two user-facing entry points that share core logic:
  - CLI: `main.py` — interactive terminal UI to scan downloads and copy to Jellyfin or local machine.
  - Bot: `telegram_bot.py` — async Telegram interface that reuses the same scanner/copy/update flows and streams progress to users.

- Core components:
  - scanner.py — connects to the Raspberry Pi over SSH (paramiko), lists downloads, parses show/movie metadata, optional TMDB lookups.
  - copier.py — two strategies: RsyncCopier (rsync over SSH or local rsync) and ExternalCopier (SFTP chunked copy). Progress reporting is via rich.
  - jellyfin.py — triggers Jellyfin library refresh (HTTP API or SSH fallback).
  - updater.py — runs apt/flatpak updates on the Pi using sudo_password (via SSH).
  - async_helpers.py — small async wrappers to run blocking calls in executors; used to keep async handlers non-blocking.
  - logger.py — structured logging support (JSONFormatter, get_logger, get_logger_adapter); op_id helpers for correlation.
  - alerts.py — minimal Telegram alert helper used for critical failures.

- Configuration: `config.yaml` (copy from `config.yaml.example`) controls SSH credentials, paths, options (dry_run), Telegram settings, TMDB/Jellyfin API keys.

---

## Key conventions and repository-specific patterns
- Modes: three operation modes (internal, external, update). Code paths for these are implemented in both `main.py` and `telegram_bot.py` — keep behavior consistent across both.

- Authentication: prefer SSH key auth (pi.key_path). If using update mode you must set `pi.sudo_password` for remote sudo commands.

- Dry-run: `config.yaml` → `options.dry_run` toggles dry-run. Copiers and updater respect this flag; tests and manual checks should use dry-run to avoid accidental writes.

- Async migration pattern: add async wrappers (in `async_helpers.py`) and `*_async` variants rather than replacing synchronous functions. Keep sync APIs intact to avoid breaking CLI flows.

- Logging / observability: use `logger.get_logger()` and `get_logger_adapter(..., op_id=...)`. `new_op_id()` is used to correlate operations. Prefer structured logs (JSONFormatter) for background services; TUI/Bot use rich for human output.

- Rsync behavior: `RsyncCopier` builds rsync args and expects `/usr/bin/rsync`. It intentionally uses `--no-perms --no-owner --no-group --partial` to avoid permission errors on remote mounts — changing these flags may cause real-world failures.

- Tests: pytest + pytest-asyncio are used. Tests add repo root to sys.path; keep imports relative to project root.

- Secrets: do not commit `config.yaml` or credentials. `config.yaml.example` is provided. Keep secrets out of git.

- Files created at runtime by the bot: `user_data.json`, `ideas.json` (local persistence). Be cautious when changing working-directory expectations.

---

## Configuration keys to check first (useful when editing code)
- config.yaml: `pi` (host, user, password or key_path, sudo_password), `paths` (downloads, jellyfin_shows, jellyfin_movies, local_destination), `options.dry_run`, `telegram.token`, `telegram.allowed_users`, `tmdb.api_key`, `jellyfin.api_key`.

---

## Where to look for common tasks
- Scanning logic & SSH calls: scanner.py
- Copying & progress handling: copier.py
- Bot UI & async orchestration: telegram_bot.py
- CLI entrypoint & global error hooks: main.py
- Updater & sudo flows: updater.py
- Async wrappers & migration guide: async_helpers.py and ASYNC_MIGRATION_PLAN.md / plan.md
- Observability: logger.py, alerts.py

---

## Tests & CI notes for Copilot
- Tests: run `pytest -q`. To run a single test use the `::` syntax shown above.
- There is no CI workflow or pre-commit config in the repo; plan.md recommends adding GitHub Actions + black/flake8/mypy. If adding CI, ensure `pytest` runs under the same Python version as `requirements.txt` expects.

---

## AI assistant / other assistant configs
- No CLAUDE.md, .cursorrules, AGENTS.md, .windsurfrules, CONVENTIONS.md, or other assistant rule files detected in the repo root.

---

## Short editing guidelines for Copilot sessions
- Make small, surgical changes. Run unit tests locally after edits.
- Preserve sync behavior when adding async wrappers; prefer adding `*_async` helpers that call into `async_helpers.py`.
- When adding dependencies, update `requirements.txt` and keep third-party additions minimal.
- Avoid changing rsync flags or SSH handling without adding integration tests or a documented migration path.
- Keep secrets out of commits; assume `config.yaml` is local-only.

---

If anything should be added (examples, CI commands, or coverage for other areas), say which area and a short goal and a follow-up change can be prepared.
