# Agent adapters

`selfevals` invokes the agent under test through an `AgentAdapter`. Three
concrete implementations ship in the runtime, all defined in
`src/selfevals/runner/adapters.py`. They share one contract — given an
`AdapterRequest`, return an `AdapterResponse` — and differ only in
transport.

This document is the reference for picking one, writing the agent on the
other side, and — when none of the three fit — writing your own adapter
against the public package surface.

---

## The contract

All adapters speak the same shape. Read these dataclasses straight from
`src/selfevals/runner/adapters.py` if you need authoritative field
information; the summary below tracks that source.

The contract is **async**: `AgentAdapter.invoke` is an `async def`, and so
is `Grader.grade`. `asyncio.run` lives only at the CLI edge — everything
above the adapter awaits natively, so many cases run concurrently without
blocking the event loop. There is no synchronous variant.

### `AdapterRequest`

What selfevals sends to your agent for each case.

| Field           | Type                 | Notes                                                                                 |
| --------------- | -------------------- | ------------------------------------------------------------------------------------- |
| `workspace_id`  | `str`                | The owning workspace's id.                                                            |
| `case_id`       | `str`                | Stable id for the `EvalCase` being run.                                               |
| `input`         | `dict[str, Any]`     | The Pydantic-compatible `EvalCase.input` payload (usually a `messages` list).         |
| `context`       | `dict[str, Any]\|None` | Optional context block (system info, retrieved docs, etc.).                         |
| `tools_allowed` | `list[str]`          | Frozen list of tool names the agent is permitted to use.                              |
| `parameters`    | `dict[str, Any]`     | Overrides from the proposer (e.g. model temperature, prompt swap). Pass through verbatim. |
| `metadata`      | `dict[str, Any]`     | Free-form metadata for tracing.                                                       |

#### Reading proposer model params

Grid/random/LLM proposers don't drop their search-space params at the top
level of `parameters` — they wrap them under a `model_params` key (the
namespace the editable contract gates):

```python
req.parameters == {"model_params": {"level": 0.7, "temperature": 0.2}}
```

Rather than reach into that envelope by hand, use the
`get_model_param` helper, which flattens it for you:

```python
level = req.get_model_param("level", 0.0)        # -> 0.7
missing = req.get_model_param("nope", 0.0)       # -> 0.0 (default)
```

It returns the supplied default when the key is absent — or when no
`model_params` envelope is present at all — so adapters never have to
know the envelope's shape.

### `AdapterResponse`

What your agent must return.

| Field                 | Type                          | Notes                                              |
| --------------------- | ----------------------------- | -------------------------------------------------- |
| `content`             | `str \| None`                 | Agent's textual reply.                             |
| `structured_output`   | `dict[str, Any] \| None`      | Structured payload (e.g. JSON tool result).        |
| `tool_uses`           | `list[AdapterToolUse]`        | List of `{tool, tool_use_id, args}` records.       |
| `stop_reason`         | `str \| None`                 | Provider-reported termination reason.              |
| `tokens_input`        | `int`                         | Input tokens consumed.                             |
| `tokens_output`       | `int`                         | Output tokens emitted.                             |
| `tokens_reasoning`    | `int`                         | Reasoning tokens (for models that report them).    |
| `tokens_cache_read`   | `int`                         | Cache-read tokens.                                 |
| `tokens_cache_creation` | `int`                       | Cache-creation tokens.                             |
| `cost_usd`            | `float`                       | Cost of this single call in USD.                   |
| `provider_metadata`   | `dict[str, Any]`              | Anything else worth keeping in the trace.          |

`AdapterToolUse` is `{tool: str, tool_use_id: str, args: dict[str, Any]}`.

Failures raise `AdapterError` (transport error, decode error, contract
violation).

The wire format for `CliCommandAdapter` and `HttpEndpointAdapter` is
JSON-serialised versions of these same shapes; see `_request_to_json` /
`_json_to_response` in the source for the exact mapping.

---

## Writing your own adapter

The three bundled adapters cover the common cases, but the contract is
public: anything that can turn an `AdapterRequest` into an
`AdapterResponse` is a valid adapter. Import the contract from the
top-level package — these names are part of the supported public surface:

```python
from selfevals import (
    AgentAdapter,
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
)
```

