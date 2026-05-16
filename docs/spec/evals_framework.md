# Evals Framework Para Self-Improving Agents

Estado: spec v0.1. Esta seccion es la version canonica del framework. Las notas historicas quedan abajo como contexto y materia prima.

## 1. Tesis

El framework no debe ser solo un runner de evals. Debe ser un sistema para:

- definir que vale la pena evaluar;
- analizar conversaciones;
- proponer skills, tools, features;
- construir datasets de calidad;
- correr experimentos reproducibles;
- observar traces de agentes a detalle;
- comparar deltas contra baselines;
- decidir si un cambio se conserva, se revierte, se investiga o se libera bajo flag;
- convertir fallas reales en regresiones permanentes;
- ayudar a agentes externos como Codex, Claude Code, Hermes u OpenClaw a leer un repo y proponer evals utiles.

La unidad central no es `EvalCase`. La unidad central es:

```text
Experiment + AgentFleet + Agent + Dataset + Trace + GraderCard + OptimizationLoop
```

`EvalCase` es solo una pieza dentro del sistema.

## 2. Principios No Negociables

1. Cada experimento empieza con metricas y thresholds explicitos.
2. Cada experimento declara que componentes se pueden cambiar y cuales quedan congelados.
3. Cada resultado debe ser reproducible: agent, parameters, dataset, grader, runtime, seeds y versiones quedan registrados.
4. Regression/golden protege contratos; capability mide progreso. No se mezclan.
5. Reliability requiere multiples corridas. Una sola corrida no mide fiabilidad.
6. Los errores tienen severidad, costo y peso. No todo fallo vale lo mismo.
7. Los LLM-as-judge son componentes auditables, calibrados y versionados.
8. El sistema debe evitar optimizar contra el juez y perder de vista al usuario.
9. Los datasets de produccion pasan por PII scrubbing antes de entrar al sistema.
10. El core debe ser agnostico al framework, con integraciones para frameworks populares.

## 3. Componentes Evaluables

El framework debe poder evaluar o iterar sobre estos componentes, siempre que el experimento lo permita:

| Componente | Que se evalua | Que se puede iterar |
|---|---|---|
| Prompt | instrucciones, formato, tono, constraints, variables | texto, secciones, ejemplos, policies |
| Modelo | proveedor, modelo, reasoning, temperature, max tokens, verbosity | seleccion y parametros |
| Graph/workflow | nodos, edges, handoffs, pasos redundantes, pasos faltantes | topologia, orden, compresion/expansion de nodos |
| Tools | descripcion, schema, codigo, permisos, granularidad, cantidad | agregar, quitar, dividir, consolidar, reescribir |
| Skills/scripts | instrucciones reutilizables y scripts ejecutables | contenido, API, ejemplos, scope |
| Dataset/testcases | cobertura, realismo, balance, dificultad, variabilidad | generacion, filtrado, graduacion, balanceo |
| Harness | runner, sandbox, logging, replay, determinismo | runtime, paralelismo, retries, captura |
| Retrieval/memory | documentos recuperados, memoria permitida, contexto | retriever, ranking, chunks, memoria, truncation |
| Guardrails | policy, refusals, escalacion, permisos | reglas, thresholds, checks deterministas |
| Monitoreo | traces, errores, latencia, costo, drift | sampling, alertas, dashboards |
| Agente completo | outcome final, trayectoria, UX, costo, seguridad | configuracion, parametros, tools, features, prompts permitidos |

## 4. Taxonomia De Evals

La taxonomia no debe ser una sola lista de tipos. Para agentes, un eval necesita varias coordenadas porque se puede evaluar una feature, la respuesta final, la trayectoria, el uso de tools, el runtime online o el juicio humano al mismo tiempo.

Cada eval debe declarar al menos:

```yaml
taxonomy:
  level: trajectory
  feature:
    primary: commerce.product_resolution
    secondary:
      - commerce.search.search_by_sku
      - commerce.customer_identification
  source:
    type: production
  ground_truth:
    methods:
      - rubric
  runtime: offline
  risk:
    overall: high
    user_trust: high
  dataset_type: regression
```

### 4.1 Nivel De Evaluacion

Esta dimension responde: que parte del sistema estoy juzgando?

| Nivel | Pregunta | Ejemplo de uso |
|---|---|---|
| single_step | Un input aislado produce el output correcto? | clasificacion, extraction, routing |
| final_response | La respuesta final cumple la rubrica? | soporte, QA, redaccion |
| step_level | Cada paso intermedio fue razonable? | planes, chains, reasoning traces |
| tool_call | Uso la tool correcta con argumentos correctos? | APIs, acciones, busquedas |
| retrieval | Recupero y uso evidencia correcta? | RAG, citations, grounding |
| memory_context | Recordo, olvido y compacto correctamente? | preferencias, restricciones, historial |
| workflow | El flujo completo siguio los pasos correctos? | onboarding, soporte, ventas, incident response |
| trajectory | La trayectoria completa fue segura, eficiente y correcta? | agentes con tools, handoffs y retries |
| conversation | Resuelve un caso multi-turn de principio a fin? | soporte conversacional, coaching, copilots |
| system | El sistema completo cumplio un objetivo de negocio? | ticket resuelto, refund correcto, tarea aprobada |

Regla: si hay tools, retrieval o decisiones intermedias importantes, no basta con `final_response`.

`trajectory` es el nivel de evaluacion: juzga el camino que siguio el agente para resolver la tarea. `Trace` es el registro observable de ese camino: pasos, tool calls, estado, reasoning disponible, errores, latencia, costos y decisiones en cascada. Un eval de nivel `trajectory` normalmente necesita un `Trace`, pero no son la misma cosa.

### 4.2 Feature Evaluada

Esta dimension responde: que parte del producto, capacidad o feature quiero medir?

`feature` es el eje principal para medir coverage. Debe ser granular, filtrable y natural para el producto. Puede representar una feature de usuario, una capacidad interna del agente o una subcapacidad tecnica que el producto necesita.

```yaml
feature:
  primary: commerce.product_resolution
  secondary:
    - commerce.search.search_by_sku
    - commerce.search.search_by_image
    - commerce.recommendation.propose_alternative
```

Reglas:

- `primary` es la feature principal que el caso pretende medir.
- `secondary` son features que tambien se ejercitan y pueden aportar coverage.
- Los nombres deben ser `snake_case` o paths con puntos.
- Los paths permiten reportes como `feature_coverage` por dominio, area y tarea.
- Las features son extensibles por proyecto; el framework no debe asumir que todos los productos tienen las mismas.

Las features no deben ser strings sueltos inventados por cada dataset. Deben venir de un registry versionado del proyecto. Ese registry puede declararse al crear un agente o una flotilla de agentes.

Namespaces recomendados:

- `commerce.*`, `support.*`, `billing.*`: product/user features.
- `agent.*`: internal agent capabilities, incluyendo `agent.skills.*` y `agent.tools.*`.
- `system.*`: platform/runtime capabilities.
- `safety.*`: safety/security capabilities.
- `ux.*`: interaction quality capabilities.

```yaml
agent_fleet:
  id: seals_commerce_agents
  version: 2026-05-16
  agents:
    - id: product_resolver
      version: v4
      features:
        - commerce.product_resolution
        - commerce.search.search_by_name
        - commerce.search.search_by_sku
        - commerce.search.search_by_image
        - commerce.recommendation.propose_alternative
      tools:
        - search_products
        - rank_products
      feature_params:
        commerce.product_resolution:
          candidate_limit: 8
          disambiguation_strategy: rank_and_explain
        commerce.search.search_by_sku:
          exact_match_required: true
    - id: customer_identifier
      version: v2
      features:
        - customer.identification
      tools:
        - search_customers

feature_registry:
  commerce.product_resolution:
    kind: product_feature
    owner: product_resolver
    description: Resolve the intended product from user context.
    default_risk:
      overall: high
      user_trust: high
    failure_weight_defaults:
      wrong_product_selected: 10
      no_alternative_suggested: 3
  commerce.search.search_by_sku:
    kind: product_feature
    owner: product_resolver
    description: Search products by SKU.
    parameters:
      exact_match_required:
        type: boolean
        default: true
  agent.tools.search_products:
    kind: agent_capability
    owner: product_resolver
    description: Use the product search tool correctly.
    parameters:
      max_tool_calls:
        type: integer
        default: 3
  agent.skills.product_disambiguation:
    kind: agent_capability
    owner: product_resolver
    description: Apply the product disambiguation skill correctly.
  safety.prompt_injection_resistance:
    kind: safety_capability
    owner: security
    description: Resist prompt injection and data exfiltration attempts.
```

El framework puede tomar features, tools y parametros desde la flotilla registrada, y despues usarlos para construir datasets, eval cases, experiments, dashboards y feature coverage.

Ejemplos de features:

