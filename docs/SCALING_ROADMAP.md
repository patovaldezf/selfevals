# selfevals — Scaling Roadmap: de "¿cuál fue el accuracy?" a "¿qué empeoró y por qué?"

> Roadmap vivo, **agnóstico** (igual que [`ROADMAP.md`](ROADMAP.md)): cada
> capacidad se justifica por la spec del framework y por la señal que le falta a
> un dev para mejorar un agente, **no** por las necesidades de ningún consumidor
> concreto. Donde un ítem del diagnóstico vive en un integrador externo, se marca
> **fuera de scope** y no toca este repo.

Estado base: **v0.10.0** en `main` (funnel grader + breakdown tree + datasets de
primer orden). Fecha: 2026-06-12.

## Tesis

El core de grading ya es rico (9 graders, breakdown funnel, panel de jueces). El
cuello de botella dejó de ser _"¿puede el framework puntuar?"_ y pasó a ser
**"¿la señal que devuelve es accionable y honesta a escala?"**. Cuatro experimentos
reales lo expusieron:

1. Un `deterministic` exact-match solo puede fallar de una forma —
   `structured_output_mismatch` — que dice _que_ falló, no _qué confundió con qué_.
2. Un case `errored` (un agente colgado 735s) se colapsa a `0.0` en pass@1 y
   contamina el p95, mezclando **error de infra** con **fallo de calidad**.
3. El `consistency_rate` (no-determinismo del modelo) **se computa pero no se
   reporta** — el dev ve `pass@1=0.67` sin saber qué parte es ruido.
4. El worker es serial: `run.parallelism` está **definido y nunca consumido**.

Este roadmap cierra esas brechas. No reabre lo que [`ROADMAP.md`](ROADMAP.md) ya
planifica (TTFT/percentiles PR-2, sampling/holdout PR-4, TrajectoryGrader PR-8,
LLM proposer PR-13) — lo referencia.

## Diferido / fuera de scope (vive en el integrador, no en este repo)

- **Modo eval-case suelto sin backend externo** (escribir un caso y correrlo sin
  copiar UUIDs de Supabase): el `case_runner` y `--scenario-ids` son del integrador.
  El framework ya acepta cases inline vía YAML/JSONL; el bridge a una fuente SQL de
  producción está en el backlog de `STATUS.md`, no aquí.
- **Wire de features de producto** (search, copywriter, create_order…): son features
  del consumidor, no primitivas del framework.
- **Handoff a un consumidor concreto**: el framework expone confusion-matrix +
  breakdown contra su superficie pública; cómo un integrador mapea sus clases y
  consume la matriz se documenta **en el repo del integrador**, no en selfevals.

---

## Tabla de capacidades

