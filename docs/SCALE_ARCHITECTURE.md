# selfevals Scale Architecture

> Target architecture for turning selfevals from a local eval runner and trace UI
> into an observability + evals feedback loop that can answer production-quality,
> cost, latency, and regression questions at high volume.

## Product Thesis

selfevals should not stop at storing traces. The product should make every
production interaction measurable, searchable, and convertible into a regression
test.

The core loop is:

```text
raw traces / production events
  -> normalized queryable facts
  -> metrics and aggregates
  -> dataset candidates
  -> eval suites and regression gates
  -> alerts and product actions
```

The current SQLite generic entity store remains valuable for local-first usage,
tests, and small deployments. The scale architecture introduces a separate
analytics-ready backend for hosted or high-volume deployments.

## Product Requirements

The scale architecture must support these product capabilities:

1. Online monitoring, not only offline evals: ingest real production runs as
   traces with pass/fail signal, tool calls, cost, latency, retries, errors, and
   user feedback.
2. First-class metrics by agent, tool, case, prompt, model, version, feature, and
   environment.
3. Living datasets from production: production trace -> candidate case -> human
   approval -> regression dataset.
4. Remote graders: support `grader.type=http` the same way agents can be HTTP,
   so teams can run custom graders without installing code inside selfevals.
5. Native human feedback: mark traces as correct, incorrect, partially correct,
   expected category, failure mode, and human notes.
6. Production dashboards: quality by day, model, prompt version, tool usage,
   tool failures, top regressions, and top failure modes.
7. Alerts: quality drops, cost spikes, tool error spikes, latency spikes, and
   critical new failure modes.
8. Strong versioning: every run records prompt version, agent version, model,
   tool schema version, dataset version, git SHA, and deploy SHA where present.
9. Production vs eval comparison: detect when eval quality diverges from
   production quality for the same feature, flow, prompt, model, or version.

## System Shape

```text
SDK / OTLP / API ingest
  -> API writes raw trace envelope
  -> normalizer extracts facts
  -> Postgres source of truth
  -> object storage for large payloads
  -> Redis queue for async work
  -> Redis streams for live run events
  -> workers compute aggregates, alerts, and dataset candidates
  -> UI / API / CI query facts and metrics
```

### Components

| Component | Responsibility | Notes |
| --- | --- | --- |
| API | Auth, ingest, reads, run launch, SSE/WebSocket edge | Stateless after moving runs and broker state out of process. |
| Postgres | Durable source of truth for queryable facts | Primary backend for hosted scale; replaces generic JSON scans on hot paths. |
| Object storage | Large prompts, responses, attachments, raw trace payloads | S3/R2/MinIO; Postgres stores pointers + content hashes. |
| Redis queue | Async experiment jobs, normalization jobs, alert jobs | RQ/Dramatiq for simple Python workflows; Celery if routing/retry complexity grows. |
| Redis streams | Live span/run events with short replay | Better than pub/sub because late subscribers can replay recent events. |
| Workers | Run evals, normalize traces, compute aggregates, fire alerts | Horizontally scalable and independent from API request lifecycle. |
| Optional ClickHouse | High-cardinality event analytics | Add only after Postgres materialized views are insufficient. |

## Storage Principles

1. Postgres is the source of truth for durable state.
2. Redis is coordination and live delivery, not the historical database.
3. Object storage holds large unbounded payloads.
4. Raw trace envelopes are retained for audit and replay.
5. Hot query dimensions are first-class columns, not `json_extract` fields.
6. Aggregates are derived and rebuildable from facts.
7. Local SQLite stays as an adapter for local-first and low-volume use.

## Security, Privacy, and Operations Requirements

These are required before hosted production use:

1. Auth, RBAC, scoped API keys, service tokens, and audit log.
2. Retention policies by workspace, environment, dataset, payload type, and
   trace type.
3. PII and secret redaction before persistence, with policy-controlled raw
   retention.
4. Schema evolution for traces, grader results, datasets, tools, and normalized
   facts.
5. Idempotent ingest and jobs through idempotency keys.
6. Durable job lifecycle: retry, lease, cancellation, timeout, dead-letter,
   progress, and logs.
7. Cursor pagination everywhere and async exports for large data.
8. Production trace sampling policies.
9. Payload-level access control separate from metrics-level access.
10. Backfills for new metrics and normalizer changes.
11. Flakiness tracking as a first-class metric.
12. Grader versioning for every result.
13. Incident workflow from alert to investigation to regression eval.
14. Workspace quotas and rate limits.
15. CI/CD regression gate API.
16. Internal SLOs for ingest latency, queue depth, normalizer lag, worker
    failures, and stream lag.

## Data Model

The goal is to preserve the current domain model while extracting the facts that
users need to query.

### Core Tenancy

#### `workspaces`

- `id`
- `slug`
- `name`
- `owner_id`
- `created_at`
- `updated_at`

#### `users`

- `id`
- `workspace_id`
- `external_id`
- `email`
- `name`
- `created_at`

Optional for local mode. In hosted mode it enables per-user quality, cost, and
tool analytics.

#### `workspace_memberships`

- `workspace_id`
- `user_id`
- `role` (`owner`, `admin`, `developer`, `viewer`, `annotator`)
- `created_at`

#### `api_keys`

- `id`
- `workspace_id`
- `name`
- `key_hash`
- `scopes_json`
- `created_by`
- `expires_at`
- `last_used_at`
- `created_at`

#### `audit_log`

Append-only record of sensitive changes.

- `id`
- `workspace_id`
- `actor_type` (`user`, `api_key`, `system`, `worker`)
- `actor_id`
- `action`
- `entity_type`
- `entity_id`
- `before_pointer`
- `after_pointer`
- `metadata_json`
- `created_at`

#### `retention_policies`

- `id`
- `workspace_id`
- `scope_type` (`workspace`, `environment`, `dataset`, `trace_type`, `payload_type`)
- `scope_id`
- `retain_raw_days`
- `retain_payload_days`
- `retain_metrics_days`
- `delete_mode` (`hard_delete`, `tombstone`, `redact_payload`)
- `created_at`

#### `redaction_rules`

- `id`
- `workspace_id`
- `name`
- `rule_type` (`pii`, `secret`, `regex`, `json_path`, `custom`)
- `pattern`
- `replacement`
- `applies_to_json`
- `enabled`
- `created_at`

#### `idempotency_keys`

- `workspace_id`
- `key`
- `request_hash`
- `entity_type`
- `entity_id`
- `status`
- `expires_at`
- `created_at`

#### `jobs`

- `id`
- `workspace_id`
- `job_type`
- `status` (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `dead_letter`)
- `priority`
- `attempt`
- `max_attempts`
- `lease_owner`
- `lease_expires_at`
- `idempotency_key`
- `input_pointer`
- `result_pointer`
- `error`
- `progress_json`
- `created_at`
- `started_at`
- `finished_at`

#### `export_jobs`

