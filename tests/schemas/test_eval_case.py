from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    MessageRole,
    PIIStatus,
    RuntimeLocation,
)
from selfevals.schemas.eval_case import (
    CaseMetadata,
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _taxonomy(
    *,
    source: DatasetSource = DatasetSource.HANDCRAFTED,
    dataset_type: DatasetType = DatasetType.CAPABILITY,
    level: Level = Level.FINAL_RESPONSE,
) -> CaseTaxonomy:
    return CaseTaxonomy(
        level=level,
        feature=FeatureTag(primary="commerce.product_resolution"),
        source=SourceInfo(type=source),
        ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
        runtime=RuntimeLocation.OFFLINE,
        dataset_type=dataset_type,
    )


def _case(**overrides: object) -> EvalCase:
    base: dict[str, Any] = {
        "id": EvalCase.make_id(),
        "workspace_id": WS,
        "name": "resolves manzanas to product",
        "task_type": "product_resolution",
        "input": {"messages": [{"role": "user", "content": "necesito manzanas rojas"}]},
        "taxonomy": _taxonomy(),
    }
    base.update(overrides)
    return EvalCase(**base)


def test_case_happy() -> None:
    c = _case()
    assert c.metadata.pii_status == PIIStatus.RAW
    assert c.holdout is False
    assert c.modalities[0] == "text"


def test_production_raw_pii_requires_approval() -> None:
    with pytest.raises(ValidationError):
        _case(taxonomy=_taxonomy(source=DatasetSource.PRODUCTION))


def test_production_raw_pii_with_approval_ok() -> None:
    c = _case(
        taxonomy=_taxonomy(source=DatasetSource.PRODUCTION),
        metadata=CaseMetadata(
            pii_status=PIIStatus.RAW,
            approved_raw_by="patricio",
            approved_raw_at=datetime(2026, 5, 16, tzinfo=UTC),
        ),
    )
    assert c.metadata.pii_status == PIIStatus.RAW


def test_production_scrubbed_pii_ok_without_approval() -> None:
    c = _case(
        taxonomy=_taxonomy(source=DatasetSource.PRODUCTION),
        metadata=CaseMetadata(pii_status=PIIStatus.SCRUBBED),
    )
    assert c.metadata.pii_status == PIIStatus.SCRUBBED


def test_primary_cannot_be_in_secondary() -> None:
    with pytest.raises(ValidationError):
        FeatureTag(primary="x.y", secondary=["x.y"])


def test_required_and_forbidden_tools_must_be_disjoint() -> None:
    with pytest.raises(ValidationError):
        _case(
            expected=Expected(
                required_tools=["search"],
                forbidden_tools=["search"],
            )
        )


def test_dataset_type_singular() -> None:
    # Schema-level: assignment must be a single DatasetType value.
    with pytest.raises(ValidationError):
        _case(taxonomy={**_taxonomy().model_dump(), "dataset_type": ["regression", "capability"]})


def test_failure_weights_non_negative() -> None:
    with pytest.raises(ValidationError):
        _case(failure_weights={"wrong_product": -3})


def test_modalities_must_be_unique_and_nonempty() -> None:
    with pytest.raises(ValidationError):
        _case(modalities=["text", "text"])
    with pytest.raises(ValidationError):
        _case(modalities=[])


def test_graders_unique() -> None:
    with pytest.raises(ValidationError):
        _case(graders=["g_a", "g_a"])


def test_ground_truth_methods_dedup() -> None:
    with pytest.raises(ValidationError):
        GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH, GroundTruthMethod.EXACT_MATCH])


# --- Multi-turn conversation input (Feature A) -----------------------------


def test_conversation_single_turn_valid() -> None:
    c = _case(input={"messages": [{"role": "user", "content": "hi"}]})
    assert c.is_conversation() is True