| Feature | Que mide |
|---|---|
| conversation.classification | clasificar conversacion, mensaje, intencion o estado |
| customer.identification | identificar cliente desde mensaje, contexto o metadata |
| commerce.product_resolution | decidir cual es el producto correcto |
| commerce.search.search_by_name | buscar producto por nombre |
| commerce.search.search_by_sku | buscar producto por SKU |
| commerce.search.search_by_use_case | buscar producto por caso de uso |
| commerce.search.search_by_image | buscar producto usando imagen |
| commerce.recommendation.propose_alternative | proponer alternativa cuando no hay resultado |
| policy.compliance | respetar reglas internas, legales o de producto |
| support.escalation | escalar a humano cuando corresponde |
| agent.tools.search_products | usar correctamente la tool de busqueda de productos |
| agent.skills.product_disambiguation | aplicar correctamente la skill de desambiguacion |
| agent.retrieval_grounding | recuperar y usar evidencia correcta |
| agent.memory_use | usar memoria permitida sin inventar ni filtrar datos |
| ux.tone | tono, claridad, brevedad y utilidad |

Para analytics, el framework debe poder calcular:

- `feature_coverage`: porcentaje de features con casos suficientes.
- `feature_pass_rate`: pass rate por feature.
- `feature_reliability`: consistencia por feature en multiples runs.
- `feature_regression_rate`: fallas en features que ya eran contrato.
- `feature_risk_exposure`: coverage ponderado por riesgo.

### 4.3 Fuente Del Caso

Esta dimension responde: de donde salio el caso?

| Fuente | Uso principal |
|---|---|
| handcrafted | reglas criticas y edge cases conocidos |
| production | distribucion real de usuarios |
| staging | casos capturados en staging |
| development | casos generados durante desarrollo |
| failure | bugs, incidentes, quejas o fallas de eval |
| synthetic | ampliar coverage o generar variaciones |
| adversarial | presionar seguridad y robustez |
| human_labeled | calibracion y casos de alta confianza |
| external_benchmark | comparacion general contra benchmarks publicos |
| simulation | conversaciones o entornos generados por simuladores |
| custom | fuente definida por el proyecto |

Cuando `source.type: failure`, debe haber detalle adicional:

```yaml
source:
  type: failure
  failure_type: production_incident
  failure_id: inc_2026_05_16_001
  first_seen_at: 2026-05-16
  detected_by: production_monitor
```

`failure_type` puede empezar con:

- `production_incident`
- `eval_failure`
- `human_review_failure`
- `user_complaint`
- `staging_bug`
- `regression_failure`
- `red_team_failure`
- `custom`

### 4.4 Ground Truth

Esta dimension responde: como sabemos si paso?

| Metodo | Cuando usarlo |
|---|---|
| exact_match | enums, routing, extraction determinista |
| schema_validation | JSON, tipos, contratos de API |
| deterministic_assertion | must_include, forbidden_tool, regex, invariantes |
| reference_answer | respuesta ideal escrita por humano |
| rubric | tareas abiertas con criterios claros |
| pairwise_preference | comparar dos candidatos |
| outcome_based | ticket cerrado, tarea completada, usuario aprueba |
| human_judgment | casos subjetivos o de alto riesgo |
| llm_judge | escala evaluacion abierta con calibracion humana |

Un caso puede tener varios metodos. Si hay mas de uno, el sistema lo trata como evaluacion combinada sin necesitar un enum `hybrid`.

```yaml
ground_truth:
  methods:
    - deterministic_assertion
    - llm_judge
```

Regla: todo eval critico debe tener al menos una parte determinista o una calibracion humana fuerte.

### 4.5 Runtime

Esta dimension responde: donde y cuando corre?

| Runtime | Funcion |
|---|---|
| offline | evals locales o CI con datasets versionados |
| replay | re-ejecutar traces historicos con nuevas variantes o parametros |
| simulation | correr usuarios, entornos o herramientas simuladas |
| shadow | observar candidato junto a produccion sin afectar usuario |
| canary | liberar a trafico limitado y medir degradacion |
| online | evaluar produccion continuamente |
| human_review | cola de revision humana |

### 4.6 Dataset Type

Esta dimension responde: para que decision se usa?

| Dataset type | Decision que habilita |
|---|---|
| smoke | detectar roturas obvias rapido |
| golden | proteger comportamiento canonico estable |
| regression | impedir que vuelvan bugs o incidentes |
| capability | medir progreso y cobertura |
| production_sample | detectar drift y priorizar trabajo |
| adversarial_safety | bloquear riesgos de seguridad o abuso |
| calibration | medir y mejorar judges |
| incident_queue | clasificar fallas antes de graduarlas |

`dataset_type` reemplaza a nombres ambiguos como `dataset_role`. Un dataset, un eval case y un experiment pueden compartir esta linea taxonomica, pero no necesitan los mismos campos. Ejemplo: un `Dataset` puede tener `dataset_type: capability`; un `EvalCase` puede tener `dataset_type: regression`; un `Experiment` puede declarar que corre contra varios `dataset_types`.

### 4.7 Taxonomia Por Entidad

La taxonomia aplica a `Dataset`, `EvalCase` y `Experiment`, pero no con exactamente los mismos campos. La linea conceptual debe ser la misma para que los reportes crucen bien, pero cada entidad necesita parametros propios.

```yaml
dataset:
  taxonomy:
    dataset_type: capability
    feature_scope:
      - commerce.product_resolution
      - commerce.search
    source_mix:
      production: 0.6
      synthetic: 0.3
      handcrafted: 0.1

eval_case:
  taxonomy:
    level: trajectory
    feature:
      primary: commerce.product_resolution
      secondary:
        - commerce.search.search_by_sku
    source:
      type: production
    ground_truth:
      methods:
        - deterministic_assertion
        - llm_judge
    dataset_type: regression

experiment:
  taxonomy:
    target_features:
      - commerce.product_resolution
      - commerce.search.search_by_sku
    target_levels:
      - trajectory
      - tool_call
    dataset_types:
      - capability
      - regression
      - adversarial_safety
    runtime: offline
  datasets:
    optimization: capability/commerce_product_resolution_v3
    gates:
      - regression/commerce_v7
      - adversarial_safety/prompt_injection_v2
  reliability:
    repetitions_per_case: 5
    metrics:
      - pass^k
      - consistency_rate
  target:
    primary:
      metric: feature_pass_rate
      feature: commerce.product_resolution
      operator: ">="
      value: 0.92
    guardrails:
      - metric: regression_pass_rate
        operator: "=="
        value: 1.0
      - metric: p95_latency_ms
        operator: "<="
        value: 4500
      - metric: cost_per_task_usd
        operator: "<="
        value: 0.035
  editable:
    prompt: true
    prompt_variables: true
    model: false
    model_params: true
    tool_descriptions: true
    tool_code: false
    tool_params: false
    workflow_graph: false
    memory_config: false
    retriever_config: true
    dataset: false
    ground_truth: false
    graders: false
  search_space:
    model_params:
      reasoning:
        - low
        - medium
        - high
      temperature:
        min: 0
        max: 0.7
    retriever_config:
      top_k:
        - 3
        - 5
        - 8
    prompt_variables:
      product_disambiguation_strategy:
        - ask_clarifying_question
        - rank_and_explain
        - propose_best_match_with_alternatives
  frozen:
    fleet_version: seals_commerce_agents:2026-05-16
    agents:
      - product_resolver:v4
      - customer_identifier:v2
    tools:
      - search_products:v4
      - get_customer:v2
    dataset_versions:
      - capability/commerce_product_resolution_v3
      - regression/commerce_v7
    grader_versions:
      - product_resolution_judge:v2
      - deterministic_tool_args:v1
```

Esto permite metricas como `feature_coverage`, `feature_pass_rate`, `feature_reliability` y `feature_regression_rate` sin forzar que todo viva en una sola etiqueta.

La divergencia importante:

- `Dataset` declara el universo de casos que cubre: feature scope, tipo, fuentes, balance, version y filtros.
- `EvalCase` declara una prueba concreta: input, expected result, ground truth, failure weights, risk y metadata.
- `Experiment` declara una intervencion: que quiere mejorar, contra que datasets corre, que parametros puede modificar, que queda congelado, cual es el search space y que decision tomar segun los resultados.

### 4.8 Riesgo

Esta dimension responde: que tan caro es fallar?

`risk` es un objeto definido por cada proyecto. El framework debe exigir una forma minima para poder filtrar y hacer gates, pero no debe asumir que todos los productos tienen riesgo financiero, medico, legal o de privacidad.

```yaml
risk:
  overall: high
  user_trust: high
  privacy: medium
  reversibility: reversible
```

Reglas:

- `overall` es requerido.
- Las demas dimensiones son custom del proyecto.
- Cada proyecto puede declarar su propio `risk_registry`.
- Los gates pueden apuntar a cualquier dimension de riesgo.

```yaml
risk_registry:
  overall:
    enum: [low, medium, high, critical]
  user_trust:
    enum: [low, medium, high, critical]
  privacy:
    enum: [none, low, medium, high, critical]
  reversibility:
    enum: [reversible, needs_approval, irreversible]
```

### 4.9 Tipos Canonicos Derivados

Los nombres simples siguen siendo utiles, pero deben entenderse como combinaciones de dimensiones:

| Nombre corto | Equivale a |
|---|---|
| single-step eval | `level: single_step`, normalmente `runtime: offline` |
| workflow eval | `level: workflow`, con features de planning/tool/recovery |
| final-response eval | `level: final_response`, ground_truth rubric/reference/human |
| trajectory eval | `level: trajectory`, requiere la trayectoria del agente y normalmente produce un `Trace` completo |
| online eval | `runtime: online`, normalmente production_sample |
| human eval | `ground_truth.methods: [human_judgment]` o `runtime: human_review` |
| simulation eval | `runtime: simulation`, source simulation |
| adversarial eval | `source: adversarial` o feature de safety/security |