- `id`
- `workspace_id`
- `export_type` (`runs`, `traces`, `dataset`, `metrics`, `annotations`)
- `format` (`jsonl`, `csv`, `parquet`)
- `filters_json`
- `status`
- `result_pointer`
- `created_by`
- `created_at`
- `finished_at`

#### `sampling_policies`

- `id`
- `workspace_id`
- `environment_id`
- `name`
- `policy_json`
- `enabled`
- `created_at`

Examples:

- keep 100% failed runs;
- keep 100% high-cost or high-latency runs;
- sample 1% successful production runs;
- keep all runs for canary deploys;
- keep all runs with user feedback.

#### `quotas`

- `workspace_id`
- `metric_name`
- `limit_value`
- `window_seconds`
- `created_at`

#### `backfill_jobs`

- `id`
- `workspace_id`
- `backfill_type`
- `source_schema_version`
- `target_schema_version`
- `filters_json`
- `status`
- `progress_json`
- `created_at`
- `finished_at`

#### `synthetic_generation_jobs`

- `id`
- `workspace_id`
- `dataset_id`
- `generator_type` (`llm`, `template`, `mutation`, `simulation`, `multimodal`)
- `seed`
- `source_dataset_version_id`
- `source_filters_json`
- `prompt_version_id`
- `model_version_id`
- `target_count`
- `status`
- `result_dataset_version_id`
- `quality_report_pointer`
- `created_by`
- `created_at`
- `finished_at`

#### `synthetic_examples`

Tracks provenance for generated cases.

- `id`
- `workspace_id`
- `generation_job_id`
- `case_id`
- `case_version_id`
- `source_case_id`
- `source_run_id`
- `generation_method`
- `seed`
- `quality_score`
- `accepted`
- `created_at`

#### `dataset_cleaning_jobs`

- `id`
- `workspace_id`
- `dataset_id`
- `source_dataset_version_id`
- `cleaning_type` (`dedupe`, `validate`, `label_review`, `schema_fix`, `redact`, `balance`)
- `rules_json`
- `status`
- `result_dataset_version_id`
- `report_pointer`
- `created_by`
- `created_at`
- `finished_at`

### Cross-Cutting Metadata and Annotations

Metadata and annotations are transversal. They should apply to traces,
experiments, runs, spans, datasets, cases, versions, alerts, and failure modes
without adding a bespoke table for every new comment or tag workflow.

#### `entity_metadata`

Structured, queryable metadata attached to any entity. Use this for stable
filters and dimensions that product surfaces need to query.

- `id`
- `workspace_id`
- `entity_type`
- `entity_id`
- `key`
- `value_text`
- `value_number`
- `value_bool`
- `value_json`
- `source` (`sdk`, `api`, `import`, `system`, `human`)
- `created_at`

Indexes:

- `(workspace_id, entity_type, entity_id)`
- `(workspace_id, entity_type, key, value_text)`
- `(workspace_id, key, value_text)`

#### `entity_tags`

Human and system tags for lightweight grouping.

- `id`
- `workspace_id`
- `entity_type`
- `entity_id`
- `tag`
- `source` (`system`, `human`, `import`)
- `created_by`
- `created_at`

Indexes:

- `(workspace_id, entity_type, tag)`
- `(workspace_id, entity_type, entity_id)`

#### `annotations`

Comments, reviews, labels, investigation notes, and decisions attached to any
entity. For example: trace review notes, experiment analysis, span-level bug
comments, dataset curation notes, or failure-mode taxonomy discussion.

- `id`
- `workspace_id`
- `entity_type`
- `entity_id`
- `span_id`
- `run_id`
- `author_id`
- `annotation_type` (`comment`, `review`, `label`, `decision`, `investigation`)
- `status` (`open`, `resolved`, `accepted`, `rejected`)
- `severity` (`info`, `low`, `medium`, `high`, `critical`)
- `body`
- `label_json`
- `metadata_json`
- `created_at`
- `updated_at`

Indexes:

- `(workspace_id, entity_type, entity_id, created_at DESC)`
- `(workspace_id, annotation_type, status, created_at DESC)`
- `(workspace_id, severity, created_at DESC)`

#### `annotation_links`

Links annotations to related entities when a note spans multiple objects, for
example "this trace shows why this case was added to this dataset".

- `workspace_id`
- `annotation_id`
- `target_entity_type`
- `target_entity_id`
- `relationship`
- `created_at`

#### `environments`

Named execution environments. This prevents production, staging, eval, shadow,
and canary traffic from being mixed accidentally in metrics.

- `id`
- `workspace_id`
- `name` (`prod`, `staging`, `eval`, `dev`, `shadow`, `canary`)
- `type` (`production`, `preproduction`, `evaluation`, `development`)
- `description`
- `is_production`
- `created_at`
- `updated_at`

### Version Dimensions

#### `agent_versions`

- `id`
- `workspace_id`
- `name`
- `version`
- `git_sha`
- `deploy_id`
- `environment_id`
- `created_at`

#### `deployments`

- `id`
- `workspace_id`
- `environment_id`
- `agent_version_id`
- `git_sha`
- `deploy_sha`
- `deploy_id`
- `released_at`
- `created_at`

#### `prompt_versions`

- `id`
- `workspace_id`
- `name`
- `version`
- `content_hash`
- `payload_pointer`
- `created_at`

#### `model_versions`

- `id`
- `provider`
- `model`
- `revision`
- `pricing_version`
- `created_at`

#### `tool_versions`

- `id`
- `workspace_id`
- `tool_name`
- `schema_version`
- `implementation_version`
- `input_schema_hash`
- `output_schema_hash`
- `created_at`

### Evaluation Domain

#### `experiments`

- `id`
- `workspace_id`
- `name`
- `goal`
- `state`
- `mode`
- `primary_metric_name`
- `proposer_strategy`
- `max_iterations`
- `created_at`
- `updated_at`

#### `iterations`

- `id`
- `workspace_id`
- `experiment_id`
- `iteration_index`
- `state`
- `variant_id`
- `hypothesis`
- `proposed_parameters_json`
- `decision_outcome`
- `primary_metric_name`
- `primary_metric_value`
- `cost_usd`
- `duration_ms`
- `created_at`

#### `datasets`

- `id`
- `workspace_id`
- `name`
- `version`
- `source` (`manual`, `import`, `production`, `simulation`)
- `status` (`draft`, `active`, `archived`)
- `description`
- `created_by`
- `created_at`
- `updated_at`

#### `eval_cases`

- `id`
- `workspace_id`
- `dataset_id`
- `case_version_id`
- `experiment_id`
- `name`
- `external_id`
- `task_type`
- `feature_primary`
- `feature_secondary_json`
- `level`
- `holdout`
- `status` (`draft`, `active`, `retired`)
- `source` (`manual`, `import`, `production`, `simulation`)
- `input_hash`
- `input_pointer`
- `expected_hash`
- `expected_json`
- `execution_context_id`
- `metadata_json`
- `created_by`
- `created_at`
- `updated_at`

Indexes:

- `(workspace_id, dataset_id, status)`
- `(workspace_id, dataset_id, feature_primary)`
- `(workspace_id, dataset_id, holdout)`
- `(workspace_id, input_hash)`
- `(workspace_id, external_id)`

