---
name: connect-your-agent
description: Wire an agent (or a new agentic product) into selfevals so it can be evaluated. Use when you need to connect an existing agent to a selfevals run, decide which adapter (embedded / cli / http / custom) fits how the agent is built, write the shim that adapts the agent to the AdapterRequest→AdapterResponse contract, or report tokens/cost/model and emit tool_uses/structured_output so graders can read them. Use when the user says "connect my agent", "how do I plug this into selfevals", "evaluate my deployed endpoint", or as the integration step handed off from `evaluate-this-repo`. For choosing graders/dataset, use `design-your-dataset`; for authoring/running the spec, use `run-eval-experiment`.
---

# Connect your agent (the adapter contract)

selfevals calls the agent under test through an **adapter**. One contract, four
ways to satisfy it. The full reference is `docs/adapters.md` and the source of
truth is `src/selfevals/runner/adapters.py` — read those for authoritative field
info. This skill is the decision + the accionable path.

## The contract (one shape, every transport)

For each case, selfevals sends an **`AdapterRequest`** and expects an
**`AdapterResponse`** back. The public names are importable from the top-level
package:

```python
from selfevals import AdapterRequest, AdapterResponse, AdapterToolUse, AgentAdapter
```

**`AdapterRequest`** (what selfevals sends):

- `workspace_id`, `case_id` — ids.
- `input: dict` — the `EvalCase.input` payload, usually a `messages` list.
- `context: dict | None` — system info / retrieved docs.
- `tools_allowed: list[str]` — the frozen set of permitted tool names.
- `parameters: dict` — **proposer overrides** (temperature, prompt swap, …).
  Read model params with the helper, not by hand:
  `level = req.get_model_param("level", 0.0)` (it flattens the
  `parameters["model_params"]` envelope and returns the default if absent).
- `metadata: dict`, `otlp_endpoint: str | None` — tracing; out-of-process agents
  can point their OTel exporter at `{otlp_endpoint}/v1/traces` to nest spans.

**`AdapterResponse`** (what your agent returns):

- `content: str | None` — the textual reply.
- `structured_output: dict | None` — JSON result. **Graders read this** (e.g.
  `set_match` reads `structured_output["detected"]`, `confusion` reads a class
  field, `funnel` extracts paths from it). If you want structured grading, you
  must populate this.
- `tool_uses: list[AdapterToolUse]` — `{tool, tool_use_id, args}` records. The
  `trajectory` grader and the funnel `tool_called`/`span_exists` matches read
  these. Emit them or those graders see nothing.
- `stop_reason: str | None`.
- token fields: `tokens_input`, `tokens_output`, `tokens_reasoning`,
  `tokens_cache_read`, `tokens_cache_creation`.
- `cost_usd: float` — cost of this single call.
- `provider_metadata: dict` — put `{"provider": ..., "model": ...}` here so the
  trace shows the real model.

Failures raise `AdapterError`. Never return `None` or a bare string from a
custom adapter (the embedded path tolerates a bare `str`, but prefer the real
type).

### Model name & cost in the trace

An agent is a black box, so selfevals resolves model/cost in this order:
`provider_metadata` you return → the spec's `agent.model: {provider, name}`
(cli/http) → the bound Agent record (embedded) → `"unknown"`. If you return
**tokens but no cost** and no model is known anywhere, `cost_usd` stays `$0.00` —
declare `agent.model` (cli/http) or set `cost_usd`/`provider_metadata` yourself
to get real pricing.

## Pick the transport

| Your agent is…                              | Use        | YAML `agent:` block |
| ------------------------------------------- | ---------- | ------------------- |
| An in-process Python callable               | **embedded** | `entrypoint: "mod:fn"` |
| A binary / another language / needs isolation | **cli**   | `type: cli, command: [...]` |
| A hosted/deployed/staging service           | **http**   | `type: http, url: "..."` |
| None of these (custom client/SDK)           | **custom** | `entrypoint:` to a factory that builds your `AgentAdapter` |

### Embedded (fastest to iterate)

```yaml
agent:
  entrypoint: mypkg.agent:run        # module.path:callable (sync or async)
```

