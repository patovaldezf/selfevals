# SDK Façade + OTLP Receiver — Design Doc

**Status**: Blueprint for the next implementation session. No code shipped yet.
**Owner**: pato
**Target session**: dedicated; ~1500-2000 LOC, several new deps.

This doc translates §F.2.1 of `operational_spec_v0.1.md` ("plug-and-play SDK") into an implementation plan tight enough that the next session can build it without re-doing the research. The research that informed this doc lives in this conversation; the conclusions are baked in below.

---

## 1. What we're building

Two halves that talk to each other:

**(a) The user-side SDK** — installed via `pip install selfeval` in the user's agent repo. One call activates everything:

```python
import selfeval
selfeval.init(project="support_bot")

from anthropic import Anthropic
client = Anthropic()
client.messages.create(model="claude-sonnet-4-6", ...)   # ← captured
```

That single `init()` call must:
- Configure an OpenTelemetry `TracerProvider`.
- Auto-detect which provider SDKs the user has installed (`anthropic`, `openai`, `boto3`, `google.cloud.aiplatform`, `cohere`, `langchain`, `langgraph`, `crewai`, `llama_index`, `dspy`).
- For each detected SDK, call the matching `openinference-instrumentation-*` `Instrumentor().instrument(tracer_provider=...)`.
- Pick the OTLP exporter target: env var `SELFEVAL_OTLP_ENDPOINT` (set by `selfeval run` for the current process) → fall back to `OTEL_EXPORTER_OTLP_ENDPOINT` → fall back to disabled-with-warning.
- Be idempotent (calling `init()` twice is a no-op the second time).

**(b) The framework-side OTLP receiver** — embedded in `selfeval run`. Owns the lifecycle:
- Picks a free localhost port at startup.
- Binds an OTLP/HTTP receiver at `POST /v1/traces` (protobuf body).
- Exports `SELFEVAL_OTLP_ENDPOINT=http://127.0.0.1:<port>` into the child-process env before invoking the user's agent.
- Receives OTLP `ResourceSpans`, decodes them, and bridges each span into the active `TraceRecorder`.
- Drains pending spans on iteration completion (flush window) before letting the `OptimizationLoop` close the `Trace`.
- Shuts down cleanly when the run finishes.

---

## 2. Why this shape and not another

### Decisions already made (do not re-litigate)

| Decision | Picked | Killed alternatives |
|---|---|---|
| Instrumentation source | **OpenInference** (Arize) | OpenLLMetry, custom wrappers, LiteLLM, vLLM, Helicone |
| Wire format | **OTLP/HTTP + protobuf** | OTLP/gRPC (heavier dep), JSON OTLP (no SDK exporter ships it well) |
| Receiver host | **Embedded in `selfeval run`** | External collector (more moving parts), file-tail (loses real-time) |
| User DX | **`selfeval.init()`** (one line, monkey-patches at install time) | `wrap_anthropic()` per-client (breaks plug-and-play), env-var only (only LangChain) |
| Deps | **Optional extras** (`pip install selfeval[telemetry]`) | All-in (50MB), nothing (forces manual config) |

Rationale: see the research turn in the chat — Traceloop, Weave and AgentOps all use this exact pattern (`init()` + import-time monkey-patch via `wrapt`). OpenInference is preferred over OpenLLMetry because Arize keeps the Claude Agent SDK / LangGraph / CrewAI Instrumentors current and the semconv is closer to the official OTel GenAI conventions.

### Failure modes we're consciously absorbing

- **SDK upgrades break monkey-patches.** Mitigation: pin OpenInference upgrades in CI, run a daily smoke against the latest `anthropic`/`openai` SDKs, ship a `selfeval doctor` command that detects "instrumented version != SDK version" mismatch.
- **Double instrumentation** if the user also runs Langfuse/LangSmith. Mitigation: detect their TracerProvider, log a clear warning, **do not** install our Instrumentors on top.
- **Streaming spans** get the wrong duration with naive patches. OpenInference handles this; we trust it.
- **Bedrock SigV4** isn't patchable like a normal HTTP client. OpenInference patches `boto3.client('bedrock-runtime').invoke_model` directly. Works.

---

## 3. New top-level package layout

```
src/selfeval/
├── sdk/                              # NEW — user-installed package surface
│   ├── __init__.py                   # re-exports init, shutdown, current_span
│   ├── facade.py                     # selfeval.init() — the one-liner
│   ├── auto_instrument.py            # detect+install OpenInference Instrumentors
│   ├── exporter.py                   # OTLP HTTP exporter wired to our endpoint
│   └── context.py                    # ContextVar for iteration_id / project
└── runner/
    ├── otlp_receiver.py              # NEW — embedded OTLP HTTP server
    └── otlp_to_recorder.py           # NEW — translate OTel ResourceSpans → TraceRecorder
```

---

## 4. `selfeval.init()` — exact signature & semantics