#### `execution_contexts`

Reusable execution context for eval cases and production replay. This captures
the conditions required to reproduce or simulate a run beyond the raw input.

- `id`
- `workspace_id`
- `name`
- `environment_id`
- `user_ref`
- `session_ref`
- `tenant_ref`
- `locale`
- `timezone`
- `channel` (`web`, `api`, `slack`, `email`, `voice`, `batch`, `test`)
- `feature_flags_json`
- `tool_state_json`
- `retrieval_context_json`
- `memory_context_json`
- `fixtures_pointer`
- `secrets_policy`
- `metadata_json`
- `created_at`

Use cases:

- replay a production trace under similar user/session context;
- test locale/timezone-sensitive behavior;
- pin feature flags or tool availability;
- attach mock fixtures for deterministic tools;
- record retrieval or memory state needed by the agent.

#### `case_versions`

Immutable versions of a testcase. `eval_cases` points at the current version,
while historical runs keep the exact `case_version_id` they evaluated.

- `id`
- `workspace_id`
- `case_id`
- `dataset_id`
- `version`
- `input_hash`
- `input_pointer`
- `expected_hash`
- `expected_json`
- `execution_context_id`
- `metadata_json`
- `change_reason`
- `created_by`
- `created_at`

Indexes:

- `(workspace_id, case_id, version)`
- `(workspace_id, dataset_id, created_at DESC)`

#### `dataset_versions`

Immutable membership snapshots. Experiments should run against a
`dataset_version_id`, not the mutable dataset head, so regression results stay
reproducible.

- `id`
- `workspace_id`
- `dataset_id`
- `version`
- `name`
- `description`
- `case_count`
- `content_hash`
- `created_by`
- `created_at`

#### `dataset_version_cases`

- `workspace_id`
- `dataset_version_id`
- `case_id`
- `case_version_id`
- `split` (`train`, `validation`, `holdout`, `canary`, `shadow`)
- `weight`
- `created_at`

Indexes:

- `(workspace_id, dataset_version_id, split)`
- `(workspace_id, case_id)`

#### `case_labels`

Human or judge labels attached to a specific case version.

- `id`
- `workspace_id`
- `case_id`
- `case_version_id`
- `labeler_type` (`human`, `judge`, `import`)
- `labeler_id`
- `label_json`
- `confidence`
- `review_status` (`pending`, `accepted`, `rejected`)
- `created_at`

#### `case_annotations`

Freeform review comments, severity tags, and expected-output edits before they
become a new `case_versions` row.

- `id`
- `workspace_id`
- `case_id`
- `case_version_id`
- `author_id`
- `annotation_type`
- `body`
- `metadata_json`
- `created_at`

#### `dataset_examples`

Examples promoted from production traces into datasets.

- `id`
- `workspace_id`
- `dataset_id`
- `source_run_id`
- `source_trace_id`
- `source_span_id`
- `label_status` (`unlabeled`, `labeled`, `reviewed`)
- `input_pointer`
- `expected_json`
- `metadata_json`
- `dedupe_hash`
- `selection_reason`
- `created_at`

Indexes:

- `(workspace_id, dataset_id, label_status)`
- `(workspace_id, source_run_id)`
- `(workspace_id, dedupe_hash)`

### Runtime Facts

#### `runs`

One logical agent attempt, whether it came from an eval or production.

- `id`
- `workspace_id`
- `experiment_id`
- `iteration_id`
- `eval_case_id`
- `dataset_id`
- `user_id`
- `session_id`
- `thread_id`
- `run_index`
- `repetition`
- `environment_id`
- `execution_context_id`
- `agent_version_id`
- `deployment_id`
- `prompt_version_id`
- `model_version_id`
- `dataset_version_id`
- `status`
- `started_at`
- `ended_at`
- `duration_ms`
- `cost_usd`
- `tokens_input`
- `tokens_output`
- `tokens_reasoning`
- `tokens_total`
- `span_count`
- `tool_call_count`
- `error_count`
- `created_at`

Indexes:

- `(workspace_id, started_at DESC)`
- `(workspace_id, experiment_id, iteration_id)`
- `(workspace_id, eval_case_id, started_at DESC)`
- `(workspace_id, user_id, started_at DESC)`
- `(workspace_id, environment_id, started_at DESC)`
- `(workspace_id, execution_context_id, started_at DESC)`
- `(workspace_id, agent_version_id, started_at DESC)`
- `(workspace_id, deployment_id, started_at DESC)`
- `(workspace_id, prompt_version_id, started_at DESC)`
- `(workspace_id, model_version_id, started_at DESC)`

#### `traces`

Raw trace envelope metadata.

- `id`
- `workspace_id`
- `run_id`
- `schema_version`
- `raw_pointer`
- `raw_hash`
- `created_at`

#### `spans`

Common span table for latency, hierarchy, status, and correlation.

- `id`
- `workspace_id`
- `run_id`
- `trace_id`
- `parent_span_id`
- `span_kind`
- `name`
- `status`
- `started_at`
- `ended_at`
- `duration_ms`
- `error_type`
- `error_message`
- `attributes_json`
- `created_at`

Indexes:

- `(workspace_id, run_id, started_at)`
- `(workspace_id, span_kind, started_at DESC)`
- `(workspace_id, name, started_at DESC)`
- `(workspace_id, status, started_at DESC)`
- `(workspace_id, duration_ms DESC)`

#### `artifacts`

Generic multimodal artifacts produced or consumed by runs, spans, cases, and
datasets.

- `id`
- `workspace_id`
- `owner_entity_type`
- `owner_entity_id`
- `artifact_type` (`text`, `image`, `audio`, `video`, `document`, `json`, `binary`)
- `mime_type`
- `content_hash`
- `byte_size`
- `duration_ms`
- `width`
- `height`
- `frame_count`
- `pointer`
- `metadata_json`
- `created_at`

Indexes:

- `(workspace_id, owner_entity_type, owner_entity_id)`
- `(workspace_id, artifact_type, created_at DESC)`
- `(workspace_id, content_hash)`

#### `audio_facts`

Derived facts for voice evaluations.

- `id`
- `workspace_id`
- `artifact_id`
- `run_id`
- `span_id`
- `duration_ms`
- `sample_rate`
- `channels`
- `language`
- `transcript_pointer`
- `word_timestamps_pointer`
- `speaker_turns_json`
- `speech_to_text_model`
- `created_at`

#### `voice_metrics`

- `id`
- `workspace_id`
- `run_id`
- `span_id`
- `turn_count`
- `barge_in_count`
- `interruption_count`
- `silence_ms`
- `ttft_ms`
- `time_to_first_audio_ms`
- `end_to_end_latency_ms`
- `word_error_rate`
- `task_completion_label`
- `created_at`

#### `image_facts`

Derived facts for vision evaluations.

- `id`
- `workspace_id`
- `artifact_id`
- `run_id`
- `span_id`
- `width`
- `height`
- `ocr_text_pointer`
- `detected_objects_json`
- `vision_model`
- `created_at`

