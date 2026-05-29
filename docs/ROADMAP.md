# selfevals — Roadmap

> Roadmap vivo del framework. Reemplaza un plan interno previo que estaba escrito
> alrededor de un producto específico. Este doc es
> **agnóstico**: cada capacidad se justifica por la spec del framework
> (`docs/spec/evals_framework.md`) y por best-practices públicas de la industria
> (Anthropic, Sierra τ-bench, LangSmith, Hamel Husain), no por las necesidades de ningún
> consumidor concreto.

Estado base: **v0.3.0** mergeado en `main` (async-first + schema multi-turno). 559 tests
default / 597 full, verdes.
Fecha: 2026-05-27.

## Tesis

selfevals está pasando de **barrido de parámetros sobre suites single-shot estáticas** a
**evaluación profunda del agente**: multi-turno, trayectoria, funnels, simulación de
usuario, panel de jueces. La spec ya describe todo esto; el roadmap es cerrar la brecha
entre spec y código.

## Contrato agnóstico (no negociable)

- selfevals es un paquete público de PyPI. **NO importa ni referencia ningún producto
  consumidor.** El código está limpio de acoplamiento; mantenerlo así.
- Los consumidores escriben su propio `AgentAdapter` contra la **superficie pública** del
  paquete (`AgentAdapter`, `AdapterRequest/Response`, `Grader`, `GradeResult`, `EvalCase`).
  Cero "bridge" dentro de este repo.
- Toda primitiva nueva es genérica y se prueba con los `examples/` propios
  (`hello_anthropic`, `hello_openai`, `example_pingpong`), nunca con un consumidor externo.

## Diferido a próxima vuelta (NO en este roadmap)

Analítica en producción: online/shadow/canary runtime, muestreo de traces de prod →
cases, CI regression gate. · Voice (streaming, TTFT en vivo, barge-in, ). ·
Code-editing proposer (`agent_loop`). · El **frontend completo** vive en su propio doc:
[`docs/FRONTEND.md`](FRONTEND.md).

---

## Tabla de capacidades

| # | Capacidad | Spec § | Cita de industria | Status | Esfuerzo | Fase/PR |
|---|-----------|--------|-------------------|--------|----------|---------|
| 1 | Async + paralelismo | §10 handoff; operational §2 | Hamel: automatizar el loop, no QA manual | ✅ 0.3.0 | M | hecho |
| 2 | Multi-turno (ConversationInput + executor) | §4 (level: conversation), §7 traces | Sierra τ-bench; multi-turn-eval-pattern | ✅ schema (0.3.0) / ❌ executor | L | F5 (executor) |
| 3 | `GradeResult.breakdown` funnels | §9 decision; composite-eval-pattern | LangSmith component isolation | ❌ | M | F3 / PR-7 |
| 4 | TrajectoryGrader (diagnóstico) | §7 traces; §3 targets | Anthropic + τ-bench: grade output-state, traj diagnóstica | ❌ | M | F4 / PR-8 |
| 6 | LLM proposer | operational §2, §10 | τ-bench Ghostwriter loop | ❌ | S–M | F7 / PR-13 |
| 7 | Inyección prompt/params end-to-end | §6 experiment contract | — | 🟡 parcial | M | con #11 |
| 8 | Sampling estratificado + splits + holdout | §11 anti-overfit; §5 portfolio | Anthropic capability≠regression | 🟡 schema | S | F2 / PR-4 |
| 9 | TTFT / p95 / tokens-per-sec | operational §8 trace model | τ-bench: reportar costo+latencia con accuracy | 🟡 schema | S | F2 / PR-2 |
| 10 | Auto-wiring CLI/HTTP desde YAML | §6 contract | — | ✅ | S | F2 / PR-6 |
| 11 | Superficie pública limpia (consumidor externo) | §10 | — | ❌ | S | F0 / PR-0 |
| 12 | Researcher-evaluable (artifacts) | §3 targets (full agent) | — | ❌ | M | F4 / PR-9 |
| 14 | Cost model (esquema real de labs) | §6 (cost_per_task guardrail) | τ-bench cost tracking | 🟡 parcial | M | F2 / PR-3 |
| 15 | Simulador de usuario (LLM-as-user) | §4 (source: simulation) | τ-bench user simulator (4 estrategias) | ❌ | L | F6 / PR-12 |
| 17 | Panel de jueces / counterfactuals / calibración | §8 graders; §2.7 | Hamel: jueces binarios + calibración humana | 🟡 parcial | M | F4 / PR-10 |
| 18 | GuardrailGrader (contenido, de primera clase) | §6, §9 decision | τ-bench policy compliance subscores | ❌ | S | F2 / PR-5 |

Leyenda: ✅ done · 🟡 parcial (schema/parcial) · 🔄 en curso · ❌ ausente.

