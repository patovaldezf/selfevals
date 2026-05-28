"""Diff two IterationRecords — one math source, two renderings.

`compute_compare(a, b)` is the single source of truth: it turns two
IterationRecords into a structured `CompareResult` (frozen dataclasses,
no pydantic — the reporter is core and must not couple to the web
layer). The CLI's `selfevals compare` calls `render_compare`, which is
a thin set of string builders over the same `CompareResult`. The HTTP
bridge (`selfevals.api.queries.load_compare`) projects the same
`CompareResult` into pydantic for the web UI.

What the diff shows:
- header (which is A, which is B, when each ran)
- proposed parameters: every key that differs, side-by-side
- metrics: primary + every guardrail + every reliability metric
- failure modes: in A only, in B only, common to both
- funnel: rolled-up grader funnel by node path (mean score per node)
- a recommendation when one iteration clearly wins (with an honest
  "new failure modes introduced" note)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from selfevals.schemas.iteration import IterationRecord


# --- Structured result --------------------------------------------------
#
# Frozen dataclasses, deliberately not pydantic: the reporter is core and
# must stay free of the web layer's dependency. The API mirrors these in
# `selfevals.api.schemas` and projects across the boundary in queries.


@dataclass(frozen=True)
class ParamDiffRow:
    key: str
    a: str
    b: str
    changed: bool


@dataclass(frozen=True)
class MetricDiffRow:
    name: str
    a: float | None
    b: float | None
    delta: float | None


@dataclass(frozen=True)
class FailureModesDiff:
    only_a: dict[str, int]
    only_b: dict[str, int]
    common: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class FunnelDiffRow:
    path: str
    a: float | None
    b: float | None
    delta: float | None


@dataclass(frozen=True)
class Recommendation:
    kind: Literal["winner", "tie", "different_metric", "none"]
    winner: str | None
    metric_name: str | None
    """Shared primary-metric name when both iterations report the same one
    (kind in {"winner", "tie"}); None for "none"/"different_metric"."""
    a_metric_name: str | None
    b_metric_name: str | None
    """Each side's primary-metric name. Populated whenever both iterations
    have a primary metric, so the "different metric" verdict can name both."""
    a_value: float | None
    b_value: float | None
    delta: float | None
    new_failure_modes: list[str]


@dataclass(frozen=True)
class CompareResult:
    a_id: str
    b_id: str
    a_iteration: int
    b_iteration: int
    a_created_at: str
    b_created_at: str
    a_decision: str | None
    b_decision: str | None
    proposal_diff: list[ParamDiffRow]
    metrics_diff: list[MetricDiffRow]
    failure_modes: FailureModesDiff
    funnel_diff: list[FunnelDiffRow]
    recommendation: Recommendation


# --- Math: the single entry point --------------------------------------


def compute_compare(a: IterationRecord, b: IterationRecord) -> CompareResult:
    """Turn two IterationRecords into a structured diff.

    This is the one place the comparison math lives; both the CLI
    renderer and the HTTP bridge consume its output. The branching
    mirrors the original `render_compare` line-builders exactly so the
    CLI output stays byte-identical.
    """
    return CompareResult(
        a_id=a.id,
        b_id=b.id,
        a_iteration=a.iteration,
        b_iteration=b.iteration,
        a_created_at=a.created_at.isoformat(timespec="seconds"),
        b_created_at=b.created_at.isoformat(timespec="seconds"),
        a_decision=a.decision.outcome if a.decision else None,
        b_decision=b.decision.outcome if b.decision else None,
        proposal_diff=_compute_proposal_diff(a, b),
        metrics_diff=_compute_metrics_diff(a, b),
        failure_modes=_compute_failure_modes(a, b),
        funnel_diff=_compute_funnel_diff(a, b),
        recommendation=_compute_recommendation(a, b),
    )


def _compute_proposal_diff(a: IterationRecord, b: IterationRecord) -> list[ParamDiffRow]:
    a_params = _flatten(a.proposed_parameters)
    b_params = _flatten(b.proposed_parameters)
    keys = sorted(set(a_params) | set(b_params))
    rows: list[ParamDiffRow] = []
    for key in keys:
        rows.append(
            ParamDiffRow(
                key=key,
                a=_fmt(a_params.get(key, _MISSING)),
                b=_fmt(b_params.get(key, _MISSING)),
                changed=a_params.get(key) != b_params.get(key),
            )
        )
    return rows


def _compute_metrics_diff(a: IterationRecord, b: IterationRecord) -> list[MetricDiffRow]:
    a_metrics = _metrics_map(a)
    b_metrics = _metrics_map(b)
    keys = sorted(set(a_metrics) | set(b_metrics))
    rows: list[MetricDiffRow] = []
    for key in keys:
        av = a_metrics.get(key)
        bv = b_metrics.get(key)
        delta = bv - av if av is not None and bv is not None else None
        rows.append(MetricDiffRow(name=key, a=av, b=bv, delta=delta))
    return rows


def _compute_failure_modes(a: IterationRecord, b: IterationRecord) -> FailureModesDiff:
    a_modes = _failure_modes(a)
    b_modes = _failure_modes(b)
    a_keys = set(a_modes)
    b_keys = set(b_modes)
    return FailureModesDiff(
        only_a={m: a_modes[m] for m in sorted(a_keys - b_keys)},
        only_b={m: b_modes[m] for m in sorted(b_keys - a_keys)},
        common={m: (a_modes[m], b_modes[m]) for m in sorted(a_keys & b_keys)},
    )


def _compute_funnel_diff(a: IterationRecord, b: IterationRecord) -> list[FunnelDiffRow]:
    a_funnel = _funnel_map(a)
    b_funnel = _funnel_map(b)
    if not a_funnel and not b_funnel:
        return []
    keys = sorted(set(a_funnel) | set(b_funnel))
    rows: list[FunnelDiffRow] = []
    for key in keys:
        av = a_funnel.get(key)
        bv = b_funnel.get(key)
        delta = bv - av if av is not None and bv is not None else None
        rows.append(FunnelDiffRow(path=key, a=av, b=bv, delta=delta))
    return rows


def _compute_recommendation(a: IterationRecord, b: IterationRecord) -> Recommendation:
    a_primary = a.metrics.primary if a.metrics else None
    b_primary = b.metrics.primary if b.metrics else None
    if a_primary is None or b_primary is None:
        return Recommendation(
            kind="none",
            winner=None,
            metric_name=None,
            a_metric_name=a_primary.name if a_primary else None,
            b_metric_name=b_primary.name if b_primary else None,
            a_value=None,
            b_value=None,
            delta=None,
            new_failure_modes=[],
        )
    if a_primary.name != b_primary.name:
        # Different metrics → no apples-to-apples comparison.
        return Recommendation(
            kind="different_metric",
            winner=None,
            metric_name=None,
            a_metric_name=a_primary.name,
            b_metric_name=b_primary.name,
            a_value=a_primary.value,
            b_value=b_primary.value,
            delta=None,
            new_failure_modes=[],
        )
    delta = b_primary.value - a_primary.value
    if abs(delta) < 1e-9:
        return Recommendation(
            kind="tie",
            winner=None,
            metric_name=a_primary.name,
            a_metric_name=a_primary.name,
            b_metric_name=b_primary.name,
            a_value=a_primary.value,
            b_value=b_primary.value,
            delta=delta,
            new_failure_modes=[],
        )
    winner = "B" if delta > 0 else "A"
    loser_failures = _failure_modes(a if winner == "B" else b)
    winner_failures = _failure_modes(b if winner == "B" else a)
    new_failures = sorted(set(winner_failures) - set(loser_failures))
    return Recommendation(
        kind="winner",
        winner=winner,
        metric_name=a_primary.name,
        a_metric_name=a_primary.name,
        b_metric_name=b_primary.name,
        a_value=a_primary.value,
        b_value=b_primary.value,
        delta=delta,
        new_failure_modes=new_failures,
    )


# --- Rendering: thin string builders over CompareResult ----------------


def render_compare(a: IterationRecord, b: IterationRecord) -> str:
    """Return the full markdown-style comparison string.

    Byte-for-byte identical to the pre-refactor renderer (pinned by
    `tests/reporter/test_compare_structured.py`). Note the cadence: a
    blank line is appended after *every* section unconditionally — even
    when the funnel section renders nothing.
    """
    result = compute_compare(a, b)
    lines: list[str] = []
    lines.extend(_render_header(result))
    lines.append("")
    lines.extend(_render_proposal_diff(result))
    lines.append("")
    lines.extend(_render_metrics_diff(result))
    lines.append("")
    lines.extend(_render_failure_modes(result))
    lines.append("")
    lines.extend(_render_funnel_diff(result))
    lines.append("")
    lines.extend(_render_recommendation(result))
    return "\n".join(line for line in lines if line is not None)


def _render_header(result: CompareResult) -> list[str]:
    a_dec = result.a_decision if result.a_decision else "-"
    b_dec = result.b_decision if result.b_decision else "-"
    return [
        f"Comparing iter A (#{result.a_iteration}) vs iter B (#{result.b_iteration})",
        "",
        f"- **A** `{result.a_id}`  iter=#{result.a_iteration}  "
        f"at={result.a_created_at}  decision=`{a_dec}`",
        f"- **B** `{result.b_id}`  iter=#{result.b_iteration}  "
        f"at={result.b_created_at}  decision=`{b_dec}`",
    ]


def _render_proposal_diff(result: CompareResult) -> list[str]:
    if not result.proposal_diff:
        return ["## Proposal diff", "", "_(no parameters on either iteration)_"]
    out = [
        "## Proposal diff",
        "",
        "| param | A | B | changed? |",
        "|-------|---|---|----------|",
    ]
    for row in result.proposal_diff:
        changed = "yes" if row.changed else ""
        out.append(f"| `{row.key}` | {row.a} | {row.b} | {changed} |")
    return out


def _render_metrics_diff(result: CompareResult) -> list[str]:
    if not result.metrics_diff:
        return ["## Metrics diff", "", "_(no metrics on either iteration)_"]
    out = [
        "## Metrics diff",
        "",
        "| metric | A | B | Δ |",
        "|--------|---|---|---|",
    ]
    for row in result.metrics_diff:
        out.append(
            f"| `{row.name}` | {_fmt_num(row.a)} | {_fmt_num(row.b)} | {_delta_str(row.a, row.b)} |"
        )
    return out


def _render_failure_modes(result: CompareResult) -> list[str]:
    fm = result.failure_modes
    if not (fm.only_a or fm.only_b or fm.common):
        return ["## Failure modes", "", "_(no failure modes recorded on either iteration)_"]
    out = ["## Failure modes", ""]
    if fm.only_a:
        out.append("- in **A only**: " + ", ".join(f"`{m}`({c})" for m, c in fm.only_a.items()))
    if fm.only_b:
        out.append("- in **B only**: " + ", ".join(f"`{m}`({c})" for m, c in fm.only_b.items()))
    if fm.common:
        out.append(
            "- in **both**: "
            + ", ".join(f"`{m}` A={ca} B={cb}" for m, (ca, cb) in fm.common.items())
        )
    return out


def _render_funnel_diff(result: CompareResult) -> list[str]:
    if not result.funnel_diff:
        return []
    out = [
        "## Funnel diff",
        "",
        "| node | A score | B score | Δ |",
        "|------|---------|---------|---|",
    ]
    for row in result.funnel_diff:
        out.append(
            f"| `{row.path}` | {_fmt_num(row.a)} | {_fmt_num(row.b)} | {_delta_str(row.a, row.b)} |"
        )
    return out


def _render_recommendation(result: CompareResult) -> list[str]:
    rec = result.recommendation
    if rec.kind == "none":
        return []
    if rec.kind == "different_metric":
        return [
            "## Recommendation",
            "",
            f"_iterations report different primary metrics_ "
            f"(A=`{rec.a_metric_name}` vs B=`{rec.b_metric_name}`); no recommendation.",
        ]
    if rec.kind == "tie":
        assert rec.metric_name is not None and rec.a_value is not None
        return [
            "## Recommendation",
            "",
            f"A and B tie on `{rec.metric_name}` ({rec.a_value:.4g}); "
            "compare guardrails or failure modes to decide.",
        ]
    assert rec.metric_name is not None
    assert rec.a_value is not None and rec.b_value is not None and rec.delta is not None
    new_note = (
        f"; new failure modes introduced: {', '.join(f'`{m}`' for m in rec.new_failure_modes)}"
        if rec.new_failure_modes
        else "; no new failure modes"
    )
    return [
        "## Recommendation",
        "",
        f"**{rec.winner} is better:** `{rec.metric_name}` {rec.delta:+.4g} "
        f"({rec.a_value:.4g} → {rec.b_value:.4g}){new_note}.",
    ]


_MISSING = object()


def _flatten(params: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict params into `a.b.c` -> value pairs.

    The search space uses nested keys (`model_params.level`); flattening
    lets the diff table line them up by leaf path.
    """
    out: dict[str, Any] = {}
    for k, v in params.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, prefix=key))
        else:
            out[key] = v
    return out