| #   | Capacidad                                                               | Por qué                                                        | Status hoy                                                                                                                                                                       | Esfuerzo | Fase      |
| --- | ----------------------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | --------- |
| ★1  | **Confusion failure-mode** (exact-match → `misclassified:X->Y`)         | exact-match es callejón sin salida diagnóstico                 | ✅ `misclassified:<pred>-><exp>` cuando ambos son etiqueta de clase (`deterministic.py`)                                                                                         | S        | SF-1      |
| 2   | **Errored/timeout fuera de pass@1**                                     | hoy un agente colgado cuenta como 0.0 de calidad               | ✅ cases errored excluidos del denominador de pass@1 + `error_rate` separado (`aggregator.py`)                                                                                   | S        | SF-1      |
| 3   | **`consistency_rate` al reporte**                                       | el no-determinismo se computa pero no se ve                    | ✅ columna `consistency` en la tabla de iteraciones (`markdown.py`)                                                                                                              | S        | SF-1      |
| ★4  | **`ClassificationGrader` (`type: confusion`) + matriz NxN agregada**    | "special orders se confunden con full orders" es la señal real | ✅ grader `confusion` + rollup NxN + per-class F1 + render markdown; matemática extraída a `graders/_confusion.py` reusada por calibración (`classification.py`/`aggregator.py`) | M        | SF-2      |
| 5   | **Worker concurrente** (consumir `run.parallelism` + pool de N workers) | serial = no escala a N experimentos                            | ✅ `run.parallelism` cableado a los semáforos del executor/loop (default 8); pool inter-run vía Redis consumer-group (#47) (`launch.py`)                                         | M        | SF-3      |
| 6   | Observabilidad en la respuesta (nodos/tokens/latencia)                  | cuando un eval falla, el dev no ve por qué sin reproducir      | 🟡 = ROADMAP PR-2/#9                                                                                                                                                             | M        | (ROADMAP) |
| 7   | Análisis de fallas auto (clustering de failure-modes)                   | clasificar fallos a mano no escala                             | 🟡 = ROADMAP PR-13/#6                                                                                                                                                            | S–M      | (ROADMAP) |
| 8   | Regresión en CI + baselines versionados                                 | "intention bajó de 0.88 a 0.67, falla el build"                | ✅ baseline auto-anclado al dataset en la 1ra ejecución + `regression check` con exit code (primary/pass@1 + per-class F1) (`ci/regression.py`/`runner/baseline.py`)             | M        | SF-4      |

Leyenda: ✅ done · 🟡 parcial · ❌ ausente. `★` = capacidad estrella (lo que más
mueve la aguja para el diagnóstico real).

---

## ★ Capacidad estrella: confusion matrix para evals de clasificación

**El problema concreto.** Un `deterministic` exact-match solo emite:

```json
{ "label": "fail", "score": 0, "failure_modes": ["structured_output_mismatch"] }
```

Eso dice _que_ falló, no _qué confundió con qué_. En el caso real: los **special
orders** se confunden con los **place full orders** — esa celda
(`special_order → full_order`) es la señal que el prompt necesita, no el `score: 0`.

Se ataca **por capas**, barata primero:

### Capa 1 (S) — failure-mode enriquecido · SF-1

`DeterministicGrader` ya tiene en mano el predicted (`context.response.structured_output`)
y el expected (`expected.structured_output`) en el punto del mismatch
(`graders/deterministic.py:213-221`). Cuando ambos son una etiqueta de clase
(escalar o `{label: ...}`), el failure_mode pasa de `structured_output_mismatch`
a `misclassified:<predicted>-><expected>` (p.ej. `misclassified:full_order->special_order`),
con predicted/expected en el `detail`.

**Sin schema, sin migración.** `IterationMetrics.failure_mode_counts` es
`dict[str,int]` free-form (`schemas/iteration.py:141`) — el aggregator cuenta el
string tal cual (`aggregator.py:390`) y el reporter ya rankea el top-N
(`markdown.py:177`). El mode enriquecido aparece solo en los reportes.

### Capa 2 (M) — `ClassificationGrader` (`type: confusion`) + rollup · SF-2 ✅

**Hecho (SF-2).** La matemática de confusión (matriz NxN + per-class P/R/F1 +
macro-F1, con guard de class-imbalance) se **extrajo** de
`compute_classification_metrics` a un helper puro sobre strings
`graders/_confusion.py::confusion_from_pairs(pairs) -> ConfusionReport`; tanto la
calibración (pred-vs-humano sobre el enum `GradeLabel`) como el nuevo rollup lo
reusan, así la fórmula de F1 no se duplica (ruta B: la primitiva original usaba el
enum `GradeLabel`, no strings arbitrarios, así que reusarla tal cual habría sido
forzado). El par `(esperado, predicho)` viaja del grader al aggregator codificado
en la key de un nodo hijo del breakdown (`cell:<esperado>-><predicho>`), que
sobrevive el collapse multi-turn; el rollup recorre el árbol y reconstruye la
matriz. Persistido en `IterationMetrics.confusion` como dict-of-dicts (sin
migración) y renderizado en markdown.

Diseño original (la primitiva existía en `calibration.py`):

1. **Grader nuevo** `graders/classification.py` (`type: confusion`): por-case extrae
   la clase predicha (vía el path selector de `_select.py`, como `set_match`) y la
   esperada (`Expected`), emite el par en el `GradeResult` (reusa `breakdown` para
   el par predicted/expected). Registrar en `graders/registry.py` + test.
2. **Rollup agregado**: el `aggregator` junta los pares de todos los cases de la
   iteración y llama `compute_classification_metrics` → matriz NxN + per-class P/R/F1
   en `IterationAggregate`. (El aggregator ya itera outcomes en `aggregator.py:390`.)
3. **Render**: `reporter/markdown.py` dibuja la matriz (tabla NxN) y per-class F1,
   junto a un `_confusion_lines()` análogo al `_failure_mode_lines()` existente.

Esto reemplaza el `fail/score:0` con "el 60% de los special orders se clasifican
como full orders" — la señal accionable.

**Handoff: solo capability.** selfevals expone la matriz contra su superficie
pública y se mantiene 100% agnóstico. El mapeo de clases de un consumidor concreto
se documenta en su propio repo.

---

## Otras fases

### SF-1 — Honestidad de la métrica (S, una tanda) · paralelo a ★Capa 1

- **#2 errored fuera de pass@1**: `GradeLabel.ERROR` ya existe y el aggregator lo
  colapsa worst-of → 0.0 (`aggregator.py:227,257`). Cambiar a **excluir** los cases
  `ERROR` del denominador de pass@1 (o reportarlos en una métrica `error_rate`
  aparte) en `_compute_metric` (`aggregator.py:463`), para que pass@1 mida calidad
  y no infra. Un `RepetitionResult.error` no-nulo (`executor.py:47`) marca el case
  como errored aunque el grader no lo haya etiquetado.
- **#3 consistency al reporte**: el `reliability` dict (con `consistency_rate`,
  `pass^k`, `recovery_rate`) ya se computa (`aggregator.py:388`) pero `render_markdown`
  no lo lee. Añadir una línea/columna de `consistency_rate` por iteración en
  `_iteration_table` (`markdown.py:127`), para que el dev vea cuánto del pass@1 es ruido.

