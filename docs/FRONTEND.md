# selfevals — Frontend Spec ("nuestro propio LangSmith")

> Spec completa del frontend de selfevals: la web UI para **visibilidad total** de evals —
> runs, debug detallado, drill-down de qué hace el agente, versionado de datasets/cases,
> latencia/TTFT, comparación de iteraciones, dashboards de failure modes. La promesa del
> `evals_framework.md` §11: "desarrollar nuestro propio LangSmith".
>
> Este doc cubre: estado actual (qué ya existe), arquitectura, cada vista (existente +
> faltante), cada endpoint (existente + faltante con contrato), SSE/live, auth/roles, UX por
> pantalla, y el roadmap FE por fases.
>
> **No es greenfield.** Ya hay una SvelteKit app + API FastAPI + SSE de traces en vivo
> funcionando. Stack decidido en [`docs/web/decisions.md`](web/decisions.md). El backend que
> el FE espeja evoluciona según [`docs/ROADMAP.md`](ROADMAP.md).

Fecha: 2026-05-27.

---

## 0. Stack (ya decidido — no relitigar)

| Capa | Elección | Nota |
|------|----------|------|
| Framework | **SvelteKit 2.61 + Svelte 5.55** | `+page.server.ts` load encaja con superficies read-mostly. Mismo design language que `pato-os`. |
| Deploy | Vercel (web) + FastAPI sidecar (Python API) | adapter-node hoy; Vercel adapter objetivo. |
| API | REST plano + tipos TS espejo | Pydantic v2 es la fuente de verdad. OpenAPI gratis en `/api/openapi.json`; futuro `openapi-typescript` para codegen. |
| Server-state | `@tanstack/svelte-query` | UI-state en stores nativos. Routing-state en URL params (`$page`). |
| Tablas | `@tanstack/svelte-table` | columnas/sorting/filtering. |
| Charts | LayerCake (D3-on-Svelte) | sparklines, anchor-set, barras de clusters. |
| CSS | Tailwind v4 + shadcn-svelte/bits-ui | primitivos accesibles, skin propio vía design tokens. |
| Fonts | Inter + JetBrains Mono (self-hosted) | tabular numerals en celdas mono. |
| Auth | stub `X-SelfEvals-User: local` | sin auth real en MVP; el header viaja para enchufar auth después sin tocar pantallas. |

**Nota de doc obsoleto:** `docs/web/decisions.md:72-78` lista "live trace streaming" y "SSE
para run progress" como *out of scope v0*. **Ya están implementados** (`api/sse.py`,
`api/broker.py`). Este doc es la fuente actualizada.

---

## 1. Arquitectura actual

```
┌─────────────────────┐   REST + SSE    ┌──────────────────────────┐
│  SvelteKit (web/)   │ ───────────────▶│  FastAPI (api/)          │
│  +page.server.ts    │                 │  app.py · queries.py     │
│  load() → fetch     │◀─ EventSource ──│  sse.py · broker.py      │
└─────────────────────┘                 └────────────┬─────────────┘
                                                      │ WorkspaceScope
                                                      ▼
                                         ┌──────────────────────────┐
                                         │ SQLite + filesystem store │
                                         │ (storage/)                │
                                         └────────────┬─────────────┘
                                                      ▲ publish spans
                          OTLP receiver (runner/) ────┘ (broker_bridge)
```

- **Web y API son servicios desacoplados.** El CLI orquesta los runs (`selfevals run`); la
  web lee resultados terminados y traces en vivo cuando un run está en curso y emite spans
  vía el broker.
- **Aislamiento por workspace** estructural en el storage (`storage/interface.py` —
  `WorkspaceScope`). Sin auth en la capa de storage; el caller garantiza el `workspace_id`.
- **API hoy es read-mostly**: ~12 GET + 1 POST (crear workspace). Toda la mutación del
  lifecycle de experimentos pasa por el CLI.