#### `video_facts`

- `id`
- `workspace_id`
- `artifact_id`
- `run_id`
- `span_id`
- `duration_ms`
- `frame_count`
- `fps`
- `keyframes_pointer`
- `scene_segments_json`
- `transcript_pointer`
- `created_at`

#### `llm_calls`

- `id`
- `workspace_id`
- `run_id`
- `span_id`
- `provider`
- `model`
- `model_version_id`
- `prompt_version_id`
- `input_tokens`
- `output_tokens`
- `reasoning_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `cost_usd`
- `ttft_ms`
- `tokens_per_second`
- `temperature`
- `stop_reason`
- `input_pointer`
- `output_pointer`
- `created_at`

#### `tool_calls`

- `id`
- `workspace_id`
- `run_id`
- `span_id`
- `tool_name`
- `tool_version_id`
- `status`
- `latency_ms`
- `error_type`
- `error_message`
- `input_pointer`
- `output_pointer`
- `created_at`

Indexes:

- `(workspace_id, tool_name, created_at DESC)`
- `(workspace_id, tool_name, status, created_at DESC)`
- `(workspace_id, run_id, tool_name)`

#### `remote_graders`

Registered HTTP graders. These mirror `agent.type=http`: selfevals owns the
contract and persistence, while the customer owns grader implementation.

- `id`
- `workspace_id`
- `name`
- `version`
- `url`
- `auth_ref`
- `timeout_ms`
- `schema_version`
- `created_at`
- `updated_at`

#### `grader_invocations`

- `id`
- `workspace_id`
- `remote_grader_id`
- `run_id`
- `trace_id`
- `eval_case_id`
- `status`
- `latency_ms`
- `error_type`
- `error_message`
- `request_pointer`
- `response_pointer`
- `created_at`

#### `grader_results`

- `id`
- `workspace_id`
- `run_id`
- `trace_id`
- `eval_case_id`
- `experiment_id`
- `iteration_id`
- `grader_name`
- `label`
- `score`
- `confidence`
- `reason_pointer`
- `created_at`

Indexes:

- `(workspace_id, experiment_id, iteration_id)`
- `(workspace_id, grader_name, label, created_at DESC)`
- `(workspace_id, eval_case_id, created_at DESC)`

#### `human_feedback`

Native feedback over production or eval traces. This is separate from
`grader_results` because feedback can arrive after the run and may be edited or
reviewed by humans.

- `id`
- `workspace_id`
- `run_id`
- `trace_id`
- `span_id`
- `user_id`
- `feedback_label` (`correct`, `incorrect`, `partial`, `unclear`)
- `expected_category`
- `failure_mode_id`
- `score`
- `note`
- `source` (`human`, `thumbs`, `support_review`, `import`)
- `created_at`

Indexes:

- `(workspace_id, run_id)`
- `(workspace_id, feedback_label, created_at DESC)`
- `(workspace_id, failure_mode_id, created_at DESC)`

#### `failure_modes`

- `id`
- `workspace_id`
- `slug`
- `title`
- `definition`
- `status`
- `created_at`
- `updated_at`

#### `run_failure_modes`

Join table between runs/grades and classified failure modes.

- `workspace_id`
- `run_id`
- `grader_result_id`
- `failure_mode_id`
- `source` (`deterministic`, `judge`, `human`, `import`)
- `confidence`
- `created_at`

Indexes:

- `(workspace_id, failure_mode_id, created_at DESC)`
- `(workspace_id, run_id)`

### Cost and Usage Facts

Costs can be read from `runs` and `llm_calls`, but a separate append-only ledger
is useful when provider pricing changes or allocations need correction.

#### `usage_ledger`

- `id`
- `workspace_id`
- `run_id`
- `span_id`
- `provider`
- `model`
- `pricing_version`
- `input_tokens`
- `output_tokens`
- `reasoning_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `cost_usd`
- `created_at`

### Alerts and Regressions

#### `quality_baselines`

- `id`
- `workspace_id`
- `scope_type` (`experiment`, `dataset`, `agent_version`, `prompt_version`)
- `scope_id`
- `metric_name`
- `value`
- `window_start`
- `window_end`
- `created_at`

#### `regressions`

- `id`
- `workspace_id`
- `baseline_scope_id`
- `candidate_scope_id`
- `metric_name`
- `baseline_value`
- `candidate_value`
- `delta`
- `severity`
- `created_at`

#### `alert_rules`

- `id`
- `workspace_id`
- `name`
- `metric_name`
- `scope_json`
- `condition_json`
- `enabled`
- `created_at`

#### `alert_events`

- `id`
- `workspace_id`
- `alert_rule_id`
- `metric_name`
- `observed_value`
- `threshold_value`
- `scope_json`
- `status`
- `created_at`

## Ingestion Pipeline

### Trace Ingest

1. SDK, OTLP importer, or API receives a trace/run payload.
2. API stores the raw envelope in object storage.
3. API resolves environment, deployment, prompt, model, agent, dataset, and tool
   version identifiers from the payload or request context.
4. API inserts `traces` metadata and minimal `runs` row.
5. API enqueues `normalize_trace(trace_id)` in Redis.
6. Worker parses raw trace and upserts spans, LLM calls, tool calls, grader
   results, failure mode links, run totals, and usage ledger rows.
7. Worker publishes live events to Redis Streams.
8. Worker enqueues aggregate and alert checks.

### Remote Grader Invocation

1. Experiment spec or workspace config references `grader.type=http`.
2. Worker sends a bounded request to the registered remote grader endpoint.
3. Remote grader returns the canonical `GradeResult` shape: label, score,
   confidence, reason, failure modes, and optional breakdown/funnel tree.
4. Worker records `grader_invocations` for latency/errors and writes
   `grader_results`.
5. Failed grader calls produce grader error rows instead of crashing the whole
   run, unless the experiment policy marks the grader as required.

### Human Feedback

1. User or reviewer marks a trace/span/run as correct, incorrect, partial, or
   unclear.
2. API writes `human_feedback`.
3. Optional reviewer assigns expected category and failure mode.
4. Feedback can drive dataset candidate selection, grader calibration, and
   production quality metrics.

### Experiment Run

1. API validates request and inserts/updates `experiments`.
2. API enqueues `run_experiment(experiment_id, options)`.
3. Worker claims the job, writes state transitions to Postgres, and emits live
   run events to Redis Streams.
4. Each case/repetition produces runs/traces/spans.
5. Aggregation writes `iterations`, `grader_results`, `run_failure_modes`, and
   regression records.

### Production-to-Dataset

1. User filters production runs by quality, cost, latency, tool errors, or
   failure mode.
2. User selects runs or an auto-rule selects candidates.
3. selfevals creates `dataset_examples` linked to source traces.
4. Human or judge labels expected outputs.
5. Reviewed examples are added to versioned datasets.
6. CI or scheduled jobs run eval suites against new agent/prompt/model versions.

## Dataset and Testcase Management

Datasets are product-critical state, not static JSON files. At scale they need
versioning, lineage, deduplication, review workflows, and fast filtering.

