# selfevals → framework 100x de QA + iteración para seals

> **ARCHIVADO (2026-05-27).** Documento histórico, escrito alrededor de un
> consumidor concreto. Reemplazado por el roadmap agnóstico en
> [`../ROADMAP.md`](../ROADMAP.md). Se conserva solo como referencia; no refleja
> el estado actual ni el contrato público del paquete.

> Lista exhaustiva de **todo lo que le falta a selfevals** para evaluar e iterar
> sobre **toda** la superficie de seals: graph (pilot/sales/search/identify/
> extractor/action/copywriter), **researcher**, **voice (Retell)**, **copilot
> (tool selection)** — más lo que venga.
>
> Cada gap tiene: qué falta, por qué importa para seals, dónde engancha (file
> path real), esfuerzo (S/M/L/XL) y orden de implementación.
>
> Estado base: selfevals **v0.2.2**. Fecha: 2026-05-27.

---

## 0. Marco de la decisión

selfevals hoy es un **optimizador de barridos de parámetros sobre suites
estáticas single-shot**: corre N casos por un adapter **síncrono**, los califica
con un `GradeResult` **plano**, propone el siguiente set de params (grid/random/
manual) y decide keep/reject. Eso es sólido y funciona end-to-end.

seals es lo contrario de "single-shot estático": grafos **async** (LangGraph),
conversaciones **multi-turno** con ground-truth por turno, un agente
**researcher** con loop de tools sin límite, **voice** en tiempo real (latencia,
barge-in, TTFT) y **copilot** cuya calidad es la *secuencia* de tools, no el
output final.

El veredicto de la sesión anterior se mantiene: **selfevals = capa de
orquestación de experimentos; seals single_step/ = motor de ejecución + scoring**.
Pero para que selfevals sea "100x" hay que cerrar los gaps de abajo. La mayoría
**no son del adapter de seals** — son capacidades que faltan en el core de
selfevals.

### Resumen de severidad

| # | Gap | Severidad | Esfuerzo | Bloquea |
|---|-----|-----------|----------|---------|
| 1 | Ejecución **async** + paralelismo real | 🔴 CRÍTICO | M | TODO seals (graph es async) |
| 2 | **Multi-turno** real (replay turn-by-turn + usuario simulado) | 🔴 CRÍTICO | L | sales, special_orders, voice |
| 3 | `GradeResult.breakdown` (**funnels** multi-nivel) | 🟠 ALTO | M | reusar evaluadores de seals sin perder drill-down |
| 4 | **TrajectoryGrader** (orden de tools / routing) | 🟠 ALTO | M | copilot tool-selection, researcher |
| 5 | **Voice**: async streaming + latencia/TTFT/barge-in | 🔴 CRÍTICO | XL | voice (dominio entero sin runtime) |
| 6 | **LLM proposer** (agente propone el siguiente prompt) | 🟠 ALTO | M | iteración "self-improving" de verdad |
| 7 | **Inyección de prompt/params** end-to-end hasta el grafo | 🟠 ALTO | M | iterar prompts sin tocar `src/core/graph/**` |
| 8 | **Sampling estratificado** + splits capability/regression enforced + holdout | 🟡 MEDIO | S | reproducibilidad, sub-experimentos rápidos |
| 9 | **TTFT / p95 latency / tokens-per-sec** computados (no stub) | 🟡 MEDIO | S | voice + guardrails de latencia |
| 10 | **Auto-wiring** de adapters CLI/HTTP desde YAML | 🟢 BAJO | S | DX (hoy requiere Python entrypoint) |
| 11 | **Puente seals↔selfevals** (EmbeddedAdapter + bridge async, mapeo `eval.*`↔SQLite) | 🟠 ALTO | M | la integración en sí |
| 12 | **Researcher como evaluable** (artifacts como side-effect, sin interrupts) | 🟠 ALTO | M | evaluar researcher |
| 13 | **Code-editing proposer** (`agent_loop`: editar tool_code/graph/skills) | 🟡 MEDIO | XL | iterar más allá del prompt |
| 14 | **Cost model** real por proveedor/modelo | 🟡 MEDIO | S | guardrails de costo confiables |
| 15 | **Simulación conversacional** (LLM-as-user multi-turno) | 🟠 ALTO | L | voice + flujos largos sin datos reales |
| 16 | **Online eval / production traces** (muestreo de prod → cases) | 🟡 MEDIO | M | dataset production-derived |
| 17 | **Panel de jueces / counterfactuals / calibración** (anti judge-hacking) | 🟡 MEDIO | M | confiar en LLMJudge para taste (copywriter) |
| 18 | **Guardrails como evaluadores de primera clase** | 🟡 MEDIO | S | precios dobles, fuga de RFC, PII |
| 19 | **Web UI / `selfevals serve`** (dashboards, drill-down, "tu LangSmith") | 🟡 MEDIO | XL | visibilidad total (la visión del framework) |
| 20 | **CI / regression gate** (PR hook, baseline tracking) | 🟡 MEDIO | M | "100% regression no baja nunca" |

