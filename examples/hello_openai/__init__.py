"""Realistic example: an OpenAI-backed agent optimized by selfevals.

The companion `experiment.yaml` runs three EvalCases (sentiment, structured
extraction, open-ended support reply) through `agent.run`, scores them with
a `DeterministicGrader` plus an `LLMJudgeGrader`, and sweeps the temperature
parameter via the `GridProposer`.

This is the OpenAI twin of `examples/hello_llm/` (Anthropic): same cases,
same graders, same search space — only the provider call differs, so the
two are directly comparable.

When `OPENAI_API_KEY` is unset, the agent falls back to a deterministic
fake so the example remains runnable without credentials. The fake is
designed to make the temperature sweep meaningful: cooler temperatures
produce concise, structured answers and warmer temperatures hedge, so the
grading signal differs across the search space.
"""
