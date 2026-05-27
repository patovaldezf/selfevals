# Evals Framework for Self-Improving Agents

Status: design specification v0.1.

This document defines the conceptual model behind selfevals. It is intentionally
framework-agnostic: the same primitives should work for local CLIs, CI jobs,
agent runtimes, web dashboards, and long-running optimization loops.

## 1. Thesis

selfevals is not just an eval runner. It is a system for deciding what should be
evaluated, capturing agent behavior, constructing reliable datasets, running
reproducible experiments, analyzing failures, and turning production mistakes
into permanent regression coverage.

The central unit is:

```text
Experiment + AgentFleet + Agent + Dataset + Trace + GraderCard + OptimizationLoop
```

`EvalCase` is one part of that system, not the system itself.

## 2. Non-Negotiable Principles

1. Every experiment starts with explicit metrics and thresholds.
2. Every experiment declares which components are editable and which are frozen.
3. Every result is reproducible: agent version, parameters, dataset, grader,
   runtime, seeds, and framework versions are recorded.
4. Regression and golden datasets protect contracts; capability datasets measure
   progress. They must not be treated as the same decision surface.
5. Reliability requires repeated runs. A single run does not measure stability.
6. Failures have severity, cost, and weight. Not all failures are equivalent.
7. LLM judges are auditable, calibrated, and versioned components.
8. The optimizer must not overfit to the judge at the expense of user outcomes.
9. Production data must be scrubbed for PII before it enters datasets.
10. The core stays runtime-agnostic, with integrations layered around it.

## 3. Evaluation Targets

selfevals can evaluate or iterate over the following components when an experiment
explicitly allows it:

| Component | Evaluated surface | Editable surface |
| --- | --- | --- |
| Prompt | instructions, format, tone, constraints, variables | text, sections, examples, policies |
| Model | provider, model, reasoning mode, temperature, max tokens | model choice and parameters |
| Workflow | nodes, edges, handoffs, redundant or missing steps | topology, ordering, compression, expansion |
| Tools | description, schema, code, permissions, granularity | add, remove, split, consolidate, rewrite |
| Skills/scripts | reusable instructions and executable helpers | content, API, examples, scope |
| Dataset | coverage, realism, balance, difficulty, variability | generation, filtering, graduation, balancing |
| Harness | runner, sandbox, logging, replay, determinism | runtime, parallelism, retries, capture |
| Retrieval/memory | retrieved documents, allowed memory, context use | ranker, chunks, memory policy, truncation |
| Guardrails | policy, refusals, escalation, permissions | rules, thresholds, deterministic checks |
| Monitoring | traces, errors, latency, cost, drift | sampling, alerts, dashboards |
| Full agent | final outcome, trajectory, UX, cost, safety | configuration, tools, prompts, parameters |

## 4. Eval Taxonomy

An eval needs multiple coordinates because an agent can be judged by final answer,
trajectory, tool use, retrieved evidence, runtime behavior, or human judgment at
the same time.

```yaml
taxonomy:
  level: trajectory
  feature:
    primary: commerce.product_resolution
    secondary:
      - commerce.search.search_by_sku
      - commerce.customer_identification
  source:
    type: production
  ground_truth:
    methods:
      - rubric
  runtime: offline
  risk:
    overall: high
    user_trust: high
  dataset_type: regression
```

### Level

`level` answers: which part of the system is being judged?

| Level | Question |
| --- | --- |
| `single_step` | Does one isolated input produce the correct output? |
| `final_response` | Does the final answer satisfy the rubric? |
| `step_level` | Was each intermediate step reasonable? |
| `tool_call` | Was the correct tool called with correct arguments? |
| `retrieval` | Was the correct evidence retrieved and used? |
| `memory_context` | Was memory used, forgotten, and compacted correctly? |
| `workflow` | Did the full flow follow the required steps? |
| `trajectory` | Was the complete path safe, efficient, and correct? |
| `conversation` | Was a multi-turn case resolved end to end? |
| `system` | Did the full system achieve the business objective? |