### Design Goals

- Experiments are reproducible: every run records the exact dataset version and
  case versions used.
- Dataset heads can change without rewriting historical runs.
- Testcases can be imported in bulk without blocking API requests.
- Production traces can become candidate cases with lineage back to source runs.
- Cases can be searched by feature, failure mode, model behavior, labels,
  metadata, cost, latency, and production frequency.
- Splits are explicit and stable: train, validation, holdout, canary, shadow.
- Large inputs/expected outputs live in object storage, not Postgres rows.

### Dataset Lifecycle

```text
draft dataset
  -> import or create cases
  -> label/review cases
  -> dedupe and validate
  -> publish dataset version
  -> run experiments against dataset version
  -> monitor production coverage gaps
  -> add new candidate cases
  -> publish next dataset version
```

### Case Lifecycle

```text
candidate
  -> labeled
  -> reviewed
  -> active
  -> versioned
  -> retired
```

Important rule: editing a testcase creates a new `case_versions` row. Historical
eval runs keep their original `case_version_id`.

### Bulk Import

Imports should run as background jobs:

1. API receives CSV/JSONL/Parquet/upload pointer.
2. API creates an `import_jobs` row and enqueues `import_dataset(job_id)`.
3. Worker validates schema, streams records, computes hashes, writes payloads to
   object storage, upserts cases, and records per-row errors.
4. Worker creates a draft dataset version or updates the dataset draft head.
5. User reviews import summary before publishing.

#### `import_jobs`

- `id`
- `workspace_id`
- `dataset_id`
- `source_pointer`
- `source_format`
- `status`
- `total_rows`
- `valid_rows`
- `invalid_rows`
- `duplicate_rows`
- `error_pointer`
- `created_by`
- `created_at`
- `finished_at`

### Deduplication

Every case should carry stable hashes:

- `input_hash`: canonical hash of the input payload.
- `expected_hash`: canonical hash of expected output.
- `dedupe_hash`: canonical hash of input + task type + key metadata.

Use dedupe at two levels:

- hard duplicate: same `dedupe_hash`;
- near duplicate: embedding similarity or normalized text similarity, optional
  later if volume justifies it.

### Search and Filtering

For Postgres-first scale:

- B-tree indexes for exact filters: dataset, split, feature, status, source.
- GIN indexes on `metadata_json` for structured metadata filters.
- `tsvector` generated column for text search over case name, input preview,
  expected preview, labels, and annotations.
- Optional pgvector for semantic search later.

The UI should never load an entire dataset into memory. Dataset pages must use
cursor pagination and server-side filters.

### Splits and Sampling

Splits live in `dataset_version_cases`, not inside mutable case rows. This lets
the same case belong to different splits across dataset versions.

Supported split policies:

- fixed membership;
- stratified by feature/failure mode/task type;
- random with seed;
- production-frequency weighted;
- holdout locked after publication.

Sampling should write a manifest for reproducibility:

#### `run_case_manifests`

- `id`
- `workspace_id`
- `run_id`
- `experiment_id`
- `dataset_version_id`
- `sampling_policy_json`
- `case_count`
- `content_hash`
- `created_at`

#### `run_case_manifest_items`

- `workspace_id`
- `manifest_id`
- `case_id`
- `case_version_id`
- `execution_context_id`
- `split`
- `weight`

### Coverage Analytics

Datasets should answer:

- Which failure modes are covered?
- Which production failure modes are not covered?
- Which features/task types are overrepresented or underrepresented?
- Which high-cost production paths have no eval coverage?
- Which high-latency production paths have no eval coverage?
- Which tools appear in production but not in eval datasets?
- Which users/sessions produced cases now represented in evals?
- Which dataset cases are stale because production distribution changed?

### Dataset Quality Gates

Before publishing a dataset version:

- no invalid cases;
- no unresolved duplicate warnings above threshold;
- required metadata present;
- expected outputs validate against schema;
- holdout split cannot shrink without explicit approval;
- protected failure modes have minimum coverage;
- labels meet review policy;
- content hash is recorded.

## Synthetic Data and Dataset Cleaning

Synthetic data should be a controlled dataset operation, not an untracked prompt
that dumps cases into production datasets.

### Synthetic Generation Flow

```text
coverage gap / seed examples / failure mode
  -> generation job
  -> synthetic candidates
  -> validation graders
  -> dedupe / contamination checks
  -> human review policy
  -> accepted cases
  -> new dataset version
```

Generation modes:

- template-based cases;
- LLM-generated cases from a failure mode;
- mutations of existing cases;
- production-trace variants;
- user simulator conversations;
- voice/audio synthetic conversations;
- vision/image/document variants.

Quality checks:

- schema validity;
- expected output validity;
- no duplicate or near-duplicate cases;
- no leakage from holdout/protected datasets;
- realistic metadata and execution context;
- target failure mode is actually represented;
- generator/judge disagreement review;
- label confidence threshold;
- human review sampling.

### Dataset Cleaning Flow

```text
dataset version
  -> cleaning job
  -> proposed changes
  -> review report
  -> accepted changes
  -> new dataset version
```

Cleaning operations:

- exact deduplication;
- near-duplicate clustering;
- schema validation and repair;
- expected-output normalization;
- metadata normalization;
- PII/secret redaction;
- label conflict detection;
- stale case detection;
- split rebalance;
- failure-mode coverage balancing;
- flakiness quarantine.

Cleaning must never mutate a published dataset version in place. It produces a
new dataset version plus a diff report.

### Synthetic Data Provenance

Every synthetic case must retain:

- generation job id;
- generator type and version;
- prompt/model used for generation when applicable;
- seed;
- source case/run/failure mode;
- quality score;
- review status;
- accepted/rejected reason.

This allows teams to filter metrics by real vs synthetic data and detect when
synthetic cases stop predicting production quality.

## Annotations, Metadata, and Execution Context

These three concepts should stay distinct:

- metadata: structured facts used for filtering and grouping;
- tags: lightweight labels for organization;
- annotations: human/system notes and review decisions;
- execution context: reproducibility inputs needed to run or replay a case.

### Annotation Targets

Annotations can attach to:

- traces and spans;
- runs;
- experiments and iterations;
- eval cases and case versions;
- datasets and dataset versions;
- failure modes;
- alerts and regressions;
- prompt, agent, model, and tool versions.

### Common Annotation Workflows

- Review a production trace and mark it as a candidate eval.
- Explain why an experiment regressed.
- Add a span-level note to a bad tool call.
- Mark a failure-mode classification as accepted or rejected.
- Record human judgment on a grader disagreement.
- Document why a case was retired from a dataset.
- Link a production trace annotation to the dataset case it created.

### Metadata Guidelines

Use first-class columns for hot dimensions. Use `entity_metadata` for flexible
but still queryable attributes. Use raw payloads only for data that is not part
of filtering, grouping, or alerting.

Good metadata keys:

- `feature`
- `flow`
- `customer_tier`
- `region`
- `locale`
- `channel`
- `integration`
- `policy_version`
- `retrieval_index_version`
- `memory_snapshot_id`

