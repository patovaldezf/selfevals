# Deploying the selfevals API

The default API is a stateful, long-running service: FastAPI over a local
SQLite file, with experiment runs executing on **in-process background
threads**. That shape decides the simplest host. For the first scale step,
configure Postgres for storage and Redis for live events before introducing
Kafka, NATS, ClickHouse, or external workflow engines.

## Why Fly.io (and why not Vercel)

**Vercel does not fit.** Vercel runs serverless/edge functions: stateless, with
a request-scoped execution budget and an ephemeral, read-only filesystem. Three
hard blockers for this service:

- **The `POST .../experiments/run` loop runs for minutes on a background
  thread.** A serverless function is billed/limited per request and is torn down
  after the response returns — the background run would be killed. There is no
  always-on process to carry it.
- **SQLite needs a real, writable, persistent disk** (WAL mode uses
  `-wal`/`-shm` sidecar files). Serverless filesystems are ephemeral and reset
  between invocations, so state would not survive.
- **The span broker holds live run state in memory** for SSE. That requires a
  single durable process, not per-request isolates.

**Fly.io fits** because it runs a normal long-lived container with a persistent
volume:

- one always-on machine carries the background run threads to completion;
- a Fly **volume** mounted at `/data` gives SQLite a durable disk that survives
  deploys/restarts;
- the in-memory broker works because it is one process, not many isolates.

Railway or any VPS / container host with a persistent disk would work equally
well — the requirement is "long-running process + persistent volume", which
Fly provides with the least config. With `SELFEVALS_STORAGE_URL=postgresql://...`
and `SELFEVALS_REDIS_URL=redis://...`, API reads and SSE fan-out can move off
single-process SQLite/in-memory state. Experiment execution still runs in
process until a Redis-backed job queue is added.

## What ships

The image is **API-only**. The SvelteKit dashboard (`web/`) is a separate Node
SSR process; bundling it would mean Node + a second process in the container.
Workstreams B (seals) and C (the Playground) consume the **API**, and C ships
its own frontend — so the API is the deliverable. The dashboard can be added
later as a second Fly app or a second process if a hosted dashboard is wanted.

## Files

- `Dockerfile` — multi-stage build with `uv`, installs `selfevals[web]`, runs
  `python -m selfevals.api` bound to `0.0.0.0:8080`, DB on the volume.
- `.dockerignore` — keeps state, the web app, tests, and docs out of the image.
- `fly.toml` — one always-on machine, a `/data` volume, and a `/api/health`
  check. `auto_stop_machines = false` + `min_machines_running = 1` so a machine
  is never reaped mid-run.

## Deploy steps

Prerequisite: `flyctl` installed and authenticated (`fly auth login`).

```bash
# 1. Create the app (first time only). --no-deploy: provision before shipping.
fly launch --no-deploy --copy-config --name selfevals-api --region iad

# 2. Create the persistent volume the SQLite db lives on (first time only).
#    Match the region to primary_region in fly.toml.
fly volumes create selfevals_data --region iad --size 1

# 3. Deploy.
fly deploy

# 4. Verify.
fly status
curl -s https://selfevals-api.fly.dev/api/health        # {"status":"ok",...}
curl -s https://selfevals-api.fly.dev/api/workspaces
```

### Build the image locally first (optional sanity check)

```bash
docker build -t selfevals-api .
docker run --rm -p 8080:8080 -v "$PWD/_data:/data" selfevals-api
curl -s localhost:8080/api/health
```

## Operational notes

- **Provider API keys.** If the deployed agents call an LLM provider, set the
  key as a Fly secret (never in `fly.toml`): `fly secrets set ANTHROPIC_API_KEY=...`.
  Add the matching extra to the Dockerfile's `uv sync` line
  (e.g. `--extra anthropic`) so the SDK is installed.
- **CORS.** `api/app.py` currently allows `localhost:5173`. Before the Playground
  hits the deployed API from another origin, add that origin to the
  `allow_origins` list (this is a deliberate, explicit change — not wildcarded).
- **Backups.** The whole state is one SQLite file on the volume. Snapshot it with
  `fly ssh console -C "sqlite3 /data/selfevals.sqlite '.backup /data/backup.sqlite'"`
  or use Fly volume snapshots.
- **Postgres storage.** Install `selfevals[postgres]` and set
  `SELFEVALS_STORAGE_URL=postgresql://user:pass@host:5432/dbname`. The API keeps
  the generic entity contract but projects hot entities into relational tables
  with indexes for experiments, iterations, cases, traces, run ids, and thread
  ids.
- **Redis live events.** Install `selfevals[redis]` and set
  `SELFEVALS_REDIS_URL=redis://host:6379/0`. Live span events use Redis Streams;
  Redis is coordination/live delivery, not durable historical storage.
- **Redis run workers.** With `SELFEVALS_REDIS_URL` set, `POST .../experiments/run`
  enqueues a durable `RunJob` instead of starting a local daemon thread. Run
  `selfevals worker runs` in one or more long-lived worker processes to consume
  jobs with leases, retries, cancellation, and dead-lettering.
- **Scaling.** With only SQLite and the in-memory broker, do **not** scale to
  multiple machines. With Postgres + Redis, read/API and SSE state can run across
  processes, and experiment execution can move to workers. Local/dev mode still
  falls back to in-process threads when Redis is absent.
- **Local runtime profile.** During local scale testing, use runtime variables
  (`SELFEVALS_STORAGE_URL`, `SELFEVALS_REDIS_URL`) rather than separate test-only
  URLs. The checked-in `.env` carries the current local Postgres/Redis targets.

## NOT done here

No real deploy is performed by this repo. These files make the deploy
reproducible; running `fly deploy` is a manual, approved step.
