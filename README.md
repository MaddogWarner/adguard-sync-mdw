# AdGuard Sync

AdGuard Sync mirrors configuration from one primary AdGuard Home server to one or more followers. It uses the AdGuard Home HTTP control API only and does not edit `AdGuardHome.yaml`.

It runs as a single lightweight container, polls on a fixed interval, records what changed and where followers have drifted, and presents it all on a small dashboard.

## Features

- **One-way full mirror**, primary → followers: add missing items, update changed ones, and prune follower-only extras (per-domain `prune` toggle).
- **Six sync domains**: block lists, allowlists, custom user rules, DNS rewrites, upstream DNS, and blocked services.
- **Drift detection**: every run records the difference between primary and follower *before* applying, viewable on the Drift page.
- **Dashboard** (FastAPI + HTMX) with Status, Drift, History, and Settings pages, a manual **Sync now** trigger, host health, light/dark theme toggle, and a JSON API.
- **Edit config from the dashboard**: the Settings page writes `config.yaml` live (interval, dry run, retention, log level, TLS, hosts, scope) with a timestamped backup on each save. Passwords are never form fields — they stay in `.env`.
- **HTTPS by default** with a generated self-signed certificate, or bring your own (see [TLS](#tls)).
- **Safe by default**: global `dry_run` records intended changes without writing; secrets come from the environment, never the YAML.
- **Scheduled** every 5, 10, or 15 minutes, with automatic history retention.
- **Multi-arch images** (`linux/amd64`, `linux/arm64`) for x86 servers, Raspberry Pi, and NAS.

## Screenshots

**Status** — host health and the latest sync run per follower:

![Status page](https://github.com/MaddogWarner/adguard-sync-mdw/releases/download/v1.1.0/status.png)

**Drift** — differences between primary and followers, expandable per item (here: blocked services and a missing rewrite):

![Drift page](https://github.com/MaddogWarner/adguard-sync-mdw/releases/download/v1.1.0/drift.png)

**History** — per-run added/updated/removed counts and status:

![History page](https://github.com/MaddogWarner/adguard-sync-mdw/releases/download/v1.1.0/history.png)

The header toggle switches between light and dark themes (it follows your system preference by default):

![Light theme](https://github.com/MaddogWarner/adguard-sync-mdw/releases/download/v1.1.0/theme-light.png)

## What It Syncs

- Block lists
- Allowlists
- Custom user rules
- DNS rewrites
- Managed upstream DNS fields: `upstream_dns`, `bootstrap_dns`, `fallback_dns`, `upstream_mode`
- Blocked services (the blocked-service IDs and their schedule)

Blocked-services sync uses AdGuard Home's `blocked_services/get` and `blocked_services/update` endpoints (AdGuard Home v0.107.x or newer). On older hosts that lack these endpoints the domain is skipped automatically without affecting the other domains.

The dashboard supports light and dark themes via the toggle in the header; the choice is remembered per browser and otherwise follows your system preference.

The primary is the source of truth. Followers receive add, update, replace, and prune actions based on the configured scope.

## Quick Start

Prebuilt multi-arch images (`linux/amd64`, `linux/arm64`) are published to the GitHub Container Registry:

```bash
docker pull ghcr.io/maddogwarner/adguard-sync-mdw:latest
```

```bash
cp .env.example .env   # fill in your AdGuard passwords

docker run -d --name adguard-sync \
  -p 8080:8080 \
  --env-file .env \
  -v "$PWD/config:/config" \
  -v "$PWD/data:/data" \
  --restart unless-stopped \
  ghcr.io/maddogwarner/adguard-sync-mdw:latest
```

On first run, `config.yaml` is seeded automatically from the bundled example. Open the dashboard at `https://<host>:8080`, go to **Settings**, and update the primary and follower host URLs to your real AdGuard instances.

Or with Docker Compose, using the published image instead of a local build:

```yaml
services:
  adguard-sync:
    image: ghcr.io/maddogwarner/adguard-sync-mdw:latest
    container_name: adguard-sync
    env_file: .env
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
      - ./data:/data
    restart: unless-stopped
```

Pin a specific release (e.g. `:v1.2.0`) instead of `:latest` for reproducible deployments. Available tags are listed on the [releases page](https://github.com/MaddogWarner/adguard-sync-mdw/releases) and the [package page](https://github.com/MaddogWarner/adguard-sync-mdw/pkgs/container/adguard-sync-mdw).

### Build locally instead

To build from source rather than pull the image, the bundled `docker-compose.yml` uses `build: .`:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
docker compose up --build
```

The dashboard listens on `https://<host>:8080` (HTTPS by default — see [TLS](#tls)). Keep it LAN-only by default and set `DASHBOARD_USER` and `DASHBOARD_PASSWORD` when exposing it beyond a trusted host.

## TLS

The dashboard serves HTTPS by default.

- **Self-signed (default):** with no certificate configured, a self-signed certificate is generated on first start under the data directory (`/data/certs/`) and reused thereafter. Browsers will warn that it is not trusted — expected for a self-signed cert; accept the exception or supply your own.
- **Provide your own certificate:** mount a PEM cert and key into the container and point `tls.cert_file` / `tls.key_file` (or the `TLS_CERT_FILE` / `TLS_KEY_FILE` env vars) at them.

  ```yaml
  tls:
    enabled: true
    cert_file: /config/certs/server.crt
    key_file: /config/certs/server.key
  ```

- **Disable TLS:** set `tls.enabled: false` (or `TLS_ENABLED=false`) only when TLS is terminated by a reverse proxy in front of the container.

Serve a provided certificate by also mounting it, e.g. `-v "$PWD/certs:/config/certs:ro"`.

## Configuration

`CONFIG_PATH` defaults to `/config/config.yaml`. `DATABASE_PATH` can override the SQLite path for local smoke tests or alternate mounts.

The dashboard Settings page can edit the app-managed `config.yaml` and apply normal sync settings live. The config file must be writable by the container user, so do not mount it read-only. TLS listener changes and database path changes are saved but require a container restart because those components are initialised at process startup.

| Field | Purpose |
|---|---|
| `interval_minutes` | Sync interval. Must be `5`, `10`, or `15`. |
| `dry_run` | Records drift and intended changes without making write calls. |
| `database_path` | SQLite path. Defaults to `/data/adguard-sync.db`. |
| `history_retention_days` | Number of days to retain sync history before purging related run, change, and drift rows. Defaults to `14`. |
| `log_level` | JSON stdout log level. |
| `tls.enabled` | Serve the dashboard over HTTPS. Defaults to `true`. |
| `tls.cert_file` / `tls.key_file` | Optional PEM cert and key. When unset, a self-signed pair is generated. Must be set together. |
| `primary` | Source AdGuard Home host. |
| `followers` | One or more destination hosts. |
| `scope.*.enabled` | Enables a sync domain. |
| `scope.*.prune` | Removes follower-only items where applicable. |

Host fields are `name`, `url`, `username`, `password`, and `verify_ssl`.

Use `${ENV_VAR}` placeholders for secrets. The Settings page does not render password fields; set and maintain passwords in `.env`.

```yaml
password: ${ADGUARD_PRIMARY_PASSWORD}
```

## API

- `GET /healthz`
- `GET /api/status`
- `GET /api/runs`
- `GET /api/drift`
- `POST /api/sync`

Dashboard pages are `/`, `/drift`, `/history`, and `/settings`.

## Security Notes

- Do not commit `.env` or real `config.yaml` files.
- Prefer HTTPS AdGuard URLs with `verify_ssl: true`.
- Only set `verify_ssl: false` for controlled testing or trusted internal certificates with a documented exception.
- Configure dashboard Basic Auth via `DASHBOARD_USER` and `DASHBOARD_PASSWORD`.
- Logs are structured JSON and must not include secrets.

## Operational Notes

`dry_run: true` is the safest first deployment mode. Review `/drift` and `/history`, then disable dry run after confirming the proposed changes.

History retention runs at startup and daily. Rows older than `history_retention_days` are deleted from SQLite, including related change and drift records.

Filters are refreshed on the follower only after blocklist or allowlist changes are applied.

## Contributors

- **[David (MaddogWarner)](https://github.com/MaddogWarner)** — project owner: vision, requirements, review, and live testing.
- **Claude (Anthropic)** — architecture, the v1.0.0 feature work (blocked-services sync, theme toggle, HTTPS), reviews, and deployment.
- **Codex (OpenAI)** — initial implementation of the build spec.

## License

Released under the [MIT License](LICENSE).
