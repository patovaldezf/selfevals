# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- **Gate de no-regresión en CI + baseline anclado al dataset (SF-4 del
  SCALING_ROADMAP).** Cierra el loop "¿el agente empeoró sobre estos casos?":
  - **Baseline automático en la primera ejecución.** El primer run que se
    completa sobre un dataset (`ds_xxx`) registra su `best_iteration` como el
    baseline de ESE dataset, sin comando manual. Se persiste un
    `DatasetBaseline` (BaseEntity, `_id_prefix="dbl"`, id derivado
    deterministicamente del `dataset_id` → un baseline por dataset, lookup
    directo sin scan); sin migración (la tabla `entities` es genérica). El
    auto-set vive en `cmd_run` tras persistir las iteraciones y es **defensivo**
    (un fallo de storage nunca tumba un run) e **idempotente** (un run posterior
    mejor NO mueve el baseline). El baseline se ancla al dataset, no al
    experimento, para medir regresión contra un set de casos fijo cross-experimento.
  - **Gate `selfevals regression check <ws> --dataset <ds> --iteration <itr>`.**
    Compara la iteración actual contra el baseline del dataset y devuelve exit
    `0` (ok) / `1` (regresión) / `2` (error de uso) — consumible por CI
    (`selfevals regression check ... || exit 1`). Gatea: primary/pass@1 (drop),
    per-class F1 de la confusion matrix (drop por clase aunque el macro aguante)
    y error_rate (rise, opcional). La matemática vive en
    `ci/regression.py::evaluate_regression`, función pura sin I/O.
  - **`selfevals baseline show|set <ws> --dataset <ds>`** para inspeccionar o
    re-baselinar explícitamente (subir el listón a propósito).

- **`run.parallelism` ahora se consume (antes dead code)** — el campo
  `run.parallelism` del YAML (`schemas/experiment.py:163`, `ge=1 le=64`) estaba
  definido pero nunca leído: el `Executor` y el `OptimizationLoop` usaban su
  semáforo hardcoded a 8. `runner/launch.py` ahora cablea `parallelism` a
  `Executor(concurrency=...)` y `OptimizationLoop(grade_concurrency=...)`, de
  modo que el campo controla de verdad cuántos cases corre el executor en
  paralelo y cuántos graders puntúa el loop a la vez. Su default subió de 1 a 8
  para preservar la concurrencia legacy de los runs que no lo declaran (ver
  Changed). Parte de SF-3 del SCALING_ROADMAP.

- **`ClassificationGrader` (`type: confusion`) + matriz NxN agregada (SF-2,
  ★Capa 2 del SCALING_ROADMAP).** Capacidad estrella: scoring de clasificación
  single-label de N clases con matriz de confusión NxN, per-class P/R/F1 y
  macro-F1 a nivel de iteración.
  - **Grader nuevo `graders/classification.py`.** Por-case extrae la clase
    _predicha_ de `structured_output` vía el path selector (`extract`, default
    `"label"`) y la clase _esperada_ del case (`Expected.outcome`, o un path en
    `Expected.structured_output` con `expected_from`); PASS si coinciden, FAIL si
    no. En miss emite el failure-mode `misclassified:<predicho>-><esperado>` (el
    mismo tag que SF-1) para el análisis de fallos. Registrado como `confusion`
    en `graders/registry.py` y declarable en YAML (`type: confusion`,
    `params: {extract, expected_from, case_sensitive}`).
  - **El par `(esperado, predicho)` viaja por el breakdown.** Como el aggregator
    descarta los `details` de cada grade, el par se codifica en la _key_ de un
    nodo hijo del breakdown: `cell:<esperado>-><predicho>`. Va en un hijo (no en
    la raíz) a propósito: el collapse multi-turn descarta la raíz del breakdown e
    injerta solo los hijos bajo `turn_N`, así la celda sobrevive tanto en cases
    single-turn como conversación. El diagonal (correcto) sobrevive porque el
    breakdown se emite también en PASS.
  - **Refactor: `graders/_confusion.py` reutilizable.** Se extrajo la matemática
    de confusión (matriz NxN + P/R/F1 + macro-F1, con guard de class-imbalance) a
    `confusion_from_pairs(pairs) -> ConfusionReport`, helper puro sobre strings.
    `compute_classification_metrics` (calibración) ahora lo llama, así la fórmula
    de F1 vive en **un solo sitio**. Sus tests siguen verdes.
  - **Rollup en `aggregator.py`.** `_rollup_confusion` recorre el árbol de
    breakdowns recursivamente, parsea las keys `cell:` de vuelta a pares y los
    pasa a `confusion_from_pairs`. Resultado en `IterationAggregate.confusion`
    (`None` si no corrió grader confusion). Persistido en `IterationMetrics.confusion`
    como dict-of-dicts `{esperado: {predicho: count}}` (JSON-serializable, sin
    migración) y rehidratado en el path de reporte.
  - **Render markdown.** Sección "Confusion matrix" con la tabla NxN
    (filas=esperado, columnas=predicho) + accuracy/macro-F1 + per-class F1. Se
    omite limpio cuando no hay grader confusion (igual que Funnel / Cost & Time).
  - **Showcase.** El ejemplo `showcase` ahora declara un grader `type: confusion`
    (`category_class`) sobre `structured_output["category"]`; la matriz aparece
    en el reporte y un test la asserta en ambos niveles del grid.

- **Honestidad de la métrica (SF-1).** Tres cambios chicos para que las cifras
  no mientan:
  - **`misclassified:X->Y` en exact-match.** Cuando `structured_output`
    esperado y predicho son ambos una _etiqueta de clase_ (un dict de un solo
    par, p.ej. `{label: full_order}`, o un escalar por el lado predicho), el
    `DeterministicGrader` emite el failure-mode `misclassified:<predicho>-><esperado>`
    en vez del genérico `structured_output_mismatch`. Así el aggregator tabula
    qué clase se confundió con cuál (matriz de confusión gratis). Dicts complejos
    / listas conservan el tag genérico. Sin migración: `failure_mode_counts` es
    `dict[str,int]` free-form.
  - **`error_rate` separado y pass@1 sin contaminar.** Los cases cuyo label
    efectivo de la primera repetición es `ERROR` (el run reventó, no que el
    agente respondió mal) se **excluyen del denominador de pass@1** en vez de
    contar como 0.0 de calidad. Un `RepetitionResult.error` no-nulo marca el case
    como errored aunque ningún grader lo etiquete. Se expone un campo nuevo
    `error_rate` en `IterationAggregate` / `IterationMetrics` (fracción de cases
    errored sobre el total), persistido y rehidratado. `pass@k`/`pass^k` y el
    path `primary_grader` quedan intactos.
  - **Columna `consistency` en el reporte markdown.** La tabla de iteraciones
    renderiza `reliability["consistency_rate"]` como porcentaje; muestra `—` si
    la iteración no midió esa métrica (sin romper).
- **Ejemplo `showcase`** — segundo ejemplo copiable (`selfevals examples copy
showcase`) que ejercita todo el surface de grading en un solo spec: un grader
  de cada tipo (`deterministic`, `set_match`, `judge_panel`, `funnel`) y un
  funnel con cada match kind builtin (`exists`/`equals`/`by_index`/`by_key`/
  `tool_called`/`span_exists`, un `set_match` anidado vía `feeds_extract` y una
  referencia `{grader: ...}`). El agente determinista
  (`selfevals.examples.showcase:run`) emite un `structured_output` rico +
  `tool_uses`, y un `judge` determinista deja correr `judge_panel` 100% offline.
  El grid proposer demuestra la mejora `level=0.0` (gate falla → hijos SKIPPED)
  → `level=1.0` (todo pasa). Pensado como referencia ejecutable de cómo se
  configura cada grader en YAML, complementando al minimalista `pingpong`.

