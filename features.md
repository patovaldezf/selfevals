  Qué nos falta para volar — y qué le falta a selfevals

  Hay que separar dos preguntas distintas:

  ---
  A. Lo que falta del lado de B (nosotros) — el grueso del trabajo

  Hoy el bridge hace una sola cosa: 1 LLM call sobre los mensajes → exact-match. El motor viejo tiene 12 evaluadores y solo 2
  features nuestras son "messages-only". Los gaps reales:

  🔴 Gap #1 — Evaluación graph-backed (el bloqueador grande).
  8 de los 12 evaluadores (search, identify_customer, extract_message_content, create_quote, create_order,
  confirm_order_permission, copywriter, special_orders) no se evalúan con una LLM call — necesitan correr el grafo real con ES +
  Supabase + checkpoints, y leer el state resultante. Nuestro adapter HTTP actual no puede hacer eso. Para esos features, el
  /invoke de seals tendría que correr el grafo de verdad (no reconstruir una cadena), leer el checkpoint, y devolver el state
  detectado. Es la pieza que separa "2 features" de "los 12".

  🔴 Gap #2 — Scoring sofisticado como custom graders.
  El motor viejo tiene confusion matrices, many-to-many field matching (productos), fuzzy + LLM-fallback, council de 6 jueces
  (human_writer), funnels de 2 stages (finder→resolver). Hoy usamos el deterministic built-in (exact-match plano). La buena
  noticia: selfevals soporta custom graders por dotted-path que devuelven un BreakdownNode tree rico — toda esa lógica vieja puede
  vivir como graders custom. Pero hay que escribirlos.

  🟡 Gap #3 — Multi-turn real / penultimate checkpoint.
  confirm_order_permission y copywriter evalúan turn-by-turn contra labels de Supabase; human_writer/context_usage leen el
  checkpoint penúltimo. selfevals tiene MultiTurnExecutor nativo, pero hay que mapear nuestros labels per-turn.

  ---
  B. Lo que le falta a selfevals (A) — sorprendentemente poco

  selfevals 0.7.0 ya es mucho más potente que nuestro motor viejo. Lo que el viejo NO tenía y selfevals SÍ (production-ready):

  - ✅ Optimization loop real (propone cambios → corre → puntúa → decide → repite, guarda el mejor). El viejo era una calculadora
  estática.
  - ✅ Judge panel genérico (consenso, defensas) → reemplaza nuestro council hand-rolled de 6 miembros.
  - ✅ Reliability metrics (pass@k, pass^k, consistency_rate con repeticiones) → el viejo no medía robustez, solo single-shot.
  - ✅ Trace/observability (spans, costo, tokens, cache, SSE en vivo, comparar experimentos) → el viejo no tenía nada.
  - ✅ Holdout/splits/stratified sampling → el viejo no gestionaba datasets.
  - ✅ Error analysis (taxonomía de failure modes, hipótesis) → el viejo solo reportaba pass/fail.

  Lo que le falta a selfevals (gaps reales):
  1. 🔴 Error-analysis auto-trigger NO está cableado (el schema lo declara, pero el loop no llama al trigger por fail_rate). ~20
  líneas en el loop de A.
  2. 🟡 Auth stubbed (solo header X-SelfEvals-User) — bloquea exponerlo fuera de localhost. El FE/Playground lo va a topar.
  3. 🟡 Counterfactual paraphrase generation stubbed (no-op).
  4. 🟡 Dataset versioning está en 0.8.0, no en 0.7.0.
  5. 🟡 User simulator es interfaz, sin agente default.

  ---
  El veredicto: "100x" se logra, y el 80% del trabajo es nuestro

  selfevals ya tiene el motor 100x. Lo que falta para volar es casi todo del lado B: escribir los graders custom + hacer el /invoke graph-backed. No es que
  falte capacidad en A — es que solo estamos usando el 10% de A.

  datasets feature

    1. Multi-turn adapter para voice — el AdapterRequest asume un invoke por caso. Voice necesita simular un diálogo. Aquí sí podría hacer falta un request a
  selfevals si quieres que el motor maneje la conversación; pero el patrón normal es que el adapter HTTP de seals corra el diálogo internamente y devuelva la
  trace para que trajectory la califique. Hay que verificar qué espera el trajectory grader como trace/spans — eso decide si es trabajo de seals o request a
  selfevals. necesitamos poder simular 2 agentes conversacionando entre ellos. uno es el que estamos evaluando y nosotros simulamos la contraparte, tanto voz como texto deben de poderse cubrir aca.

  necesitamos mas evaluadores como algo para el siguiente caso: 
   Qué hace cada grader que SÍ existe en 0.7.0

  Todos comparten el mismo principio: el verdict sale de reglas declaradas en EvalCase.expected o de invariantes — el grader nunca confía en lo que el agente
  diga de sí mismo.

  ┌───────────────────────┬──────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │        Grader         │      Qué evalúa      │                                         Cómo decide PASS/FAIL                                          │
  ├───────────────────────┼──────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ deterministic         │ Etiquetas/outputs    │ Reglas de expected: must_include, must_not_include, required_tools, forbidden_tools, regex_match, y    │
  │                       │ exactos              │ structured_output equality (match exacto del dict). Es el que usa categorize_message hoy.              │
  ├───────────────────────┼──────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ guardrail             │ Invariantes de       │ Reglas deterministas: patrones prohibidos/requeridos, PII (email/phone/SSN/tarjeta con Luhn), valores  │
  │                       │ contenido            │ monetarios contradictorios. Hard-gate: viola → FAIL siempre.                                           │
  ├───────────────────────┼──────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ trajectory            │ Trayectoria de tools │ Output-state autoritativo (delega en deterministic) + funnel diagnóstico: orden de tools, loops,       │
  │                       │                      │ routing. Para agentes con tool-calling (→ voice).                                                      │
  ├───────────────────────┼──────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ artifact_completeness │ Artefactos           │ Valida structured_output contra output_schema + required_sections. Para agentes que producen           │
  │                       │ estructurados        │ documentos (→ researcher).                                                                             │
  └───────────────────────┴──────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  El patrón clave: selfevals calcula el score él mismo, dentro de su proceso, comparando contra expected.

  Por qué seals pidió passthrough / http_callback

  Aquí está el conflicto de diseño. seals tiene scoring calibrado que ninguno de esos 4 graders sabe hacer: para intention_detection/extract, el matching es
  many-to-many — completeness/precision/F1 sobre conjuntos, con un breakdown tree (funnel anidado) que el FE ya sabe renderizar. Eso es la IP de evals de
  seals.

  El problema es que esa lógica vive en seals (build_funnel_for), y selfevals califica dentro de su proceso. Hay un choque con dos hechos del contrato:

  1. El AdapterRequest HTTP no lleva expected. Verificado arriba: solo input/context/metadata.taxonomy. Entonces seals, dentro de su /invoke, no tiene contra 
  qué comparar — tiene el detected pero no el expected.
  2. Los graders corren in-process importando código. Para que selfevals use el funnel de seals tendría que import seals — lo que rompe el desacople duro
  (seals nunca importa selfevals y viceversa).

  Resultado: ni deterministic (exact-match, demasiado rígido para sets) ni must_include+min_recall (aproxima recall pero no precision/F1) preservan el funnel
  calibrado.

  Para qué se necesita passthrough, entonces: es la pieza que cierra el círculo de "que seals se califique a sí mismo y selfevals solo persista el resultado":

  Opción 1 (la que prefieren):
    1. selfevals reenvía `expected` en el AdapterRequest HTTP
    2. seals calcula el funnel DENTRO de /invoke (ya tiene detected + ahora expected)
    3. seals lo devuelve en AdapterResponse.structured_output = {passed, score, breakdown, ...}
    4. ── aquí entra passthrough ── un grader trivial que ADOPTA ese score/breakdown como verdad
       en vez de recalcular. No importa nada de seals; es genérico.

  passthrough es el grader que dice "el agente ya se puntuó correctamente, solo persiste su score y deja que el breakdown tree llegue al storage". Sin él,
  aunque seals calcule el funnel perfecto, selfevals lo ignoraría y re-puntuaría con su propio grader.

  http_callback (Opción 2) es la alternativa más general: en vez de que el agente se autopuntúe, selfevals POSTea (case, response, trace) a un endpoint de
  grading de seals y recibe el GradeResult. Mantiene el grading remoto y desacoplado, reusable por cualquier cliente — pero es más trabajo en el motor.

  En una frase

  passthrough/http_callback se necesitan solo para preservar el scoring calibrado many-to-many de seals (intention/extract) sin que selfevals tenga que
  importar código de seals. Los 4 graders que sí existen cubren los casos donde selfevals puede puntuar solo (exact-match, schema, trayectoria, guardrails) —
  que alcanzan para categorize, researcher y voice, pero no para el funnel propietario de intention/extract.

  ¿Quieres que evalúe si conviene empujar la Opción 1 (más chica, desbloquea ya) o si dado que 0.7.0 ya trae trajectory+artifact, hay forma de reformular
  intention/extract para no necesitar passthrough en absoluto?

