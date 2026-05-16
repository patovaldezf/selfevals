# Operational Spec v0.1 — Cerrando los Gaps del Framework

Este documento ataca los gaps operacionales del framework canónico (`evals_framework.md`). No reescribe el spec; lo extiende con la mecánica que falta para que pase de "blueprint conceptual" a "sistema operable".

Cada sección sigue la estructura: **Suposiciones → Preguntas → Recomendación → Tensiones honestas**.

Honestidad brutal habilitada. Si una decisión tiene fricción real, lo digo. Si no estoy seguro, lo digo. Si recomiendo algo que va a doler, lo digo.

---

## §A. OptimizationLoop — Mecánica Completa

### A.1 Suposiciones declaradas

1. El loop tiene **dos motores intercambiables**: un `handoff` headless (corre solo, típicamente en cloud) y un `agent_loop` (agente externo como Claude Code, Codex, Cursor, OpenClaw, corriendo local con acceso al codebase). Ambos comparten el mismo state machine y los mismos artefactos, pero **tienen capacidades distintas de cambio**.
2. El loop opera sobre **iteraciones**, no sobre tiempo. Una iteración = una propuesta + una ejecución + una decisión.
3. **Capacidad de cambio depende del modo**. La línea divisoria no es "código sí/no" — es "¿requiere acceso al repo del usuario?".

   **`handoff` (cloud headless)** puede tocar todo lo que vive como **dato declarativo** en el spec del experiment:
   - ✅ `prompt` (texto completo del system prompt, secciones, ejemplos few-shot)
   - ✅ `prompt_variables`
   - ✅ `model` (provider, modelo)
   - ✅ `model_params` (reasoning, temperature, verbosity, max_tokens)
   - ✅ `tool_descriptions` (el texto que el LLM ve sobre cada tool)
   - ✅ `tool_params` (parámetros de configuración, no su código)
   - ✅ `retriever_config`, `memory_config` cuando son parámetros
   - ❌ `tool_code` (requiere repo del usuario)
   - ❌ `workflow_graph` cuando implica código (nodos nuevos, edges nuevos)
   - ❌ `skills` cuando implican archivos del repo

   Esto es bastante: prompt search, model comparison y parametric tuning son experimentos enormes que no necesitan tocar el repo. El cloud headless es genuinamente poderoso.

   **`agent_loop` (local con codebase, Claude Code / Codex / Cursor / OpenClaw)** puede cambiar **todo lo anterior MÁS** lo que requiere repo:
   - ✅ Todo lo del handoff
   - ✅ `tool_code` (escribir/refactorizar tools)
   - ✅ `workflow_graph` (agregar/quitar nodos, cambiar edges)
   - ✅ `skills` (crear/editar skills del repo)
   - ✅ Generar diffs/PRs reales, escribir tests, refactorizar

   El Proposer en `agent_loop` puede producir un `Proposal.code_changes` con diffs reales. **Este es el modo 10x del framework**: el agente externo tiene contexto del repo + traces de fallas + métricas del experiment + skills del SDK, y propone cambios estructurales reales.

4. El loop **no es un solver universal**. Es un orquestador de hipótesis. La inteligencia de propuesta vive en una estrategia intercambiable (ver A.4).
5. **Enforcement crítico**: `editable.tool_code: true`, `editable.workflow_graph: true` y `editable.skills: true` solo son legales cuando el experiment declara `mode: agent_loop`. Si los declaras en `mode: handoff`, el framework rechaza el experiment al validar. Cualquier `editable` puramente declarativo (`prompt`, `model`, `model_params`, `tool_descriptions`, etc.) es legal en ambos modos.

### A.2 State machine del Experiment

```text
draft → queued → running → (paused) → completed | aborted | superseded
                                ↓
                          [iteration loop]
                                ↓
                  proposing → executing → evaluating → deciding → recording
```

Estados terminales: `completed`, `aborted`, `superseded`.
Estados transitorios: `draft`, `queued`, `running`, `paused`.

Reglas:
- `draft`: spec editable, no ha corrido nada.
- `queued`: spec frozen, esperando recurso.
- `running`: al menos una iteración ejecutada.
- `paused`: detenido manualmente; estado preservado.
- `completed`: alcanzó `convergence` o `max_iterations`.
- `aborted`: cancelado antes de completar; razón obligatoria.
- `superseded`: un experimento posterior lo invalidó (link explícito al sucesor).

### A.3 Anatomía de una iteración

Cada iteración produce un `IterationRecord`:

```yaml
iteration: 7
parent_iteration: 6
state: completed
proposer:
  type: strategy
  strategy: bayesian_with_priors
  inputs_used:
    - iteration: 6
      reason: best primary so far
    - iteration: 3
      reason: lowest cost variant
hypothesis: |
  Increasing reasoning to high with top_k=5 should improve grounding
  without hitting the latency guardrail because the retrieval set was
  the limiting factor in iteration 6.
proposed_parameters:
  model_params:
    reasoning: high
    temperature: 0.2
  retriever_config:
    top_k: 5
execution:
  variant_id: support_agent_prompt_search_candidate_007
  ran_against:
    optimization: capability/support_policy_v3 (stratified, 120 cases, seed=42)
    gates:
      - regression/support_v7 (full, 180 cases)
  trace_run_ids:
    - run_2026_05_17_abc...
metrics:
  primary:
    weighted_success: 0.913 (baseline 0.871, +0.042)
  guardrails:
    regression_pass_rate: 1.0 (pass)
    p95_latency_ms: 4210 (pass)
    cost_per_task_usd: 0.038 (FAIL, threshold 0.035)
  reliability:
    pass_carat_5: 0.84
    consistency_rate: 0.91
decision:
  outcome: require_tradeoff_review
  rationale: |
    Primary improved meaningfully but cost guardrail breached by 8%.
    Triggered tradeoff_review per decision matrix.
  next_action: spawn_subexperiment
  next_action_payload:
    goal: reduce cost while keeping primary >= 0.91
    editable: { retriever_config: true, model_params: { reasoning: [medium, high] } }
duration_seconds: 612
cost_usd: 4.12
```

### A.4 Estrategias de propuesta (Proposer)

#### A.4.1 Qué es el Proposer

En cada iteración del loop, alguien tiene que decidir **qué variante probar a continuación**. Ese "alguien" es el `Proposer`: el motor de búsqueda. Sin Proposer, el loop no sabe qué probar — solo sabe correr lo que le digan.

El framework no impone UN algoritmo. Define una **interfaz** `Proposer` y permite intercambiar implementaciones (strategies) según el problema, el presupuesto y el modo (`handoff` vs `agent_loop`).

#### A.4.2 Strategies disponibles

| Strategy | Qué hace en cristiano | Cuándo usar | Compatible con |
|---|---|---|---|
| `manual` | Humano escribe cada variante a mano | Debug, exploración inicial, casos donde la intuición humana es mejor que cualquier algoritmo | ambos modos |
| `grid` | Prueba todas las combinaciones posibles del `search_space` | Espacio pequeño y ortogonal (ej. 3 reasoning × 3 top_k × 2 strategies = 18 variantes) | ambos modos |
| `random` | Muestrea valores al azar dentro del `search_space` | Baseline rápido. Sorprendentemente competitivo cuando el espacio es grande | ambos modos |
| `bayesian` | Modela la función "params → métrica" con un Gaussian Process (o similar) y elige el siguiente punto que cree va a mejorar más | Espacio continuo y caro de evaluar (>50 iteraciones potenciales). **Default recomendado para parametric search**. | ambos modos |
| `bandit` | Reparte tráfico/presupuesto entre pocas variantes y va dando más a la que gana | Canary, online evals con tráfico real | `handoff` para online; `agent_loop` raramente |
| `evolutionary` | Toma las N mejores variantes, las cruza/muta para generar la siguiente generación | Espacios discretos grandes (variantes de prompt, combinaciones de tools) | ambos modos |
| `llm_proposer` | Un LLM lee history + traces + failure modes y propone con razonamiento explícito | Cuando hay poco data y la intuición de un modelo grande supera a la búsqueda ciega. Más caro por iteración. | ambos modos, pero **brilla en `agent_loop`** porque ahí puede proponer cambios de código, no solo parámetros |

