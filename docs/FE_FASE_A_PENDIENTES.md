# Fase A — Pendientes y deuda diferida

Vivo. Se actualiza cada vez que un PR de Fase A se mergea y descubre
algo que debe quedar registrado para después.

Para el plan completo, ver [FRONTEND_PRODUCT_PLAN.md](FRONTEND_PRODUCT_PLAN.md).

## Estado de la Fase A

| # | PR | Estado | Notas |
|---|---|---|---|
| A1 | #16 los 3 bugs + plan | ✅ merged | — |
| A2 | #17 link iter → traza | ✅ merged | — |
| A3 | #18 resolver pointers | ✅ merged | — |
| A4 | #19 selfevals serve | ✅ merged | latente BUG-4 hasta este PR |
| A5 | #20 identidad humana sobre ULID | ✅ merged | — |
| A6 | #21 span kind visible + densidad | ✅ merged | QA visual destrabada al fix-ear BUG-4 |
| BUG-4 | #22 proxy `/api` en `selfevals serve` | ✅ merged | hooks.server.ts + `SELFEVALS_API_BASE` |
| A7 | #23 a11y filas ([button]/[link]) | ✅ merged | — |
| A8 | paginación + virtual scroll | 🟡 in progress | envelope solo en `/experiments` por ahora |

## Pendientes registrados (orden de descubrimiento)

### De A5 (#20)

1. **URL routing por slug humano.** El URL sigue siendo el ULID
   (`/<ws_id>/experiments/<exp_id>`). Slugs humanos en la ruta
   requieren:
   - `queries.workspace_detail` aceptar slug además de id.
   - Posiblemente `queries.list_experiments` / `experiment_detail`
     resolver experimento por slug dentro de un workspace.
   - Routes SvelteKit con `[workspace]` aceptan ambos sin cambio.
   - Scope: backend resolver + tests. No es de Fase A; ir como propio
     PR cuando duela.

2. ~~**Anchor-set: CopyableId chip dentro del row.**~~ ✅ Resuelto en
   A7. Row reescrito de `<a>` envolvente a `<div>` con `<a>` (link al
   experiment) y `<CopyableId>` lado a lado. focus-within ring para
   feedback de teclado consistente.

3. ~~**QA visual de los chips CopyableId.**~~ ✅ Desbloqueado con
   BUG-4. Render visual confirmado en el trace viewer y experiment
   detail vía `selfevals serve`. Hover, tick `copied`, y stop-propagation
   en celdas clickeables siguen siendo verificación manual (no test
   automatizado — vitest no está montado, ver A6).

4. **Workspace ID en overview no es copiable.** El `/[workspace]/+page.svelte`
   muestra `workspace.slug` como chip y `workspace.name` como h1, pero
   el ULID del workspace no aparece en ningún lado. Si alguien lo
   necesita para curl/CLI, debe ir a otra ruta o leerlo de la URL.
   Decisión consciente — no añadir hasta que duela.

### De A6 (en progreso)

1. ~~**QA visual del trace tree.** Bloqueado por BUG-4.~~ ✅ Desbloqueado
   con el fix de BUG-4. Dogfood: `curl :web_port/<ws>/traces/<run>`
   muestra glyph ◆ + label "llm" + nombre "adapter_response", todo
   renderizado correctamente.
2. **Iconografía de spans.** Los glifos Unicode (`◆ ✦ ⚙ ◇ ▽ △ ◉ ↦
   ☞ ◈ ✕`) son funcionales pero no son SVGs. Si el set crece o el
   peso visual se queda corto, evaluar un set SVG inline (sin
   dependencia externa) — pero NO antes de que un usuario real se
   queje del look actual.
3. **`tokens_per_second` en el árbol.** Lo expongo en el API
   (`keep_keys`) pero NO lo renderizo en SpanNode aún: en pingpong da
   `None`. Cuando ROADMAP #9 lo pueble, añadir como fact (junto a TTFT
   y tokens). Mismo para `time_to_first_token_ms` en ejemplos reales.
4. **Densidad: facts ocultas en mobile.** `hidden sm:inline-block`
   esconde los facts en viewports angostos. El plan §1.2-7 dice
   "mobile está roto" → decisión consciente desktop-first, no
   regresión. Cuando llegue mobile, refactorizar SpanNode para
   colapsar facts a chevron expandible.

### De A7 (cerrado)

Nada de deuda diferida — pendiente A5#2 (anchor-set CopyableId chip)
quedó resuelto en este PR.

### De A8 (en progreso)

1. **Pagination envelope solo en `/experiments`.** Los demás endpoints
   de lista (`/workspaces`, `/iterations`) siguen devolviendo lista
   plana. Cuando algún usuario tenga >100 de cualquiera de esos, hacer
   el mismo upgrade (la receta ya está). Por ahora, deuda registrada.

