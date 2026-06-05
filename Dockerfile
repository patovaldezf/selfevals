# selfevals API — production image (API only; the SvelteKit dashboard is a
# separate Node process and is intentionally not bundled here).
#
# Multi-stage: build a venv with uv from the locked dependencies, then copy it
# into a slim runtime. The result is a single long-running uvicorn process —
# which matches the runtime model (experiment runs execute on in-process
# threads, so the machine must stay up for the duration of a run).

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
# project. `--extra web` pulls FastAPI + uvicorn; add provider extras here
# (e.g. `--extra anthropic`) if the deployed agents need them.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra web

# ---- runtime stage ---------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user; the volume is chowned to it below.
RUN useradd --create-home --uid 10001 app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SELFEVALS_DB=/data/selfevals.sqlite

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src

# `/data` is the Fly volume mount point — the SQLite file (+ -wal/-shm and the
# object store) live here so they survive deploys and restarts.
RUN mkdir -p /data && chown -R app:app /data /app
USER app
VOLUME ["/data"]

EXPOSE 8080

# Bind to 0.0.0.0:8080 (Fly's internal port). The DB path comes from
# SELFEVALS_DB so it lands on the mounted volume.
CMD ["python", "-m", "selfevals.api", "--host", "0.0.0.0", "--port", "8080", "--db", "/data/selfevals.sqlite"]
