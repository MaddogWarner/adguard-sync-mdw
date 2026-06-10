# Changelog

All notable changes to this project will be documented in this file.

## [1.2.0] - 2026-06-10

### Added

- `config.yaml` is now seeded automatically from the bundled example on first run if the file is absent. No manual copy step required — run the container, open Settings, and configure your hosts from the dashboard.
- `.env.example` added to the repository.

### Changed

- Volume mount updated to a directory (`./config:/config` instead of `./config.yaml:/config/config.yaml`). Docker now creates `./config/` as a directory on the host when it does not exist, eliminating the Docker directory-creation trap that caused the v1.1.1 bug.

## [1.1.2] - 2026-06-10

### Fixed

- Config load now catches an empty or skeleton `config.yaml` (e.g. created with `touch`) and reports exactly which required fields are missing with a clear instruction to copy `config.example.yaml`.
- README Quick Start warning updated to cover both the directory and the empty-file traps.

## [1.1.1] - 2026-06-10

### Fixed

- Config load now emits a clear, actionable error when `config.yaml` is a directory instead of a file. This happens on a fresh install when Docker bind-mounts a non-existent host path and creates a directory there automatically. The message tells the user to stop the container, delete the directory, create the file from `config.example.yaml`, and start again.
- README Quick Start now warns that `config.yaml` must exist as a file on the host before starting the container.

## [1.1.0] - 2026-06-10

### Added

- Dashboard Settings page to edit the app-managed `config.yaml` live — sync interval, dry run, history retention, log level, TLS, hosts, and per-domain scope — without SSH. Each save writes a timestamped backup. TLS listener and database path changes are saved but require a container restart.

### Changed

- Settings page no longer renders password fields; passwords must be set and maintained in the `.env` file as `${ENV_VAR}` references, which are preserved on save.
- `config.yaml` is now mounted writable in the Docker Compose examples so the dashboard can save validated settings and create timestamped backups.
- Dashboard footer now shows version `1.1.0` and the updated Settings page attribution text.

## [1.0.0] - 2026-06-10

### Added

- Blocked services sync (a sixth sync domain): mirrors the primary's blocked-service IDs and their schedule to followers, with drift detection on the Drift page.
- Light/dark theme toggle in the dashboard header. The choice is remembered per browser and otherwise follows the system preference.
- HTTPS for the dashboard, enabled by default. A self-signed certificate is generated under the data directory when none is provided; a user-supplied PEM cert/key can be set via `tls.cert_file`/`tls.key_file` (or `TLS_CERT_FILE`/`TLS_KEY_FILE`). TLS can be disabled with `tls.enabled: false` when terminating TLS at a reverse proxy.
- App version shown in the dashboard footer.

### Changed

- Hosts that do not expose the `blocked_services/get` endpoint (older AdGuard Home versions) are detected gracefully: the blocked-services domain is skipped for them without marking the host unreachable or affecting other domains.

## [0.1.0] - 2026-06-10

### Added

- Initial AdGuard Sync implementation scaffold.
- Config validation, AdGuard API client, pure diff engine, SQLite persistence, sync engine, scheduler, FastAPI dashboard/API, container files, CI, and release workflow.
- Tolerant AdGuard API parsing for `null` list fields returned by some versions.
- Expandable Drift page details comparing primary and follower values.
- Automatic 14-day sync history retention with related change/drift purge.
- Status page host health for configured primary/follower AdGuard hosts, plus project footer links.
- Dashboard Sync now button now renders a readable result and refreshed status tables instead of raw API JSON.
