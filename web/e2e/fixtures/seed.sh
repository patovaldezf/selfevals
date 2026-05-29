#!/usr/bin/env bash
#
# Build a deterministic fixture database for E2E tests.
#
# The web UI is a thin client over the FastAPI bridge, which reads from
# a SQLite db. Rather than mock the API, our E2E suite runs against a
# *real* backend pointed at a throwaway db that we seed here by running
# the canonical example experiment through the real `selfevals run`
# pipeline — the same path the test suite's `seeded_db` fixture uses
# (tests/api/test_api.py). This exercises the SSR proxy in
# hooks.server.ts for real, which a mock never would.
#
# Output: $E2E_DB (default web/e2e/.fixtures/e2e.sqlite), populated with
# one workspace, one experiment, two iterations, four traces, two
# decisions. Idempotent: the db is recreated from scratch every run.
#
# Requirements: the project's Python venv with selfevals installed.
# Resolution order for the interpreter:
#   1. $SELFEVALS_PYTHON if set
#   2. ../.venv/bin/python (repo-local uv venv)
#   3. `uv run python` if uv is on PATH
#   4. plain `python3`
set -euo pipefail

# --- locate paths -----------------------------------------------------
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # web/e2e/fixtures
web_dir="$(cd "$here/../.." && pwd)"                    # web
repo_dir="$(cd "$web_dir/.." && pwd)"                   # repo root

E2E_DB="${E2E_DB:-$web_dir/e2e/.fixtures/e2e.sqlite}"
SPEC="${E2E_SPEC:-$repo_dir/evals/experiments/example_pingpong.yaml}"
MAX_ITERS="${E2E_MAX_ITERS:-2}"

mkdir -p "$(dirname "$E2E_DB")"
# SQLite leaves -wal / -shm sidecars; clear all three for a clean slate.
rm -f "$E2E_DB" "$E2E_DB-wal" "$E2E_DB-shm"

# --- resolve the selfevals CLI ---------------------------------------
run_selfevals() {
  if [[ -n "${SELFEVALS_PYTHON:-}" ]]; then
    "$SELFEVALS_PYTHON" -m selfevals.cli.main "$@"
  elif [[ -x "$repo_dir/.venv/bin/selfevals" ]]; then
    "$repo_dir/.venv/bin/selfevals" "$@"
  elif command -v uv >/dev/null 2>&1; then
    (cd "$repo_dir" && uv run selfevals "$@")
  elif command -v selfevals >/dev/null 2>&1; then
    selfevals "$@"
  else
    echo "seed.sh: could not find the selfevals CLI. Set SELFEVALS_PYTHON" >&2
    echo "  to a Python that has selfevals installed, or create the repo venv." >&2
    exit 1
  fi
}

echo "seed.sh: seeding $E2E_DB"
echo "         spec=$SPEC  max-iterations=$MAX_ITERS"

# --persist-traces all  → store every trace so the trace-detail route
#                         has something to render.
run_selfevals --db "$E2E_DB" run "$SPEC" \
  --max-iterations "$MAX_ITERS" \
  --persist-traces all \
  >/dev/null

echo "seed.sh: done."