🔴 = bloqueante duro · 🟠 = necesario para el valor real · 🟡 = importante · 🟢 = DX

---

## 1. 🔴 Ejecución async + paralelismo real

**Qué falta.** `AgentAdapter.invoke` es `def` síncrono
(`runner/adapters.py:91`). Los 3 adapters usan llamadas bloqueantes
(`subprocess.run`, `urllib.urlopen`). `Executor.run_case` corre repeticiones en
un `for` secuencial (`runner/executor.py:92-123`). El campo
`Experiment.run.parallelism` existe en el schema (`experiment.py:141`) pero
**nunca se consume**.

**Por qué importa para seals.** `run_graph` es `async def`
(`src/core/graph/invoke.py:106`) y usa `graph.astream(...)`. **Todo** seal —
pilot, sales, search, researcher, voice — es async. Hoy tendrías que envolver
cada caso en `asyncio.run()` dentro de un adapter sync, perdiendo el paralelismo
que ya tiene tu `run_cases` (`asyncio.Semaphore(max_concurrency)`).

**Dónde engancha.** Añadir `async def invoke_async` al ABC (default: ejecuta
`invoke` en `asyncio.to_thread`), y un `AsyncExecutor` que respete
`run.parallelism` con un `Semaphore`. El loop de optimización
(`optimization/loop.py`) llama al executor → hacerlo async-aware.

**Esfuerzo: M.** Es refactor del executor + adapter ABC, no reescritura total.

---

## 2. 🔴 Multi-turno real (replay turn-by-turn + estado entre turnos)

**Qué falta.** `EvalCase.input` es `dict[str, Any]` opaco
(`schemas/eval_case.py:131`). Existe `RunInfo.thread_id` / `thread_position`
(`schemas/trace.py:52-61`) pero **solo para ensamblar traces post-hoc**: el
executor corre el caso **una vez**, no reproduce turno por turno. No hay
`thread_id`/checkpointer que se propague entre turnos, ni usuario simulado.

**Por qué importa para seals.** Tus datasets que valen oro son
**conversaciones completas** cliente↔Vitau (los 151+48 scenarios). `sales`,
`special_orders`, `confirm_order_permission` tienen **ground-truth por turno**
(`data_providers/types.py:36` — `turn: int`). Evaluar un solo turno no captura
el flujo. `run_graph` ya soporta resumir desde interrupts
(`Command(resume=...)`, keyed por `interrupt_id`) — el framework debe poder
manejar eso.

**Dónde engancha.** Un `ConversationEvalCase` (o convención sobre `input`) con
`turns: list[Turn]`, y un `MultiTurnExecutor` que: (a) mantenga un `thread_id`
estable, (b) por cada turno human → invoque el agente → capture el trace del
turno, (c) en interrupts, reanude con el siguiente turno. Cada turno = un Trace
que comparte `thread_id`; el grading puede ser por-turno y agregado.

**Esfuerzo: L.** Nuevo paradigma de ejecución. Es el gap que más desbloquea.

---

## 3. 🟠 `GradeResult.breakdown` — funnels multi-nivel

**Qué falta.** `GradeResult` es plano: `label/score/reason/failure_modes/details`
(`graders/base.py:30-38`). El propio `STATUS.md:51-54` dice que el `breakdown`
funnel-style está **deferred hasta que el dogfooding de seals fije la forma**.

