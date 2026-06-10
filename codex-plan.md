# Codex Build Spec — AdGuard Sync

> **Audience:** Codex (the executor). This is the authoritative, ordered build
> spec. Claude owns the architecture; Codex builds against this document. Build
> the steps **in order** — each step lists its deliverables and a verification
> gate that must pass before moving on. Do not skip the tests.

---

## 0. What we're building (one paragraph)

A single lightweight Docker container that keeps multiple AdGuard Home servers in
configuration lockstep. One host is the **primary** (source of truth); its
config is mirrored to one or more **followers** on a 5/10/15-minute schedule,
entirely via the AdGuard Home HTTP **control API** (never by editing
`AdGuardHome.yaml`). It syncs block lists, allowlists + custom rules, DNS
rewrites, and upstream DNS forwarders; detects drift; logs every change; and
exposes a FastAPI dashboard + JSON API. Full-mirror reconciliation (add / update
/ prune) with a global `dry_run` and per-domain `prune` safety toggle.

---

## 1. Conventions & ground rules

- **Language/stack:** Python 3.12, FastAPI, uvicorn, httpx (async), APScheduler
  (AsyncIOScheduler), pydantic v2 + pydantic-settings, Jinja2 + HTMX, PyYAML,
  stdlib `sqlite3` (thin repository — do **not** pull in an ORM unless a step
  explicitly calls for it). Tests: pytest, pytest-asyncio, respx. Lint/format:
  ruff.
- **AU English** in all user-facing text and docs (organise, colour, behaviour).
- **Security:** never log secrets; never write passwords to disk; validate all
  config at load; assume hostile/malformed API responses and handle them.
- **Async throughout** — the AdGuard client, engine, and scheduler are async and
  share the FastAPI event loop. One `httpx.AsyncClient` per host, reused.
- **Typing:** full type hints; code must pass `ruff check`.
- **No network calls in unit tests** — mock with respx.
- Keep functions small and pure where possible (especially `differ.py`).
- Package name: `app`. Container exposes port **8080**. SQLite DB lives at
  `/data/adguard-sync.db` (mounted volume).

---

## 2. Repository layout (target end state)

```
adguard-sync/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── logging_setup.py
│   ├── adguard/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── models.py
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── differ.py
│   │   └── result.py
│   ├── storage.py
│   ├── scheduler.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── status.html
│       │   ├── drift.html
│       │   └── history.html
│       └── static/
│           ├── style.css
│           └── htmx.min.js
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_differ.py
│   ├── test_client.py
│   ├── test_storage.py
│   └── test_engine.py
├── Dockerfile
├── docker-compose.yml
├── config.example.yaml
├── .env.example
├── .gitignore
├── .dockerignore
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── CLAUDE.md
└── .github/workflows/{ci.yml,release.yml}
```

---

## 3. Build steps (ordered)

### Step 1 — Project scaffolding

- `pyproject.toml` with deps (runtime + `[dev]` extras: pytest, pytest-asyncio,
  respx, ruff). Configure ruff (line length 100, target py312) and pytest
  (`asyncio_mode = "auto"`, testpaths `tests`).
- `.gitignore` (Python, `.env`, `data/`, `__pycache__`, `.venv`), `.dockerignore`.
- `app/__init__.py` with `__version__ = "0.1.0"`.
- `app/logging_setup.py`: configure JSON structured logging to stdout
  (timestamp, level, logger, message, plus arbitrary `extra` fields). A single
  `setup_logging(level)` function.

**Gate:** `pip install -e .[dev]` succeeds; `ruff check .` passes on the scaffold.

---

### Step 2 — Config loading & validation (`app/config.py`)

- Pydantic models: `HostConfig` (name, url, username, password, verify_ssl),
  `ScopeItem` (enabled, prune — prune optional/ignored where N/A), `ScopeConfig`
  (blocklists, allowlists, user_rules, rewrites, upstream_dns), `AppConfig`
  (interval_minutes, dry_run, scope, primary, followers, dashboard auth fields).
