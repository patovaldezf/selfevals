# Taxonomy

The taxonomy is the shared language for datasets, eval cases, experiments, reports,
and optimization decisions. It is designed for two readers at once: humans need names
that match industry vocabulary, and agents need crisp fields that are hard to confuse.

## Core Principles

- Prefer established evaluation terms over project-specific jargon.
- Use closed enums where downstream logic depends on stable values.
- Use free-form tags only for product- or domain-specific segmentation.
- Preserve one primary classification per dimension, with secondary tags for overlap.
- Make every label useful for filtering, reporting, or optimization.

## Evaluation Level

`Level` describes what the grader is judging.

| Level | Use |
| --- | --- |
| `single_step` | One bounded transformation or decision. |
| `multi_step` | A short sequence where intermediate reasoning matters. |
| `final_response` | The user-visible answer. |
| `tool_call` | Tool choice and arguments. |
| `retrieval` | Document lookup and grounding quality. |
| `memory_context` | Recall and use of stored context. |
| `workflow` | Whether a prescribed process or graph was followed. |
| `trajectory` | The full path through reasoning, retries, tools, cost, and safety. |
| `conversation` | A multi-turn exchange from start to finish. |
| `system` | End-to-end business outcome. |
| `agent` | Agent artifact quality without executing a task. |

## Dataset Source

`DatasetSource` captures where a case came from.

| Source | Use |
| --- | --- |
| `handcrafted` | Expert-written cases for critical behavior. |
| `production` | Anonymized or sampled production traffic. |
| `failure` | Prior failures promoted into regression coverage. |
| `staging` | Pre-production traffic. |
| `dev` | Developer-authored local tests. |
| `synthetic` | Generated cases used to broaden coverage. |
| `adversarial` | Prompt-injection, abuse, safety, and boundary tests. |
| `human_labeled` | Cases with explicit human labels. |
| `external_benchmark` | Public or third-party benchmark data. |
| `simulation` | Simulated users, tools, or environments. |
| `custom` | Domain-specific source defined by the workspace. |

## Ground Truth

`GroundTruthMethod` describes how correctness is established.

| Method | Use |
| --- | --- |
| `exact_match` | Deterministic strings, labels, or enum values. |
| `schema_validation` | Structured output contracts. |
| `deterministic_assertions` | Rule-based checks. |
| `reference_answer` | Human-written expected answer. |
| `rubric` | Criteria-based scoring. |
| `pairwise_preference` | A/B preference judgment. |
| `outcome_based` | Real-world task completion. |
| `human_judgment` | Human evaluation. |
| `llm_judge` | LLM-based evaluation. |
| `hybrid` | Multiple methods combined. |

## Dataset Role

| Role | Purpose |
| --- | --- |
| `smoke` | Fast sanity check for obvious breakage. |
| `capability` | Broad measurement of what the system can handle. |
| `regression` | Stable contracts that should not break. |
| `golden` | High-confidence canonical behavior. |
| `reliability` | Repeated runs for variance and consistency. |
| `production_sample` | Drift and real-traffic monitoring. |
| `adversarial` | Safety and robustness pressure. |

## Feature Tags

Each case has a `primary` feature and may have secondary tags. The primary feature
answers "what should this case most directly improve?" Secondary tags preserve overlap
without making reporting ambiguous.

Feature names should use dotted paths:

```text
commerce.product_resolution
support.refund_policy
search.sku_lookup
conversation.intent_routing
```

## PII Status

PII metadata is part of the case contract, not an afterthought. A case should declare
whether sensitive data was dropped, redacted, tokenized, hashed, or intentionally
retained under a restricted access policy.

## Promotion Policy

Capability cases graduate to regression when behavior becomes contractual:

- The expected behavior is clear.
- The failure has occurred in production or carries high product risk.
- Human labels are reliable.
- The grader is calibrated enough to enforce the case.
- The product policy is stable.
- The team is willing to block releases on failure.
