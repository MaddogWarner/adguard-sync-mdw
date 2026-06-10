# AdGuard Sync

AdGuard Sync mirrors configuration from one primary AdGuard Home server to one or more followers. It uses the AdGuard Home HTTP control API only and does not edit `AdGuardHome.yaml`.

## What It Syncs

- Block lists
- Allowlists
- Custom user rules
- DNS rewrites
- Managed upstream DNS fields: `upstream_dns`, `bootstrap_dns`, `fallback_dns`, `upstream_mode`

The primary is the source of truth. Followers receive add, update, replace, and prune actions based on the configured scope.

## Quick Start

```bash
cp config.example.yaml config.yaml
cp .env.example .env
docker compose up --build
```

The dashboard listens on port `8080`. Keep it LAN-only by default and set `DASHBOARD_USER` and `DASHBOARD_PASSWORD` when exposing it beyond a trusted host.

## Configuration

`CONFIG_PATH` defaults to `/config/config.yaml`. `DATABASE_PATH` can override the SQLite path for local smoke tests or alternate mounts.

| Field | Purpose |
|---|---|
| `interval_minutes` | Sync interval. Must be `5`, `10`, or `15`. |
| `dry_run` | Records drift and intended changes without making write calls. |
| `database_path` | SQLite path. Defaults to `/data/adguard-sync.db`. |
| `log_level` | JSON stdout log level. |
| `primary` | Source AdGuard Home host. |
| `followers` | One or more destination hosts. |
| `scope.*.enabled` | Enables a sync domain. |
| `scope.*.prune` | Removes follower-only items where applicable. |

Host fields are `name`, `url`, `username`, `password`, and `verify_ssl`.

Use `${ENV_VAR}` placeholders for secrets:

```yaml
password: ${ADGUARD_PRIMARY_PASSWORD}
```

## API

- `GET /healthz`
- `GET /api/status`
- `GET /api/runs`
- `GET /api/drift`
- `POST /api/sync`

Dashboard pages are `/`, `/drift`, and `/history`.

## Security Notes

- Do not commit `.env` or real `config.yaml` files.
- Prefer HTTPS AdGuard URLs with `verify_ssl: true`.
- Only set `verify_ssl: false` for controlled testing or trusted internal certificates with a documented exception.
- Configure dashboard Basic Auth via `DASHBOARD_USER` and `DASHBOARD_PASSWORD`.
- Logs are structured JSON and must not include secrets.

## Operational Notes

`dry_run: true` is the safest first deployment mode. Review `/drift` and `/history`, then disable dry run after confirming the proposed changes.

Open confirmation items for live smoke testing:

- Confirm `upstream_mode` field and value semantics against the target AdGuard Home version.
- Current behaviour refreshes filters only after blocklist or allowlist changes.