#### A.4.3 Interfaz canónica

Cada strategy implementa este protocolo:

```python
class Proposer(Protocol):
    def propose(
        self,
        search_space: SearchSpace,
        history: list[IterationRecord],
        budget: Budget,
        mode: Literal["handoff", "agent_loop"],
        repo_context: RepoContext | None,  # Solo poblado en agent_loop
    ) -> Proposal: ...

    def should_stop(self, history: list[IterationRecord]) -> bool: ...


class Proposal(BaseModel):
    parameters: dict          # Valores dentro del search_space
    code_changes: list[Diff] | None  # Solo válido en agent_loop con editable.tool_code/prompt/workflow_graph
    hypothesis: str          # Por qué este cambio debería mover qué métrica
    confidence: float        # 0-1
    inputs_referenced: list[IterationRef]  # Qué iteraciones previas inspiraron esto
```

`code_changes` solo se acepta cuando `mode == "agent_loop"` y el `editable` correspondiente está en `true`. Validación estricta al recibir la proposal.

#### A.4.4 Cómo el Proposer ve los judges (anti-gaming)

**Regla**: el `llm_proposer` (y cualquier Proposer que tenga LLM razonando) NO recibe el texto de las rúbricas de judges bloqueantes. Solo recibe:
- Métricas agregadas (`feature_pass_rate`, `weighted_success`, etc.).
- Failure modes estructurados (cluster_id, count, severity).
- Counts de violación por categoría.

Esto está enforced en la capa de contexto que se le pasa al Proposer. El framework lo loggea cuando aplica este filtro, por auditoría.

Los Proposers no-LLM (grid, random, bayesian) ni siquiera ven texto — solo el espacio numérico/categórico — así que el problema no aplica.

### A.5 Sub-experimentos

Un experimento puede **engendrar sub-experimentos** cuando una decisión es `spawn_subexperiment` o `require_tradeoff_review`. El sub-experimento:

- Hereda `frozen` del padre.
- Reduce el `search_space` al subset relevante.
- Tiene un `parent_experiment_id` y un `triggered_by_iteration`.
- Su resultado puede promover de vuelta al padre, o quedarse independiente.

Esto resuelve la pregunta "datasets grandes con hipótesis rápidas" de las notas históricas (línea 1053).

### A.6 Convergencia y stop

Stop conditions (cualquiera dispara `completed`):
- `max_iterations` alcanzado.
- `convergence.min_delta` no mejorado en `convergence.patience` iteraciones consecutivas.
- `budget.cost_usd_cap` alcanzado.
- `budget.wall_clock_hours_cap` alcanzado.
- Un guardrail bloqueante falló N veces seguidas (default 3).
- Strategy específica retorna `should_stop = True`.

Cada stop registra `stop_reason` explícita en el `Experiment.completion`.

### A.7 Reglas de gobernanza del Proposer

Decisiones tomadas (no son preguntas abiertas, son las reglas que el framework enforcea):

1. **Strategy se declara por experimento**, con un default configurable a nivel workspace. Cada `Experiment.yaml` lleva `proposer.strategy: bayesian` (o el que sea).
2. **Cambiar strategy mid-experiment está prohibido**. Si necesitas cambiar de bayesian a evolutionary, terminas el experimento actual y abres uno nuevo con `parent_experiment_id` apuntando al original. Razón: experimentos con strategy mixta son imposibles de interpretar y rompen reproducibilidad. La trazabilidad parent→child mantiene el linaje claro.
3. **Expansión del `search_space`**: configurable por experimento vía `proposer.allow_search_space_expansion: bool` (default `false`).

   ```yaml
   proposer:
     strategy: llm_proposer
     allow_search_space_expansion: true  # default false
   ```

   - `false`: el search_space es contractual. Cualquier propuesta fuera del rango se rechaza y se loggea como `proposal_rejected: out_of_search_space`. Reproducibilidad máxima.
   - `true`: el Proposer puede emitir `search_space_expansion_request` como parte de su `Proposal`. La expansión queda registrada como diff en el `IterationRecord` correspondiente. Si el experimento corre con HITL, la expansión pasa por aprobación humana; si corre autónomo, se aplica directamente. En cualquier caso queda auditada.

   Útil cuando la creatividad del `llm_proposer` para descubrir parámetros no contemplados vale más que el rigor de un rango cerrado.

4. **El `editable` es contractual**. Si declaraste `tool_code: false`, ningún Proposer (ni siquiera `llm_proposer` con creatividad) puede proponer un diff de tool. La proposal se rechaza al validar y se loggea como `proposal_rejected: editable_violation`. Para cambiar el `editable`, mismo flujo: pausar + edit + reanudar (HITL) o sub-experiment con `parent_experiment_id`.

### A.8 Anti-gaming del Proposer

El `llm_proposer` es la estrategia más sexy y la más peligrosa. Es la que va a tentar a optimizar contra el judge (porque puede leer rúbricas si no se le restringe). **Decisión enforced**: el Proposer NO ve el texto de las rúbricas de judges bloqueantes durante propuesta. Solo ve métricas agregadas y feedback estructurado (clusters de failure modes, counts, severities), no el texto del judge.

Esto está implementado en la capa de contexto: hay un `ProposerContext` que se construye filtrando explícitamente `judge.rubric_text` para judges con `blocking: true`. El framework loggea cada vez que aplica este filtro, para auditoría.

Los Proposers no-LLM (grid, random, bayesian, bandit, evolutionary) operan sobre el espacio numérico/categórico — nunca ven texto de rúbricas — así que el problema no aplica.

---

## §B. Trace — Schema Operacional

### B.1 Suposiciones

1. **Decisión técnica fundamental: OpenTelemetry como wire format, NO como dependencia obligatoria.** El framework define su schema canónico (`Trace`). OpenTelemetry es **una de las formas de transportar** un Trace, no el modelo de datos.
2. Esto significa: cualquier agente que exporte OTel spans puede ser ingestado sin tocar su código. Anthropic SDK, OpenAI SDK, LangChain/LangGraph, LlamaIndex, CrewAI, OpenLLMetry-instrumentado — todos consumibles vía adapter. Agentes que no usen OTel usan nuestro SDK directo.
3. **Naming convention: OpenInference donde aplique.** Donde OpenInference (Arize) o OTel `gen_ai.*` ya definen un atributo (`llm.input_messages`, `llm.token_count.prompt`, `tool.name`, etc.), usamos ese nombre. Donde no existe vocabulario estándar (`evals.eval_case_id`, `evals.iteration`, `decision.alternatives_considered`), usamos prefijo `evals.*`. Esto nos da compatibilidad gratis con Phoenix, Arize y cualquier herramienta que hable OpenInference.
4. **Translation layer en el importer**: acepta `gen_ai.*` (OTel oficial), `openinference.*`, `langsmith.*`, `langfuse.*` y los traduce al schema canónico. La pluralidad vive en el importer; el storage usa una sola representación.
5. El `Trace` es **append-only**. Una vez cerrado, no se edita. Re-runs producen Traces nuevos vinculados al original via `links.replay_of` o `links.parent_trace_id`.

### B.2 Schema canónico

