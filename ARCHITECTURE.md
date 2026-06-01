# selfevals — Arquitectura

> Estado: documento de arquitectura v0.1 (selfevals v0.5.0). Es la fuente de
> verdad sobre **qué vive dónde y por qué** en el repo. No describe la
> implementación de cada módulo; fija las piezas, sus fronteras y las utilidades
> reusables. Regla del repo: **verificar reportes contra el código antes de
> arreglar** (`docs/STATUS.md` es el snapshot honesto de qué funciona).

## Qué es selfevals

Framework de evals AI-native auto-mejorante para agentes. Corre experimentos
(search space x cases x reps) con grading concurrente, optimiza la búsqueda y
reporta. Core Python con dependencias mínimas (pydantic, pyyaml, httpx); cada
proveedor (`[anthropic]`, `[openai]`, `[bedrock]`...) y la API web (`[web]`) son
extras opcionales que se lazy-importan, así el install base se mantiene ~2MB.
El authoring surface es YAML, no Python.

## El sistema en una línea

```
evals/experiments/*.yaml
   → repo/loader (hidrata a ExperimentSpec/Pydantic)
   → cli/commands (construye adapters + graders)
   → optimization/loop (sampling → runner/executor corre el agente → graders puntúan → aggregator)
   → storage (sqlite) + reporter (markdown/json/compare)
```

## Piezas