Esto mantiene la taxonomia simple para hablar, pero precisa para implementar.

## 5. Datasets Como Portafolio

No debe existir un solo mega-dataset. El framework opera un portafolio:

| Dataset | Funcion | Bloquea? |
|---|---|---|
| smoke | detectar roturas obvias | si, 100% |
| golden | comportamiento canonico estable | si, 98-100% segun riesgo |
| regression | bugs/incidentes corregidos que no deben volver | si, casi binario |
| capability | medir amplitud y progreso | normalmente no |
| production_sample | medir realidad reciente y drift | no por defecto |
| adversarial/safety | seguridad, abuse, injection, leaks | si para casos criticos |
| incidents | cola de fallas reales para clasificar y graduar | no, hasta graduarse |
| calibration | casos etiquetados por humanos para calibrar judges | no como producto, si como calidad del judge |

Regla practica:

- `golden`: contrato canonico estable.
- `regression`: fallo real o bug corregido.
- `capability`: mapa de progreso y hill-climbing.
- `production_sample`: distribucion real viva.
- `adversarial`: resistencia a riesgo y abuso.

Reliability no es un `dataset_type`; es un modo de medicion. Cualquier dataset puede correrse con repetitions para medir `pass^k`, consistency rate y varianza.

## 6. Schema Minimo De Caso

```yaml
id: refund_policy_001
version: 1
task_type: policy_qa
modalities:
  - text
taxonomy:
  level: trajectory
  feature:
    primary: policy.compliance
    secondary:
      - agent.retrieval_grounding
      - agent.tools.search_policy_docs
  source:
    type: production
  ground_truth:
    methods:
      - deterministic_assertion
      - llm_judge
  runtime: offline
  risk:
    overall: high
    user_trust: high
  dataset_type: regression
input:
  messages:
    - role: user
      content: Can I return this after 90 days?
context:
  documents:
    - id: returns_policy
      version: 2026-05-01
expected:
  outcome: fail_if_policy_misrepresented
  must_include:
    - 30 days
  must_not_include:
    - 90 days
    - guaranteed refund
  required_tools:
    - search_policy_docs
  forbidden_tools:
    - refund_payment
graders:
  - policy_compliance_judge:v3
  - deterministic_required_citation:v1
failure_weights:
  policy_violation: 10
  missing_citation: 3
  tone_issue: 1
metadata:
  pii_status: scrubbed
  owner: support
  tags:
    - refunds
    - policy
blocking:
  merge: true
  release: true
```

Los casos deben ser filtrables como bloques de lego por `taxonomy.dataset_type`, `taxonomy.risk`, `task_type`, `taxonomy.level`, `taxonomy.feature`, `taxonomy.source.type`, `language`, `tool`, `policy_area`, `modality`, `owner` y `tags`.

Los pesos de errores pueden venir de defaults globales y tener override por caso:

```yaml
failure_weight_defaults:
  wrong_product_selected: 10
  policy_violation: 10
  pii_leak: 10
  invalid_tool_arguments: 6
  no_alternative_suggested: 3
  missing_citation: 3
  tone_issue: 1

case_override:
  failure_weights:
    no_alternative_suggested: 5
```

## 7. Experiment

Un experimento declara objetivo, metricas, restricciones, datos, parametros, variantes, loop y criterio de decision.

```yaml
id: support_agent_prompt_search_2026_05_15
goal: Improve policy QA success without increasing high-risk failures.

taxonomy:
  target_features:
    - policy.compliance
    - agent.retrieval_grounding
  target_levels:
    - trajectory
    - final_response
  dataset_types:
    - capability
    - regression
  runtime: offline

target:
  primary:
    metric: weighted_success
    operator: ">="
    value: 0.92
  guardrails:
    - metric: high_risk_false_negatives
      operator: "=="
      value: 0
    - metric: p95_latency_ms
      operator: "<="
      value: 4500
    - metric: cost_per_task_usd
      operator: "<="
      value: 0.035

editable:
  prompt: true
  prompt_variables: true
  model: false
  model_params: true
  tool_descriptions: true
  tool_code: false
  tool_params: false
  workflow_graph: false
  memory_config: false
  retriever_config: true
  dataset: false
  ground_truth: false
  graders: false

search_space:
  model_params:
    reasoning:
      - medium
      - high
    temperature:
      min: 0
      max: 0.5
  prompt_variables:
    citation_style:
      - concise
      - explicit_policy_reference
  retriever_config:
    top_k:
      - 3
      - 5
      - 8

frozen:
  fleet_version: support_fleet:2026-05-16
  agents:
    - support_agent:v12
  tool_code:
    - search_policy_docs:v4
    - get_order_status:v2
  datasets:
    - capability/support_policy_v3
    - regression/support_v7
  graders:
    - policy_compliance_judge:v3
    - tool_trace_grader:v2

experiment_variants:
  baseline: support_agent:v11
  candidates:
    - id: support_agent_prompt_search_candidate_001
      agent: support_agent:v12
      parameters:
        model_params:
          reasoning: high
          temperature: 0.2
        prompt_variables:
          citation_style: explicit_policy_reference
        retriever_config:
          top_k: 5

datasets:
  optimization: capability/support_policy_v3
  gates:
    - regression/support_v7
  reliability_eval:
    repetitions_per_case: 5
    metrics:
      - pass^k
      - consistency_rate

run:
  sample_strategy: stratified
  max_iterations: 20
  repetitions_per_case: 5
  parallelism: 8
  convergence:
    min_delta: 0.005
    patience: 3

decision:
  if_regression_fails: investigate
  if_guardrail_fails: reject
  if_primary_improves_but_cost_regresses: require_tradeoff_review
  if_success: keep_candidate
```

## 8. Metricas Y Tradeoffs

El sistema debe soportar thresholds `>`, `>=`, `<`, `<=`, `==`, `in_range`, `not_in_range` y ventanas de cambio contra baseline.

Metricas core:

| Categoria | Ejemplos |
|---|---|
| Calidad | accuracy, weighted_success, rubric_score, preference_win_rate |
| Fiabilidad | pass@k, pass^k, variance, consistency_rate |
| Seguridad | policy_violation_rate, jailbreak_success_rate, pii_leak_rate |
| Tools | tool_selection_accuracy, tool_arg_validity, forbidden_tool_calls |
| Retrieval | citation_precision, citation_recall, grounding_score |
| Costo | cost_per_task, tokens_in/out, tool_cost |
| Latencia | p50/p95 latency, time_to_first_token, barge_in_latency |
| UX | human_approval_rate, escalation_rate, clarification_quality, task_completion |
| Cobertura | feature_coverage, feature_pass_rate, unknown_intent_rate |

### pass@k vs pass^k

- `pass@k`: probabilidad de que al menos una de k corridas pase. Mide potencial con retries.
- `pass^k`: probabilidad de que k corridas consecutivas pasen. Mide confiabilidad repetida.

Para agentes de produccion, `pass^k` importa mas en rutas criticas. Para exploracion o generation tasks, `pass@k` puede ser aceptable.

### Target multidimensional

No se debe esconder tradeoffs en un solo numero. Se usan tres capas:

1. `primary_metric`: lo que se quiere optimizar.
2. `guardrail_metrics`: limites que no se pueden romper.
3. `tradeoff_report`: delta de costo, latencia, cobertura, fiabilidad y UX contra baseline.

Una mejora solo es auto-aceptable si mejora la primaria y no rompe guardrails. Si mejora calidad pero sube costo/latencia fuera de tolerancia, requiere review explicita o feature flag.

## 9. OptimizationLoop

Loop base:

1. Investigar: leer experimentos previos, traces, failure modes, memoria, docs y configuracion.
2. Formular hipotesis: explicar que cambio deberia mover que metrica y por que.
3. Proponer delta: modificar solo parametros/componentes permitidos por `editable`.
4. Ejecutar evals: subset rapido, regression gate, dataset completo o reliability run, segun etapa.
5. Comparar baseline: metricas agregadas, por segmento, por severidad y por costo.
6. Decidir: keep, reject, revert, feature flag, abrir investigacion o lanzar subexperimento.
7. Registrar: parameter diff, traces, metricas, conclusion y siguiente hipotesis.

El motor puede operar en dos modos:

- `handoff`: corre experimentos largos sin supervision continua, por ejemplo durante la noche.
- `agent_loop`: un agente externo usa skills/docs del SDK para proponer, correr, revisar y pedir confirmacion cuando haga falta.

## 10. Decisiones Cuando Hay Conflicto

| Resultado | Decision default |
|---|---|
| mejora capability y pasa regression | keep candidate |
| mejora capability pero rompe regression critica | reject o investigate; no merge |
| mejora capability pero rompe regression no critica | investigate; posible feature flag |
| mejora accuracy pero sube costo dentro de tolerancia | keep si tradeoff esta declarado |
| mejora accuracy pero rompe costo/latencia guardrail | require_tradeoff_review |
| baja capability pero mejora safety critica | posible accept con release note |
| judge y humano discrepan | no usar como gate hasta calibrar |
| production_sample muestra drift | crear incident/capability cases y priorizar |

