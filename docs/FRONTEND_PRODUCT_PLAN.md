# selfevals Frontend — Product Plan

> El plan de producto del frontend de selfevals: la web UI para **visibilidad total**
> de evals de agentes AI. Este doc es la síntesis de un review de producto en 4 capas:
> (1) dogfood a detalle del producto actual con evidencia, (2) benchmark competitivo
> citado, (3) los filtros de fundadores aplicados a las decisiones grandes, y (4) el
> plan: visión, momento mágico, priorización despiadada de vistas, decisiones de UX por
> pantalla, y dependencias de backend.
>
> Fuentes que espeja: [`docs/FRONTEND.md`](FRONTEND.md) (spec FE), [`docs/ROADMAP.md`](ROADMAP.md)
> (backend que el FE espeja). Stack cerrado en [`docs/web/decisions.md`](web/decisions.md) —
> no se relitiga.
>
> Estado: **plan, pendiente de aprobación.** No se ha tocado código de la app. Solo tras
> aprobación, programamos.

Fecha: 2026-05-27.

---

## 0. TL;DR (para el que solo lee esto)

- **El producto actual ya es bueno donde existe.** El experiment detail (Iterations/Compare/
  Decisions + drawer de iteración) es calidad Stripe/Linear. El dogfood lo confirma con
  screenshots. Esto NO es un rescate; es subir un buen v0 a un gran v1.
- **Pero hay 3 bugs apilados que hacen el producto inusable más allá del landing** en una
  sesión real (detalle en §1.3). Todos backend o borde FE↔API, todos baratos. **Son el
  Bloqueo 0: nada del plan importa hasta que cada ruta deje de dar 500.**
- **El momento mágico no es "ver un dashboard". Es: de una métrica que bajó, llegar en
  dos clics a la traza exacta del agente que la causó, con el prompt/args/output reales
  delante.** Hoy ese camino está roto en los dos extremos (no hay link iteración→traza, y
  los pointers del trace no se resuelven).
- **El white space competitivo, confirmado por el benchmark: nadie junta (a) calibración
  de jueces de primera clase con (b) "muéstrame qué empeoró" en un clic.** Langfuse tiene
  (a), LangSmith/Braintrust tienen (b). selfevals puede tener ambas. Ese es el ángulo.
- **Priorización (despiadada): Fase A = arreglar el camino crítico (los 3 bugs + link
  iteración→traza + resolver pointers + `serve`). Fase B = thread viewer + funnel.
  Fase C = jueces/calibración (la apuesta diferenciadora). Lo demás se difiere o se corta.**

---

## 1. Capa 1 — Dogfood del producto actual (con evidencia)

Metodología: levanté API (`python -m selfevals.api --db`) + web (`vite dev`, proxy `/api`
→ `:8000`), sembré un DB limpio con el ejemplo bundled `example_pingpong` (sin API key:
2 experimentos, 4 iteraciones, 4 decisiones, 4 trazas), y navegué cada ruta con un browser
headless tomando screenshots. Las capturas viven en `/tmp/selfevals_shots/` (efímeras; las
describo aquí).

### 1.1 Qué funciona (y funciona bien)

| Ruta | Veredicto | Evidencia |
|------|-----------|-----------|
| `/` workspace list | ✅ Limpio, SSR, sin errores de consola | Card con métricas, CTA implícito |
| `/[ws]` overview | ✅ (tras desbloquear) Hero + 3 stat cards + tabla de experimentos con sparkline + skeletons honestos | "Failure clusters soon", "Datasets 2 active" |
| `/[ws]/experiments` | ✅ **Mejor de lo que la spec dice** ("scaffolded"). Tabla limpia: nombre+ULID, state, proposer, iterations, updated | — |
| `/[ws]/experiments/[exp]` | ✅ **La joya.** Breadcrumb + eyebrow + título + goal; 4 stat cards (Best primary, Target, Iterations, Trend+sparkline); 3 tabs | Calidad Linear/Stripe |
| → tab Iterations | ✅ #, Parameters (chip mono), Primary, **Δ best con signo+color**, Decision badge (amber/verde), Rationale | Color semántico correcto |
| → drawer de iteración | ✅ Drawer derecho, dim de fondo, "Close (Esc)", Hypothesis, Parameters (JSON pretty), grid de métricas, Decision badge+rationale, Record id mono | Patrón drill-down bien hecho |
| → tab Compare | ✅ Dos `<select>` accesibles, A/B side-by-side, `Δ B − A: +1` | Ver gaps en §1.4 |
| → tab Decisions | ✅ Audit trail: #, badge, timestamp, rationale | — |
| `/[ws]/anchor-set` | ✅ **Mejor de lo que la spec dice** ("skeletal"). Cards por experimento con "latest" + sparkline; honesto sobre el gap §H | — |
| `/[ws]/traces/[tr]` | 🟡 Renderiza (3 paneles: árbol de spans / detalle / meta) pero **inestable** — ver §1.3 bug #3 | Header, árbol `case:say pong → adapter_response`, spans son `[button]` (accesibles) |
| `/[ws]/clusters` | ⚪ Stub diseñado: "Top clusters" hardcoded, card "Connect Linear (soon)", empty state honesto §J.6 | Ver §1.5 (Linear no está en el ROADMAP) |
| `/[ws]/datasets` | ⚪ Stub delgado: "ships next, use the CLI" | — |
| `/ws_DOES_NOT_EXIST` | ✅ 404 limpio "Workspace not found" | error() de SvelteKit bien usado |