- Loader `load_config(path)`:
  - Read YAML, perform `${ENV_VAR}` interpolation from `os.environ` on string
    values (raise a clear error if a referenced env var is missing).
  - Validate: `interval_minutes ∈ {5,10,15}`; every URL parses with http/https
    scheme; `len(followers) >= 1`; no follower url equals primary url; names
    unique.
  - Config path from env `CONFIG_PATH` (default `/config/config.yaml`).
- Dashboard auth: `DASHBOARD_USER` / `DASHBOARD_PASSWORD` from env (optional).

**Tests (`test_config.py`):** valid config loads; env interpolation works;
missing env var raises; bad interval rejected; duplicate primary/follower url
rejected; empty followers rejected.

**Gate:** `pytest tests/test_config.py` green.

---

### Step 3 — AdGuard data models (`app/adguard/models.py`)

Pydantic models mirroring the API payloads we read/write (tolerant of extra
fields — `model_config = ConfigDict(extra="ignore")`):

- `Filter` (url, name, enabled, id?) — used for both blocklists and allowlists.
- `Rewrite` (domain, answer).
- `UpstreamDnsConfig` (upstream_dns: list[str], bootstrap_dns: list[str],
  fallback_dns: list[str], upstream_mode: str) — **only the managed fields**.
- `FilteringStatus` (filters, whitelist_filters, user_rules: list[str],
  interval).
- `HostSnapshot` (host name, blocklists, allowlists, user_rules, rewrites,
  upstream, version, reachable: bool) — the full per-host state the differ
  consumes.

**Gate:** models import and round-trip sample JSON in `test_client.py` fixtures.

---

### Step 4 — AdGuard API client (`app/adguard/client.py`)

`AdGuardClient` wrapping one host. Constructor takes `HostConfig`; builds an
`httpx.AsyncClient` with Basic Auth, `base_url`, `verify=verify_ssl`, sane
timeout (e.g. 15s). Async context-manager (`__aenter__`/`aclose`).

Read methods → return models:
- `async get_status()` → version/running (`GET /control/status`).
- `async get_filtering_status()` → `FilteringStatus`
  (`GET /control/filtering/status`).
- `async get_rewrites()` → `list[Rewrite]` (`GET /control/rewrite/list`).
- `async get_dns_info()` → `UpstreamDnsConfig` (`GET /control/dns_info`).
- `async snapshot()` → `HostSnapshot` (calls the above; sets `reachable=False`
  and logs on connection error rather than raising, so one dead follower doesn't
  abort the run).

Write methods (each returns success/raises a typed `AdGuardApiError` on non-2xx):
- `add_filter(url, name, whitelist)` → `POST /control/filtering/add_url`.
- `remove_filter(url, whitelist)` → `POST /control/filtering/remove_url`.
- `set_filter(url, name, enabled, whitelist)` → `POST /control/filtering/set_url`
  (body `{url, whitelist, data:{name,url,enabled}}`).
- `set_user_rules(rules: list[str])` → `POST /control/filtering/set_rules`.
- `set_filtering_interval(interval)` → `POST /control/filtering/config`.
- `add_rewrite(domain, answer)` / `delete_rewrite(domain, answer)` →
  `POST /control/rewrite/{add,delete}`.
- `set_dns_config(cfg: UpstreamDnsConfig)` → `POST /control/dns_config` (send
  only managed fields).
- `refresh_filters(whitelist)` → `POST /control/filtering/refresh`.

**Tests (`test_client.py`):** with respx, assert each method hits the right
path/verb with the right JSON body; `snapshot()` assembles a `HostSnapshot`;
unreachable host → `reachable=False`, no exception; non-2xx → `AdGuardApiError`.

**Gate:** `pytest tests/test_client.py` green.

---

### Step 5 — Diff engine (`app/sync/differ.py` + `app/sync/result.py`)

`result.py` dataclasses/enums:
- `Domain` enum: `BLOCKLISTS, ALLOWLISTS, USER_RULES, REWRITES, UPSTREAM_DNS`.
- `Op` enum: `ADD, UPDATE, REMOVE, REPLACE`.
- `ChangeAction(domain, op, target: str, detail: dict)` — one intended change.
- `DriftItem(domain, kind, target, primary_value, follower_value)`.
- `DomainResult(domain, actions: list[ChangeAction], drift: list[DriftItem])`.