## 11. Graduacion De Casos

Un caso se gradua de capability/incidents a regression cuando:

- el comportamiento esperado es claro;
- el fallo importa en produccion;
- hay consenso humano o policy formal;
- el grader puede medirlo con baja ambiguedad;
- la solucion ya fue implementada;
- el caso representa una clase relevante, no solo una rareza;
- el equipo acepta bloquear deploys si vuelve a fallar.

Flujo:

1. aparece fallo en produccion, capability o red-team;
2. se etiqueta;
3. se identifica root cause;
4. se decide si es bug, gap de capacidad o policy ambiguity;
5. se corrige agente, parametro, prompt, tool, policy o workflow;
6. se valida con humanos o grader calibrado;
7. se convierte en regression;
8. entra a CI/release gate;
9. si vuelve a fallar, bloquea o dispara rollback segun severidad.

## 12. LLM-As-Judge Y Grader Cards

Cada judge tiene una `GraderCard`. Si hay ensemble, hay una card por judge y otra del ensemble.

```yaml
name: policy_compliance_judge
version: v3
purpose: Detect policy violations in support answers
blocking: true
input_fields:
  - user_message
  - agent_response
  - retrieved_policy_docs
output:
  label: pass | fail
  reason: string
human_reference:
  dataset: policy_compliance_calib_v2
  annotators: 3
  adjudication: required_on_disagreement
metrics:
  precision: 0.94
  recall: 0.97
  high_risk_false_negatives: 0
thresholds:
  min_precision: 0.90
  min_recall: 0.95
  max_high_risk_false_negatives: 0
review_cadence: weekly
```

Un judge bloqueante necesita:

- dataset de calibracion humano;
- confusion matrix;
- precision/recall por clase;
- analisis de false positives y false negatives;
- version congelada;
- monitoreo de drift;
- ejemplos negativos y positivos en la rubrica;
- limites claros de uso.

## 13. Como Evitar Optimizar Contra El Judge

Diseñar buenos datasets ayuda, pero no es suficiente. El framework debe tener defensas explicitas:

1. Holdout sets ocultos: casos que el optimizer no ve durante hill-climbing.
2. Rotacion de judges: cambiar o combinar judges calibrados para reducir gaming.
3. Human spot checks: muestreo humano de winners, especialmente cuando hay saltos grandes.
4. Outcome metrics: medir resultados reales cuando existan, no solo rubricas textuales.
5. Production canaries: validar candidatos contra trafico real limitado antes de rollout amplio.
6. Counterfactual evals: probar casos semanticamente equivalentes con wording distinto.
7. Adversarial evals contra el optimizer: casos diseñados para detectar hacks al grader.
8. Penalizacion por overfit: si mejora train/capability pero no holdout/reliability run, se rechaza.
9. Versionado de prompt/judge/dataset: detectar cuando se optimiza contra una version especifica.
10. Decision logs: todo cambio aceptado debe explicar por que mejora al usuario, no solo el score.

La pregunta central de cada mejora debe ser: "que comportamiento de usuario o negocio mejoro?", no "que numero subio?".

## 14. Como Validar Que Un Eval Es Bueno

Un eval tambien necesita evals. Pruebas minimas:

| Prueba | Que detecta |
|---|---|
| human agreement | si la rubrica es clara para humanos |
| judge-human agreement | si el judge sirve para ese caso |
| mutation testing | si el eval falla cuando introducimos errores conocidos |
| paraphrase stability | si pequenos cambios de wording no cambian el veredicto sin razon |
| adversarial stability | si el agente puede pasar explotando la rubrica |
| segment coverage | si cubre idiomas, intents, riesgos, tools y edge cases relevantes |
| failure usefulness | si cuando falla produce diagnostico accionable |
| flake rate | si el eval es demasiado variable |
| leakage check | si expected answers o labels se filtran al agente |
| cost/runtime check | si puede correr en CI o release sin ser prohibitivo |

Un eval entra como gate solo si su flake rate y ambiguedad estan dentro del umbral definido.

## 15. Production Data, PII Y Governance

Todo dataset derivado de produccion pasa por pipeline:

1. ingestion de traces;
2. deteccion de PII;
3. redaccion o tokenizacion reversible segun permisos;
4. normalizacion de schemas;
5. etiquetado automatico inicial;
6. review humana para muestras de alto riesgo;
7. versionado del dataset;
8. aprobacion antes de usarse como gate.

Campos minimos de PII:

- `pii_status`: raw, detected, scrubbed, synthetic, approved_raw.
- `pii_policy`: redacted, tokenized, hashed, dropped.
- `retention_days`.
- `consent_basis` si aplica.
- `access_level`: public, internal, restricted, secret.

Produccion nunca debe alimentar directamente un optimizer sin scrub, sampling y control de permisos.

## 16. Roles Y Permisos En La Web

La UI tipo LangSmith debe soportar roles configurables:

| Rol | Permisos |
|---|---|
| viewer | ver dashboards, runs y reportes no restringidos |
| evaluator | crear datasets, etiquetar casos, revisar failures |
| experimenter | crear experimentos y correrlos en sandboxes permitidos |
| maintainer | aprobar graduation, editar graders, configurar gates |
| admin | gestionar usuarios, secretos, retention, integraciones |
| auditor | acceso read-only a historial, decisiones y compliance |

Permisos criticos:

- ver datos con PII;
- exportar datasets;
- modificar graders bloqueantes;
- promover casos a regression;
- cambiar thresholds de release;
- aprobar feature flags;
- correr experimentos con tools reales;
- borrar o archivar traces.

## 17. Observability: Nuestro LangSmith

La plataforma debe capturar:

- run_id, experiment_id, fleet version, agent versions y parameter versions;
- input, output y estado final;
- mensajes, turns, contexto y memory blocks;
- tool calls, args, outputs, errores y retries;
- retrieved docs, scores y citations;
- tokens, costo, latencia, time to first token, p95;
- grader outputs, reasons y confidence;
- diffs contra baseline;
- screenshots/audio/attachments cuando aplique;
- decision log: keep/reject/revert/flag/investigate;
- links a dataset cases, incidents y PRs.

La UI debe permitir:

- comparar runs lado a lado;
- filtrar por dataset, risk, tool, error, metrica, modelo, agente, feature y parametro;
- reproducir un run;
- inspeccionar traces paso a paso;
- convertir failure en dataset case;
- graduar cases;
- ver trend de metricas por release;
- ver calibracion y drift de judges.

## 18. Storage: YAML + DB

Decision recomendada:

- YAML/JSONL en repo para specs versionables: datasets curados, Experiment, GraderCard, AgentFleet y Agent manifests.
- Base de datos para runs, traces, variantes generadas, dashboards, permisos y auditoria.
- Object storage para payloads grandes: screenshots, audio, video, attachments, full traces pesados.

Supabase puede ser buen MVP si queremos auth, Postgres, storage y realtime rapido. El core no debe depender semanticamente de Supabase; debe tener un storage interface para cambiar a Postgres puro, SQLite local, S3, ClickHouse u otro backend.

## 19. Framework Agnostico, Integraciones Practicas

Core agnostico:

- define schemas;
- corre experimentos;
- registra traces;
- evalua outputs;
- calcula metricas;
- versiona agentes, parametros, datasets, graders y experiment_variants;
- expone SDK/CLI.

Integraciones:

- adapters para LangGraph, OpenAI Agents SDK, CrewAI, custom Python/TS, Codex/Claude Code style agents;
- wrappers de tools;
- trace exporters/importers;
- dataset importers desde logs, LangSmith, OpenTelemetry o archivos.

El repo `seals` debe consumir este framework como SDK, no contaminar el core con assumptions de seals.

## 20. Multimodal

MVP: texto primero, pero el schema debe soportar attachments desde el inicio.

```yaml
input:
  messages:
    - role: user
      content:
        - type: text
          text: Please inspect this invoice.
        - type: image
          asset_id: asset_invoice_001
```

Modalidades futuras:

- text;
- image;
- audio;
- voice;
- browser/computer-use;
- physical/sensor events;
- structured objects.

No implementar todos los graders multimodales en MVP, pero no cerrar el schema a texto plano.

## 21. MVP

El MVP debe demostrar el loop completo con texto:

1. CLI para inicializar `evals/`.
2. Schemas para `EvalCase`, `DatasetManifest`, `Experiment`, `AgentFleet`, `Agent`, `Trace`, `GraderCard`.
3. Runner local para un agente callable por comando o funcion.
4. Graders deterministas y LLM-as-judge basico.
5. Dataset portfolio: smoke, regression, capability, production_sample, adversarial_safety, calibration.
6. Repetitions para pass@k/pass^k.
7. Reporte markdown/HTML con deltas contra baseline.
8. Trace capture con tool calls y latencia.
9. PII scrubber basico para production-derived samples.
10. Skill/docs para que un coding agent lea el repo y proponga evals utiles.

No-MVP:

- RLHF completo;
- UI web completa;
- simulaciones multimodales avanzadas;
- optimizer muy sofisticado;
- marketplace de integrations;
- permisos enterprise granulares.

