# Web + API stack decisions

Locked-in choices for the selfevals web surface. Deviations from the
recommendation in `docs/prompts/web_session_prompt.md` are explained
here; everything not mentioned follows the prompt's defaults.

## Framework: SvelteKit, not Next.js

The session prompt recommended Next.js 15. User-directed deviation:
**SvelteKit** instead. Rationale: SvelteKit's data-loading model
(`+page.server.ts` `load`) is a direct fit for the data-heavy,
read-mostly surfaces here, with less ceremony than RSC + boundary
juggling; the Svelte component model produces less generated DOM,
which matters for the dense iteration/trace views; and pato has
chosen SvelteKit for the parallel `pato-os` project, so design
language carries over.

Deploy target: same shape — Vercel adapter for the web, FastAPI as a
sidecar for the Python API.

## API layer: plain REST + Zod, not tRPC

The Python backend already owns the truth (Pydantic v2 schemas).
SvelteKit's server `load` functions call FastAPI directly with a
typed `fetch` wrapper. We get FastAPI's auto-generated OpenAPI at
`/api/openapi.json` for free; if we ever want generated TS types we
can run `openapi-typescript` against it.

## State: native Svelte stores + TanStack Query for Svelte

Server-state caching via `@tanstack/svelte-query` (same library
under the hood as Next.js's TanStack Query). UI-only state stays in
native Svelte stores — no Zustand. Routing-level state lives in URL
search params via SvelteKit's reactive `$page`.

## Auth: none for MVP

Per the prompt. A stub `X-SelfEvals-User: local` header travels
through every request so we can plug real auth in later without
touching the screen code.

## Tables: TanStack Table for Svelte (`@tanstack/svelte-table`)

Same column/sorting/filtering model as the React version; published
by the same project.

## Charts: LayerCake (Svelte-native), Recharts equivalents on hand

LayerCake is the standard Svelte chart toolkit (declarative layers
on top of D3 scales). It covers sparklines, the longitudinal
anchor-set view, and the simple bars on the failure-clusters
skeleton. Side-by-side prompt diffs don't need a chart library.

## CSS: Tailwind v4 + bits-ui / shadcn-svelte base, overridden aggressively

`shadcn-svelte` (built on bits-ui) is the Svelte port of shadcn/ui.
Accessible primitives, no opinionated look. The look comes from the
design tokens in this directory and from rewriting the component
skins inline.

## Fonts: Inter + JetBrains Mono self-hosted

Loaded via `@fontsource/inter` and `@fontsource/jetbrains-mono` (no
external font CDN call at runtime). Tabular numerals on by default
in monospace cells.

## Backend service: FastAPI under `src/selfevals/api/`

Per recommendation. New console script `selfevals-api`. Reuses the
existing Pydantic models verbatim — never duplicates schema.

## Out of scope for v0

- Cross-experiment compare (lives on the anchor-set surface only).
- Real authentication and team management.
- Live trace streaming (web reads finished traces).
- Server-sent events for run progress; CLI is still where you watch
  a run in real time.