✻ Cooked for 34s

※ recap: Goal: figure out what selfevals needs to evaluate voice, researcher, and copilot. Findings: the bridge only covers categorize and intention; 
  selfevals 0.7.0 already ships graders for the three agents but the Workstream A request (expected + passthrough) is still pending. Next: decide whether to 
  push that request or reformulate to avoid it. (disable recaps in /config)

❯   Aquí está el conflicto de diseño. seals tiene scoring calibrado que ninguno de esos 4 graders sabe hacer: para intention_detection/extract, el matching   
  es                                                                                                                                                          
    many-to-many — completeness/precision/F1 sobre conjuntos, con un breakdown tree (funnel anidado) que el FE ya sabe renderizar. Eso es la IP de evals de   
    seals. porque esto se requiere asi?                                                                                                                       

⏺ Buena pregunta de fondo. "¿Por qué requiere ser así?" tiene dos capas: (1) por qué el matching es many-to-many y no exact-match, y (2) por qué eso vive como
  IP en seals y no como un grader genérico. Déjame mirar el código real del matcher de intentions para no contártelo de memoria.

⏺ Bash(cd "/Users/patriciovaldez/Desktop/seals ideas/chat-repo/seals/llm" && echo "===== intention_detection matchers =====" && sed -n '1,120p'
      src/core/playground/eva…)
  ⎿  ===== intention_detection matchers =====
     import typing as t
     … +118 lines (ctrl+o to expand)