`differ.py` — **pure** functions, no I/O, given primary + follower snapshots:
- `diff_filters(primary, follower, *, whitelist, prune)` → `DomainResult`:
  key by URL. ADD primary-only; UPDATE where name/enabled differ; REMOVE
  follower-only **iff prune**.
- `diff_user_rules(primary, follower)` → REPLACE action iff ordered lists differ.
- `diff_rewrites(primary, follower, *, prune)` → key by `(domain, answer)`; ADD
  primary-only; REMOVE follower-only iff prune.
- `diff_upstream(primary, follower)` → REPLACE action iff any managed field
  differs (compare normalised lists).
- `diff_host(primary_snapshot, follower_snapshot, scope)` → orchestrates the
  above honouring `scope` enable/prune flags; returns `list[DomainResult]`.

**Tests (`test_differ.py`) — the most important suite.** For every domain cover:
add, update, prune-on, prune-off (extras retained), and the no-op (already
in-sync → empty actions & drift) case. Assert drift is populated even when
actions would be applied.

**Gate:** `pytest tests/test_differ.py` green with these cases.

---

### Step 6 — Persistence (`app/storage.py`)

Thin SQLite repository (stdlib `sqlite3`, WAL mode, created on startup). Tables:
- `sync_runs` (id, started_at, finished_at, follower, status, added, updated,
  removed, error).
- `changes` (id, run_id FK, domain, op, target, outcome, detail).
- `drift` (id, run_id FK, follower, domain, kind, target, primary_value,
  follower_value).

API: `init_db()`, `record_run(...) -> run_id`, `record_changes(run_id, ...)`,
`record_drift(run_id, ...)`, `latest_run_per_follower()`, `recent_runs(limit,
filters)`, `current_drift()`. Timestamps in UTC ISO-8601.

**Tests (`test_storage.py`):** init creates schema; record + read back a run with
changes and drift; `latest_run_per_follower` returns the newest per follower.

**Gate:** `pytest tests/test_storage.py` green.

---

### Step 7 — Sync engine (`app/sync/engine.py`)

`SyncEngine(config, storage)`:
- `async run_once()` → snapshot primary once; for each follower: snapshot,
  `diff_host`, then (unless `config.dry_run`) apply each `ChangeAction` via the
  client mapping op→client method; record run/changes/drift; after blocklist or
  allowlist changes call `refresh_filters`. Catch per-follower exceptions so one
  failure doesn't abort others; mark status `success` / `partial` / `failed` /
  `in_sync`.
- `async sync_follower(primary_snapshot, follower)` → the per-follower unit.
- Apply order within a follower: removes/updates/adds is fine; do `set_rules` and
  `set_dns_config` as single REPLACE calls.
- Respect `dry_run`: compute + record drift and intended actions with outcome
  `skipped(dry_run)`, issue **zero** write calls.

**Tests (`test_engine.py`):** against a respx-mocked AdGuard, assert the correct
write calls for a representative diff; assert an in-sync follower issues no
writes and logs `in_sync`; assert `dry_run=True` issues **zero** write calls but
still records drift; assert one unreachable follower doesn't stop the others.

**Gate:** `pytest tests/test_engine.py` green.

---

### Step 8 — Scheduler (`app/scheduler.py`)

Wrap `AsyncIOScheduler`: register an interval job (`interval_minutes`) calling
`engine.run_once()`; guard against overlapping runs (`max_instances=1`,
`coalesce=True`). Expose `start()`, `shutdown()`, `trigger_now()` (for the
dashboard "Sync now"), and `reschedule(interval)` if interval changes at runtime.

**Gate:** a small test (or in `test_engine.py`) that `trigger_now()` invokes the
engine once.

---

### Step 9 — Web app (`app/web/routes.py`, templates, static)