```python
from selfevals import AdapterRequest, AdapterResponse

def run(req: AdapterRequest) -> AdapterResponse:
    last_user = next(
        (m for m in reversed(req.input.get("messages", [])) if m.get("role") == "user"),
        {"content": ""},
    )
    temp = req.get_model_param("temperature", 0.0)
    # ... call your agent here ...
    return AdapterResponse(content=str(last_user["content"]), tokens_input=10, tokens_output=5)
```

A sync callable is offloaded to a thread; `async def run(req)` is awaited. No
isolation: a bug in the agent crashes the selfevals run. No adapter-layer
timeout.

### CLI (JSON over stdio)

```yaml
agent:
  type: cli
  command: ["./bin/my-agent", "--mode", "eval"]   # required, non-empty
  env: { MY_TOKEN: "..." }                          # optional (replaces inherited env)
  timeout_seconds: 30                               # optional; default 60
  model: { provider: openai, name: gpt-5 }          # optional; enables pricing
```

Your command reads a JSON `AdapterRequest` on stdin, writes a JSON
`AdapterResponse` on stdout; non-zero exit → `AdapterError`. Minimal bash:

```bash
#!/usr/bin/env bash
request="$(cat)"
content="$(printf '%s' "$request" | jq -r '.input.messages[-1].content // ""')"
jq -n --arg c "$content" '{content: $c, tokens_input: 10, tokens_output: 5, tool_uses: [], stop_reason: "end_turn"}'
```

### HTTP (deployed/staging)

```yaml
agent:
  type: http
  url: "https://agent.example.com/eval"     # required
  headers: { Authorization: "Bearer ..." }  # optional (merged over Content-Type)
  timeout_seconds: 30                        # optional; default 60
  model: { provider: anthropic, name: claude-opus-4-8 }   # optional; enables pricing
```

POST receives the JSON `AdapterRequest`; return the JSON `AdapterResponse`. Non-2xx
→ `AdapterError` (429/5xx are classified retryable). A FastAPI endpoint that
mirrors the request schema and returns the response dict is all you need (see
`docs/adapters.md` for the full example).

### Custom adapter

When none of the three fit, subclass `AgentAdapter` and implement the single
async method; wire it via an `entrypoint:` to a factory (there is **no** `type:`
tag for user-defined adapters):

```python
from selfevals import AdapterRequest, AdapterResponse, AgentAdapter, AdapterToolUse

class MyAgentAdapter(AgentAdapter):
    def __init__(self, client, *, agent=None):
        self._client = client
        self.agent = agent                      # may be None; executor reads it

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        reply = await self._client.complete(
            request.input.get("messages", []),
            temperature=request.parameters.get("temperature", 0.0),
        )
        return AdapterResponse(
            content=reply.text,
            tool_uses=[AdapterToolUse(tool=c.name, tool_use_id=c.id, args=c.args) for c in reply.tool_calls],
            tokens_input=reply.usage.input_tokens,
            tokens_output=reply.usage.output_tokens,
            cost_usd=reply.usage.cost_usd,
        )
```

## Connecting an agent you didn't write for selfevals

The usual case from `evaluate-this-repo`: the repo's agent has its own signature.
**Don't change their agent — write a thin shim** next to it that adapts
`AdapterRequest` → their function → `AdapterResponse`, and point the spec's
`entrypoint:` at the shim. Map their inputs from `req.input` (and `req.context`),
their outputs into `content`/`structured_output`/`tool_uses`, and pass token/cost
through when the provider reports them.

## What you must / must not do

- **`invoke` must be `async def`** for a custom adapter. Sync clients →
  `EmbeddedAdapter` (it threads for you) or `asyncio.to_thread`.
- **Return `AdapterResponse` or raise `AdapterError`.** Never `None` / bare
  string from a custom adapter.
- **Populate `structured_output` / `tool_uses` if graders need them** —
  `set_match`/`confusion`/`funnel` read `structured_output`; `trajectory` and
  funnel tool matches read `tool_uses`. No emit, no signal.
- **Report tokens and `cost_usd`** when the provider gives them; declare
  `agent.model` (cli/http) so token-only responses still get priced.
- **Adapters do not assemble traces.** Return the response; the recorder ingests
  it. Keep the adapter small.