## 22. Estructura De Repo Recomendada

```text
evals/
  README.md
  datasets/
    smoke/
    golden/
    regression/
    capability/
    production_sample/
    adversarial/
    incidents/
    calibration/
  experiments/
    active/
    archived/
  graders/
    deterministic/
    llm/
    cards/
  agents/
    fleets/
    definitions/
    features/
  experiment_variants/
    agents/
    prompts/
    tools/
    workflows/
  runs/
    local/
  reports/
  schemas/
  skills/
```

## 23. Interfaces Del SDK

API conceptual:

```python
from evals_framework import Experiment, AgentAdapter

agent = AgentAdapter.from_command("python run_agent.py")
experiment = Experiment.load("evals/experiments/active/support_policy.yaml")

result = experiment.run(agent)
result.write_report("evals/reports/support_policy.html")
```

CLI conceptual:

```bash
evals init
evals inspect-repo
evals propose --agent codex --scope support_agent
evals run experiments/active/support_policy.yaml
evals compare runs/baseline runs/candidate
evals graduate incidents/refund_001 --to regression
evals calibrate graders/policy_compliance.yaml
```

## 24. Criterio De Exito Del Proyecto

El proyecto esta funcionando cuando:

- un usuario instala el SDK;
- corre un prompt/skill de bootstrap;
- el agente analiza el repo;
- propone una estructura de evals con datasets no slop;
- crea casos con criterios claros;
- corre un baseline;
- sugiere experimentos con metricas y thresholds;
- itera solo sobre componentes permitidos;
- produce reportes que explican si conviene shippear, investigar o revertir;
- convierte fallas reales en regression cases;
- mantiene visibilidad total de traces, costo, latencia, judges y datasets.

## 25. Gaps Cerrados En Esta Version

| Gap | Decision |
|---|---|
| Unidad central del sistema | `Experiment + AgentFleet + Agent + Dataset + Trace + GraderCard + OptimizationLoop` |
| Que componentes se pueden iterar | Se declara en `editable` por experimento |
| Regression vs capability vs reliability | Regression/capability son dataset types; reliability es modo de medicion con repetitions |
| pass@k vs pass^k | Ambos soportados; `pass^k` para confiabilidad repetida |
| Errores con severidad | `failure_weights` por tipo de error y metricas ponderadas |
| Target multidimensional | primary metric + guardrails + tradeoff report |
| Que hacer si mejora una metrica y rompe otra | Tabla de decisiones default y override por experimento |
| Como evitar optimizar contra el judge | holdouts, rotacion de judges, human checks, canaries, outcome metrics |
| Como saber si un eval es bueno | human agreement, mutation testing, flake rate, leakage checks, coverage |
| Calibracion de judges | `GraderCard` versionada con precision/recall y referencia humana |
| Produccion y PII | pipeline de scrub, retention, access level y aprobacion |
| LangSmith propio | observability model con traces, experiment_variants, deltas y decision logs |
| Supabase + YAML | YAML/JSONL para specs versionables, DB para runs, object storage para assets |
| Framework agnostico vs integraciones | core agnostico con adapters practicos |
| Multimodal en MVP | schema preparado para attachments; ejecucion MVP texto primero |
| Web roles/permisos | viewer, evaluator, experimenter, maintainer, admin, auditor |
| MVP | definido como loop local completo con schemas, runner, graders, traces y reportes |

---

# Notas Historicas Y Materia Prima

la intencion de que sea un framework es que nos debe de explicar como tratar cada uno de los componentes, ya sea:
- el prompt
- el modelo
- el graph (quiza hay nodos que deberian de comprimirse en uno solo, o quiza vale la pena agregar mas nodos para completar mas tareas)
- el workflow (hay pasos que vale la pena incluir o eliminar o simplificar, etc..)
- el agente completo
- las tools (desde su descripcion, su codigo, su capacidad, verificar si el agente deja de ser eficiente despues de n tools, revisar si hay tools que deberian de juntarse en una sola, quitar tools, agregar tools para hacer mas cosas, revisar scripts (alternativas de tools dentro de skills))
- skills
- el dataset, los testcasesm revisar que realmente capturen toda la variabilidad, dificultad, el espectro completo, que sean ralistas y no slop, etc..
- todo el sistema
- el harness
- retrieval de memory y manejo de contexto
- monitoreo y captura de errores
- guardrails

se deben de empezar los experimentos definiendo una o mas metricas con threshold especifico (puede ser mayor que, igual, menor que, en rango de, mayor o igual, menor o igual, etc...) algunos ejemplos de metricas: accuracy, reliability, costo, latencia, ux (definir que es ux para ese punto especifico), intervencion humana, mayor numero de aproves de tareas, mas tareas completadas, mas coverage, etc... se debe de poder definir una o mas metricas e iniciar el experimento iterando todas las partes permitidas hasta completar el numero de pasadas maximas o convergencia. el experimento debe de formular hipotesis y correr variando los parametros de acuerdo a la informacion obtenida en todos los intentos realizados. si hay datasets muy grandes el experimento deberia de poder tener subexperimentos para probar hipotesis rapidas con subdatasets.

al momento de declarar el experimento se debe de declarar que si se puede iterar y que no se puede iterar. 
debemos de considerar tanto pass@k de pass^k; para desempenio promedio vs confiabilidad repetida
al momento de declarar el experimento es valido declarar errores como mas costos o tolerables que otros para promediar un accuracy ponderado sin ser realmente una metrica de bien vs mal en caso de ser necesario
podemos comenzar un una taxonomia como: ingle-step, workflow, final-response, trajectory, online eval, human eval, simulation eval (la idea es que sea simple, straight fordward y clara)

en cada experimento se declara un succes criteria para ver si es exitoso o no, outfinal, tool usage, schemas creados, estado , ciertas partes del estado, etc..

experimentos con human feedback, generando un RLHF para considerar cosas como taste en casos como redactar mensajes, UX, UI, etc..
datasets: estaticos, templates, simulaciones conversacionales

me gustaria que el framework fueran tan bien hecho, tan al detalle, tan ai native, tan bien documentado, que cumpla con lo siguiente:
al momento de instalar el sdk, puedes copiar y pegar un prompt y tu agente (codex, claude code, hermes, openclaw, etc..) lee las skills, analyza tu repo y empieza a construir evals de calidad (realmente calidad para tareas y cosas que si valen la pena, nada de slop) y te propone una estrcuctura a detalle de como evaluar, como probar, como testear, que y como iterar, y una estructura de datasets, porque esos, etc...
creo que un componente interesante es que deberiamos de desarrollar nuestro propio langsmith para llevar control de todos los evals, sus runs, la ejecucion del agente, debuggearlo, revisar al detalle lo que esta haciendo y como lo esta haciendo, versionamiento de los datasets y testcases, revisar a detalle latencia, tiempo a primer token, barge-in, todas las metricas definidas por experimento, transparencia, control y visibilidad total, etc..

 1. Qué Es Un Dataset De Evals
  Un dataset de evals es una colección versionada de casos que representan cómo debe comportarse un modelo, workflow o agente. En empresas grandes
  suele incluir:

  | Tipo | Para qué sirve |
  |---|---|
  | Golden dataset | Casos canónicos con input, respuesta esperada y criterios claros. Bueno para regresiones. |
  | Ground truth set | Datos etiquetados por expertos humanos; Shopify prefiere este enfoque sobre datasets “bonitos” demasiado curados. |
  | Production-derived dataset | Traces reales anonimizados o muestreados de producción. Captura distribución real. |
  | Synthetic dataset | Casos generados por LLMs o simuladores para ampliar cobertura. Útil para edge cases. |
  | Adversarial / red-team dataset | Jailbreaks, prompt injection, abuso, fraude, cyber, CBRN, policy violations. |
  | Trace dataset | No solo input/output: incluye pasos internos, tool calls, retrieval, handoffs, errores, latencia y coste. |

  La diferencia importante: en agentes, el dataset no solo pregunta “¿la respuesta final fue buena?”, sino “¿el agente llegó ahí de forma correcta,
  segura y barata?”.

los errores deben de poder tener un costo o peso para ser ponderados y entender que tan criticos son los errores y entender el estatus real del producto hasta ese snapshot o poder tomar decisiones de tradeoffs

red teaming para limitaciones del agente, stresstesting, prompt injection, security, etc..

preguntas que debe contestar el framework: que vale la pena evaluar y que no
como vale la pena evaluarlo
algo asi como llegar al gimnasio y tener las maquinas (todas las clses, funciones, metodos, etc...) tener guia de como usarlas (documentacion, skills, todo ai native, cookbooks, etc..) y capacidad de tener al entrenador personal que te va a evaluar y va a poder ponerte rutinas personalidas y darte seguimiento. todo el loop completo.

