# selfevals — Para sesiones de Claude

## Qué es / Stack

Framework de evals AI-native auto-mejorante para agentes. Corre experimentos
(search space x cases x reps) con grading concurrente, optimiza y reporta.
Python >=3.12, uv, version 0.6.0. Layout `src/`, package `selfevals`.
Deps core mínimas (pydantic, pyyaml, httpx); extras opcionales por proveedor
(`[anthropic]`, `[openai]`, `[telemetry]`, `[web]`...) que se lazy-importan.
Repo: github.com/patovaldezf/selfevals.

## Arquitectura

- NO hay `config/`. El authoring surface es YAML (`evals/experiments/*.yaml`);
  `repo/loader.py` lo hidrata a `ExperimentSpec` (Pydantic). El loader solo
  parsea/valida shape — la construcción real de adapters ocurre en `cli/commands.py`.
- Subpaquetes clave: `graders/` (registrados en `graders/registry.py`),
  `optimization/` (loop, proposers, sampling, aggregator), `runner/` (adapters
  embedded/cli/http, executor, otlp), `storage/` (sqlite + migrations),
  `repo/loader.py`, `reporter/`, `analysis/`, `api/` (FastAPI, opcional), `cli/`.
- **per-grader scoring** (v0.5.0): cada grader puntúa por separado.
- **grid-exhaust**: el grid proposer agota combinaciones y lanza
  `SearchSpaceExhaustedError` (ver `optimization/proposers.py`).

## Convenciones

- VERIFICAR reportes contra el código antes de arreglar. No asumas que un bug
  reportado es real sin leer el módulo; `docs/STATUS.md` es el snapshot honesto
  de qué funciona / qué no.
- `tests/` espeja `src/selfevals/` paquete-por-paquete; un test nuevo va en la
  ruta que matchea el módulo bajo prueba.
- Nuevo grader → `graders/` + registrar en `registry.py` + test. Nuevo adapter
  → `runner/adapters.py` (contrato `(AdapterRequest) -> AdapterResponse`) + doc
  en `docs/adapters.md`. Nuevo comando CLI → parser en `cli/main.py`, handler en
  `cli/commands.py`, tabla en `README.md`.
- Tooling es estricto: mypy `--strict`, ruff con reglas amplias, pytest
  `filterwarnings=error` + `asyncio_mode=strict`.

## Comandos clave (verificados)

```bash
uv sync --all-extras --dev          # setup
uv run ruff check .                 # lint
uv run mypy src/selfevals           # typecheck (--strict)
uv run pytest                       # tests (full surface necesita extras web+telemetry)
uv run pytest --ignore=tests/api --ignore=tests/sdk \
  --ignore=tests/runner/test_otlp_receiver.py   # surface por defecto, sin extras
uv run selfevals --help             # CLI (o `selfevals` con venv activo)
```

CLI: `init`, `run`, `report`, `compare`, `estimate`, `analyze`, `experiment`,
`iteration`, `workspace`, `failuremode`, `skills`, `examples`, `serve`.

## Subproyectos (DISTINTOS del core Python)

- `web/` — Web UI, SvelteKit 5 + Vite + Tailwind 4, **npm** (package-lock.json).
  `cd web && npm ci && npm run check && npm run build`. Sirve junto a la API con
  `selfevals serve`.
- `landing/` — Landing de marketing, Next.js 16 + React 19 + Tailwind 4, **npm**.
  Separada del web UI; es solo marketing.

## Riesgos / gotchas

- Default DB: `./selfevals.sqlite` (con `-shm`/`-wal`); flag global `--db`.
- El sdist tiene allow-list explícita en pyproject para no barrer `web/node_modules`
  ni `*.sqlite`; al tocar packaging respeta `[tool.hatch.build...]`.
- Extras de proveedor degradan limpio (try/except con error "pip install
  selfevals[<provider>]"); no asumas que están instalados.
- Aún no hay página de wiki para este proyecto (`../wiki/projects/` no la tiene).