```yaml
trace:
  id: trace_2026_05_17_a4f9...
  schema_version: 1
  run:
    run_id: run_2026_05_17_abc...
    experiment_id: support_agent_prompt_search_2026_05_15
    iteration: 7
    variant_id: support_agent_prompt_search_candidate_007
    eval_case_id: refund_policy_001
    repetition: 3
    seed: 42
  agent:
    fleet_version: support_fleet:2026-05-16
    agent_id: support_agent
    agent_version: v12
    parameters_snapshot_id: params_a4f9...
  environment:
    framework_version: evals-framework:0.1.0
    runtime: offline
    sandbox: dry_run
    tool_mocks: [refund_payment, send_email]
    timestamps:
      started_at: 2026-05-17T14:22:01.123Z
      ended_at: 2026-05-17T14:22:14.892Z
  state:
    status: completed | errored | timeout | aborted
    error: null
  spans:
    - id: span_001
      parent_id: null
      kind: agent_turn
      name: process_user_message
      started_at: 2026-05-17T14:22:01.124Z
      duration_ms: 320
      attributes:
        turn: 0
        message_role: user
        content_hash: sha256:...
    - id: span_002
      parent_id: span_001
      kind: llm_call
      name: anthropic.messages.create
      started_at: 2026-05-17T14:22:01.450Z
      duration_ms: 2104

      # Identidad del modelo
      provider: anthropic
      model: claude-sonnet-4-6
      model_version_pinned: claude-sonnet-4-6-20260301

      # Inputs completos (pointers + hashes)
      system_prompt_pointer: oss://traces/.../span_002_system.txt
      system_prompt_hash: sha256:...
      messages_pointer: oss://traces/.../span_002_messages.json
      messages_hash: sha256:...
      tools_offered:
        - search_policy_docs:v4
        - get_order_status:v2
      tools_offered_hash: sha256:...

      # Parametros de invocacion
      params:
        temperature: 0.2
        max_tokens: 4096
        reasoning: high
        verbosity: medium
        top_p: null
        stop_sequences: []
        response_format: null

      # Reasoning visible (Claude extended thinking, o1, DeepSeek-R1, etc.)
      reasoning:
        available: true
        redacted: false
        summary_pointer: oss://traces/.../span_002_reasoning_summary.txt
        full_pointer: oss://traces/.../span_002_reasoning_full.txt
        thinking_tokens: 1820
        signature: sig_abc123  # Anthropic devuelve esto, necesario para replay

      # Output completo
      output:
        content_pointer: oss://traces/.../span_002_output.json
        content_hash: sha256:...
        stop_reason: tool_use  # end_turn | tool_use | max_tokens | stop_sequence
        tool_use_requested:
          - tool: search_policy_docs
            tool_use_id: toolu_01ABC
            args_pointer: oss://traces/.../span_002_tooluse.json

      # Tokens y costo (breakdown granular)
      tokens:
        input: 1240
        input_cache_read: 800
        input_cache_creation: 0
        output: 412
        reasoning: 1820
        total: 4272
      cost_usd:
        input: 0.00372
        cache_read: 0.00024
        output: 0.00618
        total: 0.01014

      # Performance
      time_to_first_token_ms: 412
      tokens_per_second: 196
      retries: 0
      cache_hit: true

      # Provider-specific raw blob (opaco al framework, util para forensics)
      provider_metadata:
        request_id: req_01ABC
        anthropic_beta: ["prompt-caching-2024-07-31"]
        finish_details: {...}

    - id: span_003
      parent_id: span_001
      kind: tool_call
      name: search_policy_docs
      tool_version: v4
      tool_use_id: toolu_01ABC  # Linkea al llm_call que la solicito
      args_hash: sha256:...
      args_pointer: oss://traces/.../span_003_args.json
      result_pointer: oss://traces/.../span_003_result.json
      result_hash: sha256:...
      duration_ms: 180
      status: ok  # ok | error | timeout
      error: null
      retry_chain: []  # Si hubo retries, cada intento previo aqui
      sandboxed: false
      side_effects:
        wrote_to: []
        external_calls: []

    - id: span_004
      parent_id: span_001
      kind: retrieval
      name: vector_search
      retriever: pgvector
      query_pointer: oss://traces/.../span_004_query.txt
      query_hash: sha256:...
      query_embedding_model: text-embedding-3-large
      top_k_requested: 5
      top_k_returned: 3
      retrieved:
        - doc_id: returns_policy
          doc_version: v2026-05-01
          chunk_id: returns_policy.chunk_4
          raw_score: 0.91
          rerank_score: 0.88
      reranker: null
      grounding_used:
        - returns_policy.chunk_4

    - id: span_005
      parent_id: span_001
      kind: memory_read
      memory_store: gbrain
      keys_requested: [user_preferences, prior_conversation_summary]
      keys_hit: [user_preferences]
      keys_missed: [prior_conversation_summary]
      values_pointer: oss://traces/.../span_005_values.json

    - id: span_006
      parent_id: span_001
      kind: decision
      decision_type: tool_selection
      chosen: search_policy_docs
      alternatives_considered: [get_order_status, refund_payment]
      rationale_pointer: oss://traces/.../span_006.json
      confidence: 0.78
  outputs:
    final_response_pointer: oss://traces/2026/05/17/final.txt
    structured_output:
      cited_policies: [returns_policy]
      escalated: false
  grader_results:
    - grader: policy_compliance_judge:v3
      label: pass
      reason_pointer: oss://traces/2026/05/17/grader_001.json
      confidence: 0.94
    - grader: deterministic_required_citation:v1
      label: pass
  metrics:
    total_tokens_in: 1240
    total_tokens_out: 412
    total_cost_usd: 0.0083
    total_duration_ms: 13769
    tool_call_count: 1
    llm_call_count: 1
    retries: 0
    recovery_events: 0
    loop_detected: false
  links:
    parent_trace_id: null
    related_traces:
      - kind: paraphrase_variant
        trace_id: trace_2026_05_17_xxxx
      - kind: replay_of
        trace_id: null
```

### B.3 Tipos de span soportados

Set cerrado, extensible vía `kind: custom` con `custom_kind` libre:

- `agent_turn`
- `llm_call`
- `tool_call`
- `retrieval`
- `memory_read`
- `memory_write`
- `decision` (selección entre alternativas explícitas)
- `handoff` (entre agentes en una fleet)
- `human_intervention`
- `guardrail_check`
- `error` (recuperable o no)
- `custom`

### B.4 Pointer vs inline

**Decisión**: payloads grandes van a object storage; el span solo tiene `*_pointer` + `*_hash`. Esto mantiene la DB ligera, permite búsqueda eficiente sobre metadatos, y aísla PII en un storage layer con políticas distintas.

Threshold: cualquier campo >4KB se externaliza. Hashes permiten detectar cambios sin leer el contenido.

### B.5 Decisión: OpenTelemetry adapter

**Recomendación firme: OTel como transporte opcional, schema propio como verdad.**

Razones honestas:
- OTel spans no tienen vocabulario para `decision`, `grader_results`, `eval_case_id`. Forzarlo es feo.
- Si usamos OTel directamente, dependemos de su evolución para cambios.
- Pero si exponemos un OTel adapter, capturamos cualquier agente instrumentado sin que cambie su código.

Implementación:
- `framework.trace.ingest_otel(spans: list[OTelSpan]) -> Trace`: importer.
- `framework.trace.export_otel(trace: Trace) -> list[OTelSpan]`: exporter para análisis externo.
- Atributos custom se prefijan `evals.*` (ej. `evals.eval_case_id`).

### B.6 Cómo cada framework se integra

| Framework | Cómo capturamos | Esfuerzo |
|---|---|---|
| Custom Python/TS | SDK directo: `@trace_agent_turn`, `@trace_tool` decorators | Bajo |
| LangGraph | Import callbacks de LangSmith → adapter a Trace | Medio |
| OpenAI Agents SDK | Hook en su `Runner` + traducción a Trace | Medio |
| CrewAI | Subscribe a sus event listeners | Medio |
| OTel-instrumented | Importer OTel → Trace | Bajo (si ya tiene OTel) |
| Black box vía HTTP | Wrapper que graba I/O + heurística para spans | Alto, pierde detalle |

### B.7 Tensión honesta

Capturar traces de un agente "black box" (ngrok approach, ver §F) **pierde detalle interno**: no vemos qué tool consideró pero no llamó, no vemos memory reads, no vemos decisiones internas. Solo vemos I/O del agente.

