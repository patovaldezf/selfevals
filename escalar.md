
  Veredicto: listo para miles o decenas de miles de registros con uso cuidadoso; riesgoso en cientos de miles; no listo para millones sin rediseño de storage/
  queries/jobs.

  Bloqueadores principales

  1. Storage genérico en SQLite con JSON opaco
     La tabla central entities guarda todos los modelos como payload TEXT; solo indexa workspace_id, entity_type, created_at, updated_at, no campos de negocio
     como experiment_id, run_id, thread_id, iteration.
     Referencia: src/selfevals/storage/migrations/m0001_initial.py:28

  2. Consultas por json_extract sin índices
     list_entities() filtra campos no-columnas con json_extract(payload, '$....'), lo que fuerza scans sobre todas las entidades del tipo/workspace.
     Referencia: src/selfevals/storage/sqlite.py:175

  3. Endpoints cargan colecciones completas en memoria
     list_experiments() pagina después de cargar todos los experimentos y todas las iteraciones del workspace.
     Referencia: src/selfevals/api/queries.py:146

     experiment_cases() además escanea traces del experimento para calcular el último trace por case.
     Referencia: src/selfevals/api/queries.py:245

     experiment_results() reconstruye resultados leyendo iteraciones, traces y cases completos.
     Referencia: src/selfevals/api/queries.py:331

  4. Lookup de trace por run_id y thread por thread_id escanean JSON
     El propio comentario dice que no está indexado y asume tabla pequeña.
     Referencia: src/selfevals/api/queries.py:588

  5. Runs en threads daemon dentro del proceso
     No hay job queue persistente, workers separados, retry durable, leasing ni recuperación robusta si el proceso muere.
     Referencia: src/selfevals/api/run_launcher.py:83

  6. Broker SSE en memoria y single-process
     El código lo documenta como broker en memoria de un solo proceso, con Redis como reemplazo futuro.
     Referencia: src/selfevals/api/broker.py:13

  7. La documentación de deploy ya advierte no escalar horizontalmente
     Dice explícitamente: no múltiples máquinas; SQLite single-writer; broker per-process; escalar out requiere PostgresStorage.
     Referencia: docs/deploy.md:96

  Qué sí está bien

  El diseño es honesto y modular: hay StorageInterface, WorkspaceScope, payloads grandes se offloadean a filesystem, hay WAL, concurrencia acotada para
  ejecuciones/graders, y la documentación reconoce los límites. Eso ayuda mucho para migrar.

  Qué falta para millones

  - Migrar entidades calientes a tablas relacionales reales: experiments, iterations, traces, eval_cases, decisions, spans.
  - Índices compuestos mínimos: (workspace_id, experiment_id), (workspace_id, run_id), (workspace_id, thread_id), (workspace_id, experiment_id, iteration).
  - Reemplazar scans JSON por queries SQL indexadas.
  - Paginación real en todos los endpoints, no solo envelope superficial.
  - Job queue persistente: Redis/RQ, Celery, Dramatiq, Temporal, etc.
  - Broker externo para SSE/live events: Redis streams/pubsub, NATS, Kafka, etc.
  - Backend Postgres para multi-writer/multi-node.
  - Pruebas de carga con datasets sintéticos de 1M+ traces/iterations.

  Conclusión directa: no lo escalaría a millones de registros en producción con esta arquitectura. Lo consideraría una base buena para producto inicial, pero
  antes de millones necesita una fase clara de “scale architecture”: Postgres + índices + queries paginadas + workers + broker externo.