### Changed

- **Default-behavior: la concurrencia por-run pasa de 8 fijo a
  `run.parallelism` (default 8).** El semáforo del executor/loop ya no está
  hardcoded a 8: lo dicta `run.parallelism`. Para no regresar la performance de
  los runs que nunca declaran el campo, su default en el schema subió de 1 a 8,
  de modo que el comportamiento por defecto es idéntico al legacy (concurrencia 8) y ahora un run puede ajustarlo explícitamente (`run: {parallelism: N}`,
  `ge=1 le=64`).

### Fixed

- `selfevals examples copy <name>` imprimía siempre el hint "Run:" apuntando a
  `example_pingpong.yaml`; ahora nombra el spec copiado (`example_<name>.yaml`).

## [0.11.0] - 2026-06-12

OTLP end-to-end: los agentes out-of-process (cli/http) ahora pueden exportar
sus propios spans (LLM calls, chains) y quedan anidados bajo el trace del case,
en vez de aparecer solo como el span sintético `adapter_response`.

### Added

- **Receiver OTLP cableado al runner.** `build_loop` arranca un
  `start_receiver()` embebido cuando el adapter es `HttpEndpointAdapter` o
  `CliCommandAdapter` (agentes en otro proceso). El `Executor` recibe el
  `ReceiverHandle` y, por cada repetición, hace `bind_recorder(recorder)`
  alrededor del `adapter.invoke()` para que los spans recibidos durante la
  invocación se dren­en en el `TraceRecorder` de ese case. Los agentes
  `embedded` (mismo proceso) no arrancan receiver. El receiver se cierra en el
  `finally` de `OptimizationLoop.run()` vía `Executor.close()` — sin fugas de
  threads/puertos entre runs bajo `selfevals serve`.
- **`AdapterRequest.otlp_endpoint`.** Nuevo campo opcional que transporta la URL
  base del receiver (`http://127.0.0.1:<port>`) al agente. `HttpEndpointAdapter`
  y `CliCommandAdapter` lo serializan en el POST/stdio (`_request_to_json`), de
  modo que el agente sabe a dónde apuntar su `OTLPSpanExporter`
  (`{otlp_endpoint}/v1/traces`) — normalmente vía `selfevals.init(endpoint=...)`.
  El receiver, el decoder (`otlp_to_recorder`), el `bind_recorder` y el SDK
  (`sdk.init` + `auto_instrument`) ya existían; esta release los conecta.
- **`SELFEVALS_OTLP_PORT`.** Fija el puerto del receiver embebido (default 0 =
  dinámico, uno por run — correcto para runs concurrentes). Píncha­lo cuando un
  agente de larga vida (p.ej. un servidor HTTP) deba apuntar un único exporter
  OTLP a un endpoint estable que configura una sola vez (`selfevals.init`). Un
  puerto fijo asume runs no solapados (el bind chocaría y el receiver tiene un
  solo slot de recorder); usa el dinámico si corres experimentos en paralelo.

## [0.10.0] - 2026-06-12

Grader `funnel`: scoring declarativo de N niveles para evaluar agentes por
etapas (finder → resolver → …), con short-circuit por gate, failure modes por
nivel y un breakdown tree con drill-down a profundidad arbitraria. Además, el
camino de escala (Postgres/Redis, worker async) y datasets como entidad de
primer orden quedan integrados en la misma release.

### Added

- **Grader `funnel`** (`type: funnel`) — N niveles secuenciales declarables en
  YAML. Cada nivel extrae un slice del `structured_output`/trace vía un **path
  selector** (`graders/_select.py`: `foo.bar`, `foo[]`, `foo[].bar`) y lo puntúa
  con un match builtin (`exists`/`equals`/`set_match`/`by_key`/`by_index`/
  `tool_called`/`span_exists`) o con cualquier grader anidado (incl.
  `llm_judge`/`judge_panel`) por referencia. Un nivel `gate: true` que falla
  hace short-circuit de sus descendientes (no gradeas el resolver si el finder
  no encontró nada). Cada nivel emite su nodo en el breakdown, así el
  reporter/FE renderizan el funnel a cualquier profundidad.
- **`SetMatchGrader.extract`** (default `"detected"`) — el set detectado se lee
  vía el mismo path selector, así un `set_match` puede apuntar a cualquier slice
  del contrato (`"candidates[].id"`) sin conocer su shape. Sin ruptura: el
  default reproduce el comportamiento histórico.

### Changed

- **`serialize_experiment_spec` round-trippea el `dataset_source`** — un
  `RefDatasetSource` (override `dataset_id` al lanzar) se serializa como
  `{ref, version}` en vez de `cases_inline: []`, para que el worker async
  rehidrate el spec y resuelva el dataset desde storage en vez de fallar con
  "zero cases".

## [0.9.0] - 2026-06-09

Datasets pasan de schema huérfano a entidad de primer orden: persistidos,
reusables entre experimentos, gestionables por CLI y API, con sus splits/holdout
ejecutándose de verdad. Un dataset se sube una vez (sin experimento) y se
consume desde varios experimentos por referencia.

### Added

- **`selfevals dataset`** — subcomando CLI standalone: `create`/`import` (desde
  JSONL, sin lanzar experimento), `list`, `show` (metadata + statistics),
  `freeze` (recomputa manifest_hash y marca FROZEN).
- **Endpoints REST de datasets** (tipados, Orval-ready) bajo
  `/workspaces/{ws}/datasets`: `GET` list paginada (filtros `status`/`type`),
  `GET` detail (split + statistics + cases), `POST` create (cases inline JSON o
  `cases_path` en disco), `POST .../upload` (multipart `.jsonl`), `POST
.../{id}/freeze`. Añade `python-multipart` al extra `[web]`.
- **`repo/datasets.py`** — núcleo puro: `compute_manifest_hash` (invariante al
  orden, idempotente), `compute_statistics`, y `persist_dataset`, el camino
  canónico que CLI/API/launch comparten para crear un dataset.
- **Materialización inline en launch** — un experimento con cases inline crea un
  `Dataset` real (manifest + statistics) y reescribe `datasets.optimization` /
  `frozen.datasets` para apuntar a él (id derivado del experiment_id →
  idempotente entre reruns). Cierra el `ds_pingpong` decorativo.
- **Modo `dataset: { ref: ds_xxx }`** — un experimento reutiliza un dataset
  persistido por referencia: launch resuelve sus cases y su `split_allocation`
  desde storage. Un dataset → muchos experimentos.
- **Override `dataset_id` al lanzar** — `RunExperimentRequest.dataset_id` (API) y
  `selfevals run --dataset <id>` (CLI) seleccionan un dataset standalone sin
  editar el spec.

### Changed

- **`build_loop` pasa el `split_allocation` real al `OptimizationLoop`** (antes
  siempre `None`): `sample_strategy` (`random_subset`/`stratified`) y el split
  del dataset ahora se ejecutan. Sin split declarado, el comportamiento es
  idéntico a antes (todo el pool). El loader clasifica el block `dataset:` en
  `InlineDatasetSource`/`RefDatasetSource` sin tocar storage (sigue puro).

---

Dos graders más para cerrar los huecos que aparecieron al integrar selfevals
con agentes reales: scoring de conjuntos (intention/extract) y panel de jueces
declarable. selfevals sigue siendo la autoridad de scoring y permanece
agnóstico al dominio — sin `passthrough`, sin importar código del cliente.

### Added

- **Grader `set_match`** (many-to-many) — para tareas donde el ground truth es
  un _conjunto_ multi-etiqueta (intention-detection, entity-extraction).
  Compara `structured_output["detected"]` contra `Expected.must_include` y
  reporta completeness / precision / recall / F1 con un breakdown tree
  por-elemento (mismo shape que el funnel del FE). Gating configurable
  (`completeness` @ 1.0 por default, o `f1` @ threshold). Registrado por nombre
  y declarable en el bloque `graders:` con `params: {gating, threshold}`.
