# Web E2E tests (Playwright)

End-to-end tests for the SvelteKit UI, run against a **real FastAPI
backend** pointed at a **seeded throwaway database**. No API mocking —
the suite exercises the same path production does, including the SSR
`/api` proxy in [`src/hooks.server.ts`](../src/hooks.server.ts).

## Topology

```
  Playwright (chromium)
        │  drives
        ▼
  SvelteKit adapter-node server  :4173   ← node build/index.js
        │  /api/* proxied (SELFEVALS_API_BASE)
        ▼
  FastAPI bridge                 :8000   ← python -m selfevals.api
        │  reads
        ▼
  e2e/.fixtures/e2e.sqlite               ← seeded by e2e/fixtures/seed.sh
```

Playwright's `webServer` (in [`playwright.config.ts`](../playwright.config.ts))
boots **both** servers; `globalSetup` seeds the fixture db first. Ports
are off the dev defaults (web `4173`, not `5173`) so a `npm run dev`
session can run alongside the suite.

> **Why `node build/index.js` and not `vite preview`?**
> The app uses `adapter-node` and proxies `/api` inside
> `hooks.server.ts`. `vite preview` only serves the static client bundle
> and never runs server hooks, so the proxy — and thus all data loading —
> would be dead. We build, then run the real adapter-node server, exactly
> like `selfevals serve` does in production.

## Running locally

```bash
cd web
npm run test:e2e          # headless, all specs
npm run test:e2e:ui       # Playwright UI mode (watch + time-travel)
npm run test:e2e:report   # open the last HTML report
npm run e2e:seed          # (re)build the fixture db only
```

First run also needs the browser binary once:

```bash
npx playwright install chromium
```

### Prerequisites

The suite launches the Python backend, so it needs an interpreter with
`selfevals` installed. Resolution order (see `e2e/fixtures/seed.sh` and
`playwright.config.ts`):

1. `$SELFEVALS_PYTHON` — explicit interpreter path
2. `../.venv/bin/python` / `../.venv/bin/selfevals` — the repo's uv venv
3. `uv run …` if `uv` is on `PATH`
4. plain `python3` / `selfevals`

If none resolve, seeding fails with a clear message. Create the venv with
`uv sync --all-extras --dev` from the repo root.

## The fixture database

`seed.sh` runs the canonical example experiment through the **real**
pipeline:

```bash
selfevals --db e2e/.fixtures/e2e.sqlite run \
  evals/experiments/example_pingpong.yaml --max-iterations 2 --persist-traces all
```

That yields a deterministic dataset: 1 workspace, 1 experiment
(`pingpong baseline`), 2 iterations, 4 traces, 2 decisions. The db is
recreated from scratch each run and is git-ignored.

> **IDs are not stable.** Only the workspace id is fixed
> (`ws_01HZZZZZZZZZZZZZZZZZZZZZZZ`, from the spec's `workspace:` key).
> Experiment / trace / iteration ids are ULIDs minted fresh each seed, so
> tests discover them at runtime via the API or by following links — see
> `firstExperimentId()` in [`helpers.ts`](./helpers.ts). **Don't hardcode
> them.**

## Layout

```
e2e/
├── README.md            ← you are here
├── global-setup.ts      ← seeds the fixture db before the suite
├── helpers.ts           ← shared utils (WORKSPACE_ID, gotoOk, firstExperimentId)
├── fixtures/
│   └── seed.sh          ← builds e2e/.fixtures/e2e.sqlite
└── tests/
    ├── smoke.spec.ts        ← every route renders against real data
    ├── navigation.spec.ts   ← click-through flows (list → ws → experiment)
    └── api-contract.spec.ts ← pins the FastAPI JSON shapes the FE mirrors
```

## Writing a new test

- Use `gotoOk(page, path)` instead of bare `page.goto` — it fails loudly
  if the page rendered the "Backend unreachable" fallback.
- Need an experiment / trace id? Get it from `firstExperimentId(request)`
  or by clicking a link, never by hardcoding.
- Prefer role/text selectors (`getByRole('heading', { name: … })`) over
  CSS classes — the Tailwind classes churn, the semantics don't.

## CI

The `E2E (Playwright)` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
sets up uv + Python + Node, installs the Chromium binary, and runs
`npm run test:e2e`. The HTML report is uploaded as an artifact on every
run (`playwright-report`), so failures are debuggable from the Actions tab.