Subclass `AgentAdapter` and implement the single async method. Set
`self.agent` (it may be `None`); the executor reads it to stamp the
agent's snapshot ids onto the trace.

```python
from selfevals import AdapterRequest, AdapterResponse, AgentAdapter


class MyAgentAdapter(AgentAdapter):
    def __init__(self, client, *, agent=None) -> None:
        self._client = client
        self.agent = agent

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        messages = request.input.get("messages", [])
        # `parameters` carries proposer overrides (temperature, model, …).
        reply = await self._client.complete(
            messages,
            temperature=request.parameters.get("temperature", 0.0),
        )
        return AdapterResponse(
            content=reply.text,
            tool_uses=[
                AdapterToolUse(tool=c.name, tool_use_id=c.id, args=c.args)
                for c in reply.tool_calls
            ],
            tokens_input=reply.usage.input_tokens,
            tokens_output=reply.usage.output_tokens,
            cost_usd=reply.usage.cost_usd,
            stop_reason=reply.stop_reason,
        )
```

Rules of the road:

- `invoke` **must** be `async def`. If your client library is
  synchronous, either wrap it in `EmbeddedAdapter` (which offloads sync
  callables to a thread for you) or call it through `asyncio.to_thread`
  yourself.
- Return an `AdapterResponse`, or raise `AdapterError` on failure. Do not
  return `None` or a bare string.
- Populate the token/`cost_usd` fields when the provider reports them —
  the recorder and the cost/latency aggregation read them straight off
  the response.
- Adapters do not assemble traces. Return the response; the recorder
  ingests it into a `Trace` separately.

The three bundled adapters are auto-wired from YAML by the `agent:` block
(see each section below). A *custom* adapter — anything you subclass
yourself — is wired via a Python `entrypoint` (an embedded callable that
constructs and delegates to your adapter); there is no `type:` tag for
user-defined adapters.

---

## `EmbeddedAdapter`

Wraps a plain Python callable. Use this when your agent lives in the
same Python process as selfevals — the typical "iterate fast in-repo"
mode. No serialisation, no transport, no isolation: a bug in the agent
will crash the selfevals run.

The callable may be sync or async. A sync callable is offloaded to a
worker thread so it never blocks the event loop; an async callable is
awaited directly.

### When to use

- Quick iteration on an agent you already import.
- Test suites where you want deterministic responses.
- Hello-world demos and CI smoke tests.

### YAML

```yaml
agent:
  entrypoint: selfevals.examples.pingpong:run
```

`entrypoint` is `module.path:callable` (see
`evals/experiments/example_pingpong.yaml`). The loader imports the
module and resolves the callable; the CLI then wraps it in an
`EmbeddedAdapter` for you.

The explicit, tagged form is equivalent:

```yaml
agent:
  type: embedded
  entrypoint: selfevals.examples.pingpong:run
```

Both shapes select `EmbeddedAdapter`; the bare-`entrypoint` form is the
shorthand.

### Agent code

```python
from selfevals import AdapterRequest, AdapterResponse


def run(req: AdapterRequest) -> AdapterResponse:
    # Echo back the last user message; ignore tools, return token counts.
    last_user = next(
        (m for m in reversed(req.input.get("messages", [])) if m.get("role") == "user"),
        {"content": ""},
    )
    return AdapterResponse(
        content=str(last_user["content"]),
        tokens_input=10,
        tokens_output=5,
    )
```

An `async def run(req)` works identically — the adapter awaits it. The
CLI also accepts a bare `str` return type and wraps it as
`AdapterResponse(content=...)`. Anything else raises an `AdapterError`
at invoke time.

### Limitations

- No process isolation. The agent runs in your selfevals process.
- No timeout enforcement at the adapter layer (you get whatever the
  Python callable does).
- No retries, no streaming.

---

## `CliCommandAdapter`

Spawns a subprocess per case, writes a JSON request to its stdin, reads
a JSON response from its stdout. Non-zero exit code raises
`AdapterError`. Timeout defaults to 60 seconds; configurable per
instance. The subprocess is spawned and awaited asynchronously, so many
cases run concurrently without blocking the event loop.

### When to use

- Your agent is implemented in another language (Go, Rust, Node) or
  needs OS-level isolation.
- You want a clean process boundary so a crash doesn't kill selfevals.
- You already have a CLI wrapper around the agent.

### YAML

