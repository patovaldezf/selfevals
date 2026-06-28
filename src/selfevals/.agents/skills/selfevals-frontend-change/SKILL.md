---
name: selfevals-frontend-change
description: Frontend workflow for selfevals SvelteKit UI changes, API client usage, polling, SSE, loaders, localStorage, route pages, and component refactors.
---

# selfevals Frontend Change

## Workflow

1. Use `web/src/lib/api/client.ts` or a module under `web/src/lib/api/` for internal API calls. Components and routes must not create their own auth headers.
2. Keep route `+page.svelte` files focused on composition. Extract repeated tabs, panels, polling, SSE, and modal state into components or hooks.
3. Primary route data failures should become route errors. Only optional panels may degrade to empty state, and they must expose an explicit error field.
4. Guard polling with an in-flight flag or shared store. Do not start overlapping loops.
5. Pass explicit SSE error handlers for live pages and surface malformed event state in dev/test paths.
6. Access browser storage through safe helpers that handle unavailable or blocked `localStorage`.

## Verification

- Run `cd web && npm run lint && npm run check && npm run build`.
- Run relevant Playwright tests when navigation, loaders, live state, or forms change.
- Run `uv run python scripts/audit_technical_debt.py --fail-on-regression` to catch direct fetch/header regressions.