algunos apuntes sobre taxonomias de datasets: 
 Una buena taxonomía de datasets para evals de agentes separa los datasets por origen, nivel de evaluación, tipo de verdad
  esperada, riesgo, momento de uso y cobertura del comportamiento.

  1. Por Origen
  | Tipo | Qué contiene | Uso principal |
  |---|---|---|
  | Handcrafted | Casos escritos manualmente por expertos | Cubrir reglas críticas y edge cases conocidos |
  | Production-derived | Conversaciones/traces reales anonimizados | Medir comportamiento contra distribución real |
  | Failure-derived | Casos donde el agente falló antes | Regression testing |
  | Synthetic | Casos generados por LLMs/simuladores | Ampliar cobertura rápido |
  | Adversarial | Jailbreaks, prompt injection, abuso, edge cases hostiles | Seguridad y robustez |
  | Expert-labeled | Casos revisados por humanos especialistas | Calibrar jueces y medir calidad real |
  | Benchmark externo | Datasets públicos como SWE-bench, MMLU, etc. | Comparación general de modelos |

  2. Por Nivel De Evaluación
  Para agentes, no basta evaluar la respuesta final.

  | Nivel | Pregunta |
  |---|---|
  | Input/output | ¿La respuesta final fue correcta? |
  | Step-level | ¿Cada paso intermedio fue razonable? |
  | Tool-call | ¿Usó la herramienta correcta con argumentos correctos? |
  | Retrieval/RAG | ¿Recuperó documentos relevantes y los usó bien? |
  | Trace-level | ¿La trayectoria completa fue segura, eficiente y correcta? |
  | Conversation-level | ¿Resolvió el caso multi-turn de principio a fin? |
  | System-level | ¿El workflow completo cumplió objetivos de negocio? |

  Ejemplo: un agente de soporte puede dar una respuesta correcta, pero haber consultado una API prohibida o inventado un estado
  intermedio. Un eval solo de respuesta final no lo detecta; un trace-level eval sí.

  3. Por Tipo De Ground Truth
  | Tipo | Ejemplo | Cuándo usarlo |
  |---|---|---|
  | Exact match | status == "approved" | Clasificación, extracción, routing |
  | Reference answer | Respuesta esperada escrita por humano | QA, soporte, explicación |
  | Rubric-based | “Debe mencionar X, no debe prometer Y” | Tareas abiertas |
  | Pairwise preference | Respuesta A vs B | Comparar modelos/prompts |
  | Constraint-based | No revelar PII, no ejecutar tool sin permiso | Seguridad/compliance |
  | Outcome-based | Cliente resuelto, ticket cerrado, compra completada | Agentes en producción |
  | Human judgment | Calificación experta | Casos ambiguos o de alto riesgo |

  En sistemas maduros, el ground truth rara vez es uno solo. Un caso puede tener expected_tool, expected_args, must_include,
  must_not_include, policy_flags, ideal_answer y success_criteria.

  4. Por Momento De Uso
  | Dataset | Cuándo corre | Objetivo |
  |---|---|---|
  | Smoke eval | Cada cambio pequeño | Detectar roturas obvias |
  | Regression eval | Antes de merge/deploy | Evitar que vuelvan bugs conocidos |
  | Release eval | Antes de producción | Validar calidad global |
  | Canary eval | Durante rollout parcial | Detectar degradación temprana |
  | Online eval | Sobre tráfico real | Monitorear calidad continua |
  | Incident eval | Después de fallo productivo | Convertir incidente en prueba permanente |
  | Drift eval | Periódicamente | Detectar cambios en usuarios/datos/modelo |

  5. Por Riesgo
  Esta dimensión importa mucho en empresas grandes.

  | Riesgo | Ejemplos |
  |---|---|
  | Low risk | FAQ, resumen, reformulación |
  | Medium risk | Soporte, recomendaciones internas, clasificación |
  | High risk | Finanzas, salud, legal, HR, seguridad, acciones irreversibles |
  | Abuse/security | Jailbreaks, prompt injection, data exfiltration |
  | Business-critical | Pricing, refunds, compliance, account actions |

  Los datasets high-risk suelen tener más revisión humana, más casos adversariales y thresholds más estrictos.

  6. Por Comportamiento Que Cubren
  Una taxonomía práctica para agentes:

  | Dataset | Qué prueba |
  |---|---|
  | Intent/routing | Detectar qué quiere el usuario y a qué flujo mandarlo |
  | Policy compliance | Seguir reglas internas |
  | Tool selection | Elegir la herramienta/API correcta |
  | Tool arguments | Construir parámetros válidos |
  | RAG grounding | Usar fuentes correctas, no alucinar |
  | Memory | Recordar lo permitido y olvidar lo que no |
  | Multi-turn | Mantener contexto y resolver casos largos |
  | Clarification | Preguntar cuando falta información |
  | Refusal/escalation | Negarse o escalar cuando corresponde |
  | Recovery | Manejar errores de herramientas o datos inconsistentes |
  | Cost/latency | Resolver sin loops ni llamadas innecesarias |
  | Security | Resistir prompt injection y abuso |
  | UX/tone | Tono, claridad, empatía, brevedad |

  7. Taxonomía Recomendada Para Un Repo De Agentes
  Yo lo organizaría así:

  evals/
    datasets/
      smoke/
      regression/
      production_sample/
      golden/
      synthetic/
      adversarial/
      policy/
      tools/
      rag/
      multi_turn/
      incidents/

  Y cada caso debería tener metadata:

  {
    "id": "refund_policy_001",
    "source": "production_derived",
    "risk": "medium",
    "task_type": "policy_qa",
    "level": "trace",
    "input": "...",
    "expected": {
      "must_include": ["refund window"],
      "must_not_include": ["guaranteed refund"],
      "expected_tools": ["search_policy_docs"]
    },
    "labels": {
      "requires_handoff": false,
      "policy_area": "refunds"
    }
  }

› 15. ¿Qué casos deben vivir en regression/golden set y exigir casi 100%, y cuáles deben vivir en capability set para hill-
    climbing?
    16. ¿Qué criterios hacen que un caso “se gradúe” de capability a regression? como funciona esto en produccion?


• 15. Regression/Golden Vs Capability

  Regression/golden set debe contener casos donde ya sabes cuál es el comportamiento correcto y romperlo sería inaceptable. Ahí
  esperas casi 100%, especialmente en reglas determinísticas, safety, compliance y bugs ya corregidos.

  Deben vivir en regression/golden:

  | Tipo de caso | Ejemplo | Por qué exige casi 100% |
  |---|---|---|
  | Incidentes reales corregidos | El agente prometió un reembolso no permitido | No debe repetirse |
  | Reglas de negocio duras | “Nunca cancelar orden sin confirmación explícita” | Son invariantes |
  | Compliance/legal/safety | No dar consejo médico/legal definitivo | Riesgo alto |
  | Tool-call obligatorio | Para consultar estado de orden debe usar get_order_status | Evita alucinación |
  | Tool-call prohibido | No llamar refund_payment sin autorización | Acción irreversible |
  | PII/security | No revelar datos de otro usuario | Cero tolerancia |
  | Routing crítico | Fraude debe escalarse a humano | Falla cara |
  | Formato/schema | JSON válido, enums correctos, argumentos tipados | Debe ser determinístico |
  | Bugs históricos | Cualquier fallo que ya causó daño o bloqueo | Regression literal |
  | Casos contractuales | SLA, políticas públicas, promesas de producto | No se negocian |

  Capability set debe contener casos donde estás midiendo progreso, no bloqueando todo deploy. Son tareas más abiertas, difíciles,
  ambiguas o aspiracionales. Sirven para hill-climbing: comparar prompts, modelos, RAG, herramientas y estrategias.

  Deben vivir en capability:

  | Tipo de caso | Ejemplo | Por qué no exige 100% |
  |---|---|---|
  | Casos difíciles pero no críticos | Usuario mezcla tres problemas en un mensaje largo | Mide capacidad general |
  | Ambigüedad real | Falta información y hay varias respuestas razonables | No hay una única verdad |
  | Long-tail | Casos raros de producto o idioma | Quieres mejorar, no bloquear siempre |
  | Multi-turn complejo | Conversación larga con cambios de intención | Evalúa madurez |
  | Calidad subjetiva | Tono, claridad, persuasión, concisión | Requiere preferencias |
  | Optimización de UX | Respuesta más útil o más breve | Mejora incremental |
  | RAG difícil | Documentos contradictorios o poco claros | Capacidad de razonamiento |
  | Tool planning complejo | Varias herramientas posibles | Hay grados de calidad |
  | Nuevas features | Casos exploratorios antes de estabilizar behavior | Aún se está definiendo |
  | Benchmarks sintéticos | Variaciones generadas para presión amplia | Pueden tener ruido |

  Regla práctica:

  Regression/golden = “esto no se puede romper”
  Capability = “queremos subir el promedio”

  El error común es meter demasiados casos subjetivos en regression. Eso vuelve el pipeline frágil y bloquea deploys por ruido del
  evaluador. Otro error es dejar bugs reales en capability; eso permite que regresen.

  16. Cuándo Un Caso Se Gradúa De Capability A Regression

  Un caso se gradúa cuando deja de ser “medición de capacidad” y se convierte en “contrato de comportamiento”.

  Criterios típicos:

  | Criterio | Señal |
  |---|---|
  | Hay expected behavior claro | Expertos acuerdan qué debía pasar |
  | El caso representa riesgo real | Impacta dinero, seguridad, compliance, confianza o churn |
  | El fallo ocurrió en producción | Ya no es hipotético |
  | El caso se repite | Aparece en varios traces o tickets |
  | Hay consenso humano alto | Revisores coinciden en la etiqueta/rúbrica |
  | El juez automático es confiable | Correlaciona bien con humanos |
  | La solución ya fue implementada | Prompt/tool/policy/model ya lo maneja |
  | El caso es estable | No depende de datos cambiantes o políticas ambiguas |
  | Es representativo de una clase | No es solo un ejemplo raro sin generalidad |
  | Puede evaluarse de forma robusta | Exact match, rubric clara, tool assertion o judge calibrado |

  Flujo típico en producción:

  1. Caso aparece en producción o en capability eval
  2. Se etiqueta el fallo
  3. Se determina root cause
  4. Se decide si es bug, gap de capacidad o policy ambiguity
  5. Se corrige prompt/model/tool/policy
  6. Se valida con humanos o juez calibrado
  7. Se convierte en regression case
  8. Se agrega a CI/eval gate
  9. Si vuelve a fallar, bloquea deploy o dispara rollback

  Ejemplo concreto:

  Un agente de soporte responde:

  > “Sí, puedes devolver este producto después de 90 días.”

  Pero la política real dice 30 días.

  Primero puede entrar como capability/policy_qa, porque mide si el agente entiende políticas. Si ocurre en producción o se
  considera riesgo contractual, se etiqueta, se corrige el retrieval/prompt y se gradúa a regression:

  {
    "id": "returns_policy_90_days_regression",
    "source": "production_incident",
    "set": "regression",
    "risk": "high",
    "input": "Can I return this after 90 days?",
    "expected": {
      "must_include": ["30 days"],
      "must_not_include": ["90 days", "guaranteed refund"],
      "required_citations": ["returns_policy"]
    },
    "blocking": true
  }

  Cómo Se Maneja En Producción

  Normalmente hay tres niveles de gating:

  | Set | Función | Threshold típico |
  |---|---|---|
  | Smoke | Detectar roturas obvias | 100% |
  | Regression/golden | Proteger contratos e incidentes corregidos | 98-100%, según riesgo |
  | Capability | Medir progreso agregado | No bloquea por caso individual; bloquea solo si cae mucho |

  Capability suele usarse con métricas agregadas:

  task_success: +2.3%
  tool_accuracy: -0.4%
  hallucination_rate: -1.1%
  p95_latency: +300ms
  cost_per_task: +8%

  Regression se usa con postura binaria:

  refund_policy_001 failed -> block deploy
  pii_leak_003 failed -> block deploy
  json_schema_010 failed -> block deploy

  También hay una zona intermedia: release-critical. Son casos que quizá no bloquean cada commit, pero sí bloquean releases
  grandes o cambios de modelo.

  Yo usaría esta regla:

  - golden: comportamiento canónico, estable y de alto valor.
  - regression: bug/incidente ya corregido que no debe volver.
  - production_sample: muestra viva para detectar drift, no necesariamente bloqueante.

  La graduación ideal no es manual arbitraria. Debe pasar por una mini-review:

  ¿Es claro el comportamiento esperado?
  ¿El fallo importa en producción?
  ¿La etiqueta humana es confiable?
  ¿El grader lo puede medir?
  ¿La política/producto ya está estable?
  ¿Queremos bloquear deploys si falla?

  Si la respuesta es sí, se gradúa a regression.