- **`Expected.aliases`** (`dict[str, str]`, aditivo) — mapa canónico
  `{raw: canonical}` que `set_match` aplica a ambos lados antes de comparar. El
  conocimiento de dominio (alias legacy, casing) vive en el _case_, no en el
  motor: "no hacemos el mapping, pero podemos recibirlo".
- **Grader `judge_panel` declarable en YAML** — el `JudgePanelGrader` ya existía
  pero solo por construcción programática. Ahora se declara con
  `type: judge_panel` (`rubric`, `n_judges`, `consensus`). Default **3 jueces /
  majority**: panel impar → sin empates, ~3× costo de 1 juez con varianza mucho
  menor. Soporta `majority` / `unanimous` / `weighted`.
- **`GraderSpec.params`** — bolsa genérica de tuning por tipo de grader, para no
  tocar el dataclass cada vez que un grader nuevo necesita config.

## [0.8.0] - 2026-06-05

Cierra los gaps de datos que el FE reportó contra la API: resultados por caso,
traces navegables con su contenido completo, y contabilidad de costo/modelo.

### Added

- **`GET /experiments/{id}/results`** — resultado por scenario del mejor
  iteration: `expected` (la spec del case) vs `detected` (`content`,
  `structured_output`, `tools_invoked` leídos del trace), `matched`, `score`,
  `label`, `failure_modes` y `grader_results`, cada fila con `run_id`/`trace_id`.
  Reemplaza el `failure_reasons` agregado que no decía qué caso falló.
- **`CaseSummary.latest_run_id` / `latest_trace_id`** — cada case enlaza a su
  trace más reciente para abrirlo desde el listado.
- **Captura completa del trace** — el executor enruta prompt/mensajes, system
  prompt y respuesta al object store (pointer resoluble vía `/payloads`) e
  inlinea una vista (`messages_inline`, `system_prompt_inline`,
  `content_inline`, ≤4KB) en el span; expone `provider_metadata` y
  `model_version_pinned` en el `detail`. El trace muestra tokens, reasoning,
  cost, modelo y metadata. Schema de trace 1.3.0 (aditivo).
- **`agent.model: {provider, name}`** opcional en specs cli/http — permite
  sellar el modelo real en el span y derivar el costo de los tokens reportados
  cuando el agente no devuelve `cost_usd`. Sin él, el modelo queda "unknown" y
  el costo solo proviene de un `cost_usd` que el agente reporte.
- **`SELFEVALS_TRACE_SAMPLING`** (`all` | `failures-only` | `none`) — fuerza la
  política de persistencia de traces a nivel proceso sin editar el spec.
  Precedencia: request/flag explícito > env > spec.
- **`FeatureRef`** — `CaseSummary.feature` ahora es un objeto
  `{primary, secondary}`, alineando el OpenAPI con lo que la API serializa.

### Fixed

- **`/traces/{run_id}` ya no da 404** — `IterationRecord.trace_run_ids` lista
  solo los traces efectivamente persistidos, no todas las reps. Con
  `persist_traces="failed"` los casos que pasaban se anunciaban sin estar en
  storage. `GET /traces/{id}` acepta `tr_…` o `run_…` y echo-ea ambos.
- **Costo/llamadas LLM en cero** para agentes cli/http — sin un modelo conocido
  el costo no se podía derivar de los tokens; ver `agent.model` arriba.

## [0.7.0] - 2026-06-05

Conecta el streaming de traces en vivo (que no funcionaba para ninguna corrida)
y expone los eval cases de un experimento como lista navegable.

### Added

- **Live trace streaming, in-process** — el `TraceRecorder` emite cada span a
  un `SpanSink` inyectado al cerrarlo, en shape `SpanSummary`. No-op por default
  (el CLI no paga overhead); bajo `selfevals serve` se inyecta un
  `BrokerSpanSink` que publica al `SpanBroker`, y los runs F1 (background thread)
  alimentan a los suscriptores de `/stream` vía `call_soon_threadsafe`. Antes la
  infra (`SpanBroker`, `stream_trace`, `/stream`) existía pero nada conectaba el
  productor, así que el live no funcionaba para ninguna corrida. Reemplaza la
  ruta OTLP (separada e incompleta) para runs embedded. La proyección
  span→`SpanSummary` se centraliza en `trace/span_view.py`, reusada por el
  recorder y `api/queries` (antes duplicada).
- **`GET /api/workspaces/{ws}/experiments/{id}/cases`** — lista los eval cases
  que un experimento ejecutó. Los `EvalCase` ahora se persisten al lanzar un run
  (antes solo vivían en el `ExperimentSpec` en memoria), estampados con
  `experiment_id` en `runner.launch._persist_cases` (path compartido CLI + API).
  El set se reporta completo, con holdout incluido y flaggeado, ordenado por
  nombre; devuelve lista vacía (no `404`) para experimentos sin cases. Sin
  migración: el filtro usa `json_extract(payload, '$.experiment_id')`.

## [0.6.0] - 2026-06-05

Hardens the HTTP API into a stable frontier an external frontend can consume,
and adds the first write endpoint — launching an experiment over HTTP.

### Added

- **`POST /api/workspaces/{ws}/experiments/run`** — launch an experiment over
  HTTP. Non-blocking: validates and persists synchronously, returns `202`, and
  runs the optimization loop on a background thread (own SQLite connection +
  event loop, so the FastAPI loop is never blocked). Accepts either a
  `spec_path` (YAML on the server) or an inline `spec_inline` (JSON object with
  cases under `dataset.cases_inline`); the path workspace is authoritative.
  `422` on an invalid/zero-case spec, `409` when the experiment already has an
  active run, and the experiment moves to `aborted` if the run raises. Reuses
  the exact canonical wiring `selfevals run` uses.
- **Experiment list filters** — `GET .../experiments` now accepts `state`
  (validated enum) and `feature` (membership in `taxonomy.target_features`).
  Filters apply before pagination, so `total`/`has_more` describe the filtered
  set.
- **`selfevals.repo.loader.build_spec_from_mapping`** — hydrate a validated
  `ExperimentSpec` from an already-parsed mapping (the inline-spec path), reusing
  the same builders as the on-disk loader.
- **`selfevals.runner.launch`** — the canonical spec→`OptimizationLoop` wiring,
  shared by the CLI and the API so neither frontend imports the other. Grader
  registration is now confined to a locked register→resolve→unregister window
  inside `build_loop`, so concurrent runs cannot trample one another's graders.

### Changed

- **`GET /api/runs/active`** now returns a typed envelope
  `{"runs": [...]}` (`ActiveRunsResponse`) instead of a bare array, consistent
  with the other list endpoints. **Breaking** for clients that read the array
  directly. **`/decisions`** is typed as `DecisionRecordResponse`.

### Fixed

- **Migrations are idempotent over a pre-existing database.** `apply_migrations`
  backfills the tracking row when it finds the base schema (`entities`) present
  but `_selfevalss_migrations` empty — databases created before the tracker, or
  that tracked via the legacy `_bootstrap_migrations` table. Previously this
  re-ran `m0001` and raised `table entities already exists`, 500-ing every
  endpoint. `m0001` DDL also uses `IF NOT EXISTS` as defense in depth.
- **The optimization loop persists the experiment's state.** `run()` transitioned
  the experiment (`draft → running → completed`) in memory but never flushed the
  row, so a reader saw it stuck at `draft`. It now persists on entering `running`
  and on `completed`.

## [0.5.0] - 2026-05-28

