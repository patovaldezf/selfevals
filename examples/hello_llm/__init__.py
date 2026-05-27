"""Realistic example: an Anthropic-backed agent optimized by selfevals.

The companion `experiment.yaml` runs three EvalCases (sentiment, structured
extraction, open-ended support reply) through `agent.run`, scores them with
a `DeterministicGrader` plus an `LLMJudgeGrader`, and sweeps the temperature
parameter via the `GridProposer`.

When `ANTHROPIC_API_KEY` is unset, the agent falls back to a deterministic
fake so the example remains runnable without credentials. The fake is
designed to make the temperature sweep meaningful: cooler temperatures
produce concise, structured answers and warmer temperatures produce more
discursive ones, so the grading signal differs across the search space.
"""