⏺ Bash(cd "/Users/patriciovaldez/Desktop/seals ideas/chat-repo/seals/llm" && echo "===== files in intention_detection eval =====" && ls
      src/core/playground/evals/inten…)
  ⎿  ===== files in intention_detection eval =====
     __init__.py
     __pycache__
     … +51 lines (ctrl+o to expand)
  ⎿  Allowed by auto mode classifier

⏺ Déjame ver la matemática real de completeness/correctness/precision para explicarte por qué requiere ser many-to-many.

⏺ Bash(cd "/Users/patriciovaldez/Desktop/seals ideas/chat-repo/seals/llm" && sed -n '155,230p' src/core/playground/evals/intention_detection/evaluator.py)
  ⎿  def _evaluate_completeness(detected: list, expected: list) -> bool:
         """
         Evaluate completeness: All expected intentions detected (extras allowed).
     … +73 lines (ctrl+o to expand)

⏺ Ahora puedo responderte con el código en la mano. La pregunta es: ¿por qué requiere ser many-to-many y no exact-match? Tres razones, una de fondo (el
  dominio) y dos de diseño.

  1. Por qué el matching es many-to-many: porque un mensaje real tiene N intenciones a la vez

  El dato de verdad no es una etiqueta, es un conjunto. Un mensaje de cliente como "¿cuánto cuesta el WD56004 y tienen en stock? mándame cotización" tiene
  tres intenciones simultáneas: Price Check, Inventory Check, Quote Request. Lo mismo en extract: un mensaje pide varios productos, cada uno con su
  cantidad/marca.

  Cuando lo que comparas son dos conjuntos (detected vs expected), exact-match (detected == expected) te da una sola respuesta binaria que colapsa información
  crítica. Si el esperado es {Price, Inventory, Quote} y detectaste {Price, Inventory}:

  - exact-match dice: FAIL. Punto. 0%.
  - eso es una mentira útil-cero: detectaste 2 de 3 — el modelo está casi bien, le faltó una. Tratar eso igual que "no detectó nada" o "alucinó 5 intenciones
  falsas" hace el eval inútil para mejorar el modelo.

  Por eso el código (lo acabas de ver) descompone el verdict binario en tres dimensiones ortogonales que un conjunto sí permite medir:

  completeness: expected ⊆ detected   # ¿detecté TODO lo que debía? (no me falta nada)
  correctness:  detected ⊆ expected   # ¿solo detecté cosas válidas? (no inventé nada)
  precision:    detected == expected  # ambas → match perfecto

  más recall/precision/F1 continuos (|intersección| / |conjunto|). Eso te dice no solo si falló sino cómo: ¿peca de omisión (falta recall) o de exceso (falta
  precision)? Son fallas opuestas que se arreglan tuneando el prompt en direcciones opuestas. Exact-match no distingue.

  → Requiere ser many-to-many porque el ground truth ES un conjunto, y reducir un conjunto a un booleano destruye justo la señal que el eval existe para 
  capturar.

  2. Por qué el "breakdown tree" (funnel anidado): porque el FE ya lo renderiza así

  Esas tres dimensiones no se reportan como tres números sueltos sino como un árbol (completeness_passed → hijos por intención, etc.). ¿Por qué árbol y no
  flat? Porque el Playground FE ya tiene la UI para drill-down jerárquico: ves el accuracy global, lo abres y ves por dimensión, lo abres y ves qué intención
  específica falló en qué caso. Esa estructura de árbol es un contrato con el frontend que ya existe y funciona. Reescribirla a flat rompería el FE.

  3. Por qué es "IP de seals" y no un grader genérico de selfevals

  Aquí está el punto más sutil de tu pregunta. ¿No podría selfevals tener un grader set_f1 genérico? Sí para la matemática, no para la calibración. Lo que
  hace propietario al scoring no es la fórmula F1 (eso es estándar) — es:

  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.
  3. Por qué es "IP de seals" y no un grader genérico de selfevals

  Aquí está el punto más sutil de tu pregunta. ¿No podría selfevals tener un grader set_f1 genérico? Sí para la matemática, no para la calibración. Lo que
  hace propietario al scoring no es la fórmula F1 (eso es estándar) — es:

  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.

  Aquí está el punto más sutil de tu pregunta. ¿No podría selfevals tener un grader set_f1 genérico? Sí para la matemática, no para la calibración. Lo que
  hace propietario al scoring no es la fórmula F1 (eso es estándar) — es:

  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.
  Aquí está el punto más sutil de tu pregunta. ¿No podría selfevals tener un grader set_f1 genérico? Sí para la matemática, no para la calibración. Lo que
  hace propietario al scoring no es la fórmula F1 (eso es estándar) — es:

  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.
  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.
  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.

  En una frase

  En una frase


  No es un capricho: requiere ser many-to-many porque el ground truth es un conjunto multi-etiqueta y exact-match tira la señal de "qué tan cerca/de qué lado
  no-agnóstico. La matemática F1 es genérica; la calibración no lo es — y por eso hace falta passthrough (que seals puntúe, selfevals persista) en vez de un
  específica falló en qué caso. Esa estructura de árbol es un contrato con el frontend que ya existe y funciona. Reescribirla a flat rompería el FE.

  3. Por qué es "IP de seals" y no un grader genérico de selfevals

  Aquí está el punto más sutil de tu pregunta. ¿No podría selfevals tener un grader set_f1 genérico? Sí para la matemática, no para la calibración. Lo que
  hace propietario al scoring no es la fórmula F1 (eso es estándar) — es:

  - La normalización canónica que viste en matchers.py: place_full_order → "Direct Order (Place Full Order)", con ~40 alias legacy mapeados a mano. Sin eso,
  detected={price_check} vs expected={Price Check} darían F1=0 por ser strings distintos siendo la misma intención. Esa tabla de mapeo es conocimiento de
  dominio de seals, acumulado por drift histórico del esquema.
  - Los umbrales calibrados: qué cuenta como "passed" (¿completeness ≥ 1.0? ¿F1 ≥ 0.8?), qué dimensión gatea el funnel, cómo se ponderan. Eso se afinó contra
  los datos reales de seals.

  selfevals es deliberadamente agnóstico (sus graders dicen literalmente "no domain assumptions"). Meter la tabla de alias + umbrales de seals adentro de
  selfevals rompería esa agnosticidad y crearía la dependencia selfevals → seals que el diseño prohíbe.

  En una frase

  No es un capricho: requiere ser many-to-many porque el ground truth es un conjunto multi-etiqueta y exact-match tira la señal de "qué tan cerca/de qué lado
  falló"; y vive como IP de seals porque la parte calibrada (normalización canónica + umbrales) es conocimiento de dominio que volvería a selfevals
  no-agnóstico. La matemática F1 es genérica; la calibración no lo es — y por eso hace falta passthrough (que seals puntúe, selfevals persista) en vez de un
  grader F1 del motor.

  obviamente nosotros no haremos ningun mapping, pero si podemos recibir cosas asi.
  llm as a judge con cuantos judges y porque?