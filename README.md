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

## Tests

Tests run against real Postgres (the metric layer is SQL; SQLite is never used):

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web pytest
```

## Health

`GET /healthz` returns JSON. Point IT's monitor at it (see Operations below).

## Operations

**Point IT's monitor at `GET /healthz`** (for example `http://takt.plant.local:8000/healthz`, every
1 to 5 minutes). It needs no login and sends no email: Takt has no SMTP dependency (eng review D5).

| Status | Meaning |
|---|---|
| `200` | The database is reachable and every hub is fresh. Outside scheduled shifts hubs are always fresh, and with no hubs configured `/healthz` is `200`. |
| `503`, `"status": "stale"` | At least one hub has had **no new MES transaction for 30 minutes during a scheduled shift** (from the shift calendar). Check the scheduler container's logs (`docker compose logs scheduler`), the MES connection, and the hub's latest Ingest run in the admin. |
| `503`, `"status": "error"` | Takt can't reach its own Postgres database. Users see a plain "Takt can't reach its database" page. Check the `postgres` container. |

Quiet time is counted from the later of the hub's last new transaction and the start of the current
block of back-to-back scheduled shifts. A shift change does not reset the clock (with 7-15 and 15-23
shifts, a hub quiet since 14:20 is stale at 15:05), but the first shift after a gap or a non-working
day is not stale until 30 minutes in. A hub that has never sent a transaction follows the same rule:
stale 30 minutes after its block starts. The 30 minutes is `stale_after_minutes` in Site settings (admin).
Users see the same rule as a stale banner under the nav and a "STALE" line on printed sheets.

Response body:

```json
{
  "status": "stale",
  "database": "ok",
  "checked_at": "2026-10-12T14:30:00+00:00",
  "stale_after_minutes": 30,
  "hubs": [
    {
      "code": "dies",
      "name": "Dies Hub",
      "last_new_transaction_at": "2026-10-12T14:00:00+00:00",
      "last_successful_ingest_at": "2026-10-12T14:28:00+00:00",
      "in_scheduled_shift": true,
      "shift": "1st",
      "stale": true,
      "stale_since": "2026-10-12T14:30:00+00:00",
      "minutes_quiet": 30.0
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `status` | `ok`, `stale` (any hub stale) or `error` (database unreachable; `hubs` is empty) |
| `database` | `ok` or `unreachable` |
| `checked_at` | When the check ran (UTC) |
| `stale_after_minutes` | The threshold in use |
| `hubs[].last_new_transaction_at` | When ingest last saw a new or changed MES transaction |
| `hubs[].last_successful_ingest_at` | When the last ingest run for the hub succeeded |
| `hubs[].in_scheduled_shift` / `shift` | Whether a scheduled shift is running now, and which |
| `hubs[].stale` / `stale_since` | Whether the hub is stale, and when it crossed the threshold |
| `hubs[].minutes_quiet` | Minutes of the current shift with no new transaction (`null` outside a shift) |

The `web` container's Docker healthcheck also polls `/healthz`, so a stale hub marks the container
`unhealthy` in `docker ps` (Docker does not restart it; a restart would not fix an MES outage).

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