```python
def init(
    *,
    project: str,
    endpoint: str | None = None,
    sample_rate: float = 1.0,
    instrument: list[str] | None = None,    # ["anthropic", "openai", ...] or None=auto
    disable: list[str] | None = None,        # explicit opt-out per Instrumentor
    propagate_to_parent: bool = True,        # don't override if user already has OTel
) -> InitResult: ...
```

`InitResult` carries:
- `endpoint`: the URL spans are being shipped to (or `None` if disabled).
- `instrumentors_installed`: list of provider names that were activated.
- `instrumentors_skipped`: list of provider names that were detected but unavailable (missing extra).
- `warnings`: human-readable strings the user should see at boot.

Auto-detection (`instrument=None`): introspect `sys.modules`. If `anthropic` is imported (or installed), try `from openinference.instrumentation.anthropic import AnthropicInstrumentor` and call `.instrument(tracer_provider=...)`. Wrap each in try/except — missing extras are logged at INFO, not fatal.

Endpoint resolution order:
1. Explicit `endpoint=` arg.
2. `SELFEVAL_OTLP_ENDPOINT` env var (set by `selfeval run`).
3. `OTEL_EXPORTER_OTLP_ENDPOINT` env var (standard OTel).
4. None → SDK initializes the TracerProvider with a no-op exporter and logs a warning.

Idempotency: keep a module-level `_initialized` flag. Second call with same `project` returns the first `InitResult`. Second call with different `project` raises `SelfEvalAlreadyInitialized`.

---

## 5. OTLP receiver — exact contract

```python
# runner/otlp_receiver.py
def start_receiver(
    *,
    recorder_factory: Callable[[RunMetadata], TraceRecorder],
    host: str = "127.0.0.1",
    port: int = 0,                  # 0 = pick a free port
    flush_timeout_seconds: float = 5.0,
) -> ReceiverHandle: ...

class ReceiverHandle:
    endpoint: str                   # "http://127.0.0.1:54321"
    def begin_run(self, metadata: RunMetadata) -> RunContext: ...
    def stop(self) -> None: ...

class RunContext:
    def end(self) -> None: ...      # drains pending spans, closes the active recorder
```

**Threading**: the receiver runs on a background thread (stdlib `http.server.ThreadingHTTPServer`). Span ingestion appends to a thread-safe queue. The main thread (running the OptimizationLoop) consumes the queue inside `RunContext.end()` and feeds them to the `TraceRecorder` of the current iteration.

**Why not gRPC**: stdlib doesn't ship a gRPC server, and we don't want a hard `grpcio` dep. The OTel HTTP exporter is officially supported and ~10kB.

**Concurrency model**: one OTLP receiver instance per `selfeval run` process. Multiple iterations share the receiver — `RunContext` is what isolates per-iteration spans. The SDK side tags every span with `selfeval.iteration_id` (a resource attribute set from `SELFEVAL_ITERATION_ID` env or our SDK context var).

---

## 6. Span translation — OpenInference → selfeval schema

OpenInference uses the OpenTelemetry GenAI semconv (`gen_ai.*`) plus their own `openinference.*` extensions. Map them to our existing builders in `trace/recorder.py`:

| OpenInference field | Our LLMCallSpan field |
|---|---|
| `gen_ai.system` | `provider` |
| `gen_ai.request.model` | `model` |
| `gen_ai.request.temperature/top_p/max_tokens/...` | `params` dict |
| `gen_ai.prompt.{n}.role/content` | `messages_in` |
| `gen_ai.completion.{n}.role/content` | `messages_out` |
| `gen_ai.usage.input_tokens` / `output_tokens` | `TokenBreakdown` |
| `gen_ai.usage.cache_read_input_tokens` / `cache_creation_input_tokens` | cache fields on TokenBreakdown (Anthropic-specific; extend schema if not present) |
| `gen_ai.response.finish_reasons[0]` | `stop_reason` (map "end_turn"→END_TURN, "tool_use"→TOOL_USE, etc.) |
| `openinference.span.kind == TOOL` | `ToolCallSpan` |
| `tool.name`, `tool.parameters` | `tool_name`, `tool_args` |
| Span links + `parent_span_id` | parent-child stitching in our span tree |

Reasoning blocks (Claude / o1 / R1): OpenInference exposes them as additional `gen_ai.completion.{n}` entries with `role=assistant.reasoning`. Add a `ReasoningBlock` to the LLMCallSpan when present.

What we drop in MVP: arbitrary OTel resource attributes that don't have a home (log at DEBUG, keep in `provider_metadata` dict).

---

## 7. CLI integration

```python
# cli/commands.py — augment cmd_run
def cmd_run(args):
    spec = load_experiment_spec(...)
    callable_obj = resolve_agent_callable(spec.agent)
    
    with start_receiver(recorder_factory=...) as handle:
        os.environ["SELFEVAL_OTLP_ENDPOINT"] = handle.endpoint
        os.environ["SELFEVAL_PROJECT"] = spec.experiment.name
        # ... existing OptimizationLoop wiring ...
        # For each iteration, the loop opens a RunContext from `handle`
        # and the wrapped adapter calls user code under that context.
```

