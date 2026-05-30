#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Provisions everything the Playwright E2E suite (web/playwright.config.ts)
# needs to run end to end:
#   1. Python venv via uv — the suite boots the real FastAPI backend through
#      .venv/bin/python (see SELFEVALS_PYTHON in playwright.config.ts).
#   2. web/ npm dependencies (incl. @playwright/test).
#   3. The Chromium browser Playwright drives, plus its system libs.
#
# Idempotent and non-interactive: safe to re-run; each tool no-ops when its
# work is already cached in the container image.
set -euo pipefail

# Web-only: local dev machines manage their own toolchains. On a workstation
# this exits immediately and never touches the environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO_ROOT"

echo "[session-start] Syncing Python deps (uv)…"
uv sync --all-extras --dev

echo "[session-start] Installing web npm deps…"
npm install --prefix web

echo "[session-start] Ensuring Playwright Chromium is present…"
# No --with-deps: the web container already ships Chromium's system libs, and
# apt is sandboxed (some PPAs are off the network allowlist). With the pinned
# @playwright/test (1.56.0 → chromium build 1194) this matches the browser the
# image preinstalls under PLAYWRIGHT_BROWSERS_PATH, so it's a no-op offline.
# Tolerate a download failure so a locked-down network never blocks the session.
npm --prefix web exec -- playwright install chromium || \
  echo "[session-start] WARN: could not fetch Chromium; relying on preinstalled browser."

echo "[session-start] Done — E2E suite ready (cd web && npm run test:e2e)."