def _metrics_map(record: IterationRecord) -> dict[str, float]:
    out: dict[str, float] = {}
    if record.metrics is None:
        return out
    out[record.metrics.primary.name] = record.metrics.primary.value
    for g in record.metrics.guardrails:
        out[g.name] = g.value
    for name, val in record.metrics.reliability.items():
        out[name] = val
    return out


def _failure_modes(record: IterationRecord) -> dict[str, int]:
    # failure_mode_counts persists on IterationMetrics (see
    # error_analysis_design.md §5), keyed by stable mode identity.
    counts = record.metrics.failure_mode_counts if record.metrics else None
    if counts:
        return {str(k): int(v) for k, v in counts.items()}
    return {}


def _funnel_map(record: IterationRecord) -> dict[str, float]:
    """Flatten the persisted funnel tree to `path -> mean_score`.

    Nodes whose `mean_score` is None (label-only) are skipped so the diff
    table only carries comparable numbers.
    """
    funnel = record.metrics.funnel if record.metrics else None
    if not funnel:
        return {}
    out: dict[str, float] = {}

    def _walk(nodes: dict[str, Any], prefix: str) -> None:
        for key, node in nodes.items():
            if not isinstance(node, dict):
                continue
            path = key if not prefix else f"{prefix}.{key}"
            score = node.get("mean_score")
            if isinstance(score, (int, float)):
                out[path] = float(score)
            children = node.get("children")
            if isinstance(children, dict):
                _walk(children, path)

    _walk(funnel, "")
    return out


def _delta_str(a: float | None, b: float | None) -> str:
    if a is None and b is None:
        return "—"
    if a is None:
        return "(only B)"
    if b is None:
        return "(only A)"
    diff = b - a
    return f"{diff:+.4g}"


def _fmt_num(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:.4g}"


def _fmt(value: Any) -> str:
    if value is _MISSING:
        return "—"
    if isinstance(value, float):
        return f"`{value:.4g}`"
    if isinstance(value, str):
        return f"`{value}`"
    return f"`{value!r}`"


__all__ = ["CompareResult", "compute_compare", "render_compare"]