Esto está bien para production sample y comparación de outcome. Es **insuficiente** para optimización fina de prompts/parámetros. Hay que ser explícitos: **modo black box = evaluación de outcome, no de trajectory profundo.**

### B.8 Frameworks de referencia (qué tomamos de cada uno)

| Framework | Qué hace bien | Qué adoptamos |
|---|---|---|
| **LangSmith** | UI side-by-side comparison, dataset linking | Decision log pattern, run linking |
| **Phoenix (Arize)** | OTel-native + OpenInference conventions | Adoptar nombres de atributos OpenInference donde aplique |
| **Langfuse** | Cost tracking granular por token type, session grouping | Cost breakdown granular (input/cache_read/cache_creation/output/reasoning) |
| **Braintrust** | Eval-first design, scoring estructurado | Linking trace ↔ eval_case ↔ grader_results |
| **Weave (W&B)** | Versioning de prompts/ops inline | Versionado inline de prompts y tools |
| **OpenLLMetry** | Auto-instrumentation de SDKs populares | Adapter para consumir sus spans gratis |
| **Helicone** | Provider-specific metadata preservada | `provider_metadata` blob opaco para forensics |

Campos que casi nadie captura bien y que nosotros sí:

1. **Reasoning blocks completos con signature** — Claude extended thinking, o1, DeepSeek-R1. La signature permite replay y verificación de integridad anti-prompt-injection.
2. **Cache token breakdown** — `input_cache_read` vs `input_cache_creation` vs `input` puro. Diferencia 10x en costo real.
3. **Stop reason explícito** — `end_turn` vs `tool_use` vs `max_tokens` vs `stop_sequence`. Distingue "el modelo decidió terminar" de "se cortó".
4. **`tool_use_id` linkage** — cada `tool_call` apunta al `llm_call` que lo solicitó. Permite reconstruir el árbol de decisiones.
5. **Retrieval con scores raw + rerank scores** — saber si el rerank ayudó o empeoró.
6. **`provider_metadata` opaco** — blob nativo del provider sin parsear. Para debug cuando algo raro pasa.

---

## §C. Defensas Anti-Judge Integradas al Schema

### C.1 Suposiciones

1. Las defensas de §13 (canónico) deben ser **declarables y enforceables**, no buena voluntad.
2. El default debe ser **seguro**: si no declaras nada, el framework aplica defensas mínimas y advierte.

### C.2 Bloque `judge_defenses` en Experiment

```yaml
judge_defenses:
  holdout:
    enabled: true
    dataset: capability/support_policy_v3
    holdout_fraction: 0.2
    holdout_seed: 42
    holdout_visible_to_proposer: false
    promotion_rule: |
      Candidate is only promoted if holdout primary delta >= 80% of
      optimization primary delta. Otherwise flagged as suspected overfit.

  judge_panel:
    enabled: true
    rationale: |
      Panel diverso para reducir sesgo de modelo, proveedor y prompt.
      No es rotacion: TODOS los judges del panel evaluan cada caso,
      y se toma consenso.
    members:
      - judge: policy_compliance_judge:v3
        provider: anthropic
        model: claude-sonnet-4-6
        prompt_variant: strict_rubric
      - judge: policy_compliance_judge_alt:v2
        provider: openai
        model: gpt-5.5
        prompt_variant: principled_reasoning
      - judge: policy_compliance_judge_oss:v1
        provider: deepseek
        model: deepseek-v4
        prompt_variant: terse_checklist
    diversity_requirements:
      min_providers: 2
      min_prompt_variants: 2
      min_panel_size: 3
    consensus:
      rule: majority  # majority | unanimous | weighted
      block_threshold: 2_of_3
      on_disagreement:
        - flag_for_human_spot_check
        - log_for_drift_analysis

  counterfactual_pairs:
    enabled: true
    generation_strategy: paraphrase
    pairs_per_case: 3
    max_score_variance: 0.05
    on_violation: flag_case_as_unstable

  human_spot_check:
    enabled: true
    sample_rate: 0.05
    trigger_on_jump: 0.1
    queue: human_review/policy_qa

  adversarial_optimizer_set:
    enabled: true
    dataset: adversarial_safety/judge_hacking_v1
    on_failure: reject_candidate

  overfit_penalty:
    enabled: true
    capability_vs_holdout_delta_max: 0.05

  outcome_metrics:
    enabled: true
    required_when_runtime_in: [canary, online]
    metrics:
      - human_approval_rate
      - escalation_rate
      - task_completion
```

### C.3 Defaults por runtime

| Runtime | Defensas mínimas obligatorias |
|---|---|
| offline | holdout + overfit_penalty |
| replay | holdout |
| simulation | holdout + counterfactual_pairs |
| shadow | + outcome_metrics |
| canary | + outcome_metrics + human_spot_check |
| online | todas activas |

Si declaras runtime `canary` sin `outcome_metrics`, el framework rechaza el experiment al validar.

### C.4 Tensión honesta

`counterfactual_pairs` con paraphrase generation es caro y ruidoso. Para MVP recomiendo: **disponible pero opt-in**. Default off salvo en safety datasets, donde es default on.

`judge_panel` con 3 judges de proveedores distintos es 3x más caro que un single judge. Para MVP recomiendo: **default off pero infraestructura lista**. Activable por experimento cuando el caso lo amerite (safety, regression critica, judges con historial de drift). Cuando se active, la diversidad obligatoria (mínimo 2 proveedores, mínimo 2 prompt variants, mínimo 3 miembros) está enforced — no se acepta un panel "diverso" de 3 modelos del mismo proveedor con prompts gemelos.

---

## §D. Calibración de Judges — Mecánica Completa

### D.1 Versionado del dataset de calibración

Reglas:
- `calibration/{judge_name}/{version}/`
- Versión = semver: `v2.1.0`.
  - Major (`v3.0.0`): cambio de rúbrica o de etiquetas (no comparable contra v2).
  - Minor (`v2.1.0`): casos nuevos sin cambiar rúbrica.
  - Patch (`v2.0.1`): correcciones de etiquetas existentes.
- Cada versión es **inmutable**. Cambios = nueva versión.
- Cada caso tiene `annotator_ids`, `adjudicated_by`, `disagreement_count`.

### D.2 Ciclo de vida del judge

```text
draft → calibrating → calibrated → in_use → drifting → recalibrating → retired
                                          ↓
                                      shadowing (nueva versión corre paralelo)
```

Triggers:
- `calibrating` → `calibrated`: alcanza thresholds declarados en GraderCard.
- `calibrated` → `in_use`: aprobación humana explícita (rol maintainer).
- `in_use` → `drifting`: monitor de drift dispara alerta.
- `drifting` → `recalibrating`: nueva muestra humana etiquetada, judge recorrido.
- `*` → `retired`: reemplazado por sucesor (link al sucesor obligatorio).

### D.3 Qué pasa cuando un judge cae bajo `min_precision`

**Decisión: dos modos configurables por judge, default conservador.**

```yaml
on_threshold_breach:
  mode: degrade_to_advisory  # vs auto_disable, vs page_human
  grace_period_runs: 50
  notify:
    - slack: #evals-alerts
    - github_issue: true
```

- `degrade_to_advisory` (default): el judge sigue corriendo pero pierde su flag `blocking: true`. Sus resultados quedan en el reporte como "advisory" hasta recalibrar.
- `auto_disable`: el judge se desactiva completamente. Gates que dependen de él fallan abierto (alertando).
- `page_human`: bloquea cualquier release que use el judge hasta intervención.

### D.4 RLHF + Data Labeling como módulo

**Posición**: RLHF en el sentido literal (entrenar modelos con preferencias) está fuera del MVP. **Lo que sí entra en MVP es el módulo de Human Feedback Capture & Labeling**, que es el upstream de cualquier RLHF futuro.

#### D.4.1 Módulo `human_loop`

