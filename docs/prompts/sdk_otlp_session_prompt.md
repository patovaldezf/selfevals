# SDK + OTLP Receiver — Handoff Prompt for Claude Code

Copy everything below this line into a fresh Claude Code session opened in this repo (`/Users/patriciovaldez/Desktop/proyectos/mis_repos/boostrap`). The prompt is self-contained — the receiving session has no memory of the conversation that produced it.

---

## Mission

Build the **user-side SDK** (`bootstrap.init()`) and the **embedded OTLP receiver** that makes `bootstrap run` capture LLM telemetry from any agent with one line of user-side setup. This is the half that turns the existing Python library into a genuinely plug-and-play service-as-software.

The architectural decisions are **already made** — your job is to implement, not redesign. The design doc at `docs/spec/sdk_otlp_design.md` is the contract. Read it first, follow it to the letter. If you find a real reason to deviate (a dep doesn't exist, an API changed), document the deviation in the same doc before deviating.

A parallel session is already building the web UI. They consume the data you produce. Don't break their assumptions about span shape — anything you store has to be readable from the existing `TraceRecorder`/`Trace` schema.

## Read first (in this order)

1. **`docs/spec/sdk_otlp_design.md`** — the locked design. Sections 1-11 are the contract. Memorize §2 (decisions already made — do not re-litigate), §4 (`bootstrap.init()` signature), §5 (receiver contract), §6 (OpenInference → bootstrap schema mapping), §11 (acceptance criteria).
2. `docs/spec/operational_spec_v0.1.md` — §F.2.1 ("plug-and-play SDK") for the product principle. Read it once, then trust the design doc for the implementation specifics.
3. `src/bootstrap/trace/recorder.py` — the existing `TraceRecorder` is your target API. Your OTLP receiver translates incoming spans into calls on this. Read the whole file.
4. `src/bootstrap/schemas/trace.py` — the span schema (`LLMCallSpan`, `ToolCallSpan`, `TokenBreakdown`, `ReasoningBlock`, etc.). Your translation layer maps OpenInference attributes onto these.
5. `src/bootstrap/cli/commands.py` (`cmd_run`) and `src/bootstrap/runner/executor.py` — the existing run loop you'll wrap with the receiver lifecycle.
6. `README.md` + `CHANGELOG.md` v0.0.9 — what already shipped.

## Backend state when you start

- 390 tests passing on `main`. Do not regress them.
- `bootstrap run evals/experiments/example_pingpong.yaml --no-persist` works end-to-end against an in-process reference agent (no LLM calls).
- `TraceRecorder` is the canonical span builder. Spans flow into it via context managers (`rec.llm_call(...)`, `rec.tool_call(...)`). Your receiver needs to call these from the main thread after dequeueing OTLP spans.
- One runtime dep so far: `pydantic`, `pyyaml`. Optional `[telemetry]` extras live in the design doc §8 — implement that pyproject.toml structure exactly.

What does NOT exist yet and you're building (in this order):

1. **`src/bootstrap/sdk/`** — the user-facing package. Re-exported at top-level so `import bootstrap; bootstrap.init(...)` works.
2. **`src/bootstrap/runner/otlp_receiver.py`** — embedded HTTP/protobuf OTLP receiver. Spec'd in design §5.
3. **`src/bootstrap/runner/otlp_to_recorder.py`** — translation layer from OpenInference span attributes to `TraceRecorder` builder calls. Spec'd in design §6.
4. **CLI wiring** — `bootstrap run` starts the receiver before invoking the agent and drains it on iteration close. Spec'd in design §7.
5. **Example experiment** — `evals/experiments/example_anthropic.yaml` + a reference agent that makes a real Anthropic call. Used by the smoke acceptance criterion (§11 item 3).

## Implementation order (commit by commit)

Build in this exact sequence — each step has its own commit and stays green.

### Commit 1 — `pyproject.toml` extras + import scaffolding
- Add `[project.optional-dependencies]` per design §8: `telemetry`, `anthropic`, `openai`, `bedrock`, `vertex`, `langchain`, `langgraph`, `crewai`, `all`.
- Create empty `src/bootstrap/sdk/__init__.py` + `facade.py` + `auto_instrument.py` + `exporter.py` + `context.py` with module docstrings + `pass`.
- Create empty `src/bootstrap/runner/otlp_receiver.py` + `otlp_to_recorder.py`.
- Verify `uv sync` and `uv sync --extra telemetry` both work. `uv run pytest -q` still 390 green.
- Commit subject: `chore(deps,sdk): scaffold telemetry extras + sdk/receiver package layout`.

### Commit 2 — `sdk/context.py` + `sdk/exporter.py`
- `context.py`: a `ContextVar[RunContext | None]` carrying `project`, `iteration_id`, `endpoint`. Pure stdlib.
- `exporter.py`: thin wrapper over `OTLPSpanExporter` from `opentelemetry-exporter-otlp-proto-http`. Resolves endpoint per the order in design §4 (explicit → `BOOTSTRAP_OTLP_ENDPOINT` → `OTEL_EXPORTER_OTLP_ENDPOINT` → None+warning).
- Tests: env-var resolution order, explicit override wins, None returns a no-op exporter.
- Commit subject: `feat(sdk): otel exporter wiring + run-context plumbing`.

### Commit 3 — `sdk/auto_instrument.py`
- For each provider in §1 of the design doc, write a `_try_instrument_<provider>(tracer_provider) -> InstrumentResult` function that:
  - imports the OpenInference Instrumentor inside a try/except (missing extra = skip with INFO log, never fatal).
  - if available, calls `Instrumentor().instrument(tracer_provider=tracer_provider)`.
  - returns the provider name + status.
- `auto_instrument_all(tracer_provider, *, only=None, exclude=None) -> list[InstrumentResult]` orchestrates.
- Tests: mock `sys.modules` for each branch, assert correct Instrumentor is called or skipped. Use `unittest.mock.patch.dict(sys.modules, ...)`. Do not require the real `openinference-*` packages to be installed for tests.
- Commit subject: `feat(sdk): auto-detect and install openinference instrumentors`.

### Commit 4 — `sdk/facade.py` + top-level `bootstrap.init()`
- Implement `bootstrap.init(*, project, endpoint=None, sample_rate=1.0, instrument=None, disable=None, propagate_to_parent=True) -> InitResult` exactly per design §4.
- Idempotency via a module-level `_initialized` flag.
- Different-project second call raises `BootstrapAlreadyInitialized`.
- Re-export `init`, `shutdown`, `InitResult`, `BootstrapAlreadyInitialized` at `bootstrap/__init__.py` (top-level package).
- Tests: idempotency, double-init with different project raises, `only=` / `disable=` filters honored, endpoint resolution order, third-party TracerProvider already-set respected when `propagate_to_parent=True`.
- Commit subject: `feat(sdk): bootstrap.init() one-line facade`.

### Commit 5 — `runner/otlp_to_recorder.py`
- Pure function: `translate_resource_spans(resource_spans, recorder) -> None` that walks OTel `ResourceSpans` → `ScopeSpans` → `Span` and calls the appropriate `recorder.llm_call(...)` / `recorder.tool_call(...)` / etc. context managers. Stitch parents by `parent_span_id` using a `dict[bytes, BuilderRef]`.
- Implement the mapping table from design §6 exactly. For `gen_ai.completion.*` entries with `role=assistant.reasoning`, attach a `ReasoningBlock` to the `LLMCallSpan`.
- Reasoning + cache fields: if `TokenBreakdown` doesn't have `cache_read_input_tokens` / `cache_creation_input_tokens` fields today, **add them to the schema in a separate commit before this one** (with migration note in CHANGELOG). Pydantic models are the contract.
- Tests: golden fixtures of OpenInference-style `ResourceSpans` JSON for Anthropic, OpenAI, Bedrock, LangChain, LlamaIndex. Each fixture exercises one span shape; assert the resulting `TraceRecorder.build()` matches the expected Trace.
- Commit subject: `feat(runner): openinference -> TraceRecorder bridge`.

### Commit 6 — `runner/otlp_receiver.py`
- Stdlib `http.server.ThreadingHTTPServer` bound to `127.0.0.1:0`. POST `/v1/traces` handler decodes the protobuf body (use `opentelemetry-proto`'s `ExportTraceServiceRequest.FromString`), enqueues the spans onto a `queue.Queue`.
- `start_receiver(...)` → `ReceiverHandle` with `endpoint`, `begin_run(metadata) -> RunContext`, `stop()`.
- `RunContext.end()` drains the queue with the configured `flush_timeout_seconds`, hands spans to the active `TraceRecorder` via the translator from commit 5.
- Tests: starts on free port, accepts POST with a valid protobuf payload, returns 200, queues correctly, drains on stop. Use `requests` (dev dep) or `urllib` (stdlib) to fire the test POST.
- Commit subject: `feat(runner): embedded otlp http receiver`.

### Commit 7 — CLI integration
- `cmd_run` wraps the OptimizationLoop in a `start_receiver(...)` context. For each iteration, opens a `RunContext`. Exports `BOOTSTRAP_OTLP_ENDPOINT` + `BOOTSTRAP_PROJECT` + `BOOTSTRAP_ITERATION_ID` env vars before invoking the user adapter.
- For `EmbeddedAdapter`: env vars are enough (in-process pickup).
- For `CliCommandAdapter`: pass env vars via `subprocess.run(env=...)`.
- For `HttpEndpointAdapter`: document as out-of-scope for MVP per design §7.
- Tests: integration test that uses `bootstrap.init(...)` inside an `EmbeddedAdapter` callable, makes a fake call that emits a manual OTel span, and asserts the span appears in `bootstrap report` output for that iteration.
- Commit subject: `feat(cli): wire otlp receiver into bootstrap run`.

### Commit 8 — Example + acceptance
- `evals/experiments/example_anthropic.yaml` + `src/bootstrap/examples/anthropic_agent.py` (reference agent that calls Anthropic with `bootstrap.init()` activated). Use a mock provider key (or skip the test if `ANTHROPIC_API_KEY` is unset; mark the test `@pytest.mark.live`).
- Smoke test: walk through design §11 acceptance criteria 1-3 manually, log result.
- README section: "Capturing telemetry from your agent" with the 4-line snippet.
- Commit subject: `docs(examples): anthropic plug-and-play smoke + readme`.

### Commit 9 — Version bump + changelog
- Bump `version.py` and `pyproject.toml` to `0.0.10`.
- CHANGELOG entry under a new `## [0.0.10] - <date>` heading.
- Commit subject: `chore(release): v0.0.10 — sdk + otlp receiver`.

## Hard constraints — non-negotiable

- **Read the design doc before writing code.** If you start coding without having internalized §2-§6, stop and re-read.
- **Do not invent new architectural decisions.** The doc's table of decisions in §2 ("Picked / Killed alternatives") is binding. If you discover a blocker, document the deviation before committing.
- **No new co-author trailers.** The repo history was scrubbed; keep it clean. No `Co-Authored-By: Claude…` anywhere.
- **All 390 existing tests must remain green at every commit.** Run `uv run pytest -q && uv run mypy src && uv run ruff check .` before every commit.
- **mypy strict + ruff clean** for everything you add.
- **No commits with `git add -A`** before reviewing `git status` — opt-in adds only.
- **No installing packages without confirming with the user first** if you find you need anything outside the deps listed in design §8.

## How to start

1. Open the design doc and read it end-to-end. Take notes if it helps.
2. Run the existing suite to confirm your local is healthy:
   ```bash
   uv sync
   uv run pytest -q          # expect 390 passed
   uv run bootstrap run evals/experiments/example_pingpong.yaml --no-persist --max-iterations 2
   ```
3. Start with Commit 1 (scaffolding). Get green. Push.
4. Walk through commits 2-9 in order. Do not skip ahead.
5. After commit 8, manually verify each item in design §11. Paste the verification log into the v0.0.10 changelog entry.

## What "done" looks like

- All 9 commits landed on `main`, pushed, no co-author trailers.
- Total test count: 430+ (the design doc §9 budget).
- `mypy --strict src` clean. `ruff check .` clean.
- `pip install bootstrap[anthropic]` succeeds, adds <10MB.
- This snippet runs without errors when `BOOTSTRAP_OTLP_ENDPOINT` is unset (warning only):
  ```python
  import bootstrap
  bootstrap.init(project="demo")
  ```
- `bootstrap run evals/experiments/example_anthropic.yaml` (with `ANTHROPIC_API_KEY` set) captures one `LLMCallSpan` per agent call, queryable via `bootstrap report`.
- A 30-second screen recording shows: run the experiment → open the trace inspector (the web session is building this) → see the captured LLM call with tokens, latency, messages.

## When to ask the user

Ask before:
- Adding any dep not listed in design §8.
- Deviating from the design doc's decisions in §2 or signatures in §4-§6.
- Anything that would change the user-facing `bootstrap.init()` signature.
- Anything that would break a currently-green test.

Do NOT ask before:
- Writing tests, adding fixtures, restructuring your own modules under `sdk/` or `runner/otlp_*.py`.
- Choosing private helper names, file layout within the new packages.
- Adding small dev deps (testing utils).

## Final note

The user has built ~9 versions of this backbone. They will recognize sloppy scaffolding, hidden state, or shortcuts immediately. The bar is the same as the existing codebase: tight types, focused commits, clean diffs, honest changelog entries. Match the style of the last 9 PRs.

Now go.
