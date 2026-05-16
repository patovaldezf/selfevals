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

  ExperimentSpec + AgentArtifact + Dataset + RunTrace + GraderCard + OptimizationLoop

  No EvalCase solamente. EvalCase es una pieza; el producto grande es el loop completo.