Avoid putting these only in unindexed JSON if dashboards or alerts need them.

### Execution Context Guidelines

Every eval case should be runnable from:

- `input_pointer`
- `expected_json`
- `execution_context_id`
- grader config
- dataset/case version

The context should capture enough environment to make a replay meaningful, but
should not store secrets. Use `secrets_policy` and server-side secret refs
instead of embedding credentials.

## Voice and Vision Evals

Voice and vision should use the same run/case/grader/dataset architecture, but
with explicit artifact and modality facts. The important rule is that multimodal
payloads live in object storage and their derived facts are queryable.

### Voice Eval Flow

```text
audio input / live voice session
  -> artifact
  -> transcription + word timestamps + speaker turns
  -> agent run spans
  -> audio/latency metrics
  -> graders over transcript, audio facts, tool trajectory, and final answer
  -> failure modes and dataset candidates
```

Voice-specific dimensions:

- language and locale;
- speaker role and turn position;
- time to first audio;
- silence duration;
- interruption/barge-in count;
- word error rate when reference transcript exists;
- task completion after a conversation;
- policy/guardrail failures in spoken output.

Voice graders:

- transcript correctness;
- task completion;
- conversation trajectory;
- response latency;
- interruption handling;
- required/forbidden spoken content;
- tool usage during the call;
- human feedback on call quality.

### Vision Eval Flow

```text
image/video/document input
  -> artifact
  -> OCR/object/scene extraction
  -> agent run spans
  -> vision facts
  -> graders over objects, OCR, spatial claims, final answer, and tool use
  -> failure modes and dataset candidates
```

Vision-specific dimensions:

- image size and modality;
- OCR text;
- detected objects;
- scene segments/keyframes;
- spatial relationships;
- document page/region;
- visual grounding/citation;
- hallucinated object claims.

Vision graders:

- object presence/absence;
- OCR extraction correctness;
- spatial grounding;
- document field extraction;
- visual hallucination detection;
- answer-grounding against image regions;
- screenshot/UI state validation.

### Multimodal Dataset Requirements

Testcases may include multiple artifacts:

- prompt text;
- image(s);
- audio;
- video;
- documents;
- expected transcript;
- expected extracted fields;
- expected object set;
- expected tool trajectory;
- expected final answer.

Cases should store artifact pointers and metadata, not inline binary data.
Expected outputs should remain structured so deterministic graders can score
without re-running expensive multimodal judges when possible.

## Live Events

Use Redis Streams for run-level live events:

```text
selfevals:workspace:{workspace_id}:run:{run_id}:events
```

Event types:

- `run.started`
- `span.started`
- `span.finished`
- `llm_call.finished`
- `tool_call.finished`
- `grader_result.created`
- `iteration.completed`
- `run.completed`
- `run.failed`

Retention:

- Keep recent event history for short replay, for example 1 to 24 hours.
- Durable historical state remains in Postgres and object storage.

## Query Catalog

This is the product contract: the architecture must support these questions
without scanning raw JSON payloads in application memory.

### Quality

- Pass rate by workspace, experiment, iteration, dataset, eval case, grader,
  prompt version, model, provider, agent version, environment, user, and time
  window.
- Fail rate by the same dimensions.
- Pass/fail trend over time.
- Pass rate by feature, task type, dataset level, or holdout split.
- Worst eval cases by fail rate.
- Worst users/sessions by failure rate.
- Confidence-weighted judge pass rate.
- Human-label agreement with judge labels.
- Human feedback correctness rate by environment, version, feature, and user.

Example query shape:

```sql
SELECT
  prompt_version_id,
  model_version_id,
  COUNT(*) FILTER (WHERE label = 'pass')::float / COUNT(*) AS pass_rate
FROM grader_results
WHERE workspace_id = $1
  AND created_at >= $2
  AND created_at < $3
GROUP BY prompt_version_id, model_version_id;
```

### Failure Modes

- Fail rate by failure mode.
- Failure mode distribution by experiment, version, model, prompt, tool, user,
  or environment.
- New failure modes since a deploy.
- Failure modes that increased the most versus baseline.
- Failure modes with the highest cost impact.
- Failure modes with the highest latency impact.
- Examples for each failure mode.

### Tool Usage

- Tool calls per run.
- Tool calls per tool.
- Tool calls per user/session.
- Tool calls per eval case.
- Tool calls per environment and deployment.
- Tool calls by tool schema version.
- Tool success/error rate.
- Tool latency p50/p95/p99.
- Tools that correlate with failed runs.
- Tools that loop or retry excessively.
- Tool input/output examples for debugging.
- Tool cost impact when tool calls trigger downstream LLM calls.

### Cost

- Cost per run.
- Cost per eval case.
- Cost per experiment/iteration.
- Cost per workspace.
- Cost by user/session.
- Cost by model/provider.
- Cost by prompt version.
- Cost by failure mode.
- Cost by successful vs failed runs.
- Cost trend over time.
- Cost regression between versions.
- Cost per pass.

### Tokens

- Input/output/reasoning tokens per run.
- Tokens per model/provider.
- Tokens per prompt version.
- Tokens per eval case.
- Tokens per user/session.
- Cache read/write token usage.
- Token growth after deploy.
- Token outliers by run/span.

### Latency

- Run duration p50/p95/p99.
- Span duration p50/p95/p99.
- LLM TTFT p50/p95/p99.
- Tokens per second by model/provider.
- Tool latency by tool.
- Slowest spans for a run.
- Slowest eval cases.
- Latency regression by agent/prompt/model version.

### Loops and Retries

- Runs with more than N tool calls.
- Runs with repeated same-tool calls.
- Runs with repeated failed tool calls.
- Runs with back-and-forth LLM/tool loops.
- Retry count by tool/model/provider.
- Loop rate by prompt/model/agent version.
- Loop examples promoted to datasets.

Derived signals:

- `tool_call_count > threshold`
- repeated `tool_name` sequence within one run
- repeated error type in one run
- span graph depth beyond threshold
- duration/tokens much higher than baseline for same case

### Regressions

- Candidate version vs baseline pass rate.
- Candidate version vs baseline failure mode distribution.
- Candidate version vs baseline cost.
- Candidate version vs baseline token usage.
- Candidate version vs baseline latency.
- Production deploy vs previous production deploy.
- Staging/canary deploy vs current production baseline.
- Regression by protected/holdout dataset.
- Regression by feature or task type.
- New failure modes introduced by a deploy.
- CI gate result for a version.

### Production vs Evals

- Eval pass rate vs production success rate by feature/flow.
- Eval failure modes vs production failure modes.
- Eval tool error rate vs production tool error rate.
- Eval cost and latency vs production cost and latency.
- Dataset coverage of current production traffic.
- Production flows with no matching eval cases.
- Eval cases that no longer represent production traffic.
- Version where eval improved but production regressed.
- Correlation between eval score and production feedback correctness.

### Production-to-Evals