**Por qué importa para seals.** Tus evaluadores producen `FunnelResult`
multi-nivel (p.ej. search: `product_finder → product_resolver`; special_orders:
8 niveles, uno por componente). El FE de seals ya consume esa forma. Si lo
colapsas a un `score` plano pierdes el drill-down por nivel que es justo lo que
te dice *dónde* se cae el flujo.

**Dónde engancha.** Añadir `breakdown: list[FunnelLevel] | None` a `GradeResult`
(`graders/base.py`) y a `GraderResult` del trace (`schemas/trace.py:315-325`).
Que el aggregator (`optimization/aggregator.py`) y el reporter
(`reporter/markdown.py`) sepan renderizarlo. El adapter de seals mapea
`EvalResult.funnel` → ese `breakdown` directamente.

**Esfuerzo: M.** Es aditivo (campo opcional) pero toca grader + aggregator +
reporter.

---

## 4. 🟠 TrajectoryGrader — calificar el camino, no solo el output

**Qué falta.** El span model es rico (`AGENT_TURN, LLM_CALL, TOOL_CALL,
RETRIEVAL, MEMORY_READ/WRITE, DECISION, HANDOFF, GUARDRAIL_CHECK, ERROR`,
`schemas/trace.py:298-312`) y los tool calls se enlazan por `tool_use_id`. Pero
**no hay grader de trayectoria**: los graders solo leen output final
(`must_include`, tools como *set* no como *secuencia*).

**Por qué importa para seals.** **Copilot** se define por *qué tools elige y en
qué orden* (`copilot_tool_selection` está mencionado en el eval skill; tools:
`modify_suggestions, overwrite_product_price/quantity, product/quote_discount,
remove_product`). El **researcher** depende del orden `search → scrape →
register`. Evaluar solo el output no captura "llamó `remove_product` antes de
`quote_discount`".

**Dónde engancha.** Un `TrajectoryGrader(Grader)` que inspeccione
`trace.spans`: orden de tool calls, presencia/ausencia, longitud del loop
(detectar over-calling), backtracking. Failure modes nuevos:
`wrong_tool_order`, `tool_loop_overrun`, `missing_routing_decision`.

**Esfuerzo: M.** El span model ya lo soporta; falta el evaluador.

---

## 5. 🔴 Voice: async streaming + latencia/TTFT/barge-in

**Qué falta.** **Dominio entero sin runtime.** Existen `Modality.AUDIO/VOICE`
(`schemas/enums.py:321-322`) y nada más. No hay streaming, ni latencia en
tiempo real, ni barge-in, ni integración con Retell. `STATUS.md` **no menciona
voice**.

**Por qué importa para seals.** `src/core/voice/` es un agente Retell por
WebSocket (Custom LLM protocol), single-agentic-node, con 10 tools
(`identify_customer, browse_catalog, add_to_cart, create_quote, end_call`, ...).
Lo crítico de voice **no es accuracy de texto** sino: **time-to-first-token**
(<500ms), **barge-in** (interrumpir al agente), **latencia por turno**, calidad
TTS, robustez ante reconexión WebSocket.

