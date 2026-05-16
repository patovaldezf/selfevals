"""All closed enums used across bootstrap schemas.

Adding a value here is a schema migration: it changes what existing data
can legally contain. Free-form tags belong in `metadata.tags` instead.

References cite the canonical (`docs/spec/evals_framework.md`) and
operational (`docs/spec/operational_spec_v0.1.md`) specs.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Workspace membership roles. Canon §16."""

    VIEWER = "viewer"
    EVALUATOR = "evaluator"
    EXPERIMENTER = "experimenter"
    MAINTAINER = "maintainer"
    ADMIN = "admin"
    AUDITOR = "auditor"


class Level(StrEnum):
    """Evaluation level (granularity of what is being judged). Canon §4.1."""

    SINGLE_STEP = "single_step"
    MULTI_STEP = "multi_step"
    FINAL_RESPONSE = "final_response"
    STEP_LEVEL = "step_level"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    MEMORY_CONTEXT = "memory_context"
    WORKFLOW = "workflow"
    TRAJECTORY = "trajectory"
    CONVERSATION = "conversation"
    SYSTEM = "system"
    AGENT = "agent"


class DatasetSource(StrEnum):
    """Where an eval case came from. Canon §4.3."""

    HANDCRAFTED = "handcrafted"
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    FAILURE = "failure"
    SYNTHETIC = "synthetic"
    ADVERSARIAL = "adversarial"
    HUMAN_LABELED = "human_labeled"
    EXTERNAL_BENCHMARK = "external_benchmark"
    SIMULATION = "simulation"
    CUSTOM = "custom"


class GroundTruthMethod(StrEnum):
    """How correctness is established. Canon §4.4."""

    EXACT_MATCH = "exact_match"
    SCHEMA_VALIDATION = "schema_validation"
    DETERMINISTIC_ASSERTION = "deterministic_assertion"
    REFERENCE_ANSWER = "reference_answer"
    RUBRIC = "rubric"
    PAIRWISE_PREFERENCE = "pairwise_preference"
    OUTCOME_BASED = "outcome_based"
    HUMAN_JUDGMENT = "human_judgment"
    LLM_JUDGE = "llm_judge"
    HYBRID = "hybrid"


class DatasetType(StrEnum):
    """Role a case/dataset plays in evaluation. Canon §4.6.

    A case has exactly one `DatasetType`. Datasets are also typed.
    """

    SMOKE = "smoke"
    GOLDEN = "golden"
    REGRESSION = "regression"
    CAPABILITY = "capability"
    PRODUCTION_SAMPLE = "production_sample"
    ADVERSARIAL_SAFETY = "adversarial_safety"
    CALIBRATION = "calibration"
    INCIDENT_QUEUE = "incident_queue"


class SandboxMode(StrEnum):
    """Sandbox isolation for a run. Operational §F.1."""

    MOCK = "mock"
    DRY_RUN = "dry_run"
    LIVE_SANDBOXED = "live_sandboxed"
    LIVE_CANARY = "live_canary"


class RuntimeLocation(StrEnum):
    """Where a case is being evaluated. Canon §4.5."""

    OFFLINE = "offline"
    REPLAY = "replay"
    SIMULATION = "simulation"
    SHADOW = "shadow"
    CANARY = "canary"
    ONLINE = "online"
    HUMAN_REVIEW = "human_review"


class Mode(StrEnum):
    """Execution mode for an Experiment. Operational §A.1.

    `agent_loop` writes code in the user repo; `handoff` is a cloud-only
    parameter sweep against pinned artifacts.
    """

    AGENT_LOOP = "agent_loop"
    HANDOFF = "handoff"


class ProposerStrategy(StrEnum):
    """How proposals are generated. Operational §A.4.2.

    MVP implements MANUAL, GRID, RANDOM. The rest are reserved for post-MVP.
    """

    MANUAL = "manual"
    GRID = "grid"
    RANDOM = "random"
    BAYESIAN = "bayesian"
    BANDIT = "bandit"
    EVOLUTIONARY = "evolutionary"
    LLM_PROPOSER = "llm_proposer"


class ExperimentState(StrEnum):
    """Experiment lifecycle states. Operational §A.2."""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    SUPERSEDED = "superseded"


class SpanKind(StrEnum):
    """Kinds of spans in a Trace. Operational §B.3."""

    AGENT_TURN = "agent_turn"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    DECISION = "decision"
    HANDOFF = "handoff"
    HUMAN_INTERVENTION = "human_intervention"
    GUARDRAIL_CHECK = "guardrail_check"
    ERROR = "error"
    CUSTOM = "custom"


class StopReason(StrEnum):
    """Why an LLM call stopped generating. Operational §B.2."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    PAUSE_TURN = "pause_turn"
    REFUSAL = "refusal"
    ERROR = "error"


class TraceState(StrEnum):
    """Terminal state of a Trace. Operational §B.2."""

    COMPLETED = "completed"
    ERRORED = "errored"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


class ToolCallStatus(StrEnum):
    """Outcome of a tool call. Operational §B.2."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class PIIStatus(StrEnum):
    """PII handling status on a case. Canon §15."""

    RAW = "raw"
    DETECTED = "detected"
    SCRUBBED = "scrubbed"
    SYNTHETIC = "synthetic"
    APPROVED_RAW = "approved_raw"


class FeatureKind(StrEnum):
    """What kind of feature is registered. Canon §3.

    `product_feature` is user-facing; `agent_capability` is an internal
    skill; `system_capability` is infra-level; `safety_capability` enforces
    policy.
    """

    PRODUCT_FEATURE = "product_feature"
    AGENT_CAPABILITY = "agent_capability"
    SYSTEM_CAPABILITY = "system_capability"
    SAFETY_CAPABILITY = "safety_capability"


class FeatureStatus(StrEnum):
    """Lifecycle of a registered feature."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class AgentType(StrEnum):
    """Shape of an Agent artifact."""

    SYSTEM_PROMPT = "system_prompt"
    GRAPH = "graph"
    HANDOFF = "handoff"


class AgentStatus(StrEnum):
    """Lifecycle of an Agent."""

    DRAFT = "draft"
    ACTIVE = "active"
    TESTING = "testing"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"


class FleetStatus(StrEnum):
    """Lifecycle of an AgentFleet."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DatasetStatus(StrEnum):
    """Lifecycle of a Dataset manifest."""

    DRAFT = "draft"
    FROZEN = "frozen"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ToolStatus(StrEnum):
    """Lifecycle of a Tool artifact."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class GraderCardState(StrEnum):
    """Calibration lifecycle of a GraderCard. Operational §D.2."""

    CALIBRATING = "calibrating"
    CALIBRATED = "calibrated"
    IN_USE = "in_use"
    DRIFTING = "drifting"
    RECALIBRATING = "recalibrating"
    RETIRED = "retired"


class DecisionOutcome(StrEnum):
    """Possible outcomes recorded for an iteration. Operational §G.1."""

    KEEP_CANDIDATE = "keep_candidate"
    REJECT = "reject"
    REVERT = "revert"
    FEATURE_FLAG = "feature_flag"
    INVESTIGATE = "investigate"
    REQUIRE_TRADEOFF_REVIEW = "require_tradeoff_review"
    SPAWN_SUBEXPERIMENT = "spawn_subexperiment"


class IterationState(StrEnum):
    """Terminal state of an IterationRecord."""

    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class Modality(StrEnum):
    """Input/output modality supported by a case or agent. Canon §20."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VOICE = "voice"
    BROWSER_USE = "browser_use"
    SENSOR = "sensor"
