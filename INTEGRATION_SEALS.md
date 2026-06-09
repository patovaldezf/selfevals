# Workstream A — selfevals: API lista para que seals + el Playground la consuman

> **Tu repo:** `~/Desktop/proyectos/mis_repos/selfevals`
> **Tu rol:** dejar la API HTTP de selfevals (su "LangSmith") sólida, idempotente y
> consumible por un frontend externo. selfevals es **dueño de su propio storage** (SQLite
> local en F1). NO se integra con Supabase de seals; seals lo usa como cliente.

## Contexto

selfevals ya es un LangSmith funcional: motor `OptimizationLoop`, schemas (`Experiment`,
`EvalCase`, `Trace`, `Dataset`, `GraderCard`, `IterationRecord`, `DecisionRecord`), API
FastAPI en `src/selfevals/api/app.py` (~18 endpoints) y dashboard SvelteKit en `web/`.

Estamos conectando **3 repos en paralelo**:

- **A (este) — selfevals:** la API/servicio.
- **B — seals/llm:** corre sus 7 single-step evals (categorize_message, search, etc.)
  contra selfevals vía un `AgentAdapter` embedded; los datos aterrizan en el storage de
  selfevals.
- **C — seals-playground:** un FE React que consume TU API (`/api/openapi.json` → Orval) y
  muestra experiments/iterations/funnels/traces con su propia línea de diseño.

La API es la **frontera** entre los tres. Tu trabajo es que ese contrato sea estable.

## Estado actual verificado (2026-06-05)

- API levanta con: `python -m selfevals.api --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite`
- OpenAPI: `GET /api/openapi.json` (200). Docs: `/api/docs`.
- CORS ya permite `http://localhost:5173` y `http://127.0.0.1:5173` (puerto del Playground/Vite).
- Rutas existentes (todas bajo `/api`):
  - `GET /health`
  - `GET /workspaces`, `POST /workspaces`, `GET /workspaces/{ws}`
  - `GET /workspaces/{ws}/experiments`, `GET /workspaces/{ws}/experiments/{exp}`
  - `GET /workspaces/{ws}/experiments/{exp}/iterations`
  - `GET /workspaces/{ws}/experiments/{exp}/decisions`
  - `GET /workspaces/{ws}/experiments/{exp}/compare`
  - `GET /workspaces/{ws}/iterations/{itr}`, `GET .../iterations/{itr}/funnel`
  - `GET /workspaces/{ws}/traces/{trace}`, `GET .../traces/{run_id}/stream` (SSE)
  - `GET /workspaces/{ws}/threads/{thread}`
  - `GET /runs/active`
  - `GET /workspaces/{ws}/anchor-set`, `GET /workspaces/{ws}/payloads`

## BUG conocido a arreglar PRIMERO (root cause ya diagnosticado)

El migrador NO es idempotente sobre una BD preexistente. `m0001_initial.py:up()` hace
`CREATE TABLE entities` (sin `IF NOT EXISTS`); si la BD ya tiene `entities` pero la tabla de
tracking `_selfevalss_migrations` está vacía (p. ej. BD creada por una versión vieja que
usaba `_bootstrap_migrations`), `apply_migrations` re-aplica m0001 y revienta con
`sqlite3.OperationalError: table entities already exists`. Esto tira 500 en todo endpoint que
abre storage.

**Fix correcto (no parche):**

1. `apply_migrations` debe hacer **backfill** del registro de migraciones si detecta una BD
   con `entities` ya presente pero sin filas en `_selfevalss_migrations` (o migrar desde la
   tabla legacy `_bootstrap_migrations` si existe). Marcar m0001 como aplicada en ese caso.
2. Endurecer `m0001.up()` para que sea segura ante tablas/índices preexistentes
   (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) — defensa en profundidad.
3. Test de regresión: abrir un `SQLiteStorage` sobre una BD que ya tiene `entities` sin
   tracking → no debe lanzar; debe quedar con `current_version()==1`.

(En la sesión se hizo un backfill manual para desbloquear el entorno; reemplázalo por el fix
real + test.)

## Tareas