Responsabilidades:
1. **Queue de revisión humana** con prioridad por riesgo, costo de error y bloqueo activo.
2. **UI de etiquetado** (web) con keyboard shortcuts, rúbrica visible, casos similares.
3. **Inter-annotator agreement tracking** automático cuando >1 annotator etiqueta el mismo caso.
4. **Adjudication workflow** cuando hay disagreement.
5. **Pago / tracking de annotators** (registro mínimo, integración a Stripe Connect o similar fuera de scope MVP).
6. **Exportación a dataset de calibración** o `incident_queue`.

#### D.4.2 Schema de annotation

```yaml
annotation:
  id: ann_2026_05_17_001
  case_id: refund_policy_001
  trace_id: trace_2026_05_17_a4f9...
  annotator_id: user_pato
  annotator_role: domain_expert
  rubric_version: policy_qa_rubric:v3
  started_at: 2026-05-17T14:30:00Z
  submitted_at: 2026-05-17T14:32:18Z
  duration_seconds: 138
  labels:
    primary: fail
    failure_modes:
      - policy_misstated
      - missing_citation
  notes: |
    Agent said "30 days" correctly but failed to cite returns_policy
    explicitly. Borderline.
  confidence: 0.7
  flagged_for_adjudication: true
```

#### D.4.3 Inter-annotator agreement

Métricas obligatorias por rúbrica activa:
- Cohen's kappa para pass/fail.
- Krippendorff's alpha para escalas o multi-clase.
- % de adjudication required.

Si kappa < 0.6 sostenidamente → la rúbrica está rota, no los annotators. Trigger automático: revisión de rúbrica.

### D.5 Tensión honesta

En la operación real Patricio = único annotator. La sección de inter-annotator y pago son **infraestructura para crecer**, no para usar hoy. **Recomiendo implementarlo como interfaz desde día 1 pero correrlo en modo single-annotator** (donde "agreement" se evalúa contra políticas formales en lugar de otro humano).

---

## §E. Storage — Decisión

### E.1 Decisión recomendada

**Postgres + S3-compatible object storage, con YAML/JSONL en repo para specs**. NO Supabase como dependencia core.

Razones:
- Postgres es lingua franca; cualquier provider lo da (Neon, Supabase, RDS, Fly).
- Object storage es trivialmente reemplazable (S3, R2, GCS, MinIO local).
- Supabase es bueno para MVP pero **acoplarte a su SDK te encadena**. En lugar de eso: usar Postgres directo + un object storage interface.
- Para el MVP de Patricio: Postgres local + filesystem como object storage funciona perfectamente.

### E.2 Estructura

| Capa | Qué guarda | Por qué ahí |
|---|---|---|
| Repo YAML/JSONL | DatasetManifest, Experiment specs, GraderCard, AgentFleet, Agent, FeatureRegistry, RiskRegistry, Rubrics | Versionable con git, code-reviewable, PR-friendly |
| Postgres | Runs, IterationRecords, Trace metadata, Annotations, Decision logs, Calibration metrics, Audit log | Queries, joins, agregaciones, search |
| Object storage | Trace payloads grandes, attachments, screenshots, audio, full LLM responses | Tamaño, lifecycle policies, distinct access control |
| Embeddings store (v2) | pgvector dentro de Postgres | Para failure clustering y similar-case search |

### E.3 Decisión: ¿índices?

Postgres con índices en:
- `(experiment_id, iteration)` para iteration lookup.
- `(eval_case_id, agent_version, dataset_version)` para reproducibility queries.
- `(feature_primary)` para feature_coverage.
- `(judge_name, judge_version, label)` para calibration drift.
- GIN sobre `tags` y `metadata->'labels'` para filtrado libre.

### E.4 Tensión honesta

Hay un argumento real para **SQLite + filesystem en MVP de single-user**: cero ops, todo local, snapshots = `cp`. Postgres es overkill para Patricio el día 1.

**Recomendación contraintuitiva**: empezar con **SQLite + filesystem**, con la interfaz abstraída desde el inicio para migrar a Postgres + S3 sin tocar lógica de aplicación. Esto cumple multi-tenant (cada tenant = una DB SQLite) hasta que duela. Cuando duela, migración a Postgres es mecánica.

Si el target son startups con equipos, Postgres desde el inicio. Si el target es Patricio dogfooding + futuro, SQLite primero.

**Pregunta clave para ti**: ¿el MVP debe correr cómodamente en una laptop sin ops, o ya debe ser server-deployable desde día 1?

---

## §F. Race Conditions, Producción y "ngrok approach"

### F.1 Tu intuición: ngrok / connect-your-real-agent

Tu propuesta: el usuario expone su agente vía ngrok (o cualquier endpoint), el framework lo trata como prod sin serlo.

**Decisión: sí, esto hace sentido, pero con tres modos claros.**

| Modo | Qué hace | Quién paga el costo |
|---|---|---|
| `mock` | El agente está mockeado, tools mockeadas | Framework |
| `dry_run` | Agente real, tools peligrosas mockeadas, side effects bloqueados | Framework + costo LLM |
| `live_sandboxed` | Agente real con tools reales, pero sobre data sandbox (cuenta de prueba) | Framework + costo LLM + costo tools |
| `live_canary` | Agente real, tools reales, sobre tráfico de prod limitado | Producción del usuario |

### F.2 Cómo se conecta el agente

Tres opciones soportadas:

1. **SDK-embedded**: el usuario corre el agente dentro de nuestro runner. Máxima visibilidad de traces.
2. **HTTP endpoint** (tu ngrok): el framework hace requests al endpoint. Trace = I/O + grader output. Menos profundidad, pero zero-touch.
3. **CLI command**: framework ejecuta `python run_agent.py --input '...'`. Bueno para batch.

```yaml
agent_adapter:
  kind: http_endpoint
  url: https://agent.ngrok.io/run
  auth:
    type: bearer
    token_secret: ${USER_AGENT_TOKEN}
  timeout_seconds: 60
  trace_extraction:
    mode: response_headers_and_body
    span_format: otel_json
```

### F.3 Concurrencia y aislamiento

Problemas reales:

1. **Dos experimentos canary que tocan el mismo usuario en prod**: solución = **traffic allocation registry** central. Cada experiment reserva su slot (% de tráfico, segmento) y los conflictos se rechazan.
2. **Eval runs que llaman tools reales y modifican estado**: solución = **idempotency keys obligatorios** en tools que muten estado, y modo `dry_run` por default en runtime offline/replay.
3. **Race entre dos iterations del mismo experimento**: solución = **iterations son secuenciales por defecto**. Paralelismo aplica solo a casos dentro de una iteration (`parallelism: 8` corre 8 cases en paralelo, no 8 iterations).

### F.4 Tensión honesta

**El ngrok approach está bien pero tiene un problema serio: latencia y costo del round-trip.** Si tu eval corre 500 casos × 5 repetitions = 2500 calls al endpoint, y cada call es 3s sobre internet, son 2 horas mínimas. Para iterar rápido, el modo embedded gana siempre.

**Recomendación**: ngrok como modo `live_sandboxed` para validación final / drift detection. Para optimization loop intensivo, exigir SDK-embedded o CLI con caching local.

---

## §G. Output del Framework — Decision Log y Reportes

### G.1 Decision Log: schema

Cada decisión (keep, reject, revert, flag, investigate, spawn_subexperiment) genera un `DecisionRecord`:

```yaml
decision:
  id: dec_2026_05_17_001
  experiment_id: support_agent_prompt_search_2026_05_15
  iteration: 7
  variant_id: support_agent_prompt_search_candidate_007
  outcome: require_tradeoff_review
  rationale:
    automated: |
      Primary improved +4.2pts but cost guardrail breached.
      Decision matrix rule: require_tradeoff_review.
    human:
      decided_by: user_pato
      decided_at: 2026-05-17T16:00:00Z
      notes: |
        Accept tradeoff: $0.003 extra per task is worth the quality gain
        for the policy_qa segment. Track cost weekly.
      override_of_automated: false
  metrics_snapshot: ...
  affected_artifacts:
    - support_agent:v12
  next_actions:
    - kind: ship_with_flag
      flag_name: high_reasoning_policy_qa
      rollout: canary_10pct
    - kind: track_metric
      metric: cost_per_task_usd
      cadence: weekly
  superseded_by: null
```

