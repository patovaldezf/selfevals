# Spec — Anchor-set global (canonical case-set re-run across versions)

> Tarea aislada para otra sesión de Claude Code. Esta spec describe **qué falta**
> para que el anchor-set sea "global proper" en vez de las timelines per-experiment
> que hoy expone el endpoint. Self-contained: incluye el estado actual, el target,
> el diseño de datos, el endpoint, la UI y los tests.

## Context

El anchor-set es la vista longitudinal de "¿estamos mejorando de verdad?". Hoy
`GET /api/workspaces/{ws}/anchor-set` (`src/selfevals/api/queries.py:704`,
`anchor_set_history`) devuelve un atajo honesto: por cada experimento, el
primary-metric de cada iteración con métricas, ordenado por fecha. La propia
docstring lo admite:

```
"Anchor-set proper requires repeated reruns of a canonical case
 set; until that lands, we expose the per-experiment latest
 completed iteration so the chart has shape."
```

La UI (`web/src/routes/[workspace]/anchor-set/+page.svelte`) agrupa esos puntos por
experimento y dibuja una sparkline por experimento. Es útil pero **no es un anchor-set
real**: cada experimento mide casos distintos, así que las líneas no son comparables
entre sí. El anchor-set "de verdad" fija un **conjunto canónico de casos** (el "anchor
set") y lo **re-corre** contra cada versión del agente/prompt, de modo que la curva
mide la misma cosa a lo largo del tiempo — exactamente como un benchmark de regresión
visible.

## Estado actual (verificado)

- **Endpoint**: `anchor_set_history` (queries.py:704) → `list[AnchorPoint]`.
- **Schema** `AnchorPoint` (api/schemas.py): `experiment_id, experiment_name, iteration,
  primary_metric_name, primary_metric_value, decision_outcome, created_at`.
- **UI**: agrupa por `experiment_id`, una `Sparkline` por experimento.
- **Relación con datasets/baseline**: ya existe `DatasetBaseline` (la primera corrida
  sobre un dataset auto-registra su mejor iteración como baseline, idempotente) y el
  `regression check` compara una iteración contra ese baseline. El anchor-set global
  es el primo visual/longitudinal de eso: en vez de un punto de comparación, **la serie
  completa** de un case-set fijo a través de versiones.

## Target

Una serie temporal donde el eje X son **versiones** (corridas/iteraciones ordenadas) y
el eje Y es el primary-metric **medido siempre sobre el mismo anchor case-set**, para
que la curva sea comparable punto a punto. Idealmente con:

- Selección de **qué dataset/case-set** es el anchor (uno designado por workspace, o
  por feature).
- Marcadores de regresión (cuándo cayó por debajo del baseline).
- Drill-down: click en un punto → la iteración que lo produjo.

## Diseño propuesto (a validar por quien lo implemente)

### Datos
Reusar lo que ya existe en vez de inventar una entidad nueva si es posible:

- **Opción A (preferida, menos superficie)**: derivar la serie de las
  `IterationRecord` que corrieron contra un **dataset designado como anchor**. Un
  dataset ya es entidad de primer orden (`Dataset`, v0.9.0) y ya hay `DatasetBaseline`.
  Añadir una marca "este dataset es el anchor del workspace" (flag en `Dataset` o un
  pequeño registro `AnchorSet { workspace_id, dataset_id }`). El endpoint filtra las
  iteraciones cuyo run usó ese `dataset_id` y emite un punto por iteración.
- **Opción B (más fiel, más cara)**: una entidad `AnchorRun` que re-corre el case-set
  canónico on-demand contra una versión dada, desacoplada de los experimentos. Más
  trabajo (scheduler/trigger, almacenamiento de resultados propios). Sólo si la
  Opción A no captura el caso de uso.

Recomiendo **empezar por Opción A**: el 80% del valor (curva comparable sobre un
case-set fijo) sin entidad/loop nuevos.

### Endpoint
Nuevo o extendido:
- `GET /api/workspaces/{ws}/anchor-set?dataset_id=<ds>` → serie filtrada al anchor
  dataset; si no se pasa `dataset_id`, usar el dataset marcado como anchor del ws.
- `PUT /api/workspaces/{ws}/anchor-set` (o reusar el patrón de
  `PUT .../datasets/{id}/baseline`) para **designar** qué dataset es el anchor.
- Response: extender `AnchorPoint` con `dataset_id` y un flag `regressed` por punto
  (comparando contra el `DatasetBaseline` que ya se computa).

### UI
`web/src/routes/[workspace]/anchor-set/+page.svelte`:
- Si hay anchor dataset designado → una sola serie comparable (no agrupar por
  experimento), con marcadores de regresión y baseline line.
- Selector para designar/cambiar el anchor dataset.
- Fallback: si no hay anchor designado, mantener las timelines per-experiment actuales
  (no romper la vista existente).

### Tests
- `tests/api/` para el nuevo filtro por `dataset_id` y la designación.
- `tests/storage/` si se añade la marca de anchor en `Dataset`/`AnchorSet`.
- Espejar el módulo bajo prueba (convención del repo: `tests/` espeja `src/selfevals/`).

## Archivos a tocar (estimado)
- `src/selfevals/api/queries.py` (`anchor_set_history` → versión filtrada por anchor dataset).
- `src/selfevals/api/schemas.py` (`AnchorPoint` + request de designación).
- `src/selfevals/api/app.py` (endpoint PUT/GET).
- `src/selfevals/schemas/dataset.py` o nueva `AnchorSet` (la marca de anchor).
- `web/src/lib/api/client.ts` (método nuevo) + `web/src/routes/[workspace]/anchor-set/+page.{svelte,server.ts}`.
- Tests espejados.

## No-objetivos
- No construir un scheduler de reruns automáticos (eso es Opción B / backlog).
- No tocar el `regression check` CLI (ya funciona; el anchor-set lo **visualiza**, no lo reemplaza).

## Verificación
1. Designar un dataset como anchor en un workspace con varias iteraciones que lo
   usaron.
2. `GET .../anchor-set` devuelve una serie comparable (mismo case-set) con baseline y
   marcadores de regresión.
3. La UI dibuja una sola curva + permite cambiar el anchor dataset.
4. Sin anchor designado, la vista per-experimento sigue funcionando (no regresión).
