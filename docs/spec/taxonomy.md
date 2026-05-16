¿La taxonomía es principalmente para organizar datasets, correr evals, reportar dashboards, guiar optimización automática, o todo lo anterior? todo lo anterior
¿El usuario principal de la taxonomía será humano, agente automático, o ambos? el humano tiene que entenderlo, interpretarlo ser claro que es cada cosa, no poner nombres contrarias a lo que ya existe en la industria, entonces debe de ser consistente, logico, natural y el agente debe de poder entender claramente la diferencia entre cada uno de los conceptos sin tirar monte halucinar, si nada del estilo, natural a lo que es su training dataset, entonces no debe de contraponerse con ningun concepto existente, adoptar convenciones de la industria, etc... entendamos industria no solo AI agents, si no toda la capa completa de ML, DL, RL, bigdata, etc...
¿Quieres que sea simple para MVP o suficientemente completa desde el inicio aunque algunas dimensiones se usen después? completa desde el inicio
¿La taxonomía se aplica a cada EvalCase, a cada Dataset, a cada Experiment, o a los tres? a los tres
¿Un caso puede tener múltiples etiquetas por dimensión o prefieres una principal y varias secundarias? para responder esta pregunta usare a seals como referencia. tenemos varios featues y aveces me gustaria tener un case especifico para cada feature para evals que son muy unicos de cada feature. por ejemplo provar que el product resolver sanciona bien cual es el producto correcto. pero hay otros cases (generalmente los que son el mensaje o la conversacion persee) que si sriven para mas de un feature y la verdad es que me gustaria poder etiquetarlo a nivel granular maximo de este sirve para categorizar conversacion, mensaje, search, identify customer. el tema es que ademas tengo search por nombre, por sku, por use case, por imagen, ademas de etiquetar si hay resultado o no o deberia de proponer alternativa, etc... control granular para evaluacion precisa e iteracion logica para resultados de calidad
anexos: /Users/patriciovaldez/Desktop/seals\ ideas/playground/seals-playground y /Users/patriciovaldez/Desktop/seals\ ideas/chat-repo/seals/llm
¿Quieres que cada caso tenga primary_behavior + secondary_behaviors, o sólo behaviors: []? creo que arriba responde la pregunta. pero no se si behaviors sea la palabra adecuada para esto
¿Quieres que la taxonomía sea estricta con enums cerrados o extensible con tags libres? algunas partes enums cerrados para los componentes especificos del sistema y otros como tags libres para componentes utiles para personalizar evaluacion, experimentacion y resultados
8. ¿Qué niveles son indispensables para ti? (por ahora estos son los que parecen logicos, pero me gustaria dejar muy abierto aqui a diferentes tipos de niveles nuevos que se puedan ir agregadno, porque estos van en funcion de necesidad)

  - single_step
  - multi_step
  - final_response
  - step_level
  - tool_call
  - retrieval
  - memory_context
  - workflow
  - trajectory
  - conversation
  - system
 ¿tool_call, retrieval y memory_context deben ser niveles propios o comportamientos dentro de trajectory? niveles propios esta bien para evaluar granularidad y subexperimentos, pero seguramente no vayan a ser los mas usados a nivel individual, probablmeente hace mas sentidos evaluarlos en conjunto a muchos de ellos
 ¿Quieres distinguir workflow de trajectory? Mi lectura: workflow evalúa si siguió el proceso esperado; trajectory evalúa todo el camino real, incluyendo errores, retries, tools, costo y seguridad. workflow para flujos, graphs, etc... trajectory para agentes, ya que normalmente arrancan sin camino establecido especifico incial
 ¿Quieres incluir agent_artifact como nivel para evaluar prompt/model/tool/graph sin correr una tarea completa? si, pero creo que podemos llamarlo mas simple, solo agent sin necesidad del artifact
 el framework viene con la intencion de cerrar el gap que existe entre el performance del agente en produccion y su desarrollo. en una startup antes habia un chasm, ahora (en startups de ai agents) hay 2 chasms PMF (el de siempre) y accuracy (el nuevo). es decir, ahora sigue siendo complejo conceptualizar un producto que tenga atachment, haga sentido con problemas, los resuelva, sea magico, 10x superior, etc... y que el producto funcione como se supone y decimos que deberia de funcionar. para ello existe el framework la parte de medicion, iteracion, experimentacion y la parte de proposicion, analysis, monitoreo, etc..
 para lograr todo eso es importante tomar decisiones en modelo, parametros, granularidad de skills, que hable como humano, herramientas, etc... y para cada uno de estas variables hay que jugar con sus parametros (costo, latencia, efectividad, etc...) para llegar a computar un accuracy final total. ademas de tener en cuenta cosas como prompt injection, cosas a las que se debe de reusar a hacer, etc..
  17. ¿Qué fuentes quieres soportar desde el inicio? (propuesta no final)

  - handcrafted
  - production
  - failure
  - staging 
  - dev
  - synthetic
  - adversarial
  - human_labeled
  - external_benchmark
  - simulation
  - custom (permitir poner nombre)
me parece muy importante poder ponderar los errores para tomar decisiones en la iteracion y saber cuales son criticos, cuales no pasa mucho que sucedan porque por el sistema se puede rescatar el agente, etc..
necesitamos obvaiemnte un motor de generacion de data synthetica (v2) igual o powered en cloud o powered by claude code
PII status metadata
no usemos la palabra oraculo, ground_truth esta bien
  21. ¿Qué tipos de oráculo necesitamos?

  - exact match
  - schema validation
  - deterministic assertions
  - reference answer
  - rubric
  - pairwise preference
  - outcome based
  - human judgment
  - LLM judge
  - hybrid
estos pueden ser por ahora, no me encantan los nombres y seguramente habra ediciones en los tipos de oraculos para agregar, quitar, modificar, etc..
¿Quieres que un caso pueda tener varios oráculos al mismo tiempo? obvio depende de la naturaleza de lo que estemos evaluando para que no se contradigan. vamos a hablar de un ejemplo simple. categorizar como spam. tu puedes etiquetar los datos de spam y no spam para evaluarlos de manera determinista, puedes tener un sistema de labeling junto con tuning humano para evaluar efectividad del agente en etiquetado de datasets no etiquetados y ahcer una especie de RLHF en prompt en esa via y ademas se necesitan judges LLMs para conversaciones en produccion que logicamente no estan etiquetadas y quieres evaluar el accuracy del agente en produccion on the fly. aqui tendriaas que probar el prompt del etiquetador con muchisimos casos para verificar que lo hace bien + deberias de experimentar con approaches hibridos de LLM as a judge + clustering semantico + matching keywords, etc.. entre otras opciones tradicionales o hasta finetunear o entrenar un modelo para eso. pero para tomar esa decision hay que tener en cuenta el treshold de accuracy que quiero cruzar, latencia, costos, tiempo de desarrollo, recursos, etc... y con este framewrok debes de poder evaluar todo ese tipio de decisiones para agentes y workflows tanto tu, como con acompaniante delegando tareas en la nube un Agent o con tu personal ai agent (claude, codex, hermes, openclaw, etc..)