### G.2 Reportes — tres outputs canónicos

1. **Markdown report**: para PR comments, GitHub. Genera diff humano-legible.
2. **HTML report**: para análisis profundo, gráficas, side-by-side.
3. **JSON report**: para integración programática (CI, hooks, otros sistemas).

Los tres salen del mismo `ReportArtifact`. No se mantienen tres formatters distintos.

### G.3 Integraciones de notificación

| Canal | Cuándo | Qué incluye |
|---|---|---|
| GitHub PR comment | Experimento sobre branch | Markdown report + verdict |
| Slack | Decisión humana requerida | Resumen + link al decision log |
| Email | Resúmenes diarios / semanales | Trend report |
| Webhook | Custom integrations | JSON report |

Ninguno es obligatorio. Todos opt-in.

### G.4 Tensión honesta

PR comments se vuelven ruido rápido si cada iteración los manda. **Recomendación**: solo postear cuando el experimento **completa** o cuando dispara una decisión humana, no por iteración.

---

## §H. Cross-Experiment Comparison — Moving Baseline

### H.1 El problema real

En 3 meses, has corrido 30 experimentos. El dataset cambió 5 veces. El judge se recalibró 2 veces. El agente bajó por v12, v13, v14. **¿Cómo comparas experimento de mayo vs agosto?**

### H.2 Recomendación: "Calibrated comparison" con tres niveles

| Nivel | Comparación válida cuando | Cómo |
|---|---|---|
| **Strict** | Mismo dataset version, mismo grader version, mismo fleet/agent versions | Comparación directa de métricas |
| **Calibrated** | Diferentes versiones pero hay overlap de casos | Re-run del candidato sobre la intersección común |
| **Trended** | Versiones completamente distintas | Comparar métricas normalizadas (z-score contra su propio baseline) y mostrar trend, no valor absoluto |

El framework no debe permitir `Strict` cuando las versiones difieren. Debe ofrecer `Calibrated` o `Trended` con label explícito en el reporte.

### H.3 Anchor cases

Mantener un conjunto pequeño (~20-50) de "anchor cases" que **nunca cambian**: misma redacción, misma rúbrica, mismo grader pinned. Estos casos corren en cada experimento y permiten comparación strict longitudinal.

Costo: pequeño. Valor: muy alto. **Recomiendo implementarlo en MVP**.

### H.4 Tensión honesta

Re-runs sobre intersecciones para calibrated comparison son **caros**. No hacerlos rompe comparación. Solución pragmática: **lazy re-run**: solo cuando el usuario explícitamente pide la comparación. Cachear resultados.

---

## §I. Cold Start — Bootstrap del Framework

### I.1 El flujo ideal

```text
1. evals init                                       # crea estructura mínima
2. evals connect --agent <adapter>                  # conecta agente
3. evals discover                                   # análisis del repo
4. evals propose --interactive                      # propone con humano
5. evals seed --from production-logs <path>        # ingesta histórica
6. evals run smoke                                   # primer eval
7. evals propose-experiment                          # primer experimento
```

### I.2 `evals discover` — qué hace

El bootstrap skill que lee el repo. Decisiones:

**Inputs que lee** (en orden de prioridad):
1. README, ARCHITECTURE, docs/ — para entender propósito.
2. Código del agente: prompts, tool definitions, graph definitions.
3. Tests existentes — son evals primitivos.
4. Logs de uso si existen (`logs/`, `traces/`, langsmith export).
5. Tickets/incidents si están enlazados (CHANGELOG, GitHub issues).
6. Producto: ¿hay landing page o docs externas que describan features?

**Outputs propuestos**:
1. **FeatureRegistry inicial** (paths inferidos, marcados como `proposed` hasta que humano aprueba).
2. **AgentFleet manifest** con agentes detectados.
3. **Smoke dataset** con 10-20 casos generados desde docs.
4. **Risk registry** propuesta.
5. **Failure modes hipotéticos** basados en código (no en datos reales).
6. **Lista de incógnitas**: lo que no pudo inferir y necesita input humano.

### I.3 ¿Propone o pregunta?

**Ambos, en este orden:**
1. **Propone** un primer borrador completo.
2. **Marca como `confidence: low|medium|high`** cada propuesta.
3. **Pregunta solo lo `low`** en un interactive flow.
4. **`medium` queda como propuesta editable**.

Esto evita el peor anti-pattern: agente que pregunta 20 cosas antes de mostrar nada útil.

### I.4 Synthetic vs handcrafted en bootstrap

Decisión: **synthetic first, marked as such**. Etiqueta `source.type: synthetic` con `confidence: low` hasta validación humana. Capability dataset arranca con 30-100 casos sintéticos cubriendo features detectadas. Smoke arranca con 10-20 casos.

**NO** poner casos sintéticos en regression jamás. Regression solo crece con failures reales validados.

### I.5 Tensión honesta

El bootstrap skill es el **diferenciador competitivo más alto del proyecto**. También es el **componente más fácil de hacer slop**. Si propone 200 casos genéricos copiados de SWE-bench, mata la propuesta.

**Recomendación dura**: invertir en calidad de propuesta sobre cantidad. 20 casos buenos > 200 casos genéricos. El skill debe tener un `quality_gate` interno: si no puede generar al menos 10 casos con `confidence: medium+`, no genera nada y pide más input.

---

## §J. Failure Analysis Module

### J.1 Pipeline

```text
trace + grader_result → failure_classifier → failure_cluster → root_cause_hypothesis → action_suggestion
```

### J.2 Failure clustering

Algoritmo:
1. Cada failure se vectoriza (embedding sobre input + output + grader reason).
2. Clustering incremental (HDBSCAN o similar).
3. Clusters se persisten con `cluster_id`, `representative_cases`, `failure_signature`.
4. Cuando un nuevo failure se asigna a un cluster existente, incrementa el contador.

Esto convierte "100 failures sueltos" en "5 patrones con 20 instances cada uno".

### J.3 Root cause attribution

Heurísticas (orden de aplicación):
1. **Si grader es deterministic y falló**: root cause = output del agente.
2. **Si trace tiene `tool_call` con `status: error`**: root cause = tool.
3. **Si retrieval no recuperó docs necesarios** (cross-check contra expected citations): root cause = retrieval.
4. **Si memory_read no incluyó keys esperadas**: root cause = memory.
5. **Si grader es LLM y falló pero no hay otras señales**: root cause = ambiguo, escalar a humano.

Output: ranked list de hipótesis con `confidence` y `evidence_pointers`.

### J.4 Action suggestion

Por cada cluster activo:
- ¿Cuántos failures? ¿Qué severidad?
- ¿Hay un experiment activo que lo aborde?
- ¿Sugiere nuevo experiment? ¿Con qué editable?
- ¿Graduar a regression? ¿Con qué confidence?

### J.5 Tensión honesta

Root cause attribution **se equivoca seguido**. Es heurístico, no determinístico. **Recomendación**: presentarlo siempre como hipótesis ranked, nunca como "el root cause es X". Y registrar feedback humano (correct/incorrect) para mejorar las heurísticas con el tiempo.

---

## §K. Retirement Lifecycle

### K.1 Estados de retirement

| Entidad | Estados |
|---|---|
| EvalCase | active, deprecated, quarantined (flaky), retired |
| Dataset | active, frozen, retired |
| Judge | calibrating, in_use, drifting, recalibrating, retired |
| Experiment | (definido en §A.2) |
| Feature (registry) | proposed, active, deprecated, removed |

### K.2 Triggers de retirement