def test_conversation_multi_turn_valid() -> None:
    c = _case(
        input={
            "messages": [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "bye"},
            ]
        }
    )
    assert c.is_conversation() is True
    assert len(c.conversation().messages) == 4


def test_conversation_with_task_hint_sibling_survives() -> None:
    c = _case(
        input={
            "messages": [{"role": "user", "content": "hi"}],
            "task_hint": "resolve product",
        }
    )
    assert c.input["task_hint"] == "resolve product"
    # The sibling key survives onto the typed view too (extra="allow").
    assert c.conversation().task_hint == "resolve product"  # type: ignore[attr-defined]


def test_opaque_input_empty_dict_passes_through() -> None:
    c = _case(input={})
    assert c.input == {}
    assert c.is_conversation() is False


def test_opaque_input_no_messages_passes_through() -> None:
    c = _case(input={"prompt": "x"})
    assert c.input == {"prompt": "x"}
    assert c.is_conversation() is False


def test_conversation_malformed_role_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(input={"messages": [{"role": "robot", "content": "hi"}]})


def test_conversation_empty_messages_list_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(input={"messages": []})


def test_conversation_messages_not_a_list_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(input={"messages": "hi"})


def test_conversation_message_missing_content_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(input={"messages": [{"role": "user"}]})


def test_conversation_stray_key_in_message_rejected() -> None:
    # Proves Message inherits extra="forbid": a misspelled key is rejected.
    with pytest.raises(ValidationError):
        _case(input={"messages": [{"role": "user", "contnet": "hi"}]})


def test_conversation_content_as_str_accepted() -> None:
    c = _case(input={"messages": [{"role": "user", "content": "hello"}]})
    assert c.conversation().messages[0].content == "hello"


def test_conversation_content_as_block_list_accepted() -> None:
    c = _case(
        input={
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ]
        }
    )
    block_list = c.conversation().messages[0].content
    assert isinstance(block_list, list)
    assert block_list[0].text == "hi"


def test_conversation_multimodal_block_accepted() -> None:
    c = _case(
        input={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "url": "https://example.com/a.png"},
                        {"type": "text", "text": "describe this"},
                    ],
                }
            ]
        }
    )
    content = c.conversation().messages[0].content
    assert isinstance(content, list)
    # Provider-specific key on the image block survives (extra="allow").
    assert content[0].url == "https://example.com/a.png"  # type: ignore[attr-defined]
    assert content[1].text == "describe this"


def test_conversation_empty_content_list_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(input={"messages": [{"role": "user", "content": []}]})


def test_conversation_all_assistant_rejected() -> None:
    with pytest.raises(ValidationError):
        _case(
            input={
                "messages": [
                    {"role": "assistant", "content": "a"},
                    {"role": "assistant", "content": "b"},
                ]
            }
        )


def test_conversation_accessor_returns_typed_messages() -> None:
    c = _case(
        input={
            "messages": [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hi"},
            ]
        }
    )
    conv = c.conversation()
    assert conv.messages[0].role == MessageRole.SYSTEM
    assert conv.messages[0].content == "be terse"
    assert conv.messages[1].role == MessageRole.USER


def test_conversation_accessor_raises_on_opaque_input() -> None:
    c = _case(input={"prompt": "x"})
    assert c.is_conversation() is False
    with pytest.raises(ValueError, match="no `messages` key"):
        c.conversation()


def test_conversation_round_trip_preserves_input() -> None:
    original = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        ],
        "task_hint": "keep me",
    }
    c = _case(input=original)
    dumped = c.model_dump(mode="json")
    restored = EvalCase.model_validate(dumped)
    assert restored.input == original


def test_conversation_input_is_plain_json_no_pydantic_types() -> None:
    c = _case(
        input={
            "messages": [
                {"role": "user", "content": [{"type": "image", "url": "u"}]},
            ]
        }
    )
    # No Pydantic types leaked onto the field — json.dumps works directly.
    serialized = json.dumps(c.input)
    assert "messages" in serialized
