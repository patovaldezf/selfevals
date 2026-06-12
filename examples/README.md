# Examples

Four runnable examples, in increasing realism. Run them from a **source
checkout** (the `hello_*` examples import `examples.hello_*.agent`, which
needs the repo on `sys.path`).

| Example         | Provider  | Needs a key? | What it shows                                                                                     |
| --------------- | --------- | ------------ | ------------------------------------------------------------------------------------------------- |
| `pingpong`      | none      | no           | The smallest possible loop — an in-process echo agent. Start here.                                |
| `showcase`      | none      | no           | The kitchen sink — one grader of every type and a funnel with every match kind, all offline.      |
| `hello_llm/`    | Anthropic | optional     | A real agent + LLM judge over three task types, with a deterministic fake fallback.               |
| `hello_openai/` | OpenAI    | optional     | The exact same experiment as `hello_llm`, swapped to OpenAI — a side-by-side provider comparison. |

## pingpong — the smallest loop

Ships inside the package, so it's the one you can copy after `pip install`:

```bash
selfevals examples copy pingpong
selfevals run evals/experiments/example_pingpong.yaml --no-persist
```

It uses the `EmbeddedAdapter` against a trivial echo agent. No network, no
key, sub-second. Good for confirming the install works and for reading a
minimal `experiment.yaml`.

## showcase — every grader, every match kind

Also ships inside the package, copy-and-run like pingpong:

```bash
selfevals examples copy showcase
selfevals run evals/experiments/example_showcase.yaml --no-persist
```

Where pingpong is the minimal loop, `showcase` is the **catalog**: a single
spec that wires up one grader of every type (`deterministic`, `set_match`,
`judge_panel`, `funnel`) and a funnel whose levels demonstrate every builtin
match kind (`exists`, `equals`, `by_index`, `by_key`, `tool_called`,
`span_exists`, a nested `set_match` via `feeds_extract`, and a
`{grader: ...}` reference). The deterministic agent
(`selfevals.examples.showcase:run`) emits a rich `structured_output` plus tool
calls, and a deterministic `judge` lets `judge_panel` run fully offline. Driven
by `model_params.level`, the grid proposer improves from `level=0.0` (the funnel
gate fails, its children are SKIPPED — the short-circuit) to `level=1.0` (every
level passes). It's the reference for _how each grader is configured in YAML_.

## hello_llm / hello_openai — a realistic eval

Both directories contain the same three files:

- **`cases.jsonl`** — three `EvalCase`s:
  1. _sentiment_ — classify a review as positive/negative/neutral
     (graded by `DeterministicGrader`: `must_include` / `must_not_include`).
  2. _extraction_ — pull `{city, date, attendees}` as JSON
     (graded by `DeterministicGrader`: `structured_output` equality).
  3. _support reply_ — an open-ended customer-support answer
     (graded by `LLMJudgeGrader` against a rubric).
- **`agent.py`** — exposes `run(req) -> AdapterResponse` (the agent) and
  `judge(req) -> AdapterResponse` (the rubric judge). Both call the provider
  when a key + SDK are present, and fall back to a deterministic fake
  otherwise. Temperature flows from the proposer into the API call.
- **`experiment.yaml`** — wires the dataset, the agent entrypoint, the two
  graders, the `pass@1 >= 0.7` target with a `cost_usd <= 0.05` guardrail,
  and a `GridProposer` sweeping `temperature ∈ {0.0, 0.5, 1.0}`.

Run them:

```bash
# Anthropic
pip install 'selfevals[anthropic]'
export ANTHROPIC_API_KEY=sk-ant-...        # optional
uv run selfevals run examples/hello_llm/experiment.yaml --no-persist

# OpenAI
pip install 'selfevals[openai]'
export OPENAI_API_KEY=sk-...               # optional
uv run selfevals run examples/hello_openai/experiment.yaml --no-persist
```

If the key is unset (or the SDK isn't installed), the agent prints a hint
and uses the fake. The fakes are designed so the temperature sweep produces
_different_ grader outcomes — cooler temperatures clear the structured
cases, warmer ones hedge — so the example optimizes over a meaningful
search space even offline.

Expected shape of the report: three iterations, temperature `0.0` winning
`pass@1`, and the failure-modes table picking up `structured_output_mismatch`
at the warmer settings.

## Adapting one to your own agent

Copy a `hello_*` directory and change three things:

1. **`cases.jsonl`** — your real inputs and expected outcomes.
2. **`agent.py`** — replace the provider call in `_call_<provider>` with a
   call to _your_ agent. Keep the `(AdapterRequest) -> AdapterResponse`
   signature; `req.parameters["model_params"]` is what the proposer sweeps.
3. **`experiment.yaml`** — point `agent.entrypoint` at your module, set your
   `target` metric and `search_space`, and pick a proposer.

If your agent already runs as a CLI or HTTP service, you don't need a Python
entrypoint at all — use `CliCommandAdapter` or `HttpEndpointAdapter` instead
(see [`../docs/adapters.md`](../docs/adapters.md)).