| Entidad | Trigger automático | Trigger manual |
|---|---|---|
| EvalCase → quarantined | flake_rate > threshold por N runs | flag manual |
| EvalCase → retired | feature retirada, policy obsoleta | decisión humana |
| Dataset → retired | no usado en 6 meses + sucesor existe | decisión humana |
| Judge → retired | sustituto promovido a in_use | decisión humana |
| Feature → deprecated | no aparece en producción 90 días | decisión humana |

### K.3 Política de no-deletion

**Decisión: nada se borra**. Solo se marca `retired_at` y se vincula a sucesor si existe. Reproducibilidad histórica > housekeeping.

Para queries y dashboards, `retired_at != null` se filtra por default. Para auditoría queda visible.

### K.4 Cuándo un regression case se vuelve obsoleto

Solo en estos casos:
1. La policy formal cambió (citar la nueva policy).
2. La feature subyacente fue removida.
3. La rúbrica del caso se reescribe → es un caso nuevo, retirar el original.

**Nunca**: "ya no falla, vamos a quitarlo". Eso destruye la regression.

---

## §L. Costo del Framework — Budget Guardrails

### L.1 Donde se gasta dinero

| Categoría | Costo típico | Notas |
|---|---|---|
| LLM calls del agente | $0.001–0.50 / case | Depende del modelo y reasoning |
| LLM judges | $0.001–0.05 / case | Multiplicador por judge en ensemble |
| Tool calls reales | varía | Cero en mock/dry_run |
| Synthetic generation | $0.01–0.10 / case | Solo en seeding y counterfactuals |
| Humano | $1–20 / annotation | Solo si pagado |
| Storage | bajo | Postgres + S3 |
| Compute (orchestration) | bajo | Trivial |

### L.2 Budget controls

```yaml
budget:
  experiment_cap_usd: 50
  iteration_cap_usd: 5
  daily_cap_usd: 200
  monthly_cap_usd: 2000
  on_breach: abort_experiment
  notify_at_pct: [50, 80, 95]
```

Framework debe:
- Estimar costo previo a correr (`evals estimate <experiment>`).
- Trackear en tiempo real.
- Abortar limpio si excede.

### L.3 Costo del framework mismo

Por encima del costo de LLMs:
- Postgres + S3 hosted: ~$25-100/mes para uso single-user.
- Self-hosted local: $0 (laptop).

**Decisión**: MVP debe correr 100% local con $0 de infra hosted. Postgres local, filesystem como object storage.

### L.4 Tensión honesta

LLM judges en ensemble + holdouts + counterfactuals + reliability con 5 repeticiones multiplican el costo ~10-20x respecto a "run cada caso una vez". **Esto es real y duele**. Solución: niveles de rigor por etapa.

| Etapa | Rigor | Multiplicador costo |
|---|---|---|
| Smoke (cada commit) | 1 run, 1 judge, regression only | 1x |
| Pre-merge | regression + capability subset | 3x |
| Pre-release | regression + capability full + reliability | 10x |
| Periodic deep | + holdout + counterfactual + adversarial | 20-30x |

El usuario declara la etapa, el framework aplica el preset.

---

## §M. Reproducibilidad — Mecánica

### M.1 Qué se snapshotea por run

Snapshot completo:
- AgentFleet version + Agent versions.
- Parameter values (no solo el search_space, los valores exactos usados).
- Dataset version (immutable).
- Grader versions.
- Seed para sampling, para LLM (cuando soportado), para counterfactual generation.
- Framework version.
- Tool versions (incluyendo tool_code hash si está editable).
- LLM provider + model + provider-side params declarados.
- Prompt full text (no solo variables).
- Context window state si aplica.

### M.2 Determinismo: lo que sí y lo que no

**Sí determinístico:**
- Sampling de datasets.
- Counterfactual generation con seed.
- Decisión del decision matrix.
- Métricas de runs guardados.

**No determinístico inherentemente:**
- LLM provider-side (incluso temperature=0).
- Tools que llaman APIs externas con estado.
- Retrieval cuando el corpus cambia.

### M.3 Estrategia

**Tres niveles de reproducibilidad declarados:**

| Nivel | Garantía | Costo |
|---|---|---|
| `replay` | Mismo trace, sin re-correr LLM. Determinístico siempre. | Bajo |
| `re_run_same_provider` | Mismo agente + mismos params + mismo dataset. LLM puede variar. | Costo LLM |
| `re_run_pinned` | Adicional: snapshot del corpus retrieval, mocks de APIs externas | Costo LLM + storage del snapshot |

Para auditoría legal o regression crítica → `re_run_pinned`.
Para análisis casual → `replay`.

### M.4 Tensión honesta

`re_run_pinned` exige snapshotear corpus de retrieval y mock de APIs externas. Para sistemas grandes esto es **pesado** en storage. Recomendación: snapshot solo cuando el caso es `risk.overall: critical` o está en regression con `blocking: release: true`.

---

## §N. Data Leakage — Reglas Anti-Contaminación

### N.1 Reglas duras

1. **Un EvalCase tiene exactamente un `dataset_type` primary**. No vive en capability y regression simultáneamente.
2. **Holdout sets son inmutables**. Una vez declarado holdout, no se mueven a optimization. Si necesitas más optimization data, generas casos nuevos.
3. **Production-derived cases tienen un `source_event_id`** (incident, ticket, run). Si dos cases comparten `source_event_id`, son aliases del mismo evento — uno solo entra al dataset.
4. **Synthetic counterfactual pairs**: el original y la variante NO pueden vivir en datasets distintos. Si el original está en optimization, todas sus variantes también. Esto evita que el optimizer vea la variante en train y el original en holdout.
5. **Calibration datasets** están aislados. No participan en optimization ni en gates de producto. Solo se usan para calibrar judges.

### N.2 Validación automática

`evals validate datasets` corre:
- Detección de duplicados por `id`, `input_hash`, `source_event_id`.
- Detección de near-duplicates (embedding similarity > 0.95).
- Verificación de aislamiento holdout/optimization.
- Verificación de feature coverage balanceado.

CI puede ejecutar esto como pre-commit hook.

### N.3 Tensión honesta

Near-duplicates con embedding similarity es probabilístico. **Va a haber falsos positivos** (casos legítimamente distintos que se parecen). **Va a haber falsos negativos** (cases con misma intención pero wording radicalmente distinto). Recomendación: usar como **advisory** (warning), no como bloqueador, salvo en holdout vs optimization donde sí bloquea.

---

## §O. CI Integration

### O.1 Workflow GitHub Actions de ejemplo

```yaml
name: evals
on: [pull_request]

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: evals-framework/setup-action@v1
        with:
          version: 0.1.0
      - run: evals run smoke --reporter github-pr
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

  regression:
    if: github.base_ref == 'main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: evals-framework/setup-action@v1
      - run: evals run regression --reporter github-pr --strict
```

### O.2 Decisiones de gating

| Branch / event | Default gates |
|---|---|
| PR a feature branch | smoke only |
| PR a main | smoke + regression |
| Release tag | smoke + regression + reliability subset |
| Nightly | full capability + production_sample drift |
| Manual trigger | configurable |

### O.3 Reporter formats

- `github-pr`: comment sticky en el PR con tabla diff.
- `github-check`: check status pass/fail/neutral.
- `slack`: post a canal.
- `junit-xml`: para integración con dashboards de CI.
- `json`: programático.

### O.4 Tensión honesta

Si las smoke evals tardan >5 min, los developers las van a evadir. **Hard target: smoke <2 min**. Si tu smoke set tarda más, no es smoke, es regression. Reducir.

---

## §P. Multi-Tenant — Arquitectura

### P.1 Decisión

Multi-tenant **desde el inicio**, modelo de aislamiento por `workspace`.

### P.2 Modelo

```text
workspace (tenant)
  ├── members (con roles)
  ├── projects
  │     ├── agent_fleets
  │     ├── datasets
  │     ├── experiments
  │     ├── graders
  │     ├── runs
  │     └── traces
  └── secrets / api_keys
```