Per-grader optimization signal and proposer-aware convergence — both surfaced
by the brain_os integration, where a conjunctive `pass@1` masked which grader
drove the number and a grid sweep stopped 4/6 of the way through.

### Added

- **`TargetSpec.primary_grader`** (YAML: `target.primary_grader`). Scores the
  primary pass-style metric against a single named grader instead of the
  conjunctive worst-of across all graders, so an experiment can optimize toward
  one grader (e.g. `must_include`) while the others still run and report. The
  loader rejects a `primary_grader` that names no configured grader. `None`
  (default) keeps the worst-of behaviour.
- **`IterationAggregate.per_grader_pass_rate`** + **`CaseOutcome.per_grader_label`**.
  Each grader's own `pass@1` is now reported alongside the worst-of primary
  (in the JSON report under `metrics.per_grader_pass_rate` and in the markdown
  report), unmasking the per-grader signal a conjunctive pass@1 hides. Each
  grader's denominator is the cases it actually ran on.
- **`ConvergenceSpec.early_stop`** (`bool | None`, YAML: `run.convergence.early_stop`).
  Default `None` is proposer-aware: the **grid** proposer now exhausts its full
  cartesian product (a mid-grid plateau no longer skips the remaining
  combinations), while open-ended proposers (random / llm) still early-stop on a
  plateau. Set `early_stop: true` to re-enable the cutoff for grid (cheap
  hill-climbing over a large grid); set `early_stop: false` to force any proposer
  to exhaust its space.

## [0.4.2] - 2026-05-28

DX fixes surfaced by the brain_os integration (selfevals' first downstream
user). Each one removes a workaround that user had to write.

### Added

- **`AdapterRequest.get_model_param(key, default)`.** Flattens the proposer
  `parameters["model_params"]` envelope so adapters read params without
  hard-coding its shape. The envelope itself stays — it's the namespace the
  editable contract gates.
- **Declarative custom graders via dotted path.** A grader name containing
  `:` (e.g. `my_pkg.graders:TaskShapeGrader`) is imported on demand and
  instantiated — no `register_grader` side-effect import. Built-in names keep
  the registry path; the two coexist. The class must subclass `Grader` and be
  no-arg constructible.

### Changed

- **Grid truncation and case subsampling are now logged, not silent.** When a
  grid proposer has more combinations than `max_iterations`, the loop logs a
  WARNING naming how many combos are skipped (and continues — it does not
  abort). When `sample_strategy` subsamples the pool (`random_subset` /
  `stratified`), `select_optimization_set` logs an INFO `subsampled N->M
cases`. `GridProposer.grid_size(experiment)` exposes the combo count.
- **Coroutine return errors hint at `await`.** `EmbeddedAdapter.invoke` and
  the CLI coercion path now append "did you forget to await?" when a coroutine
  reaches the type check, instead of the bare "returned coroutine".

### Docs

- Documented `Expected.structured_output` as the escape hatch for
  domain-specific expected fields (the schema is `extra="forbid"`), read by
  custom graders via `context.case.expected.structured_output`.
- New "Custom graders" section in `eval_config.md` covering the dotted-path
  and programmatic-registration routes.

## [0.4.1] - 2026-05-28

### Fixed

- **Async agent entrypoints discovered via YAML now run.** An
  `agent.entrypoint: "mod:run"` pointing at an `async def` was wrapped in a
  sync shim that called it without awaiting, handing `EmbeddedAdapter` a bare
  coroutine — never awaited (RuntimeWarning) and rejected with "returned
  coroutine; expected str or AdapterResponse". `_wrap_user_callable` now
  detects `iscoroutinefunction` and installs an async wrapper, so YAML-loaded
  async entrypoints work without the `asyncio.run` bridge. (Direct
  `EmbeddedAdapter(async_fn)` use was already fine.)
- **`failure_reasons` survive the storage round-trip.** `selfevals report`
  and the web API's experiment-detail endpoint rebuilt `IterationOutcome`
  from disk with empty `case_runs`, so the JSON report's `failure_reasons`
  came back `[]` even though the inline `run --format json` populated them.
  The non-passing traces (persisted per `run.persist_traces`, default
  `failed`) are now reloaded and grouped back into `case_runs`, so a report
  rebuilt from storage shows the same per-grade rationales as the inline run.

### Changed

- `__version__` is now in sync with the packaged version (0.4.0 shipped with
  a stale `0.3.0` in `selfevals.version`).

## [0.4.0] - 2026-05-28

### Added

- **Recall-based `must_include` grading (`Expected.min_recall`).** A new
  optional `min_recall` float in `[0, 1]` on `EvalCase.expected`. When
  set (and `must_include` is non-empty), the `DeterministicGrader` grades
  `must_include` by _recall_ — the fraction of required substrings that
  appear in the response — instead of all-or-nothing: the grade is `pass`
  iff `recall >= min_recall`, and `score` becomes the recall value
  (exposed in `details["recall"]`). Missing substrings still emit their
  `missing_required_substring` failure mode for diagnostics but no longer
  force a FAIL on their own. Hard violations (`must_not_include`,
  `required_tools`/`forbidden_tools`, `regex_match`, `structured_output`)
  always take precedence: any hard violation still forces FAIL even when
  recall clears the threshold. When `min_recall` is `None` (the default),
  `must_include` stays all-or-nothing as before.
- **Cache hit counts in the JSON report.** Each iteration in
  `selfevals report --format json` (and the live `run --format json`) now
  carries a `"cache": {"hits": N, "llm_calls": M}` object — the number of
  cache-hit LLM spans and the total LLM-call spans across that iteration's
  traces — so cost/throughput consumers can read cache effectiveness
  without raw trace spelunking.
- **Per-iteration failure rationales in the JSON report.** Each iteration
  now carries a `"failure_reasons"` array: deduplicated grader rationales
  for every non-passing grade, one entry per distinct
  `(grader, label, reason)` with `score` and `failure_modes`. This lets a
  downstream consumer see _why_ a grader failed without reading SQLite.
  (Populated on a live `run`; empty when an experiment is reconstructed
  from storage, e.g. via `report` or the HTTP API — see
  [`docs/json_report_schema.md`](docs/json_report_schema.md).)
- **Thread viewer (web + API).** `GET /api/workspaces/{ws}/threads/{thread}`
  (already shipped, now documented) assembles every `Trace` sharing a
  `thread_id` into an ordered, turn-by-turn conversation (`ThreadResponse`),
  each turn carrying its `primary_grade` and `grader_results`. New web
  route `/[workspace]/threads/[thread]` renders the multi-turn conversation.
- **Funnel drill-down (web + API).** New endpoint
  `GET /api/workspaces/{ws}/iterations/{id}/funnel` returns the per-iteration
  grader funnel (`FunnelResponse`, recursive `FunnelNodeResponse` nodes read
  straight from `IterationRecord.metrics.funnel`). New "Funnel" tab on the
  experiment-detail view renders it via the recursive `FunnelNode.svelte`
  component. `nodes` is empty when no grader emitted a breakdown.
- **Server-rendered iteration compare (web + API).** New endpoint
  `GET /api/workspaces/{ws}/experiments/{id}/compare?a={itr}&b={itr}` returns
  a structured `CompareResponse` (proposal diff, metrics diff, failure-mode
  diff, funnel diff, winner recommendation, `holdout_status`) computed by the
  reporter's `compute_compare` — the single source of truth shared with the
  CLI `compare` command. Returns 404 when an iteration is unknown and 400
  when the two iterations belong to different experiments. The web "Compare"
  tab now renders this diff server-side instead of recomputing deltas in the
  browser.

### Documentation

- New [`docs/api_reference.md`](docs/api_reference.md): the canonical HTTP
  API reference — every endpoint, grouped by resource, with method, path,
  params, response schema, and error codes.