Wire it natively with `agent: {type: cli, ...}` — no Python entrypoint
proxy needed:

```yaml
agent:
  type: cli
  command: ["./bin/my-agent", "--mode", "eval"]   # required, non-empty argv
  env: { MY_TOKEN: "..." }                          # optional
  timeout_seconds: 30                               # optional; default 60
```

The CLI builds `CliCommandAdapter(command, env=..., timeout_seconds=...)`
for you. `command` must be a non-empty list of strings; `env`, when
supplied, replaces the inherited environment (stdlib `subprocess`
semantics). Omitting `timeout_seconds` falls back to the adapter default
(60 seconds).

### Agent code (bash example)

A minimal `./bin/my-agent` that satisfies the protocol:

```bash
#!/usr/bin/env bash
# Read the JSON request, echo back the user content with token counts.
request="$(cat)"
content="$(printf '%s' "$request" | jq -r '.input.messages[-1].content // ""')"
jq -n --arg c "$content" '{
  content: $c,
  tokens_input: 10,
  tokens_output: 5,
  tool_uses: [],
  stop_reason: "end_turn"
}'
```

### Limitations

- One subprocess per case: spawn overhead matters at scale.
- No retries on transient failure; non-zero exit is a hard error.
- No streaming. The full response must arrive on stdout before
  selfevals parses it.
- Timeout is enforced via `asyncio.wait_for`; on timeout the child is
  killed and `AdapterError` is raised.
- `env` overrides replace the inherited environment when supplied
  (stdlib `subprocess` semantics).

---

## `HttpEndpointAdapter`

POSTs a JSON request to a URL and reads a JSON response. Built on
`httpx.AsyncClient`, so the POST is awaited natively and many endpoints
can be hit concurrently. Default timeout 60 seconds.

### When to use

- Your agent runs as a hosted service.
- You want to point selfevals at a deployed staging environment or a
  local server tunnelled via `ngrok`.
- You need to evaluate the *deployed* surface, not an in-process copy.

### YAML

Wire it natively with `agent: {type: http, ...}` — no Python entrypoint
proxy needed:

```yaml
agent:
  type: http
  url: "https://agent.example.com/eval"          # required
  headers: { Authorization: "Bearer ..." }        # optional
  timeout_seconds: 30                              # optional; default 60
```

The CLI builds `HttpEndpointAdapter(url, headers=..., timeout_seconds=...)`
for you. `Content-Type: application/json` is always set; anything in
`headers` is merged on top. Omitting `timeout_seconds` falls back to the
adapter default (60 seconds).

### Agent code (FastAPI example)

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class Request(BaseModel):
    workspace_id: str
    case_id: str
    input: dict
    context: dict | None = None
    tools_allowed: list[str] = []
    parameters: dict = {}
    metadata: dict = {}


@app.post("/eval")
def evaluate(req: Request) -> dict:
    last_user = next(
        (m for m in reversed(req.input.get("messages", [])) if m.get("role") == "user"),
        {"content": ""},
    )
    return {
        "content": str(last_user["content"]),
        "tokens_input": 10,
        "tokens_output": 5,
        "tool_uses": [],
        "stop_reason": "end_turn",
    }
```

### Limitations

- One request per case; no batching.
- No retries on transient failure (non-2xx → `AdapterError`
  immediately).
- No streaming; the full JSON body must come back before selfevals
  parses it.
- Auth is whatever you put in `headers`. There is no built-in OAuth /
  token-refresh layer.

---

## Comparison

| Adapter               | Latency overhead         | Isolation                   | Typical use                                              |
| --------------------- | ------------------------ | --------------------------- | -------------------------------------------------------- |
| `EmbeddedAdapter`     | ~0 (function call)       | None (same process)         | Fast in-repo iteration, tests, CI smoke runs.            |
| `CliCommandAdapter`   | Subprocess spawn per case | OS process (clean exit)     | Multi-language agents, OS-level isolation, crash safety. |
| `HttpEndpointAdapter` | Network round-trip       | Remote (different host OK)  | Deployed/staging agents, dogfooding through a tunnel.    |

The transport protocol is nailed down so the layers above the adapter
can stabilise first. Streaming, retries, batching, structured auth, and
wire-format YAML helpers are deliberately left out of the bundled
adapters for now; reach for a custom adapter (above) when you need them.
