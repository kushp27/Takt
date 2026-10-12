# Takt

Line health and operator coaching from MES transactions. Reads Cores Hub and Dies Hub
read-only; never writes to the MES. Design: `docs/design/takt-design.md`.

Stack: Django + Postgres, server-rendered templates with htmx, supercronic for scheduled
jobs. Three containers: `web`, `scheduler`, `postgres`.

## Run it

```sh
cp .env.example .env        # edit secrets
docker compose up --build   # web on http://localhost:8000
```

The web container applies migrations on start. The scheduler runs `docker/crontab`:
ingest every 5 minutes, the 7-day re-read at 02:00 plant time, then the trust grade.

## Accounts and roles (pilot)

Pilot users are Django local accounts (SSO/LDAP replaces them before rollout; see the stub in
`takt/settings.py`, Open Question 6). For a dev or demo database:

```sh
docker compose exec web python manage.py seed_demo_users   # DEV/DEMO ONLY
```

It creates `builder` (superuser), `brian` (ops manager), a supervisor per area, a lead per shift
of each area, and `admin` (configuration only). The password is `$TAKT_DEMO_PASSWORD`, or a
printed dev-only default. Until Gate 1 is approved in Site settings, only the builder sees
operator names; everyone else sees per-page letters ("Operator A, B, C").

## Tests

Tests run against real Postgres (the metric layer is SQL; SQLite is never used):

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web pytest
```

## Health

`GET /healthz` returns JSON. Point IT's monitor at it (see Operations below).

## Layout

| App | What it owns |
|---|---|
| `core` | Shared schema for both hubs: hubs, lines, stations, parts, routing, operators and aliases, shift calendar, demand, jobs, scrap, settings |
| `ingest` | `manage.py ingest`; the `HubAdapter` interface; the fixture adapter; real adapter stubs |
| `metrics` | SQL views for pace, yield, gaps (versioned migrations) |
| `trust` | Trust grade and count-trust revocation |
| `accounts` | Roles and the operator identity policy |
| `disputes` | Per-job disputes |
| `ops` | `/healthz` and freshness |
| `web` | Screens, templates, CSS tokens |