- New [`docs/eval_config.md`](docs/eval_config.md): the YAML eval-config
  reference (top-level keys, `EvalCase`/`Expected` fields including
  `min_recall`, graders, agent transports, proposers) with validating
  snippets.
- New [`docs/json_report_schema.md`](docs/json_report_schema.md): the
  `report --format json` output shape, documenting every root and
  per-iteration key (including the new `cache` and `failure_reasons`).
- `docs/FRONTEND.md` §3/§5: the funnel, compare, and thread endpoints/views
  are now documented as shipped.

## [0.3.0] - 2026-05-27

### Added

- **Validated multi-turn conversation input.** When `EvalCase.input`
  carries a `messages` key it is validated as a typed conversation:
  non-empty message list, roles from a new `MessageRole` enum
  (system/user/assistant/tool), content as a string or a list of
  content blocks, multimodal-aware via the `Modality` enum. New
  `Message`, `ContentBlock`, and `ConversationInput` models, plus
  `EvalCase.conversation()` / `EvalCase.is_conversation()` accessors.
  Inputs without a `messages` key remain opaque payloads, so the
  field stays a plain JSON dict that adapters receive verbatim.
- **Async-first evaluators.** `AgentAdapter.invoke` and `Grader.grade`
  are now async. The executor runs repetitions concurrently and the
  optimization loop grades concurrently, each bounded by a
  configurable semaphore (`concurrency` / `grade_concurrency`,
  default 8). `EmbeddedAdapter` accepts sync or async callables,
  `CliCommandAdapter` uses an asyncio subprocess, and
  `HttpEndpointAdapter` is native async on httpx. `asyncio.run` is
  confined to the CLI edge.

### Changed

- `httpx` is now a runtime dependency (the default HTTP adapter
  transport), not just a dev dependency.

### Documentation

- STATUS.md and README banners read v0.3.0; multi-turn input and async
  evaluators moved into "What works"; test counts refreshed (default
  surface 559, full 597); roadmap records both as shipped in 0.3.0.

## [0.2.2] - 2026-05-27

### Documentation

- STATUS.md and README banners now read v0.2.2 (they had lagged at
  v0.2.1 despite the 0.2.2 release). Refreshed the STATUS body against
  the current tree: test counts (default surface 481 -> 528, full
  surface 566, extras-gated 9 -> 24), and the forward-looking
  "What v0.2 will probably contain" section became a "Roadmap" that
  separates what shipped in 0.2.x from what remains on the backlog.

### Documentation

- Onboarding pass after the `bootstrap` -> `selfevals` rename. Fixed the
  CI mypy target (`src/bootstrap` -> `src/selfevals`) and 13 stale
  `bootstrap` CLI/prose references in the bundled error-analysis skill.
- README rewritten for a new user: provider-extras install guidance, a
  Concepts table, both LLM examples (Anthropic + OpenAI), a full CLI
  reference, and the global `--db` placement note. Status banners bumped
  to the current release.
- New `examples/README.md` (walk-through + how to adapt to your own agent)
  and an expanded `CONTRIBUTING.md` (test layout, extras some tests need,
  where to add a grader/adapter/proposer).

No runtime or API changes — docs and packaging metadata only.

## [0.2.1] - 2026-05-27

### Changed

- **Provider extras now bundle the provider SDK**, not just the
  OpenInference instrumentor. `pip install selfevals[openai]` (and
  `[anthropic]`, `[bedrock]`, `[vertex]`, `[langchain]`, `[crewai]`) now
  pulls the provider's own SDK alongside the tracing integration — so a
  single install is enough to run and trace a provider-backed agent. This
  follows the Pydantic AI per-provider-extra pattern; core still depends on
  no provider SDK (only `pydantic` + `pyyaml`).

### Added

- **`examples/hello_openai/`** — an OpenAI twin of `examples/hello_llm/`
  (Anthropic): same three cases, same graders, same temperature sweep,
  only the provider call differs. Calls OpenAI Chat Completions
  (`gpt-4o-mini`) with a deterministic fake fallback when `OPENAI_API_KEY`
  is unset. The lazy import distinguishes "SDK missing" (prints a
  `pip install selfevals[openai]` hint) from "no API key" (silent fake).

## [0.2.0] - 2026-05-26

First release prepared for PyPI (distribution name `selfevals`; import and
CLI remain `selfevals`). Adds the error-analysis closed loop, thread
grouping, and trace message-content capture on top of the 0.1.0 runtime.

### Added

- **Error analysis + failure-mode taxonomy** — a closed loop, not a dashboard:
  it grows a per-workspace failure-mode taxonomy and drives the next experiment.
  selfevals owns the data, contract, persistence, and verification; the
  intelligence (open/axial coding) lives in an external coding agent. selfevals
  never calls an LLM. Design: `docs/spec/error_analysis_design.md`.
  - **Persistence fix** — `IterationMetrics.failure_mode_counts` now persists
    and survives a round-trip, so "top modes of experiment X" / "trend of mode
    Y across iterations" are answerable. Closes the v0.1.0 known gap; the
    markdown report and `compare` start showing real failure-mode data.
  - **`FailureMode` entity** + per-workspace taxonomy seeded by `init` (9
    canonical modes). Lifecycle CANDIDATE → OFFICIAL → RETIRED with a **human
    promotion gate**; `superseded_by` back-pointer on merge.
  - **Handshake** — `selfevals analyze pull <ws> <exp>` emits an
    `AnalysisBundle` (failed traces + live taxonomy) as JSON; `analyze push`
    ingests an `AnalysisResult` from stdin, validating-before-writing and
    enforcing the assignment XOR (`mode_id` _or_ `new_mode_slug`) and
    classify-don't-rename invariants. Re-proposing an existing slug doesn't
    duplicate it (discover-once, classify-thereafter).
  - **`failuremode` CLI** — `list / promote / retire / merge / edit` for
    taxonomy management and the human gate.
  - **Closing the loop** — `ProposerInputs.failure_modes_consulted` carries the
    prior iteration's dominant modes so a hypothesis can target a named mode;
    `IterationAggregate.fail_rate` is the trigger signal; verification reuses
    the existing `compare.py` before/after on stable mode ids.
  - **Trace persistence** — `RunSpec.persist_traces` (`none` / `all` / `failed`,
    default `failed`) controls which per-repetition traces the loop writes,
    stamped with their grader results. A plain `selfevals run` now leaves the
    failed traces in storage so `analyze pull` works without the SDK/OTLP path;
    `--persist-traces` overrides it on the CLI. Traces also carry their
    `iteration` so `analyze pull --iteration N` scopes correctly.
  - **YAML opt-in** — a declarative, governable `error_analysis:` block on an
    experiment (`enabled`, `taxonomy`, `trigger.fail_rate_above + threshold`,
    `scope`). Default off. When the trigger fires, selfevals persists an
    advisory `AnalysisStagingRecord` ("this run is worth coding") — it never
    invokes an agent. The pingpong example opts in.
  - **Bundled `error-analysis` skill** — ships inside the package
    (`selfevals/.agents/skills/`, FastAPI convention) so `pip install selfevals`
    makes it discoverable. It encodes the _method_ (open → axial coding,
    saturation, the handshake, the human gate), not intelligence. New
    `selfevals.skills` locator + `selfevals skills list / path` CLI.
  - 60+ new tests across schema round-trips, the push invariants, the
    second-round stability property, loop staging + mode carryover, the YAML
    loader, the skills locator, and the CLI cycle. mypy --strict + ruff clean.