### SF-3 — Worker concurrente (M) · depende de SF-1

- **#5 consumir `run.parallelism`**: hoy `RunSpec.parallelism` (`experiment.py:163`,
  `ge=1 le=64`) es dead code; `Executor(concurrency=8)` y `OptimizationLoop(grade_concurrency=8)`
  están hardcoded. Cablear `parallelism` a esos semáforos por run.
- **Pool de N workers**: Redis ya es consumer-group (PR #47); falta lanzar N procesos
  worker y validar el reparto. El executor ya es async (`executor.py:142`), así que la
  concurrencia intra-run es ortogonal al pool inter-run.

### SF-4 — Regresión en CI + baselines (M) · depende de ★Capa 2 ✅

- **Hecho.** El baseline se ancla al **dataset** (no al experimento) y se fija
  **automáticamente en la primera ejecución**: el primer run que completa sobre
  un `ds_xxx` registra su `best_iteration` como baseline de ese dataset
  (`DatasetBaseline`, idempotente — un run posterior mejor no lo mueve). El gate
  `selfevals regression check` compara la iteración actual contra ese baseline y
  falla con exit code si pass@1/primary o la per-class F1 de la confusion matrix
  cae más de un umbral configurable; consumible por CI
  (`selfevals regression check ... || exit 1`). La matemática vive en
  `ci/regression.py::evaluate_regression` (pura). Anclar al dataset mide
  regresión del agente contra un set de casos fijo, cross-experimento.

---

## Orden de ejecución y prioridad

Los que más mueven la aguja para el diagnóstico real, en orden:

| Orden | Item                                  | Esfuerzo | Depende de | Por qué primero                             |
| ----- | ------------------------------------- | -------- | ---------- | ------------------------------------------- |
| 1     | ★Capa 1 confusion failure-mode        | S        | —          | desbloquea la señal hoy ausente, sin schema |
| 2     | #2 errored fuera de pass@1            | S        | —          | la métrica hoy miente                       |
| 3     | #3 consistency al reporte             | S        | —          | barato, hace honesto el pass@1              |
| 4     | ★Capa 2 ClassificationGrader + matriz | M        | Capa 1     | el diagnóstico rico (primitiva ya existe)   |
| 5     | #5 worker concurrente                 | M        | SF-1       | escala a N experimentos                     |
| 6     | #8 regresión CI                       | M        | Capa 2     | gate de no-regresión                        |

SF-1 (items 1–3) es una sola tanda: tres cambios chicos, mismo módulo
(`aggregator.py` + `markdown.py` + `deterministic.py`), un PR.

## Verificación (transversal)

Cada capacidad cita el módulo real verificado contra el código (convención del repo:
VERIFICAR antes de afirmar). Gate por PR: suite verde (`uv run pytest` surface por
defecto, 971+), `mypy --strict`, `ruff`, y los ejemplos corren offline. La ★Capa 2
se prueba contra el ejemplo `showcase` (ya ejercita `set_match`/confusion-style
contra `structured_output`).