If tools, retrieval, handoffs, or intermediate decisions matter, a final-response
eval is insufficient.

### Feature

`feature` is the primary coverage axis. It can represent a user-facing feature,
an internal agent capability, a safety capability, or a system behavior.

Feature names should be stable, filterable, and project-native. Dotted paths are
preferred for analytics:

```yaml
feature:
  primary: commerce.product_resolution
  secondary:
    - commerce.search.search_by_sku
    - commerce.recommendation.propose_alternative
```

Recommended namespaces:

- `commerce.*`, `support.*`, `billing.*` for product features.
- `agent.*` for internal capabilities, including `agent.skills.*` and
  `agent.tools.*`.
- `system.*` for platform and runtime behavior.
- `safety.*` for safety and security behavior.
- `ux.*` for interaction quality.

Projects should keep a versioned feature registry. Free-form strings inside
datasets are acceptable for experiments, but they are not enough for a durable
quality system.

### Source

`source` answers: where did this case come from?

| Source | Main use |
| --- | --- |
| `handcrafted` | critical rules and known edge cases |
| `production` | real user distribution |
| `staging` | captured staging behavior |
| `development` | cases produced during implementation |
| `failure` | bugs, incidents, complaints, eval failures |
| `synthetic` | broader coverage and controlled variation |
| `adversarial` | safety and robustness pressure |
| `human_labeled` | high-confidence calibration |
| `external_benchmark` | general comparison against known benchmarks |
| `simulation` | generated users, tools, and environments |
| `custom` | project-specific source |

When `source.type: failure`, the case should include provenance:

```yaml
source:
  type: failure
  failure_type: production_incident
  failure_id: inc_2026_05_16_001
  first_seen_at: 2026-05-16
  detected_by: production_monitor
```

### Ground Truth

`ground_truth` answers: how do we know whether the behavior passed?

| Method | Use when |
| --- | --- |
| `exact_match` | deterministic enums, routing, extraction |
| `schema_validation` | JSON and API contracts |
| `deterministic_assertion` | required text, forbidden tools, regex, invariants |
| `reference_answer` | human-written ideal answer |
| `rubric` | open tasks with explicit criteria |
| `pairwise_preference` | comparing candidate outputs |
| `outcome_based` | ticket closed, task completed, user approved |
| `human_judgment` | subjective or high-risk cases |
| `llm_judge` | scalable open-ended evaluation with calibration |

Critical evals should include a deterministic component or strong human
calibration. LLM judges are useful, but they are not an oracle.

### Runtime

`runtime` answers: where and when does this eval run?

| Runtime | Function |
| --- | --- |
| `offline` | local or CI evals with versioned datasets |
| `replay` | historical traces replayed with new variants |
| `simulation` | generated users, tools, and environments |
| `shadow` | candidate observed alongside production |
| `canary` | limited traffic release with measurement |
| `online` | continuous production evaluation |
| `human_review` | queued human evaluation |

### Dataset Type

`dataset_type` answers: what decision does this dataset support?

| Type | Decision enabled |
| --- | --- |
| `smoke` | detect obvious breakage quickly |
| `golden` | protect stable, high-value contracts |
| `regression` | prevent fixed bugs and incidents from returning |
| `capability` | measure progress and coverage |
| `production_sample` | detect drift and prioritize work |
| `adversarial_safety` | block safety and abuse risks |
| `calibration` | measure and improve judges |
| `incident_queue` | classify failures before graduation |

## 5. Datasets as a Portfolio

There should not be one monolithic dataset. selfevals operates a portfolio:

| Dataset | Purpose | Usually gates? |
| --- | --- | --- |
| `smoke` | detect obvious failures | yes |
| `golden` | protect canonical behavior | yes |
| `regression` | protect fixed incidents | yes |
| `capability` | measure breadth and improvement | not by default |
| `production_sample` | measure reality and drift | not by default |
| `adversarial_safety` | pressure safety boundaries | yes for high-risk systems |
| `calibration` | calibrate judges | indirectly |
| `incident_queue` | triage failures into permanent cases | no |