- **Thread grouping** — traces can now be assembled into the conversation
  thread they belong to. `RunInfo` gains `thread_id` + `thread_position`; the
  OTel importer auto-detects the thread from `session.id` (OpenInference) or
  `gen_ai.conversation.id` (OTel GenAI), without overwriting an explicit
  caller-set `thread_id`. New read query `load_thread` + `GET
/workspaces/{ws}/threads/{thread_id}` return every trace sharing a thread,
  ordered by `thread_position` (falling back to `started_at`), each turn
  projected with its grader results so the per-turn grade is visible.
  `TraceResponse` now surfaces `thread_id` / `thread_position`. This closes the
  last trace-grouping gap versus LangSmith sessions; the run→experiment→
  iteration→decision→grade chain already existed. Eight new tests.
- OTel importer now extracts prompt/completion **message content** into
  traces. `_build_llm_span` reconstructs ordered message lists from both
  attribute families — OpenInference native (`llm.input_messages.{i}.message.*`,
  `llm.output_messages.{i}.message.*`) and the OTel GenAI alias
  (`gen_ai.prompt.{i}.*`, `gen_ai.completion.{i}.*`). When both are present the
  native family wins. Each side gets a stable `content_hash` (on
  `messages_hash` / `output.content_hash`) for dedup and drift detection, and
  the structured messages are kept inline under `provider_metadata`
  (`selfevals.messages_in` / `selfevals.messages_out`). Closes the last gap
  versus LangSmith trace capture: the actual prompt and response text are now
  in the trace, not just tokens/model/stop_reason. Five new importer tests.

## [0.1.0] - 2026-05-25

First version where the README no longer lies. `selfevals run` works
end-to-end against a real LLM agent, error paths are actionable, and
the markdown/JSON reports answer the obvious follow-up questions.
Schema-wise compatible with `0.0.9`.

### Added — usable v1 surface

Examples and quickstart:

- `examples/hello_llm/` — a real Anthropic agent (with deterministic
  fakes when `ANTHROPIC_API_KEY` is unset) over 3 EvalCases:
  sentiment classification, structured extraction, open-ended support
  reply. Two graders combined: `DeterministicGrader` for the rule
  cases + `LLMJudgeGrader` for the open-ended one. `GridProposer`
  sweeps `temperature ∈ {0.0, 0.5, 1.0}`.
- README quickstart points at `evals/experiments/example_pingpong.yaml`
  with the exact commands. Status banner updated from "no runtime
  yet" to "runtime functional".

CLI UX (Day 2):

- Every subcommand (`init`, `workspace`, `experiment`, `iteration`,
  `report`, `run`, `compare`, `estimate`) now has a user-facing
  one-line description and a copy-paste `Example:` epilog. Helper
  `src/selfevals/cli/_help.py` centralizes the pattern.
- `tests/cli/test_help_texts.py` enforces the contract.
- `docs/adapters.md` documents the three adapters with YAML config,
  per-adapter agent code, contracts, limitations, and a comparison
  table.

Errors and hardening (Day 3):

- `SelfEvalsError` / `SelfEvalsUserError` hierarchy. User-correctable
  failures exit with code 2 and a clean one-line message; internal
  errors keep their traceback.
- `src/selfevals/cli/_friendly.py` is the single translation
  chokepoint for YAML parse errors, dataset paths (with fuzzy-match
  suggestions via stdlib `difflib`), missing graders, HTTP adapter
  transport errors (URL + actionable suffix), and SQLite locked /
  corrupted cases.
- `src/selfevals/graders/registry.py` — name→factory registry.
  `deterministic` is pre-registered; `llm_judge` is registered
  on-demand by the CLI. YAML can declare top-level `graders:` and
  per-case `EvalCase.graders` filters which graders run.
- `tests/integration/test_full_loop_with_mocked_judge.py` — 7 tests
  covering the happy path plus each of the five friendly-error
  shapes.
- `docs/troubleshooting.md` documents the five common errors and
  fixes.

Reporter (Day 4):

- `src/selfevals/reporter/_metrics.py` — pure helpers
  (`compute_total_cost`, `compute_total_time_seconds`, etc.) that
  return `None` when data is absent instead of misleading zeros.
- Markdown report gains a "Cost & Time" section (omitted gracefully
  when there are no LLM calls) and a "Next steps" block with
  copy-paste inspection commands.
- JSON report exposes a stable `cost_time` block (`None` when
  missing).
- `src/selfevals/reporter/compare.py` powers `selfevals compare`:
  proposal diff table, metrics diff table, failure-mode diff, and a
  "B is better: primary +X; no new failure modes" recommendation.

### Fixed

- Console script `selfevals` was pointing at `cli.main:app`, which
  returns an int but never raised `SystemExit`, so user errors
  silently exited 0. Now points at `cli.main:main`, which wraps `app`
  in `SystemExit(...)`.
- `pyproject.toml` ruff `per-file-ignores` had no entry for
  `src/selfevals/api/**`, so legitimate FastAPI `Depends(...)`
  defaults were flagged as B008. Added the ignore.
- `pyproject.toml` `pytest.ini_options` was missing the `asyncio`
  marker registration; `--strict-markers` was rejecting async tests.
- `EvalCase.graders` was unused metadata until now — the
  `OptimizationLoop` now filters graders per case when the field is
  populated, preserving the prior "run everything" behavior when it
  is empty.

### Known gaps (not blocking v0.1.0)

- 9 tests under `tests/sdk/` and `tests/runner/test_otlp_receiver.py`
  require the `telemetry` extra (`uv sync --extra telemetry`) and
  fail without it. They are excluded from the default surface.
- 3 tests under `tests/api/` require the `web` extra
  (`uv sync --extra web`) to install FastAPI.
- Failure modes do not yet survive persistence to SQLite — the
  compare and report tooling already handles their presence gracefully
  for when the schema is extended. _(Resolved in [Unreleased]: error
  analysis persists `failure_mode_counts`.)_
- `CliCommandAdapter` and `HttpEndpointAdapter` are not yet
  auto-wired from YAML; users instantiate them via a Python
  entrypoint. `docs/adapters.md` documents the workaround.

## [0.0.9] - 2026-05-16

### Added — MVP Block A: YAML loader + `selfevals run` end-to-end

Repo loader (`src/selfevals/repo/`):

- `load_experiment_spec(path)` parses `evals/experiments/<name>.yaml` →
  `(workspace_id, Experiment, [EvalCase], AgentEntrypoint)`. YAML keys
  are 1:1 with the Pydantic field names — no DSL translation; the
  validators do all the shape checking.
- Cases can be inline (`dataset.cases_inline:`) or external JSONL
  (`dataset.cases_path:`). Mutually exclusive; both empty rejected.
- Agent entrypoint declared as `module.path:callable_name`.
  `resolve_agent_callable` defers import until the runner needs it
  (lets `selfevals inspect` validate a spec without booting user code).
- 14 tests covering inline/external loading, workspace override,
  missing fields, malformed YAML, invalid payloads, entrypoint
  resolution.

CLI `selfevals run <yaml>`:

- Loads spec → resolves agent callable → wraps as `EmbeddedAdapter`
  (str returns auto-coerced to `AdapterResponse`) → builds the
  proposer per `experiment.proposer.strategy` (grid / random /
  manual) → drives `OptimizationLoop` with `DecisionMatrixEvaluator`
  - `DeterministicGrader` → emits markdown/JSON report.
- Flags: `--workspace`, `--max-iterations`, `--reps`, `--format`,
  `--no-persist`.
- Persists `Experiment` + `IterationRecord` + `DecisionRecord` to
  SQLite when storage is enabled; auto-seeds the workspace row.
- 6 tests covering markdown/JSON output, persistence to SQLite,
  missing-spec error, validation, str→AdapterResponse coercion.

Example experiment:

- `evals/experiments/example_pingpong.yaml` + `evals/datasets/pingpong.jsonl` +
  `selfevals.examples.pingpong` reference agent. Serves as smoke test
  and onboarding artifact. `uv run selfevals run evals/experiments/example_pingpong.yaml --no-persist`
  produces a clean report out of the box.