- Production failures eligible for dataset inclusion.
- Expensive production runs that need regression coverage.
- Slow production runs that need regression coverage.
- Tool-error runs that need regression coverage.
- High-confidence judge failures not yet in a dataset.
- High-impact user sessions not represented in datasets.
- Dataset coverage by failure mode, feature, and production frequency.

### Datasets and Testcases

- List datasets by workspace, status, source, owner, and update time.
- List dataset versions with case counts and content hashes.
- Search cases by text, metadata, feature, level, task type, status, source,
  split, label status, failure mode coverage, and production lineage.
- Find duplicate or near-duplicate cases.
- Find cases missing labels, expected outputs, metadata, or review.
- Find cases by source trace/run/span.
- Find all experiments that used a dataset version.
- Find all runs that evaluated a case version.
- Compare two dataset versions: added, removed, changed, retired cases.
- Coverage by feature, failure mode, tool, task type, and production frequency.
- Coverage gaps between production traffic and eval datasets.
- Stale cases whose source behavior no longer appears in production.
- Holdout split changes between versions.
- Label disagreement by human vs judge.
- Cases with high flake rate across repeated runs.
- Synthetic vs real case performance.
- Synthetic case acceptance/rejection rate by generator.
- Dataset cleaning diff summaries.
- Datasets with PII/redaction warnings.

### Annotations and Metadata

- List annotations for any trace, run, span, experiment, case, dataset, alert,
  regression, or failure mode.
- Find open high-severity annotations across a workspace.
- Find traces with unresolved review notes.
- Find cases created from annotated production traces.
- Filter runs/cases/datasets by metadata key/value.
- Compare metrics grouped by metadata dimensions such as feature, region,
  channel, customer tier, or integration.
- Find stale metadata values no longer present in production.
- Find execution contexts used by a run, case, dataset version, or manifest.

### Voice and Vision

- Voice pass rate by language, locale, prompt, model, and environment.
- Voice latency p50/p95 by model and environment.
- Time to first audio by version.
- Barge-in/interruption handling failure rate.
- Word error rate by transcription model.
- Vision pass rate by image/document type.
- OCR failure rate.
- Object detection/grounding failure rate.
- Visual hallucination rate.
- Multimodal cost and latency by artifact type.

### Security and Operations

- Ingest latency and normalizer lag.
- Queue depth by job type.
- Worker failure rate.
- Dead-letter jobs.
- API key usage by scope.
- Payload access audit trail.
- Retention/redaction actions.
- Quota usage by workspace.
- Backfill progress and errors.
- Export job status.

### Alerts

- Pass rate drops below threshold.
- Fail rate for a failure mode rises above threshold.
- New failure mode appears after deploy.
- Cost per run rises above threshold.
- Tokens per run rises above threshold.
- Latency p95 rises above threshold.
- Tool error rate rises above threshold.
- Judge/human disagreement rises above threshold.
- Holdout regression exceeds allowed delta.
- Production quality diverges from eval quality above allowed delta.
- PII/secret redaction detects critical data.
- Queue or normalizer lag exceeds SLO.
- Workspace quota approaches limit.
- Synthetic dataset quality drops below threshold.

## Materialized Aggregates

Start with Postgres materialized views or rollup tables. Rebuild from facts when
schema changes.

Recommended rollups:

- hourly run metrics by workspace/environment/version
- hourly grader metrics by workspace/experiment/dataset/grader
- hourly failure-mode metrics
- hourly tool metrics
- hourly model cost/token metrics
- experiment iteration summaries
- dataset coverage summaries
- dataset version summaries
- case flakiness summaries

Example table:

#### `metric_rollups_hourly`

- `workspace_id`
- `bucket_hour`
- `metric_name`
- `dimension_type`
- `dimension_id`
- `count`
- `sum_value`
- `avg_value`
- `p50_value`
- `p95_value`
- `p99_value`
- `created_at`

## API Surface

Read APIs should expose both low-level facts and prepared product queries.

### Metrics

- `GET /api/workspaces/{workspace}/metrics/pass-rate`
- `GET /api/workspaces/{workspace}/metrics/failure-modes`
- `GET /api/workspaces/{workspace}/metrics/tools`
- `GET /api/workspaces/{workspace}/metrics/cost`
- `GET /api/workspaces/{workspace}/metrics/tokens`
- `GET /api/workspaces/{workspace}/metrics/latency`
- `GET /api/workspaces/{workspace}/metrics/regressions`

Common query params:

- `from`
- `to`
- `environment`
- `environment_id`
- `experiment_id`
- `dataset_id`
- `eval_case_id`
- `deployment_id`
- `prompt_version_id`
- `model`
- `provider`
- `agent_version_id`
- `tool_version_id`
- `tool_name`
- `failure_mode_id`
- `user_id`
- `group_by`

### Dashboards

- `GET /api/workspaces/{workspace}/dashboards/production-quality`
- `GET /api/workspaces/{workspace}/dashboards/model-quality`
- `GET /api/workspaces/{workspace}/dashboards/prompt-quality`
- `GET /api/workspaces/{workspace}/dashboards/tool-health`
- `GET /api/workspaces/{workspace}/dashboards/regressions`
- `GET /api/workspaces/{workspace}/dashboards/failure-modes`
- `GET /api/workspaces/{workspace}/dashboards/prod-vs-evals`

### Facts

- `GET /api/workspaces/{workspace}/runs`
- `GET /api/workspaces/{workspace}/runs/{run}`
- `GET /api/workspaces/{workspace}/runs/{run}/spans`
- `GET /api/workspaces/{workspace}/tool-calls`
- `GET /api/workspaces/{workspace}/llm-calls`
- `GET /api/workspaces/{workspace}/grader-results`
- `GET /api/workspaces/{workspace}/failure-modes/{failure_mode}/examples`
- `GET /api/workspaces/{workspace}/human-feedback`
- `POST /api/workspaces/{workspace}/runs/{run}/feedback`
- `POST /api/workspaces/{workspace}/traces/{trace}/feedback`
- `GET /api/workspaces/{workspace}/annotations`
- `POST /api/workspaces/{workspace}/annotations`
- `PATCH /api/workspaces/{workspace}/annotations/{annotation}`
- `GET /api/workspaces/{workspace}/metadata`
- `PUT /api/workspaces/{workspace}/metadata/{entity_type}/{entity_id}`
- `GET /api/workspaces/{workspace}/execution-contexts`
- `POST /api/workspaces/{workspace}/execution-contexts`
- `GET /api/workspaces/{workspace}/execution-contexts/{context}`
- `GET /api/workspaces/{workspace}/artifacts`
- `GET /api/workspaces/{workspace}/artifacts/{artifact}`

All list endpoints must be cursor-paginated.

### Environments and Versions

- `GET /api/workspaces/{workspace}/environments`
- `POST /api/workspaces/{workspace}/environments`
- `GET /api/workspaces/{workspace}/deployments`
- `POST /api/workspaces/{workspace}/deployments`
- `GET /api/workspaces/{workspace}/agent-versions`
- `GET /api/workspaces/{workspace}/prompt-versions`
- `GET /api/workspaces/{workspace}/tool-versions`

### Remote Graders