2. **FE UI de "Load more" / paginación.** El envelope viaja pero la
   UI no tiene botón "Load more" — solo muestra "X of N" cuando hay
   más páginas. La razón: nadie tiene aún >100 experimentos así que
   no hay un caso de uso real para probar. Cuando duela, añadir el
   botón con `offset` incremental.

3. **Virtual scroll: row height aproximada.** `SpanTreeFlat` asume
   `ROW_HEIGHT_PX = 28` (uniforme). Si un día metemos rows multi-línea
   (p.ej. error con traceback inline), la matemática del window se
   rompe. Solución correcta cuando llegue: `ResizeObserver` por fila
   + altura medida. Por ahora un fact en una sola línea encaja.

4. **No hay "scroll to selected".** Si la traza tiene 1000 spans y el
   span seleccionado está en el medio, abrir la página NO hace scroll
   a esa fila. La selección viene del click humano (no de la URL todavía),
   así que el seleccionado siempre empieza en `null` y el usuario
   recorre. Cuando integrumos URL `?span=sp_...`, añadir auto-scroll.

### De A8 (pendiente)

_(pendiente)_

## Bugs/deuda fuera de Fase A descubiertos en el camino

### BUG-4 ✅ FIXED — `selfevals serve` no proxya `/api` → web Node ve 404 en todo

**Síntoma.** Con `selfevals serve` (modo producción, no `npm run dev`),
toda ruta del web devuelve 404 o "Backend unreachable / API 404".
Verificado en QA de A6:

```
$ uv run selfevals --db /tmp/qa.sqlite serve --port 5188
$ curl :5189/                          # 200 pero renderiza "API 404"
$ curl :5189/<ws>/traces/<run_id>      # 404
$ curl :5189/api/workspaces            # 404 (no hay handler)
```

**Causa raíz.** `cli/commands.py:cmd_serve` spawna el Node server de
SvelteKit en `port+1` con `ORIGIN=http://host:port+1`, pero **no
configura proxy**. El FE usa `fetch('/api/...')` (relativo) en
`+page.server.ts` — en SSR eso pega al Node server (que no tiene
ruta `/api`), no a FastAPI en `port`. El propio comentario en
`commands.py:735-737` reconoce el hueco ("the built server has no
proxy, so the web side must call the API via absolute URLs") pero
nunca se implementó el fix.

**Impacto.** Toda Fase A es invisible end-to-end por la vía oficial
de dogfood. Los PRs A1-A6 pasan typecheck + pytest + API roundtrip,
pero ningún usuario ha visto el resultado renderizado vía
`selfevals serve`. (Sí funciona vía `npm run dev` + `uvicorn`
manualmente — el bug es específico de la ruta "un comando" de A4.)

**Fixes posibles (preferir 1):**
1. `SELFEVALS_API_BASE=http://host:port` env var hacia el subprocess
   Node + cliente API absoluto cuando esté presente (server-side
   load). El cliente del browser sigue relativo.
2. Hooks SvelteKit (`hooks.server.ts`) que proxean `/api` → FastAPI
   cuando detectan `SELFEVALS_API_BASE`.
3. Servir el build estático desde FastAPI y dropar el Node server
   (pierde SSR — no aceptable).

**Test a añadir.** `test_serve_web_can_reach_api_through_node`: lanza
`selfevals serve`, espera a que ambos puertos respondan, y hace
`curl :web_port/` esperando ver al menos un workspace en el HTML
renderizado (no "Backend unreachable"). Esto pinta el contrato
end-to-end y captura cualquier futuro retroceso del proxy.

**Prioridad.** Alta. Bloquea QA visual de toda Fase A. Ir como PR
propio (BUG-4), no enredarlo con A6/A7/A8. Idealmente antes de
mergear A6.

**Fix shipped (PR fix/bug-4-serve-api-proxy).** Opción 2:
`web/src/hooks.server.ts` con un `handle` que intercepta `/api/*`,
lee `SELFEVALS_API_BASE` y hace `fetch` al upstream FastAPI
streameando body (importante para SSE: `/api/.../stream` es
text/event-stream sin EOF). `cmd_serve` setea
`SELFEVALS_API_BASE=http://host:api_port` en el subprocess Node.
Dogfood end-to-end OK:
`curl :web_port/<ws>/traces/<run_id>` ahora devuelve 200 con todo
el render de Fase A (A5 nombre humano + A6 glyph + facts).

**Pendiente.** Test integration con `node` real está fuera del PR
(CI Python no tiene Node; CI Web no tiene Python). Unit test del env
var en `test_serve_spawns_node_with_correct_env` cubre la mitad Python.
Para el matrix completo, configurar un job de CI con Python + Node
juntos — registrar como deuda separada cuando duela.