Cases can graduate across datasets. A production failure starts in an incident
queue, becomes a regression case when understood, and may later become golden if
it represents a core product contract.

## 6. Experiment Contract

An experiment must specify:

- Target metric and threshold.
- Guardrail metrics and thresholds.
- Editable components.
- Frozen components.
- Search space.
- Datasets used for optimization.
- Datasets used as gates.
- Repetitions per case.
- Decision policy.

Example:

```yaml
target:
  primary:
    metric: feature_pass_rate
    feature: commerce.product_resolution
    operator: ">="
    value: 0.92
  guardrails:
    - metric: regression_pass_rate
      operator: "=="
      value: 1.0
    - metric: p95_latency_ms
      operator: "<="
      value: 4500
    - metric: cost_per_task_usd
      operator: "<="
      value: 0.035
editable:
  prompt: true
  model: false
  model_params: true
  tool_descriptions: true
  tool_code: false
  dataset: false
  ground_truth: false
  graders: false
search_space:
  model_params:
    reasoning: [low, medium, high]
    temperature:
      min: 0
      max: 0.7
frozen:
  fleet_version: commerce_agents:2026-05-16
  dataset_versions:
    - capability/commerce_product_resolution_v3
    - regression/commerce_v7
  grader_versions:
    - product_resolution_judge:v2
```

The optimizer may only touch explicitly editable surfaces. Anything not declared
editable is part of the measurement apparatus and must remain fixed.

## 7. Traces

A `Trace` is the observable record of a run: model calls, tool calls, retrieved
documents, reasoning blocks when available, timing, costs, errors, outputs, and
grader results.

Trace-level evals are required when correctness depends on the path, not just the
final answer. Examples:

- The answer is correct but used a forbidden tool.
- The agent leaked private context while producing a good final answer.
- The agent reached the right result through an unstable or expensive path.
- The agent made a bad intermediate claim that was later hidden by the final
  response.

## 8. Graders

Graders are versioned components. They must declare:

- Input surface.
- Output labels.
- Scoring scale.
- Calibration dataset.
- Known failure modes.
- Degrade behavior when unavailable.

LLM judges should be treated as models under test. A judge that cannot identify
known bad outputs should not be allowed to gate releases.

## 9. Decision Policy

The decision matrix converts aggregate metrics into an outcome:

- `KEEP_CANDIDATE`: candidate improved target metrics without violating gates.
- `REJECT`: candidate failed a gate, regressed, or did not improve enough.
- `INVESTIGATE`: result is ambiguous or below target despite improvement.
- `REQUIRE_TRADEOFF_REVIEW`: candidate improves the target while hurting a
  declared guardrail.
- `SPAWN_SUBEXPERIMENT`: evidence suggests a narrower hypothesis should be
  tested separately.

Every accepted change must include a decision record explaining why it improves
user or business behavior, not just why a score increased.

## 10. Handoff and Agent Skills

selfevals supports two complementary workflows:

- Handoff mode: run longer experiments unattended, then review the resulting
  traces, aggregates, and decision records.
- Agent-skill mode: expose analysis skills that code agents can run with human
  input in the loop.

The same data model should support both. Long-running experiments need durable
storage and reproducibility. Interactive agent work needs compact bundles,
explicit hypotheses, and reviewable proposed changes.

## 11. Anti-Overfitting Requirements

The system must defend against optimizer overfitting:

- Hold out gate datasets from the optimization loop.
- Track judge calibration separately from candidate performance.
- Require deterministic or human-verified checks for critical behavior.
- Preserve failure provenance.
- Keep decision logs for accepted changes.
- Monitor production drift after release.

The central question for every improvement is: what user, business, or safety
behavior improved? Score movement is evidence, not the goal.
