# Agent adapters

`bootstrap` invokes the agent under test through an `AgentAdapter`. Three
concrete implementations ship in the runtime, all defined in
`src/bootstrap/runner/adapters.py`. They share one contract — given an
`AdapterRequest`, return an `AdapterResponse` — and differ only in
transport.

This document is the reference for picking one and writing the agent on
the other side.

---

## The contract

All adapters speak the same shape. Read these dataclasses straight from
`src/bootstrap/runner/adapters.py` if you need authoritative field
information; the summary below tracks that source.

### `AdapterRequest`

What bootstrap sends to your agent for each case.

| Field           | Type                 | Notes                                                                                 |
| --------------- | -------------------- | ------------------------------------------------------------------------------------- |
| `workspace_id`  | `str`                | The owning workspace's id.                                                            |
| `case_id`       | `str`                | Stable id for the `EvalCase` being run.                                               |
| `input`         | `dict[str, Any]`     | The Pydantic-compatible `EvalCase.input` payload (usually a `messages` list).         |
| `context`       | `dict[str, Any]\|None` | Optional context block (system info, retrieved docs, etc.).                         |
| `tools_allowed` | `list[str]`          | Frozen list of tool names the agent is permitted to use.                              |
| `parameters`    | `dict[str, Any]`     | Overrides from the proposer (e.g. model temperature, prompt swap). Pass through verbatim. |
| `metadata`      | `dict[str, Any]`     | Free-form metadata for tracing.                                                       |

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

## `EmbeddedAdapter`

Wraps a plain Python callable. Use this when your agent lives in the
same Python process as bootstrap — the typical "iterate fast in-repo"
mode. No serialisation, no transport, no isolation: a bug in the agent
will crash the bootstrap run.

### When to use

- Quick iteration on an agent you already import.
- Test suites where you want deterministic responses.
- Hello-world demos and CI smoke tests.

### YAML

```yaml
agent:
  entrypoint: bootstrap.examples.pingpong:run
```

`entrypoint` is `module.path:callable` (see
`evals/experiments/example_pingpong.yaml`). The loader imports the
module and resolves the callable; the CLI then wraps it in an
`EmbeddedAdapter` for you.

### Agent code

```python
from bootstrap.runner.adapters import AdapterRequest, AdapterResponse


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

The CLI also accepts a bare `str` return type and wraps it as
`AdapterResponse(content=...)`. Anything else raises a `TypeError` at
invoke time.

### Limitations

- No process isolation. The agent runs in your bootstrap process.
- No timeout enforcement at the adapter layer (you get whatever the
  Python callable does).
- No retries, no streaming.

---

## `CliCommandAdapter`

Spawns a subprocess per case, writes a JSON request to its stdin, reads
a JSON response from its stdout. Non-zero exit code raises
`AdapterError`. Timeout defaults to 60 seconds; configurable per
instance.

### When to use

- Your agent is implemented in another language (Go, Rust, Node) or
  needs OS-level isolation.
- You want a clean process boundary so a crash doesn't kill bootstrap.
- You already have a CLI wrapper around the agent.

### YAML

The bundled CLI auto-wires `EmbeddedAdapter`. For `CliCommandAdapter`
today you construct it explicitly in Python (e.g. inside a custom
`entrypoint` callable that delegates to a subprocess) — wiring it from
YAML is roadmap work, not shipped.

A reasonable Python entrypoint that delegates:

```python
# my_project/cli_proxy.py
from bootstrap.runner.adapters import (
    AdapterRequest, AdapterResponse, CliCommandAdapter,
)

_adapter = CliCommandAdapter(["./bin/my-agent"], timeout_seconds=30.0)


def run(req: AdapterRequest) -> AdapterResponse:
    return _adapter.invoke(req)
```

```yaml
agent:
  entrypoint: my_project.cli_proxy:run
```

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
  bootstrap parses it.
- Timeout is enforced via `subprocess.run(timeout=...)`; on timeout the
  child gets `SIGKILL`-equivalent and `AdapterError` is raised.
- `env` overrides replace the inherited environment when supplied
  (stdlib `subprocess` semantics).

---

## `HttpEndpointAdapter`

POSTs a JSON request to a URL and reads a JSON response. Built on
`urllib` so there's no third-party dependency. Default timeout 60
seconds.

### When to use

- Your agent runs as a hosted service.
- You want to point bootstrap at a deployed staging environment or a
  local server tunnelled via `ngrok`.
- You need to evaluate the *deployed* surface, not an in-process copy.

### YAML

Same story as `CliCommandAdapter`: wire it via a small Python entrypoint
today.

```python
# my_project/http_proxy.py
from bootstrap.runner.adapters import (
    AdapterRequest, AdapterResponse, HttpEndpointAdapter,
)

_adapter = HttpEndpointAdapter(
    "https://agent.example.com/eval",
    headers={"Authorization": "Bearer ${MY_TOKEN}"},
    timeout_seconds=30.0,
)


def run(req: AdapterRequest) -> AdapterResponse:
    return _adapter.invoke(req)
```

```yaml
agent:
  entrypoint: my_project.http_proxy:run
```

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
- No streaming; the full JSON body must come back before bootstrap
  parses it.
- Auth is whatever you put in `headers`. There is no built-in OAuth /
  token-refresh layer.
- No connection pooling beyond what `urllib` does on its own (i.e. very
  little).

---

## Comparison

| Adapter               | Latency overhead         | Isolation                   | Typical use                                              |
| --------------------- | ------------------------ | --------------------------- | -------------------------------------------------------- |
| `EmbeddedAdapter`     | ~0 (function call)       | None (same process)         | Fast in-repo iteration, tests, CI smoke runs.            |
| `CliCommandAdapter`   | Subprocess spawn per case | OS process (clean exit)     | Multi-language agents, OS-level isolation, crash safety. |
| `HttpEndpointAdapter` | Network round-trip       | Remote (different host OK)  | Deployed/staging agents, dogfooding through a tunnel.    |

All three are MVP. Streaming, retries, batching, structured auth, and
wire-format YAML helpers are intentionally **not yet implemented** — the
protocol is nailed down so the layers above the adapter can stabilise
first.