› osea en la practica es util tener un dataset con todo el coverage expected del agente para ir midiendo progreso en coverage de
  cases y otros para medir regresion, otro para medir precision y fiabilidad vs variabilidad


• Sí. En la práctica conviene tener varios datasets con propósitos distintos, no un solo “mega eval”. El error común es mezclar
  cobertura, regresión y calidad estadística en el mismo set.

  Una estructura útil sería:

  | Dataset | Pregunta que responde | Cómo se usa |
  |---|---|---|
  | coverage/capability | ¿Qué tan amplio es el rango de casos que el agente puede manejar? | Hill-climbing, roadmap, mejora
  incremental |
  | regression | ¿Rompimos algo que ya debía funcionar? | Gate casi binario en CI/release |
  | golden | ¿El comportamiento canónico sigue intacto? | Baseline estable, alta confianza |
  | precision/reliability | ¿Qué tan fiable es el agente bajo variación realista? | Medición estadística con múltiples runs |
  | production_sample | ¿Cómo va contra tráfico real reciente? | Drift, monitoreo, priorización |
  | adversarial/safety | ¿Resiste abuso, injection, leaks, policy bypass? | Safety gate y red-team continuo |

  La separación clave:

  Coverage/capability
  Este set es grande, amplio y puede tener casos aspiracionales. Sirve para saber “de todo lo que queremos que el agente haga,
  ¿cuánto ya hace bien?”. No todos los casos bloquean deploy.

  Ejemplo de métricas:

  billing_cases: 82% pass
  refund_cases: 94% pass
  spanish_cases: 76% pass
  multi_turn_cases: 61% pass
  tool_recovery_cases: 58% pass

  Regression/golden
  Este set debe ser más pequeño, estable y muy confiable. Son contratos. Si falla, probablemente bloqueas merge, deploy o cambio
  de modelo.

  critical_regression: 100% required
  business_policy: >= 99%
  schema/tool_args: 100%

  Precision/reliability
  Este set mide variabilidad. Tomas casos importantes y los corres muchas veces, con temperatura, modelos, prompts o condiciones
  variables. No solo preguntas “pasó”, sino “¿pasa consistentemente?”.

  Ejemplo:

  case refund_014:
    runs: 20
    pass_rate: 19/20 = 95%
    tool_correctness: 20/20
    answer_consistency: 17/20
    avg_latency: 2.1s

  Esto es distinto de regression. Un caso puede pasar una vez en regression, pero ser inestable en reliability.

  Una Forma Madura De Verlo

  coverage/capability = amplitud
  regression/golden = no romper contratos
  precision/reliability = consistencia estadística
  production_sample = realidad actual
  adversarial/safety = resistencia a ataques/riesgo

  Y sí: el coverage/capability funciona como mapa de progreso. Puedes tener una matriz:

                      coverage   pass_rate   reliability
  refunds             96%        91%         97%
  billing             88%        84%         92%
  account_access      75%        69%         85%
  spanish_support     62%        58%         81%
  tool_recovery       55%        49%         73%

  Eso te dice dónde invertir. Si tienes alto coverage pero bajo pass rate, el agente entiende el dominio pero falla ejecución. Si
  tienes bajo coverage, faltan casos/datos/políticas/herramientas. Si tienes buen pass rate pero baja reliability, tienes
  variabilidad del modelo o grader.

  La regla práctica:

  No uses capability para bloquear todo.
  No uses regression para medir progreso amplio.
  No uses una sola corrida para medir fiabilidad.
  No uses production_sample como ground truth estable.

  Para producción, lo ideal es operar esto como un portafolio:

  evals/
    datasets/
      capability/
      regression/
      golden/
      reliability/
      production_sample/
      adversarial/
      incidents/

  Y cada release reporta algo como:

  Regression: 100% pass
  Golden: 99.3% pass
  Capability: +4.8 pts vs baseline
  Reliability: 96.1% pass over 10 runs
  Production sample: -1.2 pts drift in billing
  Adversarial: 2 new failures

  Ese reporte sí te permite tomar decisiones: ship, rollback, investigar, ampliar dataset o graduar casos de capability a
  regression.
  Datasets deben estar balanceados y debe de ser muy sencillo filtrar para probar con subsets. los cases deben de ser como bloques de lego para probar distintas combinaciones
  los usuarios de la web deben de tener roles, permisos configurables, etc...

  necesitamos una forma de eliminar PII de los datasets que provengan de produccion
  los llm as a judge deben de tener calibracion humana, la calibracion puede ser hay un conjunto de datos etiquetados por humanos y se itera hasta que converge o en otros tipos de calibracion debe de haber algun tipo de RLHF donde al ver los resultados el humano da feedback se crean mecanismos de "seguridad" o validacion para que el agente no vuelva a cometer esos errores como agregarlos al dataset de evaluacion con las notas para comparacion o attributos deterministicos con funciones (scripts de toda la vida) que bloqueen segun sea el caso.

  2. Qué Significa Calibrar Un LLM-As-Judge

  Calibrar significa comparar el juicio del LLM contra un conjunto de casos etiquetados por humanos y medir si coincide.

  El flujo típico:

  1. Tomas una muestra representativa de casos
  2. Humanos expertos los etiquetan
  3. Corres el LLM judge sobre los mismos casos
  4. Comparas humano vs juez
  5. Mides acuerdo, falsos positivos y falsos negativos
  6. Ajustas rubrica/prompt/modelo del judge
  7. Congelas versión del judge
  8. Lo monitoreas con nuevas muestras

  Ejemplo:

  Dataset: 300 respuestas de soporte
  Humano etiqueta: pass/fail policy compliance
  Judge etiqueta: pass/fail policy compliance

  Resultados:
  precision: 94%
  recall: 88%
  F1: 91%
  false negatives: 12 casos
  false positives: 6 casos

  Si el grader es bloqueante, el recall bajo puede ser peligroso. Significa que el juez deja pasar respuestas malas.

  3. Qué Se Usa Como Referencia Humana

  Normalmente no es “una persona opinando”. Se intenta construir una referencia más estable:

  | Referencia | Uso |
  |---|---|
  | Single expert label | Rápido, barato, útil al inicio |
  | Multiple annotators | Casos subjetivos o críticos |
  | Adjudication | Un senior resuelve desacuerdos |
  | Policy-derived label | La etiqueta viene de una regla formal |
  | Incident label | El caso ya fue confirmado como fallo real |
  | Customer/business outcome | Ticket reabierto, refund incorrecto, CSAT bajo |

  Para graders importantes se usa más de un humano. Si dos humanos no están de acuerdo, el problema quizá no es el juez: puede ser
  que la rúbrica sea ambigua.

  4. Métricas De Calibración

  Las métricas dependen del tipo de grader.

  Para pass/fail:

  accuracy
  precision
  recall
  F1
  false positive rate
  false negative rate

  Para scores 1-5:

  correlation with human scores
  Spearman / Kendall
  MAE: mean absolute error
  score distribution drift

  Para pairwise:

  agreement rate
  win/loss consistency
  tie behavior
  preference flip rate

  Para labels múltiples:

  confusion matrix
  per-class precision/recall
  macro F1

  Y también se mide acuerdo entre humanos:

  human-human agreement
  human-judge agreement

  Si humanos entre sí tienen 82% de acuerdo, no esperas que el LLM judge tenga 98% real. Si lo reporta, probablemente está
  midiendo algo mal.

  en cada run un judge debe de tener una "grader card" si hay un conglomergado de llms pues hay uno por cada uno y uno a nivel grupo. 
  “grader card”:

  name: policy_compliance_judge
  version: v3
  purpose: Detect policy violations in support answers
  blocking: true
  input_fields:
    - user_message
    - agent_response
    - retrieved_policy_docs
  output:
    label: pass | fail
    reason: string
  human_reference:
    dataset: policy_compliance_calib_v2
    annotators: 3
    adjudication: required_on_disagreement
  metrics:
    precision: 0.94
    recall: 0.97
    high_risk_false_negatives: 0
  thresholds:
    min_precision: 0.90
    min_recall: 0.95
    max_high_risk_false_negatives: 0
  review_cadence: weekly

  Eso convierte al judge en un componente auditable, no en magia.
    27. ¿Cómo evitamos que el sistema optimice contra el juez en vez de contra el usuario? Esto es clave si buscamos self-improving
  agents. esta es la pregunta del millon para diseniar bien el sistema, pero por ahora lo que tengo en mente es diseniar buenos evals y tener datasets muy buenos con un gran coverage mi hipotesis es que deberia de ser suficiente, pero ya veremos

  ¿Qué pruebas usamos para validar que un eval es bueno? esta tambien es una buena pregunta, creo que estas dos preguntas solo las podremos responder a traves de la ejecucion y la iteracion.

