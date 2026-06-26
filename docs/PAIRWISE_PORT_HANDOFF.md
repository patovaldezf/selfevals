# Handoff — Re-portar Pairwise + Torneos a Postgres-only

## TL;DR

La feature **pairwise judge + torneos Elo/Bradley-Terry** está 100% construida y verde
(57 tests propios, 949 totales) PERO se escribió contra el backend SQLite + tabla genérica
`entities`, que la branch `feat/postgres-only` **acaba de eliminar** (drop SQLite, schema
relacional con mappers + migrations). Hay que **re-portar la capa de storage** al nuevo esquema.
La lógica pura no cambia.

## Dónde está el código

Todo mi WIP está en `stash@{0}` (creado sobre `feat/frontend-levelup`):
```
stash@{0}: On feat/frontend-levelup: frontend-levelup WIP: pairwise/tournament + TECHNICAL_DEBT (pre postgres-only)
```
- **Tracked modificados** (10 archivos): `git stash show stash@{0} --stat`
- **Untracked nuevos** (16 archivos) viven en el 3er padre del stash:
  `git ls-tree -r 5c586811c5b786483a660278fd80180a6e6a78b0 --name-only | grep -E "pairwise|ranking|pairing|tournament"`

Para extraer un archivo de referencia SIN aplicar el stash:
```
git show 5c586811c5b786483a660278fd80180a6e6a78b0:src/selfevals/optimization/ranking.py
git show stash@{0}:src/selfevals/runner/launch.py
```

## Qué se reutiliza TAL CUAL (lógica pura, no toca storage)

Estos archivos NO dependen de SQLite/entities — cópialos del stash directo:
- `src/selfevals/optimization/ranking.py` — Elo + Bradley-Terry (MM de Hunter, **stdlib puro, sin scipy**). + `tests/optimization/test_ranking.py`.
- `src/selfevals/optimization/pairing.py` — vs_baseline O(n), all_pairs O(n²), sampled/swiss sub-O(n²), determinístico (hash de ids). + `tests/optimization/test_pairing.py`.
- `src/selfevals/graders/pairwise.py` — `PairwiseGrader` (type: pairwise, modo reference) + `judge_pair()` reusable + `swap_and_average`. + `tests/graders/test_pairwise.py`.
- `src/selfevals/runner/launch.py` (diff) — `_pairwise_factory` + rama `pairwise` en `register_grader_specs`.
- `src/selfevals/repo/loader.py` (diff) — `pairwise` en `_SUPPORTED_GRADER_TYPES` + `_parse_pairwise_params`. + `tests/repo/test_loader.py` diff.
- `src/selfevals/schemas/eval_case.py` (diff) — campo `reference_output: str | None`.

## Qué hay que RE-PORTAR (la capa de storage)

El cambio de arquitectura: antes todo iba a la tabla `entities` (blob JSON) vía
`scope.put_entity`/`list_entities`. Ahora postgres-only usa **tablas relacionales + EntityMapper +
migrations numeradas** (m0002…m0006 ya existen). Hay que:

1. **`schemas/pairwise_verdict.py` y `schemas/tournament.py`** — los modelos Pydantic ya están
   tipados estrictos (sin dict opacos), pensados justo para esto. Probablemente sirven casi tal cual
   como entidades; revisar contra el patrón de los schemas ya migrados.
2. **Migration nueva (m0007?)** — crear las tablas `pairwise_verdicts` y `tournaments` (+ tabla hija
   o columnas `a_*`/`b_*` para el PairRef, y filas de ranking). Seguir el patrón de `m0003`/`m0004`.
3. **EntityMapper para ambas** — registrar mappers como los de `m0005` (annotations, etc.). Aquí es
   donde "IDs validados en código, sin FK a entities" cambia: ahora SÍ puedes poner FK relacionales
   reales a traces/cases/experiments (era lo que Patricio quería).
4. **`runner/pairwise_ops.py`** — `ingest_verdicts`/`list_verdicts`/`compute_calibration`. La lógica
   de calibración (acuerdo LLM-vs-humano por rubric_version) es pura y se conserva; solo cambian las
   llamadas de storage al nuevo contrato relacional.
5. **`runner/pairwise_tournament.py`** — orquestador async. La lógica (pairing→judge concurrente→
   ranking) no cambia; solo el persist de verdicts + Tournament al nuevo storage.
6. **`api/pairwise_ops.py` + `api/app.py` + `api/schemas.py`** — endpoints
   `verdicts/ingest|list|calibration` y `tournaments` (POST/GET). En postgres-only la API ya inyecta
   storage por dependency (cambió el patrón `_storage`); adaptar los handlers al nuevo wiring.

## Diseño (decisiones ya tomadas con Patricio)

- **PairwiseVerdict** entidad de primer orden, `judge_kind: llm|human` → MISMO par juzgado por LLM y
  humano = base de RLHF + calibración de judges.
- Grader modo `reference` (A=output agente, B=`EvalCase.reference_output`). Salida juez
  `{preferred, margin, reason}` → GradeResult (A→PASS, B→FAIL `worse_than_reference`, tie→PASS si
  `tie_is_pass`). `swap_and_average` anti-position-bias (default off).
- Torneos: 3 estrategias de pairing + Elo/Bradley-Terry. Bradley-Terry en **stdlib** (sin scipy:
  regla de deps core mínimas).
- **FUERA de alcance** (tandas siguientes): UI web de anotación humana + torneos; modos
  `previous_iteration`/`variant` del grader e in-loop A/B (requieren 2º output vivo por el loop).

## Pasos de ejecución sugeridos

1. `git checkout feat/postgres-only` (ya estás ahí). Estudiar el patrón de storage nuevo:
   `src/selfevals/storage/` (mappers, migrations m0002-m0006), un schema ya migrado (ej. annotation),
   y cómo la API inyecta storage ahora.
2. Copiar la lógica pura del stash (ranking, pairing, grader, loader/launch/eval_case diffs).
3. Crear migration + mappers para `pairwise_verdicts` y `tournaments` (con FKs reales esta vez).
4. Re-portar `runner/pairwise_ops.py` + `pairwise_tournament.py` + API al nuevo storage.
5. Re-portar los tests (ahora necesitan Postgres: `docker-compose up` en :5433, ver
   `tests/conftest.py` — `SELFEVALS_TEST_POSTGRES_URL` / `_DEFAULT_TEST_PG`).
6. Gates: `uv run mypy src/selfevals` (strict), `uv run ruff check .`, `uv run pytest` (con PG up).
7. Commit-ship: la branch base es `feat/postgres-only`. Conventional + español, sin co-author.

## Notas de contexto

- El PR #56 (`feat/frontend-levelup`) sigue abierto; postgres-only es un refactor mayor posterior.
- `docs/STATUS.md` del stash tiene la descripción completa de la feature (copiarla/adaptarla).
- La memoria `pairwise-grader-design.md` (en ~/.claude/.../memory/) documenta el diseño cerrado.
- NO dropear `stash@{0}` hasta confirmar que todo el código fue re-portado y commiteado.
```
```
