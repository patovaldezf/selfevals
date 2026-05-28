"""Structured compare: `compute_compare` math + `render_compare` golden.

Two gates here:

1. A *golden full-string* equality test pinning the entire output of
   `render_compare` for a fixed, deterministic fixture (frozen
   `created_at`). The CLI tests in `tests/cli/test_compare.py` only assert
   substrings, so they would not catch a stray blank line or a shifted
   table separator. This test would. It is the byte-identical contract
   that lets the reporter be refactored without changing CLI output.

2. Assertions that `compute_compare` returns the correct structured
   `CompareResult` — the single math source the HTTP bridge also consumes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selfevals.reporter.compare import (
    CompareResult,
    compute_compare,
    render_compare,
)
from selfevals.schemas.enums import DecisionOutcome, IterationState, ProposerStrategy
from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _record(
    *,
    id_: str,
    iteration: int,
    params: dict[str, object],
    primary: float,
    primary_name: str = "pass@1",
    guardrails: dict[str, float] | None = None,
    funnel: dict[str, object] | None = None,
    failure_modes: dict[str, int] | None = None,
    outcome: DecisionOutcome = DecisionOutcome.KEEP_CANDIDATE,
) -> IterationRecord:
    return IterationRecord(
        id=id_,
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=iteration,
        created_at=TS,
        updated_at=TS,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters=params,
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name=primary_name, value=primary),
            guardrails=[MetricObservation(name=n, value=v) for n, v in (guardrails or {}).items()],
            failure_mode_counts=failure_modes or {},
            funnel=funnel or {},
        ),
        decision=IterationDecision(outcome=outcome, rationale="r"),
    )


def _golden_a() -> IterationRecord:
    return _record(
        id_="itr_01AAAAAAAAAAAAAAAAAAAAAAAA",
        iteration=0,
        params={"model_params": {"level": 0.0, "shared": 1}},
        primary=0.2,
        guardrails={"cost_usd_per_case": 0.10},
        funnel={"root": {"mean_score": 0.2, "children": {"step": {"mean_score": 0.1}}}},
        failure_modes={"fm_timeout": 3, "fm_format": 1},
        outcome=DecisionOutcome.REJECT,
    )


def _golden_b() -> IterationRecord:
    return _record(
        id_="itr_01BBBBBBBBBBBBBBBBBBBBBBBB",
        iteration=3,
        params={"model_params": {"level": 1.0, "shared": 1}},
        primary=0.8,
        guardrails={"cost_usd_per_case": 0.12},
        funnel={"root": {"mean_score": 0.8, "children": {"step": {"mean_score": 0.7}}}},
        failure_modes={"fm_timeout": 1, "fm_new": 2},
        outcome=DecisionOutcome.KEEP_CANDIDATE,
    )


# Captured against the pre-refactor `render_compare` for the fixture above.
# If a future change shifts a blank line, a separator row, or any wording,
# this string MUST be updated deliberately — never auto-relaxed.
_GOLDEN = (
    "Comparing iter A (#0) vs iter B (#3)\n"
    "\n"
    "- **A** `itr_01AAAAAAAAAAAAAAAAAAAAAAAA`  iter=#0  "
    "at=2026-05-01T12:00:00+00:00  decision=`reject`\n"
    "- **B** `itr_01BBBBBBBBBBBBBBBBBBBBBBBB`  iter=#3  "
    "at=2026-05-01T12:00:00+00:00  decision=`keep_candidate`\n"
    "\n"
    "## Proposal diff\n"
    "\n"
    "| param | A | B | changed? |\n"
    "|-------|---|---|----------|\n"
    "| `model_params.level` | `0` | `1` | yes |\n"
    "| `model_params.shared` | `1` | `1` |  |\n"
    "\n"
    "## Metrics diff\n"
    "\n"
    "| metric | A | B | Δ |\n"
    "|--------|---|---|---|\n"
    "| `cost_usd_per_case` | 0.1 | 0.12 | +0.02 |\n"
    "| `pass@1` | 0.2 | 0.8 | +0.6 |\n"
    "\n"
    "## Failure modes\n"
    "\n"
    "- in **A only**: `fm_format`(1)\n"
    "- in **B only**: `fm_new`(2)\n"
    "- in **both**: `fm_timeout` A=3 B=1\n"
    "\n"
    "## Funnel diff\n"
    "\n"
    "| node | A score | B score | Δ |\n"
    "|------|---------|---------|---|\n"
    "| `root` | 0.2 | 0.8 | +0.6 |\n"
    "| `root.step` | 0.1 | 0.7 | +0.6 |\n"
    "\n"
    "## Recommendation\n"
    "\n"
    "**B is better:** `pass@1` +0.6 (0.2 → 0.8); "
    "new failure modes introduced: `fm_new`."
)


def test_render_compare_is_byte_identical_to_golden() -> None:
    """Full-string equality — the byte-identical CLI contract."""
    assert render_compare(_golden_a(), _golden_b()) == _GOLDEN


def test_compute_compare_returns_correct_structured_result() -> None:
    result = compute_compare(_golden_a(), _golden_b())
    assert isinstance(result, CompareResult)

    # Header / identity.
    assert result.a_id == "itr_01AAAAAAAAAAAAAAAAAAAAAAAA"
    assert result.b_id == "itr_01BBBBBBBBBBBBBBBBBBBBBBBB"
    assert result.a_iteration == 0
    assert result.b_iteration == 3
    assert result.a_created_at == "2026-05-01T12:00:00+00:00"
    assert result.b_created_at == "2026-05-01T12:00:00+00:00"
    assert result.a_decision == "reject"
    assert result.b_decision == "keep_candidate"

    # Proposal diff: flattened, sorted, changed flag set only on the diff.
    proposal = {row.key: row for row in result.proposal_diff}
    assert set(proposal) == {"model_params.level", "model_params.shared"}
    assert proposal["model_params.level"].changed is True
    assert proposal["model_params.shared"].changed is False
    assert proposal["model_params.level"].a == "`0`"
    assert proposal["model_params.level"].b == "`1`"

    # Metrics diff: union of primary + guardrails, sorted by name.
    metrics = {row.name: row for row in result.metrics_diff}
    assert set(metrics) == {"cost_usd_per_case", "pass@1"}
    assert metrics["pass@1"].a == 0.2
    assert metrics["pass@1"].b == 0.8
    assert metrics["pass@1"].delta is not None
    assert abs(metrics["pass@1"].delta - 0.6) < 1e-9

    # Failure modes split three ways.
    fm = result.failure_modes
    assert fm.only_a == {"fm_format": 1}
    assert fm.only_b == {"fm_new": 2}
    assert fm.common == {"fm_timeout": (3, 1)}

    # Funnel diff flattens the tree by path.
    funnel = {row.path: row for row in result.funnel_diff}
    assert set(funnel) == {"root", "root.step"}
    assert funnel["root.step"].a == 0.1
    assert funnel["root.step"].b == 0.7

    # Recommendation: B wins on pass@1 with a newly introduced failure mode.
    rec = result.recommendation
    assert rec.kind == "winner"
    assert rec.winner == "B"
    assert rec.metric_name == "pass@1"
    assert rec.a_value == 0.2
    assert rec.b_value == 0.8
    assert rec.delta is not None and abs(rec.delta - 0.6) < 1e-9
    assert rec.new_failure_modes == ["fm_new"]


def test_compute_compare_tie() -> None:
    a = _record(id_="itr_a", iteration=0, params={}, primary=0.5)
    b = _record(id_="itr_b", iteration=1, params={}, primary=0.5)
    rec = compute_compare(a, b).recommendation
    assert rec.kind == "tie"
    assert rec.winner is None
    assert rec.metric_name == "pass@1"


def test_compute_compare_a_wins() -> None:
    a = _record(id_="itr_a", iteration=0, params={}, primary=0.9)
    b = _record(id_="itr_b", iteration=1, params={}, primary=0.2)
    rec = compute_compare(a, b).recommendation
    assert rec.kind == "winner"
    assert rec.winner == "A"
    assert rec.delta is not None and rec.delta < 0


def test_compute_compare_different_metric() -> None:
    a = _record(id_="itr_a", iteration=0, params={}, primary=0.5, primary_name="pass@1")
    b = _record(id_="itr_b", iteration=1, params={}, primary=0.8, primary_name="recall@5")
    rec = compute_compare(a, b).recommendation
    assert rec.kind == "different_metric"
    assert rec.a_metric_name == "pass@1"
    assert rec.b_metric_name == "recall@5"
    assert rec.winner is None


def test_compute_compare_no_primary_metric() -> None:
    a = _record(id_="itr_a", iteration=0, params={}, primary=0.5)
    # A paused iteration carries no metrics/decision — nothing to compare.
    b_no_metrics = IterationRecord(
        id="itr_b",
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=1,
        created_at=TS,
        updated_at=TS,
        state=IterationState.PAUSED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=None,
        decision=None,
    )
    rec = compute_compare(a, b_no_metrics).recommendation
    assert rec.kind == "none"
    assert rec.winner is None


def test_compute_compare_funnel_empty_when_neither_has_one() -> None:
    a = _record(id_="itr_a", iteration=0, params={}, primary=0.2)
    b = _record(id_="itr_b", iteration=1, params={}, primary=0.8)
    assert compute_compare(a, b).funnel_diff == []