- Cada workspace está completamente aislado: datos, secrets, billing.
- Un usuario puede estar en varios workspaces.
- Cross-workspace = export/import explícito.

### P.3 Roles

Ya definidos en §16 del canónico. Por workspace:
- viewer, evaluator, experimenter, maintainer, admin, auditor.

### P.4 Aislamiento de datos

- Row-level security en Postgres por `workspace_id`.
- Object storage paths prefijados por `workspace_id`.
- API tokens con scope a workspace.
- Audit log por workspace, no cross-tenant.

### P.5 Patricio = workspace de uno

En MVP single-user: Patricio crea su workspace, es admin único. Toda la mecánica multi-tenant existe pero hay una sola tenant. Esto evita refactor doloroso después.

### P.6 Tensión honesta

Multi-tenant desde día 1 agrega ~15-20% overhead a cada decisión de schema y a cada query. Para single-user, este overhead es invisible. Para multi-user, evita una migración brutal. **Vale la pena**.

---

## §Q. Métricas Faltantes — Agregar al Canónico

### Q.1 Lista de métricas a incluir en §8

```yaml
agent_health_metrics:
  recovery_rate:
    definition: |
      % de runs donde el agente recuperó exitosamente de un error de tool,
      retrieval miss, o ambigüedad. (recoveries / total_recovery_opportunities)
    higher_is_better: true

  loop_detected_rate:
    definition: |
      % de runs donde el agente quedó en bucle (mismo span pattern repetido
      >N veces sin progreso). Detectable por análisis de spans.
    higher_is_better: false

  hallucination_rate:
    definition: |
      % de runs con claims no soportados por retrieval o tool outputs.
      Detectable por grader específico o por reference comparison.
    higher_is_better: false

  steps_to_completion:
    definition: |
      Número promedio de spans (llm + tool + retrieval) hasta resolución.
      Indicador de eficiencia.
    higher_is_better: false

  human_handoff_rate:
    definition: |
      % de runs donde el agente escaló a humano. Bueno cuando el caso lo
      ameritaba, malo cuando podía resolverlo.
    higher_is_better: contextual

  tool_retry_rate:
    definition: |
      % de tool calls que se reintentaron. Síntoma de tool inestable o
      prompt ambiguo.
    higher_is_better: false

  context_compaction_loss:
    definition: |
      Cuántos tokens importantes se perdieron en compaction (medido por
      requerimientos posteriores a info comprimida).
    higher_is_better: false
```

### Q.2 Detectabilidad por modo

| Métrica | Embedded | HTTP endpoint | Black box |
|---|---|---|---|
| recovery_rate | ✓ | parcial | ✗ |
| loop_detected_rate | ✓ | ✓ (si grava timing) | parcial |
| hallucination_rate | ✓ | ✓ (con grader) | ✓ |
| steps_to_completion | ✓ | parcial | ✗ |
| human_handoff_rate | ✓ | ✓ | ✓ |

Reportar `n/a` cuando no detectable, no fingir.

---

## §R. Versionado — Política

### R.1 Reglas

**Semver para todo lo versionable:**

| Entidad | Major bump | Minor bump | Patch bump |
|---|---|---|---|
| Agent | Cambio de tools, workflow, model family | Param defaults, prompt rewrites | Bugfixes en prompt |
| Dataset | Schema break, taxonomy change | Casos nuevos, balance ajustado | Etiquetas corregidas |
| Grader | Rúbrica cambia, output schema cambia | Threshold defaults, ejemplos | Texto de rúbrica clarificado |
| FeatureRegistry | Path renames, kinds change | Features nuevas | Descriptions |
| Experiment | (no aplica — son inmutables al completarse) | | |

### R.2 Quién hace el bump

- Auto-detect propone bump basado en diff.
- Humano aprueba.
- CI valida que el bump sea consistente con el diff (ej. si cambiaste schema sin major bump, falla).

### R.3 Frozen states

Una vez una entidad entra como `frozen` en un Experiment completado, su versión específica no se puede reescribir. Si necesitas corregir, creas v+1 y dejas el experimento original apuntando a la versión vieja.

---

## §S. Preguntas Abiertas Que Necesito Que Resuelvas

Lista priorizada de decisiones que solo tú puedes tomar:

### S.1 Críticas (bloquean MVP)

1. **MVP storage**: ¿SQLite + filesystem (laptop-first) o Postgres + S3 (server-first)?
2. **Black box (ngrok) en MVP**: ¿soportado o post-MVP?
3. **Smoke vs full en CI default**: ¿qué corre en cada PR?
4. **Bootstrap skill quality bar**: ¿generamos hasta N casos sintéticos o esperamos input humano si confidence baja?

### S.2 Importantes (impactan v0.2)

5. **Strategy default del Proposer**: ¿bayesian, random, manual?
6. **Judge rotation en MVP**: ¿activo o solo infra preparada?
7. **Counterfactual pairs en MVP**: ¿solo en safety o también capability?
8. **Anchor cases en MVP**: ¿implementado o post-MVP?
9. **Human queue + UI en MVP**: ¿qué nivel de UI? (CLI puro vs web mínima)

### S.3 Tienen tradeoffs ideológicos

10. **Opinated vs neutral**: ¿el framework impone taxonomy + portfolio + GraderCard, o son recomendaciones?
11. **Cost ceiling default**: ¿abortamos solos o solo avisamos?
12. **PII en MVP**: ¿scrubber automático real o stub que advierte?
13. **OTel native vs propio**: ¿exigimos OTel para integraciones, o sólo lo aceptamos como uno de varios?

### S.4 Estratégicas a largo plazo

14. **Pricing model si esto sale al mundo**: ¿open core, hosted SaaS, enterprise on-prem?
15. **Distribución**: ¿pip install, npm install, ambos, Docker primero?
16. **Quién es el `evals propose` skill**: ¿built-in para Claude/Codex/Cursor, o portable?

---

## §T. Recomendaciones Finales — Ordenadas Por Impacto

### T.1 Top 5 cosas que cierran el spec más rápido

1. **Definir `Trace` schema canónico** (§B). Sin esto, nada se puede capturar reproducible.
2. **Definir `IterationRecord` y state machine de Experiment** (§A). Sin esto, no hay loop real.
3. **Decisión storage + multi-tenant model** (§E, §P). Cambia 30% del código.
4. **Bootstrap skill (cold start)** (§I). Es la propuesta de valor #1.
5. **CI integration mínima + reporter** (§G, §O). Sin esto, las evals son aspiracionales.

### T.2 Top 3 trampas que evitar

1. **No empezar implementación sin decidir storage**. SQLite vs Postgres cambia el día 1.
2. **No construir `llm_proposer` antes de tener métricas confiables**. Optimizar contra un judge mal calibrado es peor que no optimizar.
3. **No esperar a tener UI antes de validar el loop**. CLI + markdown reports primero. UI cuando duela.

### T.3 Top 3 cosas que NO deben entrar al MVP aunque sea tentador

1. **Distributed run orchestration**: laptop primero, K8s después.
2. **RLHF model training**: solo capture & labeling, no training.
3. **Multimodal grading complejo**: schema preparado, ejecución texto.

---

## §U. Patrón Meta Que Sigo Recomendando

El spec canónico está fuerte en **declaración** (qué). Este documento le agrega **operación** (cómo). Falta una tercera pasada de **fricción real** (qué duele cuando lo usas) que solo va a salir del **primer dogfood**. 

Recomiendo: **construir el MVP mínimo (laptop, SQLite, CLI, smoke + regression, sin loop automático) en 1-2 semanas**, correrlo contra Seals real, y dejar que el dolor empuje las siguientes decisiones. Construir el `OptimizationLoop` automático antes de eso es prematuro y se va a re-escribir.

El framework tiene la rara cualidad de estar bien pensado al nivel conceptual. La trampa es seguir pensando 3 meses más sin tocar código. **El primer eval real va a enseñar más que 100 más páginas de spec.**
