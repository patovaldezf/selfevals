# Operational Specification v0.1

This spec defines how selfeval turns evaluation data into a repeatable optimization
loop. The design goal is simple: make agent quality measurable, debuggable, and
improvable without binding users to a specific agent framework.

## System Shape

selfeval has two execution modes:

- `handoff`: headless optimization over declarative parameters such as prompts, model
  parameters, retrieval settings, and memory settings.
- `agent_loop`: an external coding agent uses selfeval reports, traces, and bundled
  skills to propose source-level changes in the user's repository.

Both modes share the same schemas, storage model, trace model, grader contracts, and
decision records. The difference is what the proposer is allowed to change.

## Optimization Loop

An iteration is a proposal, a run, an aggregate, and a decision.

1. The proposer emits candidate parameters and a hypothesis.
2. The runner executes every selected case, optionally with repetitions.
3. Graders score each case result.
4. Aggregation computes primary, guardrail, reliability, cost, and duration metrics.
5. The decision matrix chooses whether to keep, reject, investigate, or require review.
6. Optional error analysis turns failures into stable failure-mode identities.

The loop stops when the search space is exhausted, the target is met, convergence is
reached, or a caller aborts the run.

## Editable Surface

The experiment declares what may change.

Allowed in `handoff`:

- system prompts
- model choice
- model parameters
- tool descriptions
- retrieval configuration
- memory configuration

Allowed only in `agent_loop`:

- tool implementation
- workflow graph
- source code
- bundled skills

The validator rejects experiments that ask `handoff` to change source-level artifacts.

## Storage

The local implementation uses SQLite plus a content-addressed filesystem object store.
The interface is deliberately abstract so the same contracts can map to Postgres and
object storage later.

Every persistent entity is workspace-scoped. Reads and writes go through a
`WorkspaceScope`; direct cross-workspace reads are treated as isolation violations.

## Trace Model

A trace captures the path of an agent run:

- run metadata
- environment timestamps
- LLM calls
- tool calls
- retrieval spans
- decision spans
- error spans
- grader results
- aggregate token, latency, and cost metrics

Trace data exists to answer two questions: "what happened?" and "what should change?"

## Grading

Graders may be deterministic, rubric-based, LLM-based, human-labeled, or hybrid.
Every grader returns a label, optional score, confidence, and reason. Calibration data
belongs to `GraderCard` so teams can reason about trust in a grader over time.

## Decision Matrix

The decision matrix is intentionally conservative:

- Guardrail failure can reject or require tradeoff review.
- Regression on protected datasets follows the experiment policy.
- No improvement is rejected.
- Below-target performance is investigated.
- Clean improvement is kept as a candidate.

The result is persisted as a `DecisionRecord`, including rationale and metric snapshot.

## Error Analysis

Failure analysis is a loop, not a dashboard. Failed traces are bundled, classified
against a stable workspace taxonomy, and fed back into proposer inputs. The critical
rule is stable identity: failure modes have ids, lifecycle state, examples, and merge
history. Agents classify; humans promote or edit official taxonomy entries.

## Reporting

Reports must be useful to both humans and agents.

Human reports emphasize:

- target progress
- best iteration
- proposal differences
- failure modes
- cost and duration
- concrete next commands

Agent-native reports preserve structured JSON so external agents can inspect traces,
compare iterations, and propose targeted changes.

## Local-First Constraint

The default path must run locally with no hosted infrastructure. Optional web/API and
telemetry extras can be installed when users want the trace UI or OpenTelemetry capture,
but the core loop remains a small Python package with a CLI.

## Non-Goals for v0.1

- hosted auth
- billing
- distributed trace stitching across arbitrary services
- managed annotation marketplace
- automatic source-code mutation without an external coding agent
- provider-specific lock-in
