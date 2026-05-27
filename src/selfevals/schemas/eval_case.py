"""EvalCase: one atomic unit of evaluation.

A case carries:
- `input`: the messages/context fed to the agent
- `expected`: what a passing response must satisfy
- `taxonomy`: how the case is classified (level, feature, source, dataset_type, ...)
- `graders`: which graders score it
- `failure_weights`: per-failure-mode importance for weighted scoring
- `metadata`: pii_status, owner, tags

Key contracts enforced here:
1. `taxonomy.dataset_type` is a single enum value, not a list. Canon §4.6.
2. `metadata.pii_status` is required (default RAW); production+RAW requires
   explicit `metadata.approved_raw_by` + `approved_raw_at`. Canon §15.
3. `taxonomy.feature.primary` is required; `secondary` may be empty.
4. `holdout=True` cases are intended to be immutable — see Dataset for the
   regression-class immutability contract; per-case mutation is left to the
   storage layer in PR 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    MessageRole,
    Modality,
    PIIStatus,
    RuntimeLocation,
)
from selfevals.schemas.registry import RiskProfile


class FeatureTag(SelfEvalsModel):
    """Per-case feature classification."""

    primary: NonEmptyStr
    secondary: list[NonEmptyStr] = Field(default_factory=list)

    @field_validator("secondary")
    @classmethod
    def _dedup(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("secondary features must be unique")
        return value

    @model_validator(mode="after")
    def _primary_not_in_secondary(self) -> FeatureTag:
        if self.primary in self.secondary:
            raise ValueError("primary feature must not also appear in secondary")
        return self


class SourceInfo(SelfEvalsModel):
    """Where this case came from. Canon §4.3."""

    type: DatasetSource
    failure_type: str | None = None
    failure_id: str | None = None
    parent_case_id: str | None = None


class GroundTruthSpec(SelfEvalsModel):
    """Which ground-truth methods are valid for this case. Canon §4.4."""

    methods: list[GroundTruthMethod] = Field(min_length=1)

    @field_validator("methods")
    @classmethod
    def _dedup(cls, value: list[GroundTruthMethod]) -> list[GroundTruthMethod]:
        if len(set(value)) != len(value):
            raise ValueError("ground-truth methods must be unique")
        return value


class CaseTaxonomy(SelfEvalsModel):
    level: Level
    feature: FeatureTag
    source: SourceInfo
    ground_truth: GroundTruthSpec
    runtime: RuntimeLocation = RuntimeLocation.OFFLINE
    dataset_type: DatasetType
    """Singular — a case belongs to exactly one dataset type. Canon §4.6."""

    risk: RiskProfile | None = None


class Expected(SelfEvalsModel):
    """Declarative expectations consumed by deterministic graders."""

    outcome: str | None = None
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    required_citations: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)
    structured_output: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class Blocking(SelfEvalsModel):
    """Whether failures on this case block lifecycle events."""

    merge: bool = False
    release: bool = False


class CaseMetadata(SelfEvalsModel):
    owner: NonEmptyStr | None = None
    tags: list[str] = Field(default_factory=list)
    pii_status: PIIStatus = PIIStatus.RAW
    approved_raw_by: str | None = None
    approved_raw_at: datetime | None = None
    notes: str | None = None


class ContentBlock(SelfEvalsModel):
    """One block of message content. Text blocks use {"text": ...}, matching
    the de-facto chunk shape agents already read. Non-text blocks (image/audio)
    carry provider-specific keys, so extra keys are allowed."""

    model_config = ConfigDict(extra="allow")
    type: Modality = Modality.TEXT
    text: str | None = None


class Message(SelfEvalsModel):
    """A single conversation turn. Content is a plain string or a list of
    content blocks (multimodal)."""

    role: MessageRole
    content: str | list[ContentBlock]
    name: str | None = None

    @field_validator("content")
    @classmethod
    def _content_non_empty_list(cls, value: str | list[ContentBlock]) -> str | list[ContentBlock]:
        if isinstance(value, list) and len(value) == 0:
            raise ValueError("message content list must be non-empty")
        return value


class ConversationInput(SelfEvalsModel):
    """Typed view of a multi-turn EvalCase.input. Constructed on demand by
    EvalCase.conversation(); never stored on the field (input stays a plain
    JSON dict so adapters keep receiving it opaquely)."""

    model_config = ConfigDict(extra="allow")
    messages: list[Message] = Field(min_length=1)

    @field_validator("messages")
    @classmethod
    def _at_least_one_non_assistant(cls, value: list[Message]) -> list[Message]:
        if all(m.role == MessageRole.ASSISTANT for m in value):
            raise ValueError(
                "conversation must contain at least one user/system/tool message"
            )
        return value


class EvalCase(BaseEntity):
    _id_prefix: ClassVar[str] = "ec"

    name: NonEmptyStr
    task_type: NonEmptyStr
    modalities: list[Modality] = Field(default_factory=lambda: [Modality.TEXT], min_length=1)
    input: dict[str, Any]
    """The payload fed to the agent. Canon §20. When it carries a `messages`
    key it is validated as a typed multi-turn conversation (see
    ConversationInput); otherwise it is an opaque payload passed to the adapter
    verbatim. The field always stays a plain JSON dict so adapters receive it
    unchanged."""

    context: dict[str, Any] | None = None
    expected: Expected = Field(default_factory=Expected)
    taxonomy: CaseTaxonomy
    graders: list[NonEmptyStr] = Field(default_factory=list)
    failure_weights: dict[str, int] = Field(default_factory=dict)
    metadata: CaseMetadata = Field(default_factory=CaseMetadata)
    blocking: Blocking = Field(default_factory=Blocking)
    holdout: bool = False
    """If True, this case is reserved for held-out evaluation and is not
    visible to proposers."""

    content_hash: str | None = None

    @field_validator("modalities", "graders")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @field_validator("input")
    @classmethod
    def _validate_conversation_shape(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Opaque inputs (no `messages` key) pass through untouched. When
        # `messages` is present it must be a valid multi-turn shape; validate by
        # round-tripping through ConversationInput, then return the ORIGINAL dict
        # (the field stays a plain JSON dict so adapters are unaffected).
        if "messages" not in value:
            return value
        try:
            ConversationInput.model_validate(value)
        except ValidationError as exc:
            raise ValueError(f"invalid conversation input: {exc}") from exc
        return value

    def is_conversation(self) -> bool:
        return "messages" in self.input

    def conversation(self) -> ConversationInput:
        if "messages" not in self.input:
            raise ValueError("EvalCase.input has no `messages` key; not a conversation")
        return ConversationInput.model_validate(self.input)

    @field_validator("failure_weights")
    @classmethod
    def _weights_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for k, w in value.items():
            if w < 0:
                raise ValueError(f"failure weight for {k!r} must be >= 0")
        return value

    @model_validator(mode="after")
    def _pii_contract(self) -> EvalCase:
        # Canon §15: production source + raw PII requires explicit approval.
        is_prod_or_staging = self.taxonomy.source.type in (
            DatasetSource.PRODUCTION,
            DatasetSource.STAGING,
        )
        if (
            is_prod_or_staging
            and self.metadata.pii_status == PIIStatus.RAW
            and not (self.metadata.approved_raw_by and self.metadata.approved_raw_at)
        ):
            raise ValueError(
                "production/staging source with pii_status=raw requires "
                "metadata.approved_raw_by AND metadata.approved_raw_at"
            )
        return self

    @model_validator(mode="after")
    def _required_forbidden_tools_disjoint(self) -> EvalCase:
        overlap = set(self.expected.required_tools) & set(self.expected.forbidden_tools)
        if overlap:
            raise ValueError(
                f"expected.required_tools and forbidden_tools must be disjoint; "
                f"overlap: {sorted(overlap)}"
            )
        return self