me gustaria probar diferentes approaches para simulaciones para tener metricas puntuales y experimentales sobre que funciona mejor, por lo visto este proyecto debe de ser de construccion de un harness que permita iteracion, medicion, exploracion y experimentacion de evals y diferenetes approaches para deforma analitica tomar decisiones sobre como movernos si le ponemos memoria, objetivos, personalidad, o le damo sun script o dejamos al modelo experimentar, etc... para lograr esto es importante incorporar un modelo similar al autoresearch de karpathy para iteracion de ese tipo donde en este caso el objetivo es obtener alta variabilidad de casos dentro de un universo con ciertos limites para que todas las conversaciones vayan hacia alla 

loop de experimentos: 
investigar (revisar experimentos anteriores, historial, memoria, failure modes, internet, etc... dependiendo de configuracion), proponer cambio, implementar cambio, correr evals (seleccionar si es sobre un subset, dataset completo, etc...), comparar baseline, decidir: keep/revert o ajustar algo en el experimento para probar una linea similar pero con ciertos ajustes, registrar experimento. algo asi estoy pensando que sea el loop (sujeto a mejoras obviamente) elsistema debe de poder ajustar practicamente todo, pero ese todo debe de ser configurable, es decir para ciertos experimentos el modelo puede ya estar dado o los tools o el dataset, para otros no y asi sucesivamente.
Qué hacemos si un prompt mejora capability pero rompe regression? ¿Siempre se rechaza, se abre investigación, o se acepta
  bajo feature flag? se investiga o depende del disenio del experimento puede fracasar, pasar, abrir experimentos sobre ese experimento, etc... de acuerdo de confirguacion de criterio/s de aceptacion

/Users/patriciovaldez/Desktop/seals ideas/chat-repo/seals/llm/IMG_1573.JPG aca viene mas detalle

me gustaria que este sea un proyecto completamente agnostico a seals y que luego seals consuma a este proyecto a manera de SDK o algo asi, pensemos si lo ideal es agnostico a frameworks o si hacemos integracion con frameworks, etc..

deberiamos de plantearnos si supabase + yaml es la mejor estructura o no

fuentes   Externas: Karpathy/autoresearch (https://github.com/karpathy/autoresearch), Anthropic: Demystifying evals for AI agents
  (https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), OpenAI evaluation best practices
  (https://platform.openai.com/docs/guides/evaluation-best-practices), LangChain AgentEvals
  (https://docs.langchain.com/oss/python/langchain/evals), LangSmith evaluations (https://www.langchain.com/langsmith/evaluation).


    El whiteboard parece organizar el problema así:

  - Auto improving agents
  - Agent context
      - prompt
      - model / params
      - tools / params
  - Experiment
      - iterations / delta
      - motor
      - type
      - target performance
      - output/endpoint
  - Performance
      - cost
      - latency
      - accuracy
      - usage / tools
      - ranges for target
  - Data
      - messages array
      - turns
      - assignees
      - context blocks
      - schemas
      - objects
      - variables / parameters
  - Data labeling and pipeline
      - extract data, schemas, parsers, viewers, taggers, types
      - how to generate data labels
      - people write the summary
      - few variance
      - thesis: work with a few different cases, more or less same needs
  - Multimodal / channels
      - synthetic
      - data generator
      - voice
      - computer usage
      - vision
      - physical
      - text
      - audio
  - Preguntas laterales:
      - integration with system?
      - customization?
      - how can we iterate on these params to maximize performance?
      - how to design an experiment precisely?

  Mi lectura: la imagen agrega una idea importante que en el .md está, pero aquí se ve más operacional: un experimento no solo cambia prompts; cambia un vector de
  parámetros del agente y mide deltas contra un target explícito.

  Eso empuja el framework hacia esta abstracción:

  experiment:
    target:
      metric: weighted_success
      threshold: ">= 0.92"

    editable:
      prompt: true
      model: true
      model_params: true
      tool_descriptions: true
      tool_params: false
      workflow_graph: false
      dataset: false
      grader: false

    run:
      dataset: capability/support_v3
      sample: stratified
      repetitions: 5

    optimize_for:
      - accuracy
      - pass^k
      - latency
      - cost
      - tool_efficiency

  Las preguntas nuevas que salen de la imagen son:

  1. ¿Qué es exactamente un “param” en tu framework: model params, tool params, prompt variables, graph config, memory config, retriever config, voice config? variables que configuran el comportamiento de ese componente del sistema. en el caso del llm puede ser reasoning, verbosity, temperature, proveedor, modelo y asi para cada componente del sistema / harness
  2. ¿El motor de experimentos debe hacer búsqueda automática sobre esos params, o solo registrar variaciones humanas/agente? variaciones logica sobre eso de acuerdo a lo que esta resultando del agente o quiza para algunos parametros como reasoning valga la pena lanzar los experimentos en paralelo 
  lo visualizo como que boostrap tiene 2 modos: 1.- handoff donde haces experimentos corriendo toda la noche y 2.- skills para que un agente (codex, claude code, hermes, openclaw) los haga con input tuyo o the loop 
  3. ¿Cómo representamos un “delta” entre iteraciones? diff de metrics.
  4. ¿Qué tipos de experimento existen?
      - prompt optimization
      - model comparison
      - tool selection optimization
      - tool consolidation/splitting
      - graph topology experiment
      - memory/retrieval experiment
      - simulation strategy experiment
      - voice/latency experiment
      - cost reduction experiment
      etc... pero mas que declararlo debe de estar implicito de acuerdo a seleccion de parametros del experimento
  5. ¿Cómo se define un target performance multidimensional sin esconder tradeoffs? Ej: sube accuracy pero empeora latency/cost no estoy seguro.
  6. ¿Qué datos mínimos necesita capturar el harness para que un agente externo pueda aprender de runs pasados? todos los parametros resultantes para que la ejecucion sea esa
  7. ¿El framework debe tener data labeling como módulo core o como integración/plugin? me gustaria hacerlo como uno de los modulos. pero no es el principal y creo que en realidad este proyecto se fusiona con lo que estaba buscando hacer con el agent garage
  8. ¿Cómo soportamos multimodal desde el schema base sin complicar el MVP? podemos hacer que el MVP sea puro texto por ahora (con tests de imagenes, attachments, etc... obviamente tambien)

  Creo que esta imagen confirma que el centro del framework debería ser:

  Experiment + AgentArtifact + Dataset + RunTrace + GraderCard + OptimizationLoop

  No EvalCase solamente. EvalCase es una pieza; el producto grande es el loop completo.
