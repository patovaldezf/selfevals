# selfevals runtime image — serves both the API and the sharded worker (the
# SvelteKit dashboard is a separate Node process and is intentionally not
# bundled here).
#
# Multi-stage: build a venv with uv from the locked dependencies, then copy it
# into a slim runtime. Storage is Postgres and the run queue is Redis, both
# external — configure with SELFEVALS_STORAGE_URL and SELFEVALS_REDIS_URL.

# ---- build stage -----------------------------------------------------------
FROM python:3.12-slim AS build

# uv: fast, lockfile-faithful installs. Pinned by digest-free tag is fine here
# because the lockfile (uv.lock) is the real reproducibility anchor.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached across source-only changes), then the
# project. `--extra web` pulls FastAPI + uvicorn; `--extra redis` is required
# because this same image runs the sharded worker (see docker-compose.yml),
# which claims run-jobs off Redis Streams. Add provider extras here (e.g.
# `--extra anthropic`) if the deployed agents need them.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra web --extra redis

# ---- runtime stage ---------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user; the volume is chowned to it below.
RUN useradd --create-home --uid 10001 app

# Storage is Postgres-only: the container needs SELFEVALS_STORAGE_URL at run
# time (docker-compose.yml sets it). There is no in-image database path.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SELFEVALS_OBJECTS_DIR=/data/objects

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src

# `/data` holds the filesystem object store for offloaded trace payloads, so it
# survives deploys and restarts. Rows live in Postgres.
RUN mkdir -p /data && chown -R app:app /data /app
USER app
VOLUME ["/data"]

EXPOSE 8080

# Default entrypoint is the API. docker-compose.yml overrides `command` to run
# the sharded worker (`selfevals worker runs`) from this same image.
CMD ["python", "-m", "selfevals.api", "--host", "0.0.0.0", "--port", "8080"]