- `GET /api/workspaces/{workspace}/remote-graders`
- `POST /api/workspaces/{workspace}/remote-graders`
- `GET /api/workspaces/{workspace}/remote-graders/{grader}`
- `POST /api/workspaces/{workspace}/remote-graders/{grader}/test`
- `GET /api/workspaces/{workspace}/grader-invocations`

### Datasets

- `GET /api/workspaces/{workspace}/datasets`
- `POST /api/workspaces/{workspace}/datasets`
- `GET /api/workspaces/{workspace}/datasets/{dataset}`
- `GET /api/workspaces/{workspace}/datasets/{dataset}/versions`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/versions`
- `GET /api/workspaces/{workspace}/datasets/{dataset}/cases`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/cases`
- `GET /api/workspaces/{workspace}/cases/{case}`
- `GET /api/workspaces/{workspace}/cases/{case}/versions`
- `POST /api/workspaces/{workspace}/cases/{case}/versions`
- `GET /api/workspaces/{workspace}/datasets/{dataset}/diff?from_version=...&to_version=...`
- `GET /api/workspaces/{workspace}/datasets/{dataset}/coverage`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/examples/from-runs`
- `POST /api/workspaces/{workspace}/dataset-examples/{example}/label`
- `POST /api/workspaces/{workspace}/dataset-examples/{example}/promote`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/imports`
- `GET /api/workspaces/{workspace}/dataset-imports/{import_job}`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/synthetic-generations`
- `GET /api/workspaces/{workspace}/synthetic-generations/{job}`
- `POST /api/workspaces/{workspace}/datasets/{dataset}/cleaning-jobs`
- `GET /api/workspaces/{workspace}/dataset-cleaning-jobs/{job}`

### Voice and Vision

- `GET /api/workspaces/{workspace}/metrics/voice`
- `GET /api/workspaces/{workspace}/metrics/vision`
- `GET /api/workspaces/{workspace}/audio-facts`
- `GET /api/workspaces/{workspace}/image-facts`
- `GET /api/workspaces/{workspace}/video-facts`

### Operations and Security

- `GET /api/workspaces/{workspace}/jobs`
- `POST /api/workspaces/{workspace}/jobs/{job}/cancel`
- `GET /api/workspaces/{workspace}/exports`
- `POST /api/workspaces/{workspace}/exports`
- `GET /api/workspaces/{workspace}/audit-log`
- `GET /api/workspaces/{workspace}/api-keys`
- `POST /api/workspaces/{workspace}/api-keys`
- `GET /api/workspaces/{workspace}/retention-policies`
- `POST /api/workspaces/{workspace}/retention-policies`
- `GET /api/workspaces/{workspace}/redaction-rules`
- `POST /api/workspaces/{workspace}/redaction-rules`
- `GET /api/workspaces/{workspace}/quotas`
- `GET /api/workspaces/{workspace}/backfills`
- `POST /api/workspaces/{workspace}/backfills`

### Alerts

- `GET /api/workspaces/{workspace}/alert-rules`
- `POST /api/workspaces/{workspace}/alert-rules`
- `GET /api/workspaces/{workspace}/alert-events`

## Migration Plan

### Phase 1: Introduce Postgres Storage Adapter

- Add `PostgresStorage` behind the existing storage interface where possible.
- Keep SQLite as local default.
- Add relational tables for runs, traces, spans, LLM calls, tool calls, grader
  results, and failure modes.
- Add migrations and integration tests with Docker Postgres.
- Current implementation note: local scale testing uses runtime environment
  variables (`SELFEVALS_STORAGE_URL`, `SELFEVALS_REDIS_URL`) rather than
  separate test-only URLs.

### Phase 2: Normalize Current Trace Writes

- Keep raw trace persistence.
- Add normalizer that writes facts from existing trace schema.
- Make trace lookup use `runs`, `traces`, and `spans` tables.
- Replace JSON scans for `run_id`, `thread_id`, `experiment_id`, and
  `iteration` with indexed queries.
- Current implementation note: Postgres keeps the canonical raw `Trace`
  payload and additionally projects spans, LLM calls, tool calls, and grader
  results into queryable fact tables for analytics.

### Phase 3: Move Runs to Workers

- Replace API daemon threads with Redis-backed jobs when `SELFEVALS_REDIS_URL`
  is configured.
- Add `RunJob` state and `selfevals worker runs`.
- Persist job state in storage/Postgres.
- Add retry, cancellation, leases, and dead-lettering.

### Phase 4: Move Live Events to Redis Streams

- Replace in-process broker with Redis Streams adapter.
- Preserve current `SpanBroker` contract at the API boundary.
- Add short replay for late subscribers.

### Phase 5: Product Metrics and Alerts

- Add metric query endpoints.
- Add rollup jobs.
- Add regression comparison API.
- Add alert rules and alert event generation.

### Phase 6: Production-to-Dataset Loop

- Add dataset candidate creation from run filters.
- Add labeling/review lifecycle.
- Add dataset coverage views.
- Add CI regression gates against versioned datasets.

### Phase 7: Security and Operations

- Add RBAC, API keys, audit log, rate limits, and quotas.
- Add retention and redaction policies.
- Add idempotency, job lifecycle, exports, and backfill jobs.
- Add internal SLO dashboards for ingest, normalizer, queue, and worker health.

### Phase 8: Multimodal Evals

- Add artifact model for image, audio, video, document, and binary payloads.
- Add audio, voice, image, and video fact extraction.
- Add voice and vision metric APIs.
- Add multimodal graders and dataset support.

### Phase 9: Synthetic Data and Dataset Cleaning

- Add synthetic generation jobs with provenance.
- Add dataset cleaning jobs and diff reports.
- Add synthetic vs real performance analytics.
- Add contamination, dedupe, validation, and review gates.

## Non-Goals for the First Scale Pass

- Replacing Postgres with ClickHouse before there is measured need.
- Building a full BI query builder.
- Treating Redis as durable historical storage.
- Making object storage queryable.
- Removing local SQLite support.

## Readiness Bar

selfevals is ready for millions of records when:

- no hot endpoint scans raw JSON payloads in application memory;
- all list endpoints are cursor-paginated;
- runs execute in external workers, not API daemon threads;
- live events survive multiple API instances through Redis Streams;
- trace, run, span, tool, LLM, grader, cost, and failure-mode facts are
  indexed by workspace and time;
- pass rate, failure modes, tool errors, cost, tokens, latency, loops,
  regressions, dataset coverage, and alerts are queryable through stable APIs;
- production traces can be promoted into versioned eval datasets;
- datasets and cases are versioned, annotated, searchable, deduped, and
  reproducible through manifests;
- auth, RBAC, audit log, redaction, retention, quotas, idempotency, jobs,
  exports, and backfills exist;
- grader, prompt, agent, tool, schema, dataset, deploy, and environment versions
  are recorded on every run/result where relevant;
- voice and vision artifacts are stored outside Postgres and normalized into
  queryable facts;
- synthetic data and dataset cleaning produce new versioned datasets with
  provenance and review reports.