Refactor:

- `DecisionMatrixEvaluator` now inherits from `DecisionEvaluatorProtocol`
  so the type checker recognizes it as a valid argument to
  `OptimizationLoop(decision_evaluator=...)`.

20 new tests (390 total). mypy strict + ruff clean. One new runtime
dep: `pyyaml>=6,<7`.

### Added — Design docs for next implementation surfaces

- `docs/spec/sdk_otlp_design.md`: locked blueprint for the user-side
  SDK façade (`selfevals.init()`) + embedded OTLP HTTP receiver +
  OpenInference auto-instrumentation. Sections 1-11 cover the
  decisions already made (no re-litigation), package layout, exact
  signatures, span translation table, dependency tree (optional
  extras), test plan, and acceptance criteria. ~1500-2000 LOC budget,
  dedicated session.
- `docs/prompts/web_session_prompt.md`: self-contained prompt for the
  Claude Code session that builds the web UI + SDK + OTLP receiver.
  Includes product vibe (Stripe/Airbnb/ChatGPT/Claude/LangSmith/Mercury),
  page inventory (8 surfaces), design tokens, stack recommendation,
  backend contract, and "done" criteria.

## [0.0.8] - 2026-05-16

### Added — PR 8 + PR 9: Reporter + CLI

Reporter (`selfevals.reporter`):

- `render_markdown(result)` produces a PR-comment-style summary:
  experiment header (name, goal, state, mode, proposer, iterations
  run, termination reason), target + guardrail spec line, best-
  iteration callout with parameters, per-iteration table
  (`#`, primary, Δ vs running best, decision outcome, rationale —
  with pipe-escaping and 80-char rationale truncation), and a
  top-N failure-modes section drawn from
  `IterationAggregate.failure_mode_counts`.
- `render_json(result)` emits a stable, machine-readable payload
  (`schema_version=1`) keyed on iteration index, with explicit
  best-iteration reference. JSON path is what the CLI's `--format
json` flag outputs.
- Pure: no I/O, no global state — callers decide where the strings
  end up (stdout, a file, a GitHub PR comment).

CLI (`selfevals` console script, argparse-only, zero new deps):

- `selfevals init <slug>` — idempotent workspace seed via
  `seed_workspace`; prints workspace id + member count.
- `selfevals workspace show <ws_id>` — workspace metadata +
  experiment count.
- `selfevals experiment list <ws_id>` / `show <ws_id> <exp_id>` —
  inspect experiments in storage with target + iteration progress.
- `selfevals iteration list <ws_id> <exp_id>` — per-iteration
  primary metric + decision outcome.
- `selfevals report <ws_id> <exp_id> [--format markdown|json]` —
  reconstructs an OptimizationResult from stored IterationRecords +
  DecisionRecords (lossy on per-case GradeResults, lossless on
  aggregates) and pipes it through the reporter.
- `selfevals compare <ws_id> <iter_a_id> <iter_b_id>` — side-by-
  side primary metric diff between two iterations of the same
  experiment.
- `selfevals estimate --cases N --space-size M --reps K
--cost-per-call X` — dry-run upper-bound on agent calls and
  total USD cost before paying for a run.
- All user-facing errors (missing entity, primary-metric mismatch,
  invalid numeric args) go through `CommandError` → `error: <msg>`
  on stderr → exit code 2. Unexpected exceptions surface as
  tracebacks (bugs, not user errors).

18 new tests (370 total: 9 reporter + 9 CLI). mypy strict + ruff
clean. Zero new runtime deps — argparse + stdlib.

## [0.0.7] - 2026-05-16

### Added — PR 6 + PR 7: OptimizationLoop + Decision matrix

Proposers:

- `Proposer` ABC with `ProposerContext` (iteration index + history).
- `ManualProposer`: walk a caller-supplied list of `Proposal` or
  parameter dicts; raises `SearchSpaceExhaustedError` when done.
- `GridProposer`: cartesian product over list-valued entries in
  `experiment.search_space.model_params`; scalar entries are held
  constant; empty list → raises ValueError.
- `RandomProposer`: independent uniform sampling from each parameter
  spec (list, `{lo, hi}`, `{choices: [...]}`, or scalar constant).
  Bounded by `max_proposals`; seeded for reproducibility.
- All proposals are re-validated against the experiment's editable
  contract before being returned.

Aggregator:

- `aggregate_iteration(case_outcomes, primary_metric, reliability_metrics)`
  computes pass@1 / pass@k / pass^k / consistency_rate /
  stability_score / recovery_rate from per-case `CaseOutcome`s.
- Worst-of policy when multiple graders run on the same repetition:
  ERROR > FAIL > PARTIAL > SKIPPED > PASS.
- Failure-mode counts aggregated by tag.
- Guardrail metrics (`cost_usd_per_case`, `latency_ms_per_case_avg`)
  surfaced when traces report cost/duration.

OptimizationLoop:

- Transitions experiment state DRAFT → QUEUED → RUNNING → COMPLETED.
- For each iteration: ask proposer for a Proposal, run cases through
  the Executor, score per-rep results with the configured graders,
  aggregate, hand to a DecisionEvaluator, persist IterationRecord +
  DecisionRecord (when a WorkspaceScope is provided).
- Terminates on `search_space_exhausted`, `converged`, or
  `max_iterations`. Convergence = no improvement above
  `min_delta` for `patience` consecutive iterations.

Decision matrix (PR 7):

- `evaluate_iteration` (pure) + `DecisionMatrixEvaluator` (object).
  Applies the §10 canonical subset that powers MVP optimization:
  guardrail check → first-iteration target check → improvement vs
  baseline → regression handling per `Experiment.decision` policy
  (reject / investigate / spawn_subexperiment) or guardrail policy
  (reject / require_tradeoff_review).
- Missing guardrail metric values are treated as passing — the runner
  doesn't synthesize every metric in MVP and we don't fail-shut on
  absent data.
- End-to-end integration test wires the evaluator into the loop and
  verifies that improvement / no-improvement / regression each
  produce the right DecisionRecord.outcome.

47 new tests (352 total). mypy strict + ruff clean. Zero new deps.

## [0.0.6] - 2026-05-16

### Added — PR 5: Graders (deterministic + LLM judge + calibration)

- `Grader` ABC with `GraderContext` (case + trace + optional response)
  and `GradeResult` (label / score / reason / confidence / failure_modes
  / details). `GradeLabel` enum: pass, fail, partial, error, skipped.
- `DeterministicGrader`: reads rules off `EvalCase.expected`:
  must_include, must_not_include, required_tools (looks at
  ToolCallSpans in the trace), forbidden_tools, optional regex,
  structured_output equality. Configurable case-sensitive mode. Each
  rule emits a stable failure_mode tag for weighted scoring upstream.
- `LLMJudgeGrader`: invokes any `AgentAdapter` as a judge against a
  rubric prompt (`RubricTemplate` with safe substitution). Parses the
  judge's JSON output into a `JudgeDecision`; unknown labels and bad
  JSON return `GradeLabel.ERROR` rather than crashing. Honors
  `GraderCard.blocking` thresholds: when below calibration the grader
  returns SKIPPED ("degraded to advisory") unless `force=True`.
  Single-judge in MVP; panel infrastructure-ready for post-MVP.
- Calibration helpers (`compute_classification_metrics`): pair
  predictions with human labels by case_id; compute precision, recall,
  F1 for the positive class plus macro-F1, accuracy, per-label
  precision/recall, and confusion matrix. Counts high-risk false
  negatives separately (the failure mode that wakes someone up).
  Class-imbalance guard: undefined precision/recall return None.

25 new tests (305 total). mypy strict + ruff clean. Zero new deps.

## [0.0.5] - 2026-05-16

### Added — PR 4: Runner (agent adapters + sandbox + executor)

- `AgentAdapter` ABC + `AdapterRequest`/`AdapterResponse` dataclasses;
  the narrow contract between selfevals and the agent under test.
- `EmbeddedAdapter`: wraps a Python callable. Used for tests and
  in-repo agents.
- `CliCommandAdapter`: subprocess + JSON-over-stdio. Configurable
  command, env, timeout.
- `HttpEndpointAdapter`: POST JSON via stdlib `urllib` (no
  third-party HTTP dep). Configurable headers + timeout.
- All three normalize errors into `AdapterError` with the original
  cause preserved.
- `SandboxPolicy`: declarative mock/dry_run rules; `live_sandboxed`
  and `live_canary` are accepted as enum values but `ensure_runnable()`
  blocks them in MVP via `SandboxViolationError`.
- `Executor`: runs an `EvalCase` for N repetitions through a given
  adapter + sandbox; assembles a `Trace` per repetition via
  `TraceRecorder`. Records adapter LLM output as an `LLMCallSpan`,
  each tool use as a `ToolCallSpan` (sandboxed flag per policy),
  and adapter exceptions as `ErrorSpan` + `final_state=errored`.

24 new tests (280 total). mypy strict + ruff clean. Zero new deps.

## [0.0.4] - 2026-05-16

### Added — PR 3: Trace ingestion (recorder + payload router + OTel importer)

- `PayloadRouter` — small payloads (≤4 KB by default) stay inline in
  the Trace JSON; larger ones are written to the `ObjectStoreInterface`
  and replaced with `oss://` pointers + sha256 hashes. Canonical
  JSON encoding for dicts/lists guarantees stable hashing across key
  order.