### Cómo se arranca hoy
`python -m selfevals.api` (uvicorn): `--host` (def 127.0.0.1), `--port` (def 8000),
`--db` (def `./selfevals.sqlite`), `--reload`. Env `SELFEVALS_DB` como fallback.
**No existe `selfevals serve`** (ver §6).

---

## 2. Modelo de datos que el FE visualiza

La API expone *view models* (denormalizados) en `api/schemas.py`. Las entidades canónicas
viven en `src/selfevals/schemas/` (fuente de verdad). El FE consume las view shapes.

### Jerarquía
```
Workspace
 └─ Experiment            (target, guardrails, editable, search_space, proposer, run, ...)
     └─ IterationRecord   (hypothesis, proposed_parameters, metrics, decision)
         └─ DecisionRecord (outcome, rationale automated/human, metrics_snapshot)
         └─ Trace[]        (uno por run/rep; multi-turno = uno por turno, mismo thread_id)
             └─ Span[]     (discriminated union por kind)
                 └─ GraderResult[]  (label, score, failure_modes, [breakdown ← #3 futuro])
```

### View models (api/schemas.py)
- **WorkspaceSummary**: id, slug, name, owner_id, created_at, experiment_count, last_run_at.
- **WorkspaceResponse**: + `recent_health` (fracción de iteraciones recientes en
  keep_candidate).
- **ExperimentSummary**: id, name, goal, mode, state, primary_metric, primary_target
  {operator,value}, proposer_strategy, max_iterations, iteration_count, timestamps.
- **ExperimentDetailResponse**: summary + `result` (JSON de `reporter.render_json`, null si
  no ha corrido) + iterations[].
- **IterationSummary**: id, iteration, state, hypothesis, proposed_parameters,
  primary_metric_name/value, **delta_vs_best**, decision_outcome, decision_rationale,
  cost_usd, duration_seconds, trace_run_ids[], created_at.
- **TraceResponse**: id, run_id, experiment_id, iteration, thread_id, thread_position,
  final_state, started_at/ended_at, spans[], metrics{}.
- **SpanSummary**: id, parent_id, kind, name, started_at, duration_ms, **detail{}**
  (campos kind-specific filtrados — p.ej. LLM: provider/model/stop_reason).
- **ThreadTurn** / **ThreadResponse**: traces con mismo thread_id ensamblados como
  conversación ordenada (por thread_position, luego started_at); cada turn carga
  primary_grade + grader_results.
- **AnchorPoint**: experiment_id, experiment_name, iteration, primary_metric_name/value,
  decision_outcome, created_at (vista longitudinal de tendencia por workspace).

### Span kinds (lo que el trace viewer debe renderizar)
`AgentTurn · LLMCall · ToolCall · Retrieval · MemoryRead · MemoryWrite · Decision ·
Handoff · HumanIntervention · GuardrailCheck · Error · Custom`. Cada uno con payload propio
(LLMCall: tokens/cost/TTFT/reasoning; ToolCall: tool_use_id/args/result/status/sandboxed;
Retrieval: query/top_k/retrieved docs/reranker; Decision: chosen/alternatives/rationale).

---

## 3. Endpoints existentes

Base `/api/`, sin prefijo de versión. CORS para `localhost:5173`. Header `X-SelfEvals-User`
(def "local").

### Read-only (GET)
| Endpoint | Devuelve | Filtros |
|----------|----------|---------|
| `/api/health` | HealthResponse | — |
| `/api/workspaces` | WorkspaceListResponse | — |
| `/api/workspaces/{ws}` | WorkspaceResponse | — |
| `/api/workspaces/{ws}/experiments` | list[ExperimentSummary] | limit (1–500, def 100) |
| `/api/workspaces/{ws}/experiments/{id}` | ExperimentDetailResponse | — |
| `/api/workspaces/{ws}/experiments/{id}/iterations` | IterationListResponse | — |
| `/api/workspaces/{ws}/experiments/{id}/decisions` | list[dict] | — |
| `/api/workspaces/{ws}/iterations/{id}` | dict {iteration, decision} | — |
| `/api/workspaces/{ws}/traces/{trace_id}` | TraceResponse | acepta run_id como fallback |
| `/api/workspaces/{ws}/threads/{thread_id}` | ThreadResponse | — |
| `/api/runs/active` | list[{workspace_id, run_id}] | — |
| `/api/workspaces/{ws}/anchor-set` | list[AnchorPoint] | — |