| Pieza            | módulo/path                                                                                                            | qué es                                                                                   | estado             |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------ |
| **Schemas**      | `schemas/` (experiment, eval_case, grader_card, trace, fleet, dataset...)                                              | modelos Pydantic del dominio + `schemas/registry.py`                                     | código vivo        |
| **Loader**       | `repo/loader.py`                                                                                                       | YAML → `ExperimentSpec`. Solo parsea/valida shape; **no** construye adapters             | código vivo        |
| **Graders**      | `graders/` (base, deterministic, llm_judge, judge_panel, guardrail, artifact, trajectory, calibration) + `registry.py` | puntúan output; per-grader scoring (cada grader puntúa por separado)                     | código vivo        |
| **Optimization** | `optimization/` (loop, proposers, sampling, aggregator)                                                                | loop de optimización; el grid proposer agota combinaciones (`SearchSpaceExhaustedError`) | código vivo        |
| **Runner**       | `runner/` (adapters, executor, multiturn, simulator, pricing, otlp\_\*)                                                | ejecuta el agente; contrato `(AdapterRequest) -> AdapterResponse` (embedded/cli/http)    | código vivo        |
| **Storage**      | `storage/` (interface, sqlite, filesystem, migrations, seed)                                                           | persistencia; SQLite por defecto + migraciones                                           | código vivo        |
| **Reporter**     | `reporter/` (markdown, json_report, compare, \_metrics)                                                                | renderiza resultados y comparativas                                                      | código vivo        |
| **Analysis**     | `analysis/` (bundle, hypothesis, ingest, staging, schemas)                                                             | análisis de fallos / hipótesis sobre corridas                                            | código vivo        |
| **Trace / SDK**  | `trace/` (recorder, otel_importer, payload_router) · `sdk/` (facade, exporter, auto_instrument, context)               | captura de traces (OTel) del agente bajo prueba                                          | parcial / opcional |
| **CLI**          | `cli/` (main, commands, analyze_commands, \_help, \_friendly)                                                          | entrypoint `selfevals`                                                                   | código vivo        |
| **API**          | `api/` (app, broker, sse, queries)                                                                                     | FastAPI; sirve la Web UI. Extra `[web]`, import diferido                                 | opcional           |
| **web/**         | `web/` (subproyecto)                                                                                                   | Web UI **SvelteKit 5 + Vite + Tailwind 4, npm**. Stack propio, fuera del paquete Python  | subproyecto        |
| **landing/**     | `landing/` (subproyecto)                                                                                               | Landing de marketing **Next.js 16 + React 19 + Tailwind 4, npm**. Solo marketing         | subproyecto        |

## Patrones (decisiones de frontera que importan)

1. **NO hay `config/`.** El authoring surface es YAML (`evals/experiments/*.yaml`).
   `repo/loader.py` lo hidrata a `ExperimentSpec` (Pydantic) y **solo valida
   shape** — la construcción real de adapters y graders ocurre en
   `cli/commands.py`. No muevas esa lógica al loader.
2. **Core mínimo, proveedores y API como extras lazy.** El core nunca importa
   FastAPI ni SDKs de proveedor en tiempo de import; `api/*` difiere el import de
   FastAPI a call time, y los adapters degradan limpio (try/except con error
   "pip install selfevals[<provider>]"). No asumas que un extra está instalado.
3. **web/ y landing/ son subproyectos distintos del core.** Tienen su propio
   stack (SvelteKit/Next.js, npm) y no se empaquetan en el sdist (allow-list +
   exclude explícito en `pyproject.toml`). Al tocar packaging respeta
   `[tool.hatch.build...]` y nunca barras `web/node_modules` ni `*.sqlite`.
4. **Convención de extensión.** Nuevo grader → `graders/` + registrar en
   `registry.py` + test. Nuevo adapter → `runner/adapters.py` (contrato
   `AdapterRequest`→`AdapterResponse`) + doc en `docs/adapters.md`. Nuevo comando
   → parser en `cli/main.py`, handler en `cli/commands.py`, tabla en `README.md`.
   `tests/` espeja `src/selfevals/` paquete-por-paquete.

## Layout

```
src/selfevals/
├── cli/           main · commands · analyze_commands · _help · _friendly   # entrypoint `selfevals`
├── repo/          loader.py                          # YAML → ExperimentSpec (solo shape)
├── schemas/       experiment · eval_case · grader_card · trace · fleet · ...  # dominio Pydantic
├── graders/       base · deterministic · llm_judge · judge_panel · guardrail · ... + registry.py
├── optimization/  loop · proposers · sampling · aggregator
├── runner/        adapters · executor · multiturn · simulator · pricing · otlp_*
├── storage/       interface · sqlite · filesystem · seed · migrations/
├── reporter/      markdown · json_report · compare · _metrics
├── analysis/      bundle · hypothesis · ingest · staging · schemas
├── trace/  sdk/   recorder · otel_importer · payload_router · facade · exporter   # captura de traces (opcional)
├── api/           app · broker · sse · queries        # FastAPI, extra [web]
├── decision/  examples/  skills/  _internal/  _errors.py  version.py
├── web/      ← subproyecto SvelteKit (npm, fuera del paquete)
└── landing/  ← subproyecto Next.js (npm, marketing)
docs/ (STATUS.md = snapshot honesto; adapters.md, api_reference.md, ROADMAP.md)   ·   evals/   ·   tests/ (espeja src/)
```

## Utilidades reusables

Antes de escribir un helper, revisa si ya existe aquí (combate duplicación).

| qué hace                                                           | dónde vive                 | import                                                                                       |
| ------------------------------------------------------------------ | -------------------------- | -------------------------------------------------------------------------------------------- |
| Generar ULID / id con prefijo + validarlos                         | `_internal/ids.py`         | `from selfevals._internal.ids import new_ulid, new_prefixed_id, is_ulid, is_prefixed_id`     |
| Hash de contenido determinista                                     | `_internal/hashing.py`     | `from selfevals._internal.hashing import content_hash, bytes_hash`                           |
| Tiempo UTC consistente                                             | `_internal/time.py`        | `from selfevals._internal.time import utc_now, ensure_utc`                                   |
| Error de usuario con hint amigable                                 | `_errors.py`               | `from selfevals._errors import SelfEvalsUserError`                                           |
| Cargar/validar un experiment spec; resolver el callable del agente | `repo/loader.py`           | `from selfevals.repo.loader import load_experiment_spec, resolve_agent_callable`             |
| Registrar / resolver graders por nombre                            | `graders/registry.py`      | `from selfevals.graders.registry import register_grader, resolve_graders, available_graders` |
| Precio/estimación de una llamada por modelo                        | `runner/pricing.py`        | `from selfevals.runner.pricing import price_call, estimate_cost`                             |
| Seleccionar el set de optimización (random/estratificado)          | `optimization/sampling.py` | `from selfevals.optimization.sampling import select_optimization_set`                        |
| Métricas agregadas de costo/tiempo/casos de un result              | `reporter/_metrics.py`     | `from selfevals.reporter._metrics import compute_cost_time_summary, compute_total_cost`      |

## Estado

v0.5.0. Per-grader scoring y grid-exhaust implementados; core + CLI + storage +
reporter vivos. API web y captura de traces tras extras opcionales. `web/` y
`landing/` son subproyectos JS independientes. `docs/STATUS.md` manda sobre qué
funciona hoy; verifica ahí (y en el código) antes de tratar un reporte como bug.
