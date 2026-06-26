from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfevals.schemas.pairwise_verdict import PairRef, PairwiseVerdict

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _verdict(**overrides: object) -> PairwiseVerdict:
    defaults: dict[str, object] = {
        "id": PairwiseVerdict.make_id(),
        "workspace_id": WS,
        "a_ref": PairRef(kind="agent_output", trace_id="tr_a", case_id="ec_1"),
        "b_ref": PairRef(kind="reference", content_snapshot="gold answer"),
        "preferred": "a",
        "margin": 0.6,
        "judge_kind": "llm",
        "judge_id": "llm:claude-opus",
    }
    defaults.update(overrides)
    return PairwiseVerdict(**defaults)  # type: ignore[arg-type]


def test_id_prefix_is_pv() -> None:
    assert PairwiseVerdict.make_id().startswith("pv_")


def test_round_trip_preserves_nested_pair_refs() -> None:
    verdict = _verdict()
    dumped = verdict.model_dump(mode="json")
    restored = PairwiseVerdict.model_validate(dumped)
    assert restored == verdict
    assert restored.a_ref.kind == "agent_output"
    assert restored.b_ref.content_snapshot == "gold answer"


def test_tie_must_have_zero_margin() -> None:
    with pytest.raises(ValidationError, match="tie must have margin"):
        _verdict(preferred="tie", margin=0.3)


def test_tie_with_zero_margin_is_valid() -> None:
    verdict = _verdict(preferred="tie", margin=0.0)
    assert verdict.preferred == "tie"


def test_margin_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        _verdict(margin=1.5)


def test_human_judge_kind() -> None:
    verdict = _verdict(judge_kind="human", judge_id="human:pato@example.com", position="ba")
    assert verdict.judge_kind == "human"
    assert verdict.position == "ba"


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _verdict(not_a_field="x")