- `TraceRecorder` — context manager that captures spans during agent
  execution. Span context managers: `agent_turn`, `llm_call`,
  `tool_call`. Convenience emitters: `add_retrieval`,
  `add_memory_read/write`, `add_decision`, `add_handoff`,
  `add_human_intervention`, `add_guardrail_check`, `add_error`.
  Accumulates trace-level metrics (LLM call count, tool call count,
  token totals, retries). Tool call exceptions automatically mark
  the span ERROR with type+message. Exiting the context with an
  uncaught exception marks the trace ERRORED.
- `import_otel_spans` — adapter from a flat list of OTel-style span
  dicts (gen*ai.*, openinference.\_) to a selfevals Trace. Classifies
  spans by `openinference.span.kind` / `gen_ai.*` presence,
  normalizes finish reasons, preserves parent/child links, retains
  unknown attributes in `provider_metadata` or CustomSpan.payload.
  When TOOL spans carry call_ids without explicit linkage, the
  importer synthesizes ToolUseRequest entries on the nearest LLM
  span so the schema invariant holds; if no LLM span exists the
  call_id is dropped silently.
- Public surface: `selfevals.trace` re-exports `PayloadRouter`,
  `TraceRecorder`, `import_otel_spans`.

26 new tests; 256 total. mypy strict + ruff clean. Zero new deps.

## [0.0.3] - 2026-05-16

### Added — PR 2: Storage layer (SQLite + filesystem + workspace scoping)

- `StorageInterface` / `ObjectStoreInterface` / `WorkspaceScope` ABCs:
  every read or write is bound to one `workspace_id`; cross-tenant
  access is impossible by construction.
- `SQLiteStorage` with single generic `entities` table (entity_type, id,
  workspace_id, version, timestamps, payload JSON) + `objects` table.
  Indexes on (workspace_id, entity_type[, created/updated]) and a
  partial deleted_at index. Optimistic concurrency on `version`.
  WAL journal mode + foreign keys on.
- Homemade migration runner (no alembic dep): forward-only,
  `mNNNN_<slug>.py` modules with `up(conn)`, tracked in
  `_selfevalss_migrations`. Initial migration creates the tables.
- `FilesystemObjectStore`: content-addressed blobs at
  `{root}/{workspace_id}/{prefix2}/{sha256}.bin`; pointer URI
  `oss://{workspace_id}/sha256:...` encodes its workspace.
  SHA256 integrity check on read; collision detected if same hash
  resolves to different bytes.
- `seed_workspace(storage, slug, name, user_id, ...)` helper:
  idempotent by (slug, owner), creates the Workspace + one Member
  per `Role` (viewer, evaluator, experimenter, maintainer, admin,
  auditor) when `assign_all_roles=True`.
- Errors: `EntityNotFoundError`, `WorkspaceMismatchError`,
  `OptimisticConcurrencyError`, `ObjectNotFoundError`,
  `PointerHashMismatchError`, `IntegrityViolationError`.

33 new tests (231 total).

## [0.0.2] - 2026-05-16

### Added — PR 1: Schemas-first scaffolding (Pydantic v2)

Closed enums (`Role`, `Level`, `DatasetSource`, `GroundTruthMethod`,
`DatasetType`, `SandboxMode`, `RuntimeLocation`, `Mode`, `ProposerStrategy`,
`ExperimentState`, `SpanKind`, `StopReason`, `TraceState`,
`ToolCallStatus`, `PIIStatus`, `FeatureKind`/`Status`,
`AgentType`/`Status`, `FleetStatus`, `DatasetStatus`, `ToolStatus`,
`GraderCardState`, `DecisionOutcome`, `IterationState`, `Modality`).

Entities:

- `Workspace`, `Member` — multi-tenant primitives; workspace is
  self-referential (its own workspace_id == id).
- `Tool` — first-class entity needed for `editable.tool_code`.
- `FeatureRegistry`, `RiskRegistry` — declarative taxonomies.
- `AgentFleet`, `Agent` — agent_type-discriminated payloads.
- `EvalCase` — taxonomy (level, feature, source, ground_truth,
  runtime, dataset_type, risk), expected, failure_weights, blocking,
  holdout, PII contract.
- `Dataset` — manifest with split_allocation, lazy statistics by
  manifest_hash, regression-class immutability when frozen.
- `Experiment` — TargetSpec, EditableContract enforcing mode=agent_loop
  for tool_code/workflow_graph/skills, SearchSpace, FrozenSnapshot,
  ProposerSpec (MVP gates non-manual/grid/random), RunSpec, JudgeDefenses
  (live_canary requires outcome_metrics), ReliabilitySpec
  (pass@N/pass^N/consistency_rate/...), DecisionPolicy, state machine.
- `IterationRecord`, `Proposal` (with `validate_against(experiment)` =
  editable contract enforcement), `DecisionRecord` with automated +
  human rationale.
- `GraderCard` with blocking thresholds contract (precision >= 0.90,
  recall >= 0.95, max high-risk FNs == 0).
- `Annotation` with free-form labels + optional rubric_version.
- `Trace` schema (operational §B.2): RunInfo, AgentSnapshotRef,
  EnvironmentInfo, FinalState, discriminated `Span` union (12 kinds),
  TokenBreakdown with cache_read/cache_creation/reasoning, CostBreakdown,
  ReasoningBlock with provider signature, LLMOutput with
  tool_use_requested, ToolCallSpan.tool_use_id linkage validated
  trace-wide.

Internal helpers: ULID + prefixed ULID id generation (stdlib only),
canonical content_hash (sha256), tz-aware UTC time helpers.

Tests: 197 unit tests covering every validator and enum; mypy strict

- ruff (E/W/F/I/B/UP/N/SIM/RUF) clean.

## [0.0.1] - 2026-05-16

### Added

- Initial repo scaffolding: `pyproject.toml`, ruff + mypy strict + pytest config.
- `docs/spec/` with canonical eval framework spec, operational spec v0.1, taxonomy notes.
