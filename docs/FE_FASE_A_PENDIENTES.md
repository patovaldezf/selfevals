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
| A4 | #19 selfevals serve | ✅ merged | — |
| A5 | #20 identidad humana sobre ULID | 🟡 open | — |
| A6 | span kind visible | ⏳ in progress | — |
| A7 | a11y filas ([button]/[link]) | ⏳ pendiente | — |
| A8 | paginación consistente + virtual scroll | ⏳ pendiente | — |

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

2. **Anchor-set: CopyableId chip dentro del row.** El `<a>` envuelve
   toda la fila — meter `<button>` adentro es HTML inválido y se traga
   el click. **Bloqueado por A7** (filas como `[button]`/`[link]`).
   Cuando A7 reescriba el row pattern, restaurar el chip ahí.

3. **QA visual de los chips CopyableId.** En #20 se validó typecheck
   + API (curl). El render visual (hover, tick `copied`, contraste,
   stop-propagation en celdas clickeables) se difiere al dogfood
   combinado con A6/A7. Si A6 o A7 introducen una vista nueva que
   ya tenga los chips integrados, validar ahí.

4. **Workspace ID en overview no es copiable.** El `/[workspace]/+page.svelte`
   muestra `workspace.slug` como chip y `workspace.name` como h1, pero
   el ULID del workspace no aparece en ningún lado. Si alguien lo
   necesita para curl/CLI, debe ir a otra ruta o leerlo de la URL.
   Decisión consciente — no añadir hasta que duela.

### De A6 (en progreso)

1. **QA visual del trace tree.** Mismo punto que A5#3: typecheck + API
   roundtrip verificados, render visual diferido (bloqueado por el
   bug del proxy, ver "Bugs descubiertos" abajo).
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

### De A7 (pendiente)

_(pendiente)_

### De A8 (pendiente)

_(pendiente)_

## Bugs/deuda fuera de Fase A descubiertos en el camino

### BUG-4 (CRÍTICO, descubierto en A6): `selfevals serve` no proxya `/api` → web Node ve 404 en todo

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