FastAPI app (mounted/created in `main.py`):
- Pages (Jinja2 + HTMX): `/` status, `/drift`, `/history` — server-rendered,
  HTMX for "Sync now" and filtering; minimal hand-written CSS; vendored
  `htmx.min.js` (no CDN).
- JSON API: `GET /api/status`, `GET /api/runs`, `GET /api/drift`,
  `POST /api/sync` (triggers `scheduler.trigger_now()`), `GET /healthz`.
- Optional dashboard Basic Auth dependency gating UI + API (not `/healthz`) when
  `DASHBOARD_USER`/`DASHBOARD_PASSWORD` are set.
- Show a clear **DRY RUN** banner when `config.dry_run` is true.

**Gate:** `GET /healthz` returns 200; status page renders with seeded data via
FastAPI `TestClient`.

---

### Step 10 — Entrypoint (`app/main.py`)

- Load config, set up logging, init storage, build clients, build engine +
  scheduler, create FastAPI app, wire routes with dependencies.
- FastAPI `lifespan`: start scheduler on startup, shutdown cleanly; close httpx
  clients on shutdown.
- `if __name__ == "__main__"`: run uvicorn on `0.0.0.0:8080`.

**Gate:** `python -m app.main` boots locally against `config.example.yaml`
(pointed at mock/unreachable hosts) without crashing; `/healthz` responds.

---

### Step 11 — Containerisation

- **Dockerfile** (multi-stage): builder installs deps into a venv/wheels;
  runtime = `python:3.12-slim`, copy app, create non-root user, `EXPOSE 8080`,
  `HEALTHCHECK CMD` curling `/healthz`, `CMD ["python","-m","app.main"]`.
- **docker-compose.yml**: service `adguard-sync`, mount `./config.yaml:/config/config.yaml:ro`
  and `./data:/data`, env file `.env`, port `8080:8080`, `restart: unless-stopped`.
- `config.example.yaml` and `.env.example` matching the config schema.

**Gate:** `docker build .` succeeds; `docker compose up` starts; `/healthz` 200.

---

### Step 12 — CI/CD & docs

- `.github/workflows/ci.yml`: on push/PR — setup Python 3.12, install `.[dev]`,
  `ruff check .`, `ruff format --check .`, `pytest`.
- `.github/workflows/release.yml`: on tag `v*` — `docker/setup-buildx-action`,
  login to ghcr (`GITHUB_TOKEN`), buildx multi-arch (`linux/amd64,linux/arm64`),
  push `ghcr.io/${{ github.repository_owner }}/adguard-sync:{tag, latest}`,
  create a GitHub Release with notes from `CHANGELOG.md`.
- **README.md**: intro, how it works (primary→followers, full mirror), quick
  start (compose), full config reference, env/secrets, API reference, dashboard
  screenshots placeholder, security notes (LAN-only default, dashboard auth,
  verify_ssl guidance), troubleshooting.
- **CHANGELOG.md**: Keep a Changelog, `## [0.1.0]` initial entry.
- **LICENSE**: MIT (placeholder owner — confirmed at GitHub phase).

**Gate:** workflows are valid YAML; `pytest` + `ruff` green; README documents
every config field that exists in code.

---

## 4. Definition of done (whole project)

- `ruff check .` and `ruff format --check .` clean.
- `pytest` green; differ + engine suites cover add/update/prune/no-op/dry-run.
- `docker compose up` runs; `/healthz` 200; dashboard renders; scheduled job
  fires on interval.
- No secrets in logs or committed files; `.env` gitignored.
- README documents every config key; CHANGELOG has the 0.1.0 entry.

## 5. Out of scope (do NOT build)

- Bi-directional / multi-master sync (one-way push only).
- Editing `AdGuardHome.yaml` directly.
- Syncing query log, statistics, clients, DHCP, or TLS/encryption settings.
- External databases or message brokers.
- A JS build pipeline (HTMX is vendored, not bundled).

## 6. Open items for Claude/David (flag, don't decide)

- Exact `upstream_mode` value set semantics across AdGuard versions — confirm the
  field name/values against the target AdGuard version during the live smoke
  test.
- Whether `refresh_filters` should run every sync or only on blocklist change
  (default: only on change).
