# web/ — para sesiones de Claude

Web UI de selfevals. SvelteKit 5 + Vite 6 + Tailwind 4 (`@tailwindcss/vite`), **npm**
(package-lock.json). Se sirve junto a la API con `selfevals serve`; en dev, Vite en
:5173 proxya `/api` → `127.0.0.1:8000`. Dev server escucha en `localhost` (IPv6), usa
`http://localhost:5173`, NO `127.0.0.1:5173`.

## Comandos

```bash
npm ci
npm run check   # svelte-kit sync && svelte-check (0 errors es el gate)
npm run build   # adapter-node
npm run dev -- --port 5173
npm run test:e2e   # Playwright (fixtures en e2e/fixtures/seed.sh)
```

## Sistema de diseño (leer antes de tocar UI)

- **Tokens en `src/app.css`** (`@theme`): paleta monocroma + un acento indigo
  (`--color-brand`), threshold scale ok/warn/bad, dark mode completo vía
  `[data-theme='dark']`, type scale, motion tokens. **Nunca hardcodees un hex** en un
  componente — todo color pasa por un token.
- **Color de "¿es bueno?"** → `src/lib/viz/thresholds.ts` (`thresholdLevel` +
  `levelColor/levelSubtle/levelFg`). **Color de "¿qué tipo es?"** (badges categóricos:
  pass/fail, keep/reject) → `src/lib/viz/tones.ts` (`toneFg`/`toneBg`). Ambos leen los
  mismos vars de `app.css`, así que dark mode sale gratis.
- **Badges** → `ui/Pill.svelte` (el único primitivo de píldora). No re-inventes el markup.
- **Motion** → `src/lib/motion.ts`: presets `overlayScrim`/`drawerPanel`/`modalPanel`/
  `popoverPanel`/`staggerDelay`, con `prefers-reduced-motion` incorporado. Los overlays
  (`ui/Drawer`, `ui/Modal`, `ui/ConfirmDialog`, `ui/CommandPalette`) ya los usan.
- **Layout global**: `AppShell` (sidebar) + `Topbar` (breadcrumbs derivados de la URL vía
  `src/lib/nav/breadcrumbs.ts` — una página de detalle registra el nombre real de su
  entidad en el store `crumbLabels`). El título por-página va en `PageHeader.svelte`
  (eyebrow/title/subtitle/meta/actions), NO en un header ad-hoc.

## Convenciones

- **Estándar de styling: Tailwind utilities + tokens.** (Ej. `experiments/[experiment]`.)
  Componentes nuevos o migrados usan utilities; NO agregar `<style>` scoped nuevo salvo
  para lo que Tailwind no cubre. Las páginas viejas con `<style>` scoped se migran al
  tocarlas, no de golpe.
- **Runes de Svelte 5** en todo componente nuevo o tocado sustancialmente (`$props`,
  `$state`, `$derived`, snippets). No mezclar runes y API vieja en el mismo archivo. No
  migrar big-bang los legacy existentes.
- **Data layer**: SSR `load` en `+page.server.ts` + capa `src/lib/api/` (http.ts +
  resources/\*, tipos generados de OpenAPI con `npm run gen:api`). Live vía SSE
  (`src/lib/api/sse.ts`) + poll con `invalidate()`. `@tanstack/svelte-query` está
  instalado pero NO se usa (a retirar).
- **Tests** espejan la ruta del componente. Charts son SVG a mano (sin lib de charts).