**Dónde engancha (en capas):**
1. **Métricas** (ver gap #9): poblar `LLMCallSpan.time_to_first_token_ms`
   (`schemas/trace.py:202`) — hoy nunca se llena.
2. **VoiceTraceImporter**: ingerir métricas de la sesión Retell (timestamps de
   turno, TTFT, eventos de barge-in) como spans.
3. **VoiceMetrics**: latencia p50/p95/p99 por turno, tasa de barge-in,
   tiempo-a-respuesta.
4. **Modo de eval offline**: replay de transcripts de llamadas reales contra el
   voice agent (sin audio real) para accuracy de tools/decisiones; el audio/TTS
   se mide por separado con métricas de la sesión.

**Esfuerzo: XL.** Es el dominio más nuevo. Empezar por offline (replay de
transcript + métricas de la sesión Retell), dejar barge-in en vivo para después.

---

## 6. 🟠 LLM proposer — el agente propone el siguiente prompt

**Qué falta.** Solo `manual/grid/random` (`optimization/proposers.py:33-203`).
El enum `LLM_PROPOSER` existe pero el validator lo **rechaza en schema-time**
(`experiment.py:334-347`). No hay proposer que lea failure modes y genere
hipótesis.

**Por qué importa para seals.** El `evals_framework.md` pide explícitamente que
el experimento "formule hipótesis y corra variando los parámetros de acuerdo a
la información obtenida". Con grid solo barres un producto cartesiano ciego. Un
LLM proposer cierra el loop: lee la taxonomía de failure modes del error-analysis
(gap #4/#10) y propone el siguiente system prompt dirigido a la debilidad.

**Dónde engancha.** `LLMProposer(Proposer)` que recibe `ProposerContext.history`
(iteraciones previas + métricas + failure_modes) y, vía un adapter de juez/
proposer, emite un `Proposal` con `parameters` + `hypothesis`. Quitar el reject
en el validator.

**Esfuerzo: M.** El contrato `Proposal` ya incluye `hypothesis` y
`confidence`; falta el proposer concreto.

---

## 7. 🟠 Inyección de prompt/params hasta el grafo (sin tocar `src/core/graph/**`)

**Qué falta.** La plumbing existe: `Proposal.parameters` → `Executor.run_case(
parameter_overrides=...)` → `AdapterRequest.parameters`
(`runner/executor.py:145-153`). Pero **el adapter debe consumirlos
activamente** — no hay sustitución mágica de prompt.

**Por qué importa para seals.** Para iterar el system prompt de un nodo (p.ej.
`categorize_conversation`, `customer_resolver`) necesitas que un
`parameters: {system_prompt: "..."}` llegue al grafo. Pero la regla dura del PR
de seals es **cero cambios en `src/core/graph/**`**. Entonces el override no
puede editar el nodo: tiene que inyectarse vía `EvalContext`/`graph_context` o un
mecanismo de override de prompt que el grafo lea.

**Dónde engancha.** En el **bridge de seals** (gap #11): el adapter traduce
`request.parameters` → un override que `_invoke_isolated_node` mete en el
`graph_context`. Requiere que seals exponga un punto de override de prompt por
nodo (probablemente un dict `prompt_overrides` en el contexto que los nodos ya
consulten, o un wrapper de `DolphinPrompt`).

**Esfuerzo: M** (selfevals) **+ S** (seals: punto de override). Es el detalle
fino que hace que "iterar el prompt" sea real y no solo schema.

---

## 8. 🟡 Sampling estratificado + splits enforced + holdout

**Qué falta.** `RunSpec.sample_strategy` acepta `full|stratified|random_subset`
pero **nunca se consume**. `SplitAllocation` (`schemas/dataset.py:21-45`) es
metadata, no se hace cumplir. `EvalCase.holdout` existe pero el filtro no es
automático.

**Por qué importa para seals.** El `evals_framework.md` pide **sub-experimentos
con sub-datasets para hipótesis rápidas**. Seals ya tiene sampling determinista
(`md5(id||seed)`) y splits capability/regression en su loader — selfevals debería
igualarlo para que los experimentos sean reproducibles y baratos de iterar.

**Dónde engancha.** Implementar el sampling en el executor/loader según
`sample_strategy` + `seed`; filtrar `holdout=True` del set de optimización
automáticamente; rechazar promediar capability con regression.

**Esfuerzo: S.** Lógica acotada; seals ya tiene la referencia.

---

## 9. 🟡 TTFT / p95 latency / tokens-per-sec computados (no stub)

**Qué falta.** `LLMCallSpan.time_to_first_token_ms` y `tokens_per_second`
existen (`schemas/trace.py:202-203`) pero **nunca se pueblan**. El aggregator
solo computa media por caso (`aggregator.py:170`), no percentiles. `retries` /
`recovery_events` son schema-only.

**Por qué importa para seals.** Latencia es **métrica de primera clase** para
voice (gap #5) y un guardrail razonable para texto. p95 importa más que la media
para UX.

**Dónde engancha.** Poblar TTFT desde el primer chunk del stream en el
recorder/importer; computar p50/p95/p99 en el aggregator; exponerlos como
guardrails (`latency_p95_ms <= X`).

**Esfuerzo: S.** Los campos existen; falta poblarlos y agregar percentiles.

---

## 10. 🟢 Auto-wiring de adapters CLI/HTTP desde YAML

**Qué falta.** Solo `EmbeddedAdapter` se auto-wirea desde YAML
(`repo/loader.py:80-276`). CLI/HTTP requieren un Python entrypoint
(`STATUS.md:63-68`).

**Por qué importa para seals.** Si quieres correr seals como **servicio HTTP**
(aislar el proceso del agente) en vez de embebido, hoy necesitas código. DX.

**Dónde engancha.** Extender el loader para reconocer
`agent: { http: { url, headers, timeout } }` y `{ cli: { command } }`.

**Esfuerzo: S.**

---

## 11. 🟠 Puente seals ↔ selfevals (la integración)

**Qué falta.** No existe. Es el código de pegamento.

**Por qué importa.** Es *cómo* selfevals llama a seals. El bridge: implementa un
`EmbeddedAdapter` que (a) traduce `AdapterRequest` → input + overrides de seals,
(b) corre `run_cases`/`run_graph` async (vía el async path del gap #1, o
`asyncio.run` interino), (c) traduce `EvalResult.funnel` → `GradeResult`
(`breakdown` del gap #3), (d) guarda el `run_id` de seals (`eval.runs`) en el
trace de selfevals para cruzar ambas persistencias.

**Frontera de persistencia recomendada:** selfevals = source-of-truth del
**experimento** (iteraciones, decisiones, sweep, taxonomía de errores); seals =
source-of-truth de **runs/cases** (`eval.runs`, `eval.run_results`). Cruzados
por `run_id`.

**Dónde vive.** En seals:
`src/core/playground/evals/selfevals_bridge/adapter.py`. No toca
`src/core/graph/**`.

**Esfuerzo: M.**

---

## 12. 🟠 Researcher como evaluable

**Qué falta.** selfevals asume "input → output". El researcher
(`src/core/researcher/agent.py:build_researcher_graph`) acumula **artifacts como
side-effect en `state["artifacts"]`** (CompanyArtifact/ProductArtifact/
CustomerArtifact/FileOutput), no devuelve un output limpio, y corre un **loop de
tools sin interrupt** hasta FINAL_ANSWER o max iteraciones (9 tools: web search/
scrape, register company/product/customer, generate_file, compress, ask_user).

**Por qué importa.** Para evaluar researcher hay que: leer artifacts del state,
medir cantidad/calidad (¿registró 1 company + 10 products + 10 customers?),
medir over-calling de search/scrape (trajectory, gap #4), y juzgar calidad de
artifacts (LLMJudge).

**Dónde engancha.** El bridge (gap #11) debe poder devolver
`structured_output = state["artifacts"]` y `tool_uses` para el TrajectoryGrader.
Un grader de "completeness de research" (determinista: counts) + uno de calidad
(LLMJudge sobre los artifacts).

**Esfuerzo: M.**

---

## 13. 🟡 Code-editing proposer (`agent_loop`)

**Qué falta.** El schema tiene `EditableContract.tool_code/workflow_graph/skills`
y `Proposal.code_changes: list[CodeDiff]` (`schemas/iteration.py:58-75`), pero
**ningún proposer escribe código** y el loop solo persiste los diffs, no los
aplica.

**Por qué importa para seals.** El `evals_framework.md` quiere iterar **el
grafo, el workflow, las tools, las skills** — no solo el prompt. Eso es
`agent_loop` mode: un agente propone cambios de código (juntar nodos, añadir/
quitar tools, simplificar workflow), se aplican en un worktree, se re-corre.

**Dónde engancha.** Un `AgentLoopProposer` que emita `CodeDiff`, un aplicador en
worktree aislado, y gates (tests + quality gates) antes de re-correr. Es la
pieza más ambiciosa.

**Esfuerzo: XL.** Dejar para después de que el loop de prompt funcione.

---

## 14. 🟡 Cost model real por proveedor/modelo

**Qué falta.** El cost se suma desde `Trace.metrics.total_cost_usd`, pero quien
lo llena es el adapter; no hay tabla de precios por modelo. TTFT/tokens-per-sec
sin poblar (gap #9).

**Por qué importa.** Guardrails de costo (`cost_usd <= 0.05`) solo son
confiables si el costo se calcula bien. seals usa varios modelos (voice_llm de
bajo reasoning, modelos de copywriter, jueces).

**Dónde engancha.** Una tabla `model → ($/1M input, $/1M output, cache rates)` y
cálculo desde el `TokenBreakdown` (que sí está completo y validado).

**Esfuerzo: S.**

---

## 15. 🟠 Simulación conversacional (LLM-as-user)

**Qué falta.** No hay usuario simulado ni `SimulatorAdapter`.

**Por qué importa.** Para voice y flujos largos no siempre tienes el transcript
completo; un **usuario simulado** (LLM con un goal/persona) genera el siguiente
turno dinámicamente, permitiendo evaluar robustez sin datos reales. El
`evals_framework.md` lista "simulaciones conversacionales" como tipo de dataset.

**Dónde engancha.** Sobre el multi-turn executor (gap #2): un `UserSimulator`
(adapter LLM con persona + goal + criterio de éxito) que produce turnos human
hasta que se cumple el goal o se agotan los turnos.

**Esfuerzo: L.** Depende de #2.

---

## 16. 🟡 Online eval / production traces → cases

**Qué falta.** Los datasets son JSONL estático. `CaseSource.PRODUCTION_TRACE`
existe en seals pero selfevals no muestrea prod.

**Por qué importa.** El `evals_framework.md` quiere "production-derived dataset:
traces reales anonimizados". seals ya tiene scenarios derivados de Chatwoot prod;
el loop ideal muestrea fallos de prod → los convierte en cases → los añade al
dataset de regresión.

**Dónde engancha.** Un importer de traces de prod (vía el event system de seals,
`src/core/event/postgres.py`) → `EvalCase` con `source=production_trace`. Ligado
al error-analysis (gap #4/#10).

**Esfuerzo: M.**

---

## 17. 🟡 Panel de jueces / counterfactuals / calibración (anti judge-hacking)

**Qué falta.** Un solo juez (`LLMJudgeGrader`). El `GraderCard` schema es
"panel-ready" pero no hay panel ni counterfactuals ni human spot-check
implementados (`judge_defenses` en `experiment.py` es schema).

**Por qué importa para seals.** El copywriter/human_writer se juzga por **taste**
(empatía, tono, no precios dobles). Un solo juez LLM es hackeable. seals ya tiene
un *council* de 7 jueces en `human_writer/council.py` — selfevals debería poder
orquestar paneles y calibrar contra labels humanos (el `evals_framework.md` pide
RLHF/human feedback para taste).

**Dónde engancha.** Panel de N jueces con consenso/voto; counterfactuals
(parafraseo del input, varianza de score acotada); human spot-check sampling;
calibración (precision/recall/F1 — ya existe en `graders/calibration.py`).

**Esfuerzo: M.**

---

## 18. 🟡 Guardrails como evaluadores de primera clase

**Qué falta.** En selfevals los guardrails son thresholds de métricas
(cost/latency). No hay evaluador de guardrails de *contenido*.

**Por qué importa para seals.** seals tiene reglas duras de seguridad (detectadas
hoy solo dentro del human_writer eval): **dos precios en un mensaje (SEVERE)**,
**fuga de RFC/tax-id (SEVERE)**, duplicación de PDF. Esos deben ser guardrails
bloqueantes de primera clase, no un sub-check enterrado.

**Dónde engancha.** Un `GuardrailGrader` (determinista: regex de RFC, conteo de
precios) que emita `GradeLabel.FAIL` bloqueante + failure modes
`double_price`, `pii_leak`. Conectado al `GUARDRAIL_CHECK` span.

**Esfuerzo: S.**

---

## 19. 🟡 Web UI / `selfevals serve` ("tu propio LangSmith")

**Qué falta.** API HTTP es read-mostly (solo POST de workspace). SSE de traces
en vivo **sí funciona** (`api/sse.py`). Web SvelteKit scaffolded pero incompleto.
`selfevals serve` **no existe** (`STATUS.md:86`).

**Por qué importa.** El `evals_framework.md` pide explícitamente "desarrollar
nuestro propio LangSmith": control de runs, debug detallado, drill-down de qué
hace el agente, versionado de datasets/testcases, latencia/TTFT/barge-in,
visibilidad total. Es la cara visible del framework.

**Dónde engancha.** Completar el web UI sobre los endpoints + SSE existentes;
añadir `cmd_serve` que levante API + UI; vistas: funnel drill-down (gap #3),
trajectory viewer (gap #4), comparación A/B de iteraciones, dashboard de failure
modes.

**Esfuerzo: XL.** Alto valor pero no bloquea el loop CLI. Hacerlo después.

---

## 20. 🟡 CI / regression gate (PR hook + baseline tracking)

**Qué falta.** No hay hook de CI ni tracking de baseline entre runs (el
comparador A/B de seals existe, pero no un gate automático).

**Por qué importa.** Regla dura: **regression nunca baja de 100%**. Un PR que
toca un prompt debería disparar el subset de regresión y fallar el CI si baja.

**Dónde engancha.** Un comando `selfevals run --gate regression` que use el
comparador (seals `compare_runs` o el de selfevals) vs un baseline guardado, y
exit code ≠ 0 si hay regresión. GitHub Action.

**Esfuerzo: M.**

---

## Plan de implementación (orden recomendado)

### Fase 0 — Desbloquear el loop básico contra seals (lo crítico)
1. **Gap #1** async executor + paralelismo (M) — sin esto nada de seals corre bien.
2. **Gap #3** `GradeResult.breakdown` funnels (M) — para reusar evaluadores de seals.
3. **Gap #11** bridge seals↔selfevals EmbeddedAdapter (M) — la integración.
4. **Gap #7** inyección de prompt/params hasta el grafo (M+S) — para iterar de verdad.
5. **Primer experimento real**: sweep de prompt de `categorize_message`
   (single-step, barato) → report + DecisionMatrix. Validar end-to-end.

### Fase 1 — Multi-turno + trayectoria (el valor real de seals)
6. **Gap #2** multi-turn executor (L) — desbloquea sales/special_orders/voice.
7. **Gap #4** TrajectoryGrader (M) — copilot + researcher.
8. **Gap #8** sampling estratificado + splits + holdout (S).
9. **Gap #12** researcher evaluable (M).
10. Experimento multi-turno real: `special_orders` end-to-end.

### Fase 2 — Self-improving + confianza en jueces
11. **Gap #6** LLM proposer (M) — el loop "self-improving" de verdad.
12. **Gap #17** panel de jueces + calibración (M) — confiar en taste de copywriter.
13. **Gap #18** GuardrailGrader (S) — precios dobles, PII.
14. **Gap #10** + **#14** auto-wiring HTTP + cost model (S+S) — DX.

### Fase 3 — Voice + producción + visibilidad
15. **Gap #9** TTFT/p95/tokens-per-sec computados (S) — prerequisito de voice.
16. **Gap #5** voice offline (replay transcript + métricas Retell) (XL).
17. **Gap #15** simulación conversacional (L) — voice + flujos largos.
18. **Gap #16** online eval / production traces (M).
19. **Gap #20** CI regression gate (M).

### Fase 4 — Lo ambicioso
20. **Gap #19** web UI / `selfevals serve` (XL) — "tu LangSmith".
21. **Gap #13** code-editing proposer / `agent_loop` (XL) — iterar grafo/tools/skills.

---

## Notas de cierre

- **Lo crítico real son #1 (async) y #2 (multi-turno).** Sin esos dos, selfevals
  no puede tocar la mayoría de seals. Todo lo demás es valor incremental encima.
- **No reescribir el single-step de seals** dentro de selfevals — reusarlo vía el
  bridge (#11). El single-step es el motor; selfevals es el cerebro del experimento.
- **Voice (#5) es el dominio más caro y más nuevo** — atacarlo offline primero
  (replay + métricas de sesión), barge-in en vivo al final.
- selfevals es **tu propio repo** y su roadmap ya dice que se guía por dogfooding
  contra seals — así que cerrar estos gaps *es* su roadmap, no una bifurcación.