1. **Arreglar el migrador** (arriba) + test de regresión.
2. **Auditar el contrato vs lo que el Playground necesita** (ver `INTEGRATION_SEALS.md` del
   Playground para la lista de vistas). Confirmar shapes de:
   - lista de experiments (filtrable por `state`?, paginada — ya devuelve `items/total/limit/offset/has_more`),
   - experiment detail (`summary` + `result` con `best_iteration`),
   - iterations (incluye `proposed_parameters`, `primary_metric_value`, `delta_vs_best`,
     `decision_outcome`, `trace_run_ids`),
   - funnel por iteración (el shape que consume `FunnelNode.svelte` — replicarlo en la doc),
   - trace detail (spans), thread detail, compare.
     Añade lo que falte. **Probable faltante:** filtros por `feature`/`client` en experiments
     (seals correrá 1 experiment por feature; el Playground querrá filtrar).
3. **OpenAPI consumible por Orval**: verifica que `/api/openapi.json` tipa bien todas las
   respuestas (response_model en cada endpoint; nada de `dict` crudo donde el FE necesite
   tipos). El Playground genera su cliente desde ahí.
4. **NUEVO endpoint de escritura — lanzar un experimento por HTTP (F1).**
   `POST /api/workspaces/{ws}/experiments/run`. Debe **reusar el camino canónico de
   `cli/commands.py:cmd_run`** (NO inventar otro):
   `_friendly.load_spec(...)` → `_build_adapter(spec.agent)` → `_build_proposer(...)` →
   `_register_grader_specs` + `_resolve_case_graders` → `Executor(...)` +
   `OptimizationLoop(experiment, executor, proposer, graders, cases, scope,
decision_evaluator=DecisionMatrixEvaluator())` → `loop.run()`.
   Detalles:
   - **Request:** acepta o bien una ruta/nombre de spec ya en disco, o un spec inline
     (YAML/JSON) en el body. Para el caso de seals, el agente es un `embedded` entrypoint
     (`module:fn`) — el spec lo trae. `workspace_id` del path.
   - **Ejecución NO bloqueante:** el loop corre LLMs y puede tardar. Lanza la corrida en
     **background** (BackgroundTasks / task que escribe en el `scope`) y responde 202 con el
     `experiment_id` (y `run`/job id) inmediatamente. El FE hace polling de
     `GET .../experiments/{exp}` o se suscribe al SSE `/runs/active` + `.../traces/{run}/stream`.
     El estado del experiment (`draft → queued → running → completed/failed`) refleja el avance.
   - **Persistencia ON** (no `--no-persist`): se escribe `Experiment` + iterations + traces al
     storage, igual que la CLI. Asegura `_ensure_workspace`.
   - **Errores:** 422 si el spec no valida / 0 casos; 409 si ya hay una corrida activa para ese
     experiment (opcional); 500 con detalle si el loop revienta (refleja `state=failed`).
   - Tests: spec inline válido → 202 + experiment aparece y completa; spec inválido → 422;
     ejecución no bloquea el event loop.
5. **Documentar el "cómo levantar"** en el README (API + `web/` dashboard) para dev local.
6. **NO deploy todavía.** Corremos en localhost. El deploy (Fly/Railway/k8s de seals, NO
   Vercel) se decide después de validar el loop end-to-end. Si más adelante se va a un host
   serverless, habrá que implementar `PostgresStorage(StorageInterface)` (plantilla:
   `storage/sqlite.py`); con host de disco, SQLite sigue sirviendo.

## Reglas

- selfevals permanece **agnóstico a seals**. Nada en este repo importa ni menciona seals,
  LangGraph, ni Supabase de seals. Si el Playground o seals necesitan algo, se expresa como
  una capacidad genérica de la API.
- Storage detrás de `StorageInterface`. No filtrar SQLite a la capa de API.
- Quality gates del repo limpios (ruff/mypy/pytest o lo que use selfevals). Tests para todo
  lo nuevo.
- Trabaja en un branch; abre PR. No push a main sin OK.

## Verificación

```bash
python -m selfevals.api --port 8000 --db ./selfevals.sqlite
curl -s localhost:8000/api/health
curl -s localhost:8000/api/workspaces
curl -s "localhost:8000/api/workspaces/<ws>/experiments"
curl -s localhost:8000/api/openapi.json | python3 -m json.tool | head
# lanzar experimento por HTTP (no bloqueante → 202 + experiment_id):
curl -s -X POST "localhost:8000/api/workspaces/<ws>/experiments/run" \
  -H 'content-type: application/json' -d @spec.json
```

Todos 200/202, shapes tipados. El bug del migrador no reproduce sobre BD preexistente. El
`POST .../experiments/run` arranca la corrida en background y el experiment progresa a
`completed` (verificable por polling de `GET .../experiments/{exp}` o por SSE).