For `EmbeddedAdapter` (in-process): the env var alone is enough; the user's `selfeval.init()` picks it up the first time their code runs. Spans flow over HTTP to our localhost endpoint even though everything's in one process — that's fine, latency is microseconds.

For `CliCommandAdapter` (out-of-process): the env vars propagate through `subprocess.run(env=...)`.

For `HttpEndpointAdapter`: we can't inject env vars into a remote process. Two options: (a) accept that out-of-band agents don't get OTLP capture under our endpoint (they can still ship to their own OTel collector), or (b) require the user to point their own OTel collector at us. Document as out-of-scope for MVP.

---

## 8. Dependencies to add

`pyproject.toml`:

```toml
dependencies = [
    "pydantic>=2.7,<3",
    "pyyaml>=6,<7",
]

[project.optional-dependencies]
telemetry = [
    "opentelemetry-sdk>=1.25,<2",
    "opentelemetry-exporter-otlp-proto-http>=1.25,<2",
    "opentelemetry-proto>=1.25,<2",
]
anthropic = ["telemetry", "openinference-instrumentation-anthropic>=0.1"]
openai = ["telemetry", "openinference-instrumentation-openai>=0.1"]
bedrock = ["telemetry", "openinference-instrumentation-bedrock>=0.1"]
vertex = ["telemetry", "openinference-instrumentation-vertexai>=0.1"]
langchain = ["telemetry", "openinference-instrumentation-langchain>=0.1"]
langgraph = ["telemetry", "openinference-instrumentation-langgraph>=0.1"]
crewai = ["telemetry", "openinference-instrumentation-crewai>=0.1"]
all = [
    "selfeval[anthropic,openai,bedrock,vertex,langchain,langgraph,crewai]",
]
```

Default install (`pip install selfeval`) stays ~2MB. `pip install selfeval[anthropic]` adds ~5MB. `pip install selfeval[all]` ~30MB.

---

## 9. Test plan

**Unit** (per file):
- `sdk/facade.py`: idempotency, endpoint resolution order, double-init with different project raises.
- `sdk/auto_instrument.py`: each detection branch (mock `sys.modules`), missing extra is non-fatal, explicit `disable=` honored.
- `runner/otlp_receiver.py`: starts on free port, accepts POST, returns 200, queues spans, drains on `stop()`.
- `runner/otlp_to_recorder.py`: golden test for each Instrumentor's span shape (Anthropic messages, OpenAI chat, Bedrock invoke, LangChain chain).

**Integration**:
- E2E `selfeval run example_anthropic.yaml` with a real (mocked) Anthropic call. Assert: TraceRecorder captured 1 LLMCallSpan with the right provider/model/tokens.
- Out-of-process (`CliCommandAdapter` invoking `python -c "import selfeval; selfeval.init(...); ..."`): assert spans land in the receiver.
- Double-init detection: call `selfeval.init(project='a')` then `selfeval.init(project='b')` → assert raise.

**Smoke / dogfood**:
- Wire OpenInference against the real `anthropic` SDK with a sandboxed API key, verify the agent's `messages.create` call produces a span we can read back via `selfeval report`.

---

## 10. Out of scope (for this design)

- **Sampling strategies** beyond a global `sample_rate`. Tail-based sampling lives in a later iteration.
- **Persistent OTel collector** (Tempo, Jaeger, etc.). Users who want that point OpenInference at their own collector and use selfeval purely for the loop side.
- **Multi-process span correlation** beyond `SELFEVAL_ITERATION_ID` env propagation. Trace stitching across actual distributed systems is post-MVP.
- **Web UI rendering of these spans.** That belongs in the web session (see web prompt). This doc only delivers the capture pipe and the bridge to `TraceRecorder` — what the web does with them is its own scope.
- **PII redaction at the SDK side** before spans leave the user's process. Pending per user feedback.

---

## 11. Acceptance criteria for the implementing session

Done when, on a clean machine:

1. `pip install selfeval[anthropic]` succeeds and adds <10MB.
2. Following snippet works without any other config:
   ```python
   import selfeval
   selfeval.init(project="demo")
   from anthropic import Anthropic
   r = Anthropic().messages.create(model="claude-sonnet-4-6", max_tokens=50, messages=[{"role":"user","content":"hi"}])
   ```
   …and produces no errors when `SELFEVAL_OTLP_ENDPOINT` is unset (warning only).
3. `selfeval run evals/experiments/example_anthropic.yaml` captures one LLMCallSpan per agent call, persisted into the existing Trace schema, queryable via `selfeval report`.
4. All 390 existing tests still pass. New tests bring total to >430.
5. `mypy --strict` clean, `ruff` clean.
6. No new co-author trailers on commits.