**Lo que está bien y hay que PRESERVAR:** densidad correcta, tabular numerals en mono,
Δ con signo+color, badges de decisión semánticos, el drawer como patrón de drill-down,
empty states honestos ("soon", "ships next") en vez de spinners infinitos, 404 manejado.
El design language es real y consistente. No tocar el "skin".

### 1.2 Qué se siente roto o falta (UX, no bugs)

1. **El ULID es ciudadano de primera clase, el humano de segunda.** El título del workspace
   ES el ULID gigante (`ws_01HZZ…`) repetido como name y slug. Las dos `pingpong baseline`
   son indistinguibles en la tabla. El breadcrump dice `workspace` genérico + ULID de
   experimento. **Un dev nunca debería leer un ULID para orientarse.**
2. **No hay puente iteración → traza.** El drawer de iteración tiene `trace_run_ids[]` en
   los datos pero NO los muestra. No puedes ir de "esta iteración bajó" a "qué hizo el
   agente". **Esto rompe el flujo central de debug** (ver §3 momento mágico).
3. **El trace viewer no muestra el `kind` del span visualmente.** `llm_call` y `agent_turn`
   se ven idénticos en el árbol. Sin íconos/color por kind, el árbol es plano semánticamente.
4. **Los pointers del span no se resuelven.** El detalle del span muestra `provider: unknown`,
   `content_pointer: null` — con datos reales habría prompts/messages/args/results detrás
   de pointers al object store, y hoy no hay forma de verlos. La spec §7.6 (export) o un lazy
   fetch lo cubren; el FE aún no los pide.
5. **Compare es dos blobs, no un diff.** La spec prometía "prompt diff side-by-side" +
   "recomendación de ganador". Hoy son dos JSON mostrados por separado y un Δ del primary.
   Para 2 keys está bien; para un sweep real de prompts/params no escala.
6. **Filas de tabla clickeables NO son accesibles.** Las filas de iteración abren el drawer
   pero son `cursor:pointer` sobre divs, no `[button]`/`[link]` — invisibles a teclado/screen
   reader. (Los spans del trace SÍ son `[button]`: hacerlo consistente.)
7. **Mobile está roto.** El shell no colapsa, las stat cards se enciman y recortan
   ("BE… T… I…ONS"), la tabla se sale de pantalla. Defendible como desktop-first para una
   herramienta densa interna, pero hay que **decidirlo explícitamente** (ver §3, Jobs).