---

## Fases (respetan dependencias)

`#1 async` es prerequisito duro (multi-turno, trayectoria, paralelismo, TTFT-from-stream
cuelgan de él). `#3 breakdown` antes de graders funnel-aware (#4, #12). `#2 multi-turno`
antes de `#15 simulador`.

> **Decisión de enfoque async (2026-05-27): async-first PURO.** El paquete aún no tiene
> usuarios → no hay API pública que preservar → un solo contrato async (`Grader.grade` y
> `AgentAdapter.invoke` son `async`), `asyncio.run` solo en el borde CLI. Sin métodos
> sync/async duplicados ni bridge `to_thread`. Esto lo establece el release 0.3.0.

### ✅ Hecho — Release 0.3.0 async-first (mergeado en `main`, commit `b9180cd`)
- **#1 async**: `AgentAdapter.invoke` (`adapters.py:98`) y `Grader.grade` (`base.py:54`) son
  `async def` (sin variante sync ni `invoke_async`). `Executor.run_case` async con
  `asyncio.Semaphore(concurrency=8)` (`executor.py:79,112`). `OptimizationLoop.run` async con
  `grade_concurrency=8` para graders concurrentes (`loop.py:118,244`). `HttpEndpointAdapter`
  usa `httpx.AsyncClient` (httpx ahora runtime dep, `pyproject.toml:24`). `asyncio.run` solo
  en `cli/commands.py:447`. `asyncio_mode = "strict"`.
- **#2 schema multi-turno**: `MessageRole` enum (`enums.py:327`), `Message`/`ContentBlock`/
  `ConversationInput` (`eval_case.py:126-167`), validador `_validate_conversation_shape`
  (`eval_case.py:205`), accesores `is_conversation()` / `conversation()` (`eval_case.py:218-224`).
  `input` sigue dict JSON-serializable. El `MultiTurnExecutor` (ejecución turn-by-turn) queda
  para F5.

### Fase 0 — Higiene de docs + superficie pública (S) · PR-0
- Retirar el plan interno previo. Este `ROADMAP.md` lo reemplaza.
- Exportar desde `src/selfevals/__init__.py` el contrato de runtime: `AgentAdapter`,
  `AdapterRequest`, `AdapterResponse`, `AdapterToolUse`, `Grader`, `GraderContext`,
  `GradeResult`, `GradeLabel`. (Nota: `schemas/__init__.py` YA exporta el schema layer
  —`EvalCase`, `Message`, `MessageRole`, `ConversationInput`, etc.— pero el top-level
  `selfevals` sigue SDK-only.) Documentar en `docs/adapters.md` cómo un consumidor externo
  escribe un adapter contra el paquete público. (#11)
- Barrer comentarios obsoletos ("PR N", "MVP", "dogfood") en archivos tocados.

### Fase 2 — Hojas independientes (cada una S, paralelizables tras 0.3.0)
- **PR-2 #9 TTFT/p95/tokens-per-sec**: poblar `LLMCallSpan.time_to_first_token_ms` /
  `tokens_per_second` desde el stream; percentiles en `optimization/aggregator.py` →
  `guardrails["latency_ms_p95"]` (el `DecisionMatrix` ya lee guardrails por nombre).
- **PR-3 #14 cost model**: `runner/pricing.py` que **modela el esquema real de pricing de
  cada lab** (input/output, cache write/read con multiplicadores, batch, tiers) siguiendo
  la fuente oficial; los labs no exponen API de precios en runtime → modelar el esquema y
  mantener números actualizables. Arreglar bug latente: `recorder._cost_usd` nunca se
  alimenta de `AdapterResponse.cost_usd`.
- **PR-4 #8 sampling/splits/holdout**: `optimization/sampling.py`. Consumir
  `run.sample_strategy`, `SplitAllocation`, `holdout` (hoy definidos, nunca consumidos).
  Excluir `holdout=True` del set de optimización.
- **PR-5 #18 GuardrailGrader**: `graders/guardrail.py` (async). Reglas de contenido
  deterministas (regex/PII/double-value) + lee `GuardrailCheckSpan`. FAIL bloqueante.
- **PR-6 #10 auto-wiring CLI/HTTP + #7 #11**: loader reconoce
  `agent: {type: cli|http|embedded}`. Realiza la vía de consumo externo sin bridge.

### Fase 3 — Breakdown funnel (#3) (M) · PR-7, solo
- `BreakdownNode` recursivo (key/label/score/weight/reason/failure_modes/children),
  JSON-serializable. `breakdown: BreakdownNode | None` en `GradeResult`; `breakdown: dict`
  en el `GraderResult` persistible (bump `TRACE_SCHEMA_VERSION`). Aditivo: top-level
  label/score siguen autoritativos.
- Rollup por `key` en aggregator → `IterationAggregate.funnel`. Render en
  `reporter/markdown.py` + `compare.py`. **Habilita el funnel drill-down del frontend.**

### Fase 4 — Graders funnel-aware/diagnósticos (cada uno M, paralelizables tras PR-7)
- **PR-8 #4 TrajectoryGrader**: **califica output-STATE para pass/fail; la trayectoria es
  DIAGNÓSTICA.** Camina `trace.spans` en orden, emite failure modes (`wrong_tool_order`,
  `tool_loop_overrun`, `missing_routing_decision`, `redundant_retrieval`) como children
  `weight=0` (advisory) — **no flipean el label**. Gate duro opcional vía `hard_invariants`
  (forbidden_tools, max_tool_calls). Sin cambio de schema (span model ya es rico).
- **PR-9 #12 researcher-evaluable**: `ArtifactCompletenessGrader` lee
  `response.structured_output` contra `expected.output_schema` + nuevo
  `expected.required_sections`. Calidad = reusar `LLMJudgeGrader`.
- **PR-10 #17 panel de jueces**: `JudgePanelGrader` (consenso majority/unanimous/weighted),
  counterfactual variance (paráfrasis, link `TraceLink(kind="paraphrase_variant")`), human
  spot-check sampling (emite `Annotation` stubs). Consume `JudgeDefenses` de `experiment.py`
  (definido, nunca consumido). Reusa `graders/calibration.py`.

### Fase 5 — Multi-turn executor (#2) (L) · PR-11, solo
- `runner/multiturn.py` (`MultiTurnExecutor`, sobre el executor async): por cada turno user
  → history → `invoke` → graba **un Trace por turno** compartiendo `thread_id` +
  `thread_position` (ya existen). Grading por turno + agregado; per-turn → `breakdown`
  children `turn_{i}` (reusa #3). El loop despacha a `MultiTurnExecutor` cuando el caso
  `is_conversation()`.

### Fase 6 — Simulador de usuario (#15) (L) · PR-12, solo
- `runner/simulator.py` (`UserSimulator`): es él mismo un `AgentAdapter` LLM que genera el
  siguiente turno user dada la conversación. Interleave con `MultiTurnExecutor`. `SimulatorSpec`
  (persona, goal, success_criteria, stop_condition, max_turns) vive en `case.input` bajo
  `simulator:`. Turnos del simulador tagueados `{"role":"user_simulator"}` para que
  trajectory/cost los excluyan del SUT. Probar con `example_pingpong` (scripted, sin key).

### Fase 7 — LLM proposer (#6) (S–M) · PR-13
- `LLMProposer` lee `ProposerContext` (failure_modes ya computados + `HypothesisRecord`
  no consumidos de `analysis/`) y emite `Proposal` (ya tiene `hypothesis` + `confidence`).
  Modo determinista "aplica siguiente hipótesis" cuando no hay LLM (testeable offline).
- Validator `experiment.py`: añadir `LLM_PROPOSER` al set permitido; seguir rechazando
  `bayesian`/`bandit`/`evolutionary`.

---

## Orden de PRs y esfuerzo

| PR | Capacidad | Esfuerzo | Depende de |
|----|-----------|----------|-----------|
| 0.3.0 | #1 async + #2 schema | M+ | ✅ mergeado (b9180cd) |
| PR-0 | docs + superficie pública (#11) | S | 0.3.0 mergeado |
| PR-2 | #9 TTFT/percentiles | S | 0.3.0 |
| PR-3 | #14 cost model | M | 0.3.0 |
| PR-4 | #8 sampling/splits/holdout | S | 0.3.0 |
| PR-5 | #18 GuardrailGrader | S | 0.3.0 |
| PR-6 | #10 auto-wiring + #7/#11 | S | 0.3.0 |
| PR-7 | #3 breakdown funnel | M | 0.3.0 (solo) |
| PR-8 | #4 TrajectoryGrader | M | PR-7 |
| PR-9 | #12 researcher graders | M | PR-7 |
| PR-10 | #17 panel jueces | M | PR-7 |
| PR-11 | #2 multi-turn executor | L | 0.3.0, PR-7 (solo) |
| PR-12 | #15 UserSimulator | L | PR-11 (solo) |
| PR-13 | #6 LLM proposer | S–M | 0.3.0, mejor tras PR-8/PR-5 |

## Verificación (transversal por PR)

Suite base 559 / full 597 (`uv run pytest`). Gate por PR: suite verde + los 3 examples
corren en fake mode (sin API key — CI no tiene keys). Tests nuevos por capacidad documentados
en cada PR (ver dirs `tests/runner/`, `tests/graders/`, `tests/optimization/`,
`tests/schemas/`).