### Streaming (SSE)
| Endpoint | Eventos |
|----------|---------|
| `/api/workspaces/{ws}/traces/{run_id}/stream` | `snapshot` (trace completo) · `span` (uno) · `ping` (heartbeat 15s) · `complete` (final_state) |

### Write
| Endpoint | Método | Body |
|----------|--------|------|
| `/api/workspaces` | POST | CreateWorkspaceRequest {slug, name?, description?} → 201 |

---

## 4. Rutas web existentes

Routing por archivos de SvelteKit. Cliente tipado en `lib/api/client.ts`; SSE helper en
`lib/api/sse.ts` (`openTraceStream(ws, runId, handlers)`).

| Ruta | Estado | Qué hace |
|------|--------|----------|
| `/` | ✅ funcional | Lista de workspaces; error si la API no responde. |
| `/[workspace]` | ✅ funcional | Detalle: tabla de experimentos con sparkline de tendencia, chips (exp count, recent_health, anchor points), recientes. Secciones skeleton "failure clusters (soon)" + datasets. |
| `/[workspace]/experiments` | 🟡 scaffolded | Lista completa de experimentos. |
| `/[workspace]/experiments/[experiment]` | ✅ funcional | 3 tabs: **Iterations** (tabla hypothesis/params/metric/delta/decision/rationale), **Compare** (picker side-by-side con delta), **Decisions** (audit trail). Sidebar al clickear iteración: detalle completo. |
| `/[workspace]/anchor-set` | 🟡 skeletal | Vista longitudinal de anchor points. |
| `/[workspace]/traces/[trace]` | ✅ funcional + **live** | Inspector de trace. Sidebar izq: árbol de spans jerárquico. Main: detalle del span seleccionado con facetas kind-specific. **SSE**: actualiza el árbol en vivo, pill "live" mientras el stream está activo. |
| `/[workspace]/clusters` | ❌ stub | Placeholder; necesita failure-clusters API (§7). |
| `/[workspace]/datasets` | ❌ stub | Placeholder; necesita datasets + cases API (§7). |

### Componentes existentes
`AppShell` (layout) · `DecisionBadge` (outcome → badge de color) · `MetricChip` (label+value,
formato number/percent/plain) · `SpanNode` (nodo recursivo del árbol) · `Sparkline`
(LayerCake) · `ActiveRunsPill` (indicador de runs en vivo).

---

## 5. Vistas faltantes (el camino a paridad LangSmith)

Cada vista nueva espeja una capacidad del [ROADMAP](ROADMAP.md). Marcadas con la capacidad
backend de la que dependen.

### 5.1 Funnel drill-down  · depende de #3 (breakdown)
**Dónde:** dentro del trace viewer y del experiment detail.
**Qué:** un `GraderResult` con `breakdown: BreakdownNode` (árbol recursivo
key/label/score/weight/children) se renderiza como **funnel**: por nivel, pass-rate +
dónde se cae el flujo. En el experiment detail, agregado:
`IterationAggregate.funnel` → tabla/barras por `key` ("routing 0.92 → tool_order 0.71 →
final 0.65"). En el trace, el breakdown del grader de ese run.
**Componente nuevo:** `FunnelBreakdown.svelte` (árbol indentado + barras LayerCake).

### 5.2 Trajectory viewer  · depende de #4 (TrajectoryGrader)
**Dónde:** trace viewer, capa sobre el árbol de spans.
**Qué:** visualiza la **secuencia** de tool calls / decisiones (no solo el árbol jerárquico).
Resalta failure modes diagnósticos (`wrong_tool_order`, `tool_loop_overrun`,
`missing_routing_decision`, `redundant_retrieval`) como anotaciones sobre los spans, **sin**
marcar el run como fail (la trayectoria es diagnóstica, no gate — ver ROADMAP #4). Timeline
horizontal de spans con duración (waterfall), badges de modo diagnóstico.
**Componente nuevo:** `TrajectoryTimeline.svelte` (waterfall) + `DiagnosticBadge.svelte`.

### 5.3 Thread viewer (multi-turno)  · depende de #2 (executor) — endpoint YA existe
**Dónde:** ruta nueva `/[workspace]/threads/[thread]`.
**Qué:** el endpoint `GET /threads/{thread_id}` (ThreadResponse) **ya existe**. Falta la
**ruta web**. Renderiza la conversación turn-by-turn: cada turn = burbuja (user/assistant)
con su trace enlazado, su primary_grade y grader_results. Por-turno: link al trace viewer.
Cuando exista #2 (executor real) + #15 (simulador), distinguir turnos `user_simulator` de
usuario real (tag en provider_metadata).
**Componente nuevo:** `ThreadConversation.svelte` (burbujas) + `TurnGradeChip.svelte`.

### 5.4 Judge panel / calibración  · depende de #17
**Dónde:** dentro del trace viewer (cuando el grader es panel) + ruta nueva
`/[workspace]/judges`.
**Qué:** para un `JudgePanelGrader`: mostrar el **consenso** (majority/unanimous/weighted),
el voto de cada juez miembro, y la variance de counterfactuals (paráfrasis). Vista de
calibración: precision/recall/F1/macro-F1 del juez vs labels humanos (de `calibration.py`),
y la cola de human spot-check (`Annotation` stubs pendientes de revisar).
**Componentes nuevos:** `JudgeConsensus.svelte` (votos + consenso) · `CalibrationMatrix.svelte`
(confusion + métricas) · `SpotCheckQueue.svelte` (cola de anotación).

### 5.5 Failure clusters dashboard  · depende de error-analysis API (§7)
**Dónde:** ruta `/[workspace]/clusters` (hoy stub).
**Qué:** taxonomía de failure modes del workspace (entidad `FailureMode`, lifecycle
CANDIDATE→OFFICIAL→RETIRED). Por modo: frecuencia, tendencia entre iteraciones, severidad,
casos enlazados. Acciones (gated): promote/retire/merge modes. El backend ya tiene esto en
CLI (`selfevals failuremode`, `analyze pull/push`) — falta exponerlo por API.
**Componentes nuevos:** `FailureModeTable.svelte` · `ModeTrend.svelte` (LayerCake) ·
`ModeLifecycleControls.svelte`.

### 5.6 Datasets + cases browser  · depende de datasets API (§7)
**Dónde:** ruta `/[workspace]/datasets` (hoy stub).
**Qué:** lista de datasets (smoke/golden/regression/capability/...), su SplitAllocation
(optimization/holdout/reliability), statistics (by_level/feature/source/pii, holdout_count),
status (draft/active/frozen). Drill-down a cases: filtrar por taxonomy (level/feature/source/
dataset_type), ver `input`/`expected`/graders/failure_weights/pii_status. **Versionado de
datasets/cases** (la promesa §11). Edición de cases es post-MVP.
**Componentes nuevos:** `DatasetList.svelte` · `CaseTable.svelte` (TanStack Table,
filtros por taxonomy) · `CaseDetail.svelte` · `SplitAllocationBar.svelte`.

### 5.7 Latencia / costo dashboard  · depende de #9, #14
**Dónde:** experiment detail (tab nuevo "Performance") + trace viewer (por LLM span).
**Qué:** TTFT, p50/p95/p99 latency, tokens-per-sec, costo por caso/iteración/experimento.
Series por iteración (¿mejoró accuracy pero empeoró p95?). En el trace: TTFT y tokens/sec
por LLM call (los campos ya existen en `LLMCallSpan`, se poblarán con #9).
**Componentes nuevos:** `LatencyPercentiles.svelte` · `CostBreakdownChart.svelte`.

### 5.8 Live run control  · depende de serve (§6)
**Dónde:** shell global + ruta `/[workspace]/runs`.
**Qué:** hoy `/runs/active` lista pares (ws, run_id) y el `ActiveRunsPill` los muestra. Falta:
**lanzar/pausar/abortar runs desde la web** (hoy solo CLI). Requiere endpoints de mutación de
lifecycle (§7) y `selfevals serve` montando el optimization loop (§6). Vista de progreso del
run en vivo (iteración actual, casos completados, métrica parcial).
**Componentes nuevos:** `RunProgress.svelte` · `RunControls.svelte`.

---

## 6. El gap `selfevals serve`

**No existe `serve` en el CLI.** Hoy API y web se levantan por separado. Un `selfevals serve`
debería montar en un solo proceso:

1. **FastAPI app** — ya construida, solo instanciar.
2. **Web UI** — build de SvelteKit (adapter-node) servido como estático o vía el adapter.
3. **OTLP receiver** (`runner/otlp_receiver.py`) — escuchar spans de evals corriendo.
4. **SpanBroker** singleton — para el live tracing (SSE).
5. **Optimization loop** (futuro) — para lanzar runs desde la web, no solo CLI.

**Contrato propuesto:** `selfevals serve --host --port --db [--web-dist path] [--no-web]`.
Arranca API + (opcional) web + OTLP receiver + broker en un event loop. El loop de
optimización se integra cuando existan los endpoints de mutación (§7.1).

---

## 7. Endpoints faltantes (con contrato)

### 7.1 Mutación de lifecycle de experimentos
Para mover el control del CLI a la web.
- `POST /api/workspaces/{ws}/experiments` — crear experiment desde spec YAML/JSON.
  Body: el spec del experimento. → ExperimentSummary (201).
- `POST /api/workspaces/{ws}/experiments/{id}/runs` — lanzar un run (dispara el optimization
  loop). → `{run_id}`. Requiere serve (§6).
- `PATCH /api/workspaces/{ws}/experiments/{id}` — transición de estado (pause/abort/resume).
  Body: `{state}`. → ExperimentSummary.
- `POST /api/workspaces/{ws}/iterations/{id}/decision` — registrar/editar rationale humano,
  override de la decisión automática. Body: HumanRationale {decided_by, notes,
  overrides_automated}. → DecisionRecord.

### 7.2 Datasets + cases (para §5.6)
- `GET /api/workspaces/{ws}/datasets` → list[DatasetSummary] (id, name, dataset_type, status,
  statistics, split_allocation).
- `GET /api/workspaces/{ws}/datasets/{id}` → DatasetDetail + cases refs.
- `GET /api/workspaces/{ws}/cases` → list[EvalCaseSummary]. Filtros:
  `level, feature, source, dataset_type, pii_status, holdout, limit, offset`.
- `GET /api/workspaces/{ws}/cases/{id}` → EvalCaseDetail (input, expected, taxonomy, graders,
  failure_weights, metadata).

### 7.3 Failure modes + error analysis (para §5.5)
- `GET /api/workspaces/{ws}/failure-modes` → list[FailureMode] (slug, name, lifecycle,
  frequency, severity).
- `GET /api/workspaces/{ws}/failure-modes/{slug}/trend` → series por iteración.
- `POST /api/workspaces/{ws}/failure-modes/{slug}/lifecycle` — promote/retire/merge (gated).
- `GET /api/workspaces/{ws}/analysis/staging` → bundles staged pendientes de coding.

### 7.4 Traces filtrables (hoy solo por id)
- `GET /api/workspaces/{ws}/traces` — list. Filtros: `experiment_id, iteration,
  final_state, span_kind, has_failure, limit, offset`. → list[TraceSummary].

### 7.5 Analytics / agregados
- `GET /api/workspaces/{ws}/experiments/{id}/funnel` → agregado funnel por key (para §5.1).
  Depende de #3.
- `GET /api/workspaces/{ws}/experiments/{id}/performance` → percentiles latencia + costo
  por iteración (para §5.7). Depende de #9/#14.
- `GET /api/workspaces/{ws}/experiments/{id}/correlation` → correlación param↔metric.

### 7.6 Export
- `GET /api/workspaces/{ws}/experiments/{id}/export?format=csv|json` — iteraciones/resultados.
- `GET /api/workspaces/{ws}/traces/{id}/export?format=json` — trace completo con payloads
  resueltos (del object store).

### 7.7 Paginación (deuda transversal)
Los endpoints de lista no tienen paginación real (limit hardcoded 100; sorting/limit en
Python en `queries.py`). Añadir `limit`/`offset`/`order_by` consistentes — el `ListFilter`
del storage (`storage/interface.py`) ya lo soporta, falta exponerlo en la API.

---

## 8. Auth / roles / permisos (post-MVP)

Hoy: stub `X-SelfEvals-User: local`, sin auth real, aislamiento solo por workspace en storage.
El `seed_workspace()` ya crea **member roles** por defecto — la base de datos ya modela
membresía (entidad workspace member / `Role`).

**Diseño futuro (la promesa §10/§11 "usuarios con roles, permisos configurables"):**
- **AuthN**: enchufar OIDC/sesión donde hoy va el header stub. Cero cambio en pantallas (el
  header ya viaja en cada request del cliente).
- **AuthZ por workspace**: roles (owner/editor/viewer). Viewer = solo GET. Editor = mutación
  de cases/datasets/decisiones. Owner = + gestión de miembros + lifecycle de failure modes.
- **Gating de acciones destructivas/escribientes** en el FE: las acciones de §5.4
  (promote/retire mode), §5.6 (editar case), §5.8 (abort run), §7.1 (override decisión)
  requieren rol ≥ editor; deshabilitar/ocultar según el rol del `X-SelfEvals-User`.
- **Audit trail**: las decisiones ya guardan `human.decided_by/decided_at`. Extender a
  mutaciones de dataset/mode.

No tocar pantallas para esto: el contrato es que toda mutación pase por endpoints que ya
validan rol server-side, y el FE solo refleje permisos (ocultar botones).

---

## 9. UX por pantalla (principios + por-vista)

**Principios transversales** (densos, read-mostly, técnicos):
- Tabular numerals en toda celda numérica (JetBrains Mono). Métricas alineadas a la derecha.
- Color semántico consistente vía `DecisionBadge`: keep_candidate=verde, reject=rojo,
  investigate=ámbar, tradeoff_review=morado, spawn_subexperiment=azul.
- Delta siempre con signo y color (▲ verde / ▼ rojo) contra baseline o best.
- Drill-down progresivo: lista → detalle en sidebar (no navegación destructiva) donde se pueda.
- Estados vacíos explícitos ("este experimento no ha corrido", no spinner infinito).
- Live: pill "live" pulsante mientras hay SSE activo; degradar a "finished" al `complete`.

**Por vista:**
- **Workspace list (`/`)**: cards o tabla con name, exp count, recent_health (anillo de
  color), last_run_at relativo. CTA crear workspace.
- **Workspace detail (`/[ws]`)**: hero con chips (exp count, health, anchor points). Tabla de
  experimentos con sparkline de la métrica primaria. Secciones de navegación a clusters y
  datasets (activar cuando existan §5.5/§5.6).
- **Experiment detail**: tabs Iterations / Compare / Decisions / **Performance** (nuevo, §5.7)
  / **Funnel** (nuevo, §5.1). Sidebar de iteración con hypothesis + params + métricas +
  decision + record id. Compare: dos pickers, diff de params (prompt diff side-by-side) +
  diff de métricas + recomendación de ganador.
- **Trace viewer**: árbol de spans (izq) + detalle (centro) + **trajectory timeline** (nuevo,
  §5.2, abajo). Facetas por kind. Botón "ver thread" si el trace tiene thread_id. Pill live +
  spans llegando por SSE. Resolver pointers (system_prompt/messages/args/result) bajo demanda
  desde el object store (endpoint export §7.6 o lazy fetch).
- **Thread viewer (nuevo, §5.3)**: conversación vertical, burbujas por rol, grade chip por
  turno, link a cada trace. Distinguir `user_simulator`.
- **Clusters (nuevo, §5.5)**: tabla de modes + trend chart + controles de lifecycle (gated).
- **Datasets (nuevo, §5.6)**: lista + split allocation bar + case browser filtrable + case
  detail. Toggle de versión.
- **Judges (nuevo, §5.4)**: consenso del panel + calibration matrix + spot-check queue.

---

## 10. Roadmap FE por fases

El FE sigue al backend del [ROADMAP](ROADMAP.md). Una vista no se construye antes que su
capacidad backend.

### FE-Fase 0 — Pulir lo existente + serve (S–M)
- `selfevals serve` (§6): un comando levanta API + web + OTLP + broker.
- Completar rutas scaffolded: `/[ws]/experiments` y `/[ws]/anchor-set` a funcional.
- Paginación consistente en endpoints de lista (§7.7).
- Resolver pointers en el trace viewer (mostrar prompts/args/results reales).

### FE-Fase 1 — Multi-turno + funnel (M) — tras backend #2, #3
- **Thread viewer** (§5.3) — endpoint ya existe, solo falta la ruta.
- **Funnel drill-down** (§5.1) — tras #3 (breakdown) + endpoint funnel (§7.5).

### FE-Fase 2 — Trayectoria + jueces (M) — tras backend #4, #17
- **Trajectory timeline** (§5.2) — tras #4.
- **Judge panel / calibración** (§5.4) — tras #17.

### FE-Fase 3 — Datasets + clusters + performance (M–L) — tras endpoints §7.2/§7.3/§7.5
- **Datasets + cases browser** (§5.6) + versionado.
- **Failure clusters dashboard** (§5.5).
- **Latencia/costo dashboard** (§5.7) — tras #9/#14.

### FE-Fase 4 — Control de runs + auth (L) — tras serve + mutación §7.1
- **Live run control** (§5.8): lanzar/pausar/abortar desde la web.
- **Auth real + roles/permisos** (§8): gating de acciones escribientes.

---

## 11. Resumen de gaps (qué falta, priorizado)

| Gap FE | Tipo | Depende de | Prioridad |
|--------|------|-----------|-----------|
| `selfevals serve` | CLI | — | Alta (desbloquea todo lo embebido) |
| Resolver pointers en trace viewer | FE | endpoint export §7.6 | Alta (debug real) |
| Paginación de listas | API | ListFilter (existe) | Alta |
| Thread viewer | FE | endpoint existe; #2 para datos ricos | Alta (rápido) |
| Funnel drill-down | FE+API | #3 breakdown | Media |
| Trajectory timeline | FE | #4 | Media |
| Judge panel / calibración | FE+API | #17 | Media |
| Datasets + cases browser | FE+API | endpoints §7.2 | Media |
| Failure clusters | FE+API | endpoints §7.3 (CLI ya tiene) | Media |
| Latencia/costo dashboard | FE+API | #9, #14 | Media |
| Mutación lifecycle (crear/lanzar/abortar) | API | serve | Media-Baja |
| Auth + roles | API+FE | — | Baja (MVP sin auth) |
| Export CSV/JSON | API+FE | — | Baja |