8. **No hay forma de LLEGAR al trace viewer desde la UI.** La ruta existe pero ningún link
   conduce a ella (relacionado con #2). El trace viewer es código huérfano hasta que se
   enlace desde iteración/caso.

### 1.3 Bugs reales encontrados (Bloqueo 0 — con stack traces)

Estos tres están **apilados** y hacen que **toda ruta excepto `/` devuelva 500** en una
sesión normal contra un DB de uso real. Los descubrí porque el dogfood los pegó de frente.

- **BUG-1 (backend, raíz). `storage/sqlite.py:60` + handlers sync en `api/app.py`.**
  Los handlers son `def` (no `async def`) → FastAPI los corre en threadpool. La conexión
  SQLite se abre con `check_same_thread=True` (default) y `Depends(_storage)` puede crear/
  cerrar la conexión en threads distintos →
  `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that
  same thread` en `sqlite.py:76` (`close()`). El endpoint **devuelve 200 pero lanza en el
  teardown**, corrompiendo la respuesta. Fix: `check_same_thread=False` (verificado: lo
  apliqué temporalmente para poder dogfoodear, y luego lo revertí — desbloquea todo), o
  handlers `async`, o conexión por-request atada a un thread.

- **BUG-2 (borde FE↔API). `web/src/lib/api/client.ts:129-136`.** En el path de error
  (`!res.ok`) hace `await res.json()` y si truena, el `catch` hace `await res.text()` sobre
  el **body ya consumido** → `TypeError: Body is unusable: Body has already been read`. Esto
  **enmascara el error real** con un mensaje confuso y propaga un 500 a la página. Fix:
  `const raw = await res.text(); try { body = JSON.parse(raw) } catch { body = raw }`.

- **BUG-3 (backend, trace viewer). `api/sse.py:68-104`.** Tras mandar el `snapshot`, el
  stream **siempre** se suscribe al broker y espera spans para siempre — incluso si el
  snapshot ya viene con `final_state: "completed"`. Para una traza terminada nunca llega
  `_Closed` → nunca se emite `complete` → el FE se queda en modo **"live"** con un
  EventSource abierto indefinidamente. Síntomas observados: pill "live" pulsante sobre una
  traza terminada; `final state` mostrado como `live`; la conexión SSE **se filtra entre
  navegaciones** (el pill "LIVE run_…" quedó pegado en el shell al navegar a `/experiments`
  y `/anchor-set`); inestabilidad del tab. Fix: si `snapshot.final_state != running`, emitir
  `complete` y `return` sin suscribir; y en el FE, cerrar el EventSource en el teardown de la
  ruta (no filtrarlo al shell global).

> **Implicación para el plan:** la Fase A empieza por estos tres. Cuestan horas, no días, y
> sin ellos cualquier vista nueva nace sobre un producto que da 500. Es el caso de libro de
> "arregla la base antes de construir encima".

### 1.4 Gaps de Compare (detalle, porque es donde el benchmark más enseña)

Hoy: dos selectores → dos tarjetas con Hypothesis/Parameters(JSON)/Primary/Decision + un
`Δ B − A`. Falta: (a) **diff real** de params/prompt (resaltar qué cambió, no mostrar dos
blobs), (b) **todas** las métricas en el Δ, no solo primary, (c) una **llamada de ganador**
con su caveat (ver §3, Hassabis: capability≠regression), (d) escalar a comparar N>2 (ver
benchmark: LangSmith capa diff a 2 y lo critican; Braintrust ordena por regresión).

### 1.5 Scope creep detectado

`/clusters` anuncia una **integración con Linear** ("Connect Linear", "route them into
Linear", `LINEAR_API_KEY`) que **no existe en el ROADMAP** y acopla la UI a un producto
externo concreto — contra el "contrato agnóstico" del ROADMAP (selfevals no referencia
consumidores). **Recomendación: cortar el copy de Linear del stub.** Si queremos "exportar
un cluster a un tracker", que sea genérico (copiar markdown / webhook), no Linear-specific.

---

## 2. Capa 2 — Benchmark competitivo (citado)

Investigué los 7 contra sus docs/changelogs oficiales (2024–2026). Lo que cada uno hace
excepcionalmente, qué robar, qué evitar. Fuentes al final de esta sección.

### 2.1 Por producto (lo esencial)

- **LangSmith.** Trace = árbol + **waterfall**; token/cost desglosado en Input/Output/Other
  con hover; latencia incluye **TTFT**. Comparación = **heatmap por celda (rojo=regresión,
  verde=mejora)** contra un "source experiment", con **toggles "solo regresiones/mejoras"**
  por columna y 3 densidades (Compact/Full/**Diff**). Calibración: **juez auto-mejorable** —
  corriges su veredicto en la UI y eso se inyecta como few-shot al juez. **ROBAR:** heatmap
  rojo/verde + toggle "solo lo que empeoró"; el loop corregir-juez→few-shot. **EVITAR:** Diff
  capado a 2 experimentos y solo JSON/YAML.

- **Langfuse.** Trace = **árbol** (coloreable por percentil de latencia/costo vs hermanos) +
  **timeline**; en spans `GENERATION` la barra **se parte para mostrar TTFT**. Payloads
  grandes: **lazy/virtual loading bajo demanda**. **Calibración = mejor de la categoría:
  "Score Analytics" zero-config** compara dos fuentes de score (juez vs humano) con **matriz
  de confusión + Cohen's Kappa + F1 + Overall Agreement**, tabs Matched/All. **ROBAR (la
  apuesta): Score Analytics tal cual**, y la barra-timeline partida para TTFT. **EVITAR:** 10
  tipos de observación con distinciones visuales sutiles → riesgo de overload sin buenos
  íconos/colores.

- **Braintrust.** "La mejor UI de experiment compare" según reviews. **Diff mode** vs baseline
  + **"Order by regressions"** como orden de columna de primera clase. Failure drill-down:
  **vistas prefiltradas (Errors/Scorer errors/Unreviewed)** + filtro **NL o SQL/BTQL** + el
  asistente **Loop** que clusteriza fallos por prompt. Monitor: p50/p95/p99, costo por
  user/feature/model. **ROBAR:** "Order by regressions" + barra de filtro NL/SQL + vistas
  prefiltradas. **EVITAR:** deshabilitar Timeline/Thread/custom views al entrar en diff (modos
  que se pelean).

- **W&B Weave.** **Costo y latencia agregados en CADA nodo del árbol** (no solo total).
  Payloads grandes: **popout de celda con switch text/markdown/code** (el mejor manejo de
  strings grandes de los 4). Comparación side-by-side de evals/modelos; leaderboards. **ROBAR:**
  popout text/md/code + rollup de costo/latencia por nodo. **EVITAR:** dos superficies (Models
  clásico vs Weave; SDK vs "Evaluation Playground" no-code) que fragmentan el flujo.

- **Arize Phoenix (OSS).** **El más cercano a una gran experiencia de DEBUG.** Trace = árbol
  de spans + **barra de timeline inline** con latencia/tokens/costo *en el árbol*; render de
  payload **MIME-aware (markdown/JSON)**; **spans en streaming en vivo**. Failure discovery
  **espacial**: clustering por embeddings (**HDBSCAN + UMAP**, ordenado por drift, coloreado
  por correctness). Dashboard de métricas: p50/p95, costo por token type. **ROBAR:** árbol de
  spans en streaming + timeline inline; (a futuro) cluster de fallos visual. **EVITAR:** DSL de
  filtro como único mecanismo de slice (muro de curva de aprendizaje).

- **OpenAI evals dashboard.** Gesto estrella: dentro de una traza, botón **"Grade all"** que
  **promueve una traza debugeada a un eval repetible** sobre muchos ejemplos. Spans nativos de
  **handoff/guardrail**. **ROBAR:** el one-click traza→eval. **EVITAR:** separar Logs/Traces
  (debug) de Evals (grading) en superficies distintas + analítica de costo/latencia delgada.

- **Humanloop.** Mejor *comparación*: **radar plot** de evaluadores por versión + tablas
  side-by-side + **reuso de logs** entre runs (no re-corre todo). **Pero fue adquirido por
  Anthropic y apagado el 8-sep-2025** → referencia de diseño, no competidor vivo. (Dato
  relevante dado el contexto del usuario.) **ROBAR:** radar + reuso de logs. **EVITAR:** acoplar
  el flujo a "Prompt Files" versionados como unidad de iteración (awkward para agentes
  definidos en código).

### 2.2 Síntesis: dónde está el white space

| Capacidad | Quién la domina | ¿selfevals la tiene en spec? |
|-----------|-----------------|------------------------------|
| Calibración juez↔humano (matriz confusión, kappa, F1) | **Langfuse** (solo él) | Sí — §5.4 + `calibration.py` + ROADMAP #17 |
| "Muéstrame qué empeoró" en un clic | LangSmith / Braintrust | Parcial — Compare existe, falta heatmap/order-by-regression |
| Debug de traza en vivo (streaming span tree) | **Phoenix** | **Sí, YA implementado** (SSE) — único en su tier |
| Cluster visual de fallos | Phoenix | §5.5 (depende de error-analysis API) |
| Traza→eval en un clic | OpenAI | No (selfevals es CLI-first para lanzar) |

**El ángulo de selfevals (confirmado): ser el único que junta calibración de jueces seria
(robado de Langfuse) con "qué regresó" en un clic (robado de LangSmith/Braintrust), encima
de un trace viewer en vivo (que ya tiene, estilo Phoenix).** Nadie tiene las tres. Y como
selfevals YA optimiza el loop (propone params, decide keep/reject), el frontend no es solo
observabilidad pasiva: es la **cabina del piloto del loop de auto-mejora**. Eso es lo que
ninguno de los 7 es.

### 2.3 Fuentes

LangSmith: docs.langchain.com/langsmith/{observability,cost-tracking,compare-experiment-results,
evaluation-concepts}, langchain.com/articles/{agent-observability,llm-as-a-judge},
blog.langchain.com/{pairwise-evaluations-with-langsmith,regression-testing}. ·
Langfuse: langfuse.com/docs/observability/{overview,data-model,features/observation-types,
features/sessions}, langfuse.com/docs/evaluation/evaluation-methods/{score-analytics,
annotation-queues}, deepwiki.com/langfuse/langfuse/8.1-tracing-system. ·
Braintrust: braintrust.dev/docs/guides/{experiments/interpret,evals/interpret},
braintrust.dev/docs/observe/dashboards, braintrust.dev/encyclopedia/p50-p95-p99-percentiles. ·
W&B Weave: wandb.ai/site/{traces,evaluations}, docs.wandb.ai/weave/guides/{tracking/tracing,
core-types/evaluations,core-types/leaderboards,tools/evaluation_playground}. ·
Phoenix: arize.com/docs/phoenix/tracing/llm-traces(+/metrics),
deepwiki.com/Arize-ai/phoenix/5.1-tracing-and-observability,
arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments/run-experiments,
arize.com/docs/phoenix/cookbook/retrieval-and-inferences/embeddings-analysis. ·
OpenAI: developers.openai.com/api/docs/guides/{trace-grading,evals,agent-evals},
openai.github.io/openai-agents-python/tracing. ·
Humanloop: humanloop.com/platform/{evaluations,observability},
humanloop.com/docs/guides/evals/run-evaluation-ui; sunset: humanloop.com/docs/guides/migrating-from-humanloop.

---

## 3. Capa 3 — Los filtros de fundadores (decisiones grandes)

Para cada decisión de producto grande, qué diría cada uno — concreto, no genérico.

### Brian Chesky — ¿el producto "11-star"? ¿el momento mágico?
- **11-star:** un dev cambia un prompt, corre `selfevals run`, y antes de que termine ya está
  viendo en la web los casos resolverse en vivo (span tree streaming), y al terminar la web le
  dice "subió pass@1 +8pts **pero** p95 de latencia subió 30% y el juez no está calibrado en
  este feature — aquí están las 3 trazas que regresaron". Un clic y está en la traza, con el
  prompt real delante.
- **El momento mágico (lo adopto como tesis del plan):** **de un número que se movió a la
  causa raíz (la traza, el prompt, el tool call) en dos clics.** Hoy ese arco está cortado en
  ambos extremos (BUG iteración→traza; pointers sin resolver). **Reparar ese arco completo es
  la Fase A.**

### Steve Jobs — ¿qué QUITAMOS? di no a 1000 cosas.
- **Jobs cortaría el tab/sección "Datasets" del MVP** y el copy de "Connect Linear" de
  Clusters: ambos prometen sin entregar y no son el corazón (debug del agente).
- **Jobs NO construiría "Live run control" (lanzar/abortar runs desde la web) por ahora** —
  el CLI ya lanza runs; duplicar el control en la web es complejidad sin momento mágico.
  La web es para **ver y entender**, no para operar (al menos hasta que duela).
- **Jobs forzaría una decisión sobre mobile:** o es desktop-only declarado y honesto (shell
  que dice "abre en desktop" en <768px), o es responsive de verdad. Lo de hoy (roto a medias)
  es la peor opción. **Recomiendo: desktop-first declarado**, con un gate honesto en móvil.
- **Simplicidad esencial:** un eje de navegación, no cinco. El producto es
  `workspace → experimento → iteración → traza → span`. Todo lo demás (clusters, datasets,
  judges, performance) son *facetas* de ese eje, no destinos paralelos en el sidebar.

### Karpathy — ¿le sirve a alguien que de verdad itera agentes? ¿densidad correcta?
- **Sí, si arreglamos el arco número→traza y resolvemos pointers.** Un dev que itera necesita
  ver el prompt/output/args REALES, no `content_pointer: null`. **Resolver pointers (§7.6) es
  no-negociable para que esto sea "la herramienta que yo querría".**
- **Densidad: hoy correcta en las tablas** (mono, tabular, Δ con color). El trace viewer
  necesita más densidad: kind del span con ícono/color, tokens/TTFT/costo *en el árbol* (robar
  de Phoenix/Weave), no escondidos en el detalle.
- Karpathy querría **diffs, no blobs** (arreglar Compare) y **el waterfall de tiempo** del
  trajectory viewer para ver dónde se va la latencia.

### Hassabis / Amodei — rigor: ¿la UI hace fácil lo correcto, difícil lo incorrecto?
- **capability ≠ regression:** cuando Compare "declare un ganador", debe mostrar el caveat de
  holdout/regression-set (no overfittear al optimization set). El ROADMAP #8 (splits/holdout)
  habilita esto: **la UI debe separar visualmente métrica-en-optimización de métrica-en-holdout**
  y nunca felicitar una mejora que solo ocurrió en el set de optimización.
- **no overfittear al juez:** la vista de calibración (§5.4, robada de Langfuse) es
  precisamente el mecanismo que hace difícil lo incorrecto — si el juez no concuerda con
  humanos en un feature, la UI debe **degradar la confianza de esa métrica** visiblemente.
- **reproducibilidad:** cada traza ya tiene run_id/seed/params snapshot. La UI debe exponer
  "cómo reproducir esto" (el comando CLI) desde el trace viewer — ya lo hace el reporte CLI,
  llevarlo a la web.

### Altman / Brockman — ¿escala? ¿el dev se enamora en 5 minutos?
- **Primeros 5 minutos:** hoy fallan — el dev corre el ejemplo, abre la web, y la segunda
  página da 500 (Bloqueo 0). **Arreglar eso ES el onboarding.** Después, el primer "ajá" debe
  ser el span tree en vivo durante el primer `run`.
- **Escala:** paginación (§7.7) no existe — 100 hardcoded, sort/limit en Python. A 10k trazas
  se cae. Y el trace viewer no virtualiza el árbol. **Paginación + virtual scroll son deuda de
  escala que hay que pagar en Fase A/B**, no después.
- **`selfevals serve`:** un comando que levanta todo (§6) es lo que hace que "el dev lo pruebe
  en 5 min" sea real (hoy son dos procesos + proxy). Alta prioridad.

### Musk — ¿qué parte del proceso se ELIMINA? requisito por requisito.
- **Eliminar el doble proceso (API + web por separado)** → `selfevals serve`.
- **Eliminar la sincronización manual de tipos** TS↔Pydantic (`client.ts` lo admite "a mano
  hasta que duela") → `openapi-typescript` contra `/api/openapi.json`. Ya está el OpenAPI gratis.
- **Eliminar Datasets-edit del MVP, Live-run-control del MVP, Linear de Clusters.** (Ver Jobs.)
- **Eliminar el ULID como identidad visible:** que el workspace/experimento tengan nombre
  humano y el ULID sea metadato copiable, no el título.

### Jensen Huang — ¿performance visible (latencia/TTFT/costo) como ciudadano de primera clase?
- **Hoy NO.** Los campos existen en el schema (`LLMCallSpan.time_to_first_token_ms`,
  `tokens_per_second`, cost) pero la UI no los destaca. El ROADMAP #9/#14 los puebla.
- Jensen pondría **TTFT, tokens/sec y $ en el árbol del span** (Phoenix/Weave style) y un tab
  **Performance** en el experiment detail con p50/p95/p99 por iteración — la pregunta "¿mejoré
  accuracy pero empeoré p95?" debe responderse de un vistazo. **§5.7 sube de prioridad** desde
  el momento en que #9/#14 aterricen.

---

## 4. Capa 4 — El Plan de Producto

### 4.1 Visión del FE (un párrafo)

selfevals web es **la cabina del piloto del loop de auto-mejora de un agente**: no un
dashboard pasivo de observabilidad, sino la superficie donde un dev *entiende por qué una
iteración ganó o perdió* y *confía en que la ganancia es real*. Espeja el loop que el CLI
orquesta — propuesta → run → grade → decisión — y lo hace inspeccionable hasta el span, con
el prompt y el tool call reales delante. Su densidad es técnica (tabular, mono, Δ con color);
su navegación es un solo eje (`workspace → experimento → iteración → traza → span`); su
diferenciador es juntar lo que nadie junta: **calibración de jueces de primera clase**
(que la métrica sea confiable) **con "muéstrame qué regresó" en un clic** (que la regresión
sea obvia), sobre un **trace viewer en vivo**. Es la herramienta que Karpathy querría para
depurar un agente y que Amodei aprobaría por no dejarte engañarte solo.

### 4.2 El flujo / momento mágico central

**De un número que se movió a su causa raíz, en dos clics, con datos reales.**

```
Experiment detail (veo: pass@1 subió +8, pero p95 ▲30%, juez ⚠ no calibrado en "routing")
   │  clic en la iteración que regresó
   ▼
Iteration drawer (hypothesis + params + métricas + DECISION + → "ver 3 trazas que fallaron")
   │  clic en una traza
   ▼
Trace viewer (árbol de spans con kind+TTFT+costo; clic en el LLMCall que falló)
   │  el prompt/messages/output REALES se resuelven (no pointer:null)
   ▼
"Reproducir": el comando CLI exacto para re-correr este caso
```

Hoy ese flujo está cortado en (a) drawer→traza (no hay link, §1.2#2), (b) pointer→contenido
(no se resuelve, §1.2#4). **Reparar este arco completo es el corazón de la Fase A.** Todo lo
demás del plan es ampliar este arco (multi-turno, funnel, jueces) o instrumentarlo
(performance, clusters).

### 4.3 Priorización despiadada de las vistas (§5 de FRONTEND.md)

Principio: **una vista no se construye antes que (a) su capacidad backend exista y (b) el arco
central funcione.** Orden por momento-mágico-por-esfuerzo, no por completitud de paridad.

#### FASE A — Reparar el camino crítico (lo primero, sin discusión)
*Desbloquea el momento mágico. Mayormente bugs + plomería, no vistas nuevas.*

| # | Trabajo | Por qué primero | Depende de |
|---|---------|-----------------|-----------|
| A1 | **BUG-1/2/3** (§1.3): thread-safe SQLite, fix double-read en client.ts, fix SSE de traza terminada | Sin esto, 500 en toda ruta ≠ `/`. Es el onboarding. | — |
| A2 | **Link iteración → trazas** (drawer muestra `trace_run_ids[]` como links al trace viewer) | Cierra el arco drawer→traza. Es el clic 2 del momento mágico. | endpoint ya da los ids |
| A3 | **Resolver pointers en trace viewer** (prompt/messages/args/result reales bajo demanda) | Karpathy-no-negociable. Sin esto el debug es teatro. | endpoint export §7.6 o lazy fetch |
| A4 | **`selfevals serve`** (§6): un comando levanta API+web+OTLP+broker | Altman/Musk: el dev lo prueba en 5 min. | — (CLI) |
| A5 | **Identidad humana sobre ULID** (nombre del ws/exp como título; ULID = metadato copiable) | Musk/Jobs: nadie lee ULIDs para orientarse. | — (FE) |
| A6 | **Span kind visible** (ícono+color por kind en el árbol) + TTFT/tokens/costo en el nodo | Karpathy/Jensen: densidad correcta. Robado de Phoenix/Weave. | campos ya en schema |
| A7 | **Filas de tabla accesibles** ([button]/[link], no div cursor:pointer) | A11y básica; consistencia con los spans que ya lo hacen. | — (FE) |
| A8 | **Paginación consistente** (§7.7) + virtual scroll en árbol de spans | Altman: deuda de escala que se paga ahora, no a 10k trazas. | ListFilter ya existe |

#### FASE B — Ampliar el arco: multi-turno + funnel
*Las dos vistas de mayor momento-mágico-por-esfuerzo que ya tienen (casi) backend.*

| # | Vista (§) | Por qué | Depende de |
|---|-----------|---------|-----------|
| B1 | **Thread viewer** (§5.3) — ruta `/[ws]/threads/[thread]`, conversación turn-by-turn con grade por turno y link a cada traza | **El endpoint YA existe** (`GET /threads/{id}`). Es la vista de mayor valor/esfuerzo: solo falta la ruta. Multi-turno es la tesis del ROADMAP. | endpoint existe; datos ricos con ROADMAP #2 (executor, F5) |
| B2 | **Funnel drill-down** (§5.1) — `FunnelBreakdown` en trace + agregado en experiment detail | Hace visible *dónde* se cae el flujo (routing→tool→final). Robable de la idea de "component isolation". | ROADMAP #3 (breakdown) + endpoint funnel §7.5 |
| B3 | **Compare → diff real** (§1.4): diff de params/prompt resaltado, todas las métricas, ganador con caveat holdout | Karpathy/Hassabis. Robado de LangSmith (heatmap) + Braintrust (order-by-regression). | ROADMAP #8 (splits/holdout) para el caveat |

#### FASE C — La apuesta diferenciadora: jueces + trayectoria
*Donde selfevals deja de ser "otro LangSmith" y gana el white space.*

| # | Vista (§) | Por qué | Depende de |
|---|-----------|---------|-----------|
| C1 | **Judge panel / calibración** (§5.4) — `JudgeConsensus` + `CalibrationMatrix` + `SpotCheckQueue`, ruta `/[ws]/judges` | **El diferenciador.** Robar Score Analytics de Langfuse (matriz confusión + kappa + F1 + agreement). Hace difícil lo incorrecto (Amodei). Nadie más lo junta con lo demás. | ROADMAP #17 (panel jueces) + `calibration.py` (existe) |
| C2 | **Trajectory timeline** (§5.2) — waterfall horizontal con badges diagnósticos (wrong_tool_order, etc.) | Debug de agente real (Karpathy). Diagnóstico, NO gate (respeta ROADMAP #4). Robable de Phoenix timeline. | ROADMAP #4 (TrajectoryGrader) |
| C3 | **Performance dashboard** (§5.7) — tab Performance: p50/p95/p99, TTFT, tokens/sec, $ por iteración | Jensen: performance de primera clase. Sube cuando #9/#14 pueblan datos. | ROADMAP #9 (TTFT/percentiles) + #14 (cost) |

#### FASE D — Completar paridad (cuando duela la falta, no antes)
*Diferido conscientemente. Construir solo si un usuario real lo pide.*

| Vista | Veredicto | Por qué se difiere |
|-------|-----------|--------------------|
| **Failure clusters dashboard** (§5.5) | Diferir; cortar copy de Linear ya | El CLI ya tiene failuremode/analyze; la API no lo expone (§7.3). Cluster *visual* (Phoenix-style) es Fase D+. |
| **Datasets + cases browser** (§5.6) | Diferir; **cortar del MVP** (Jobs) | Edición de cases es post-MVP por la propia spec. El browser read-only espera a §7.2. |
| **Live run control** (§5.8) | **Cortar del MVP** (Jobs) | El CLI ya lanza runs. Duplicar control en web = complejidad sin momento mágico. Solo si "lanzar desde web" se vuelve dolor real. |
| **Auth + roles** (§8) | Diferir (la spec ya dice post-MVP) | El header stub ya viaja; enchufar después sin tocar pantallas. |
| **Export CSV/JSON** (§7.6 export de iteraciones) | Bajo; el de *trace* (resolver pointers) sí es A3 | Nice-to-have; no es el arco central. |

### 4.4 Decisiones de UX por pantalla (las que cambian el diseño)

- **Shell global.** Un solo eje de navegación. El sidebar muestra el nombre humano del
  workspace activo, no el ULID. El pill de runs activos vive aquí pero **debe cerrarse al
  terminar el run** (fix BUG-3) y nunca filtrarse de una traza terminada. Gate honesto en
  <768px ("selfevals está pensado para desktop").
- **Workspace list (`/`).** Nombre humano primario, ULID como `mono` secundario copiable.
  Anillo de color para recent_health (la spec lo pide; hoy es texto). Si hay 1 workspace,
  considerar saltar directo a él (Musk: eliminar un clic).
- **Workspace overview (`/[ws]`).** Mantener hero + stat cards. Anillo de color en health.
  Activar nav a clusters/datasets solo cuando existan (no linkear a stubs).
- **Experiment detail.** Mantener tal cual (es la joya) + añadir tabs **Performance** (Fase C3)
  y **Funnel** (Fase B2). Breadcrumb con nombres humanos. En el **drawer de iteración: añadir
  sección "Trazas" con los `trace_run_ids` como links** (A2) — el cambio de UX más importante
  del plan después de los bugs.
- **Compare.** De dos blobs a **diff real** (B3): resaltar qué cambió en params/prompt, todas
  las métricas con Δ+color, y un veredicto "B gana" **con su caveat** ("válido en optimization
  set; holdout: —"). Escalar a N≥2 con tabla estilo Braintrust (order-by-regression).
- **Trace viewer.** Tres paneles se mantienen. Añadir: **kind del span con ícono+color**,
  **TTFT/tokens/$ en el nodo** (A6), **payload grande con popout text/md/code** (robar Weave),
  **pointers resueltos bajo demanda** (A3), botón **"Reproducir" → comando CLI** (Hassabis),
  y **trajectory waterfall** abajo (C2). El pill "live" solo cuando `final_state == running`.
- **Thread viewer (nuevo, B1).** Conversación vertical, burbujas por rol, grade chip por turno,
  link a cada traza. Distinguir `user_simulator` cuando exista (ROADMAP #15).
- **Judges (nuevo, C1).** Consenso del panel (votos + tipo de consenso) + **CalibrationMatrix**
  (matriz de confusión + precision/recall/F1/macro-F1 vs labels humanos) + **SpotCheckQueue**
  (cola de anotación). Cuando un juez está mal calibrado en un feature, **propagar esa señal
  como un badge ⚠ en las métricas que ese juez produce** en experiment detail (Amodei: hacer
  visible la desconfianza).

### 4.5 Dónde el FE necesita backend primero (coordinación con ROADMAP)

| Trabajo FE | Necesita del backend | Estado backend | Fase |
|------------|----------------------|----------------|------|
| A1 bugs | fix sqlite thread-safety + sse complete | — (bug, no feature) | A |
| A3 resolver pointers | endpoint export §7.6 (o lazy fetch del object store) | falta (§7.6) | A |
| A8 paginación | exponer `ListFilter` en endpoints de lista (§7.7) | ListFilter existe, falta exponer | A |
| B1 thread viewer | `GET /threads/{id}` (ya existe) + datos ricos con executor | endpoint ✅; executor ROADMAP #2 (F5) | B |
| B2 funnel | `GradeResult.breakdown` (#3) + `GET …/funnel` (§7.5) | falta (ROADMAP #3, F3/PR-7) | B |
| B3 compare diff + caveat | splits/holdout (#8) para el caveat | 🟡 schema (ROADMAP #8, F2/PR-4) | B |
| C1 jueces/calibración | `JudgePanelGrader` (#17) + `calibration.py` | 🟡 parcial (ROADMAP #17, F4/PR-10) | C |
| C2 trajectory | `TrajectoryGrader` (#4) | falta (ROADMAP #4, F4/PR-8) | C |
| C3 performance | TTFT/percentiles (#9) + cost model (#14) | 🟡 schema (ROADMAP #9/#14, F2/PR-2/3) | C |
| D clusters | failure-modes/analysis API (§7.3) | CLI ✅, API falta | D |
| D datasets | datasets/cases API (§7.2) | falta | D |

**Orden de dependencia limpio:** Fase A no necesita features nuevas del ROADMAP (solo fixes +
§7.6/§7.7 que son plomería). Fase B se alinea con ROADMAP F2–F3 (PR-2/3/4/7). Fase C se alinea
con F4 (PR-8/10). Fase D espera APIs que aún no están. **El FE nunca va por delante del backend
que espeja**, como exige FRONTEND.md §10.

### 4.6 Lo que explícitamente NO hacemos (y por qué)

- **No Live run control en MVP** (el CLI lanza runs; web es para entender).
- **No Datasets edit / cases edit en MVP** (post-MVP por la propia spec; browser read-only espera §7.2).
- **No integración Linear** (scope creep contra el contrato agnóstico; si exportamos clusters, genérico).
- **No auth real en MVP** (header stub ya viaja; se enchufa sin tocar pantallas).
- **No mobile responsive completo** (desktop-first declarado con gate honesto; revisitarlo solo si hay demanda).
- **No relitigar el stack** (SvelteKit/TanStack/LayerCake/Tailwind cerrados en decisions.md).

---

## 5. Resumen ejecutable (qué aprobar)

1. **¿Apruebas el momento mágico** (§4.2: número→causa raíz en 2 clics) **como la tesis que
   ordena todo el plan?**
2. **¿Apruebas Fase A como primer sprint** (3 bugs + link iteración→traza + resolver pointers
   + `serve` + identidad humana + span kind + a11y filas + paginación)? Es el onboarding y el
   arco central; sin ella nada más importa.
3. **¿Apruebas el ángulo diferenciador** (§2.2: calibración de jueces + "qué regresó" sobre
   trace en vivo) **y que la Fase C (jueces) es la apuesta**, no un nice-to-have?
4. **¿Apruebas los cortes** (§4.6: sin live-control, sin datasets-edit, sin Linear, sin auth,
   desktop-first)?

Tras tu OK, convertimos la Fase A en tareas y empezamos a programar — empezando por los tres
bugs, que ya tengo localizados con stack trace y fix.
