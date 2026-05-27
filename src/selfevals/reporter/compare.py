"""Render a side-by-side diff between two IterationRecords.

Pure rendering: takes two IterationRecord objects and returns a
markdown-ish string. No I/O. The CLI's `selfevals compare` builds the
inputs and prints the output.

What the diff shows:
- header (which is A, which is B, when each ran)
- proposed parameters: every key that differs, side-by-side
- metrics: primary + every guardrail + every reliability metric
- failure modes: in A only, in B only, common to both
- a one-line recommendation when one iteration clearly wins
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfevals.schemas.iteration import IterationRecord


def render_compare(a: IterationRecord, b: IterationRecord) -> str:
    """Return the full markdown-style comparison string."""
    lines: list[str] = []
    lines.extend(_header_lines(a, b))
    lines.append("")
    lines.extend(_proposal_diff_lines(a, b))
    lines.append("")
    lines.extend(_metrics_diff_lines(a, b))
    lines.append("")
    lines.extend(_failure_modes_diff_lines(a, b))
    lines.append("")
    lines.extend(_recommendation_lines(a, b))
    return "\n".join(line for line in lines if line is not None)


def _header_lines(a: IterationRecord, b: IterationRecord) -> list[str]:
    a_ts = a.created_at.isoformat(timespec="seconds")
    b_ts = b.created_at.isoformat(timespec="seconds")
    a_dec = a.decision.outcome if a.decision else "-"
    b_dec = b.decision.outcome if b.decision else "-"
    return [
        f"Comparing iter A (#{a.iteration}) vs iter B (#{b.iteration})",
        "",
        f"- **A** `{a.id}`  iter=#{a.iteration}  at={a_ts}  decision=`{a_dec}`",
        f"- **B** `{b.id}`  iter=#{b.iteration}  at={b_ts}  decision=`{b_dec}`",
    ]


def _proposal_diff_lines(a: IterationRecord, b: IterationRecord) -> list[str]:
    a_params = _flatten(a.proposed_parameters)
    b_params = _flatten(b.proposed_parameters)
    keys = sorted(set(a_params) | set(b_params))
    if not keys:
        return ["## Proposal diff", "", "_(no parameters on either iteration)_"]
    out = [
        "## Proposal diff",
        "",
        "| param | A | B | changed? |",
        "|-------|---|---|----------|",
    ]
    for key in keys:
        av = _fmt(a_params.get(key, _MISSING))
        bv = _fmt(b_params.get(key, _MISSING))
        changed = "yes" if a_params.get(key) != b_params.get(key) else ""
        out.append(f"| `{key}` | {av} | {bv} | {changed} |")
    return out


def _metrics_diff_lines(a: IterationRecord, b: IterationRecord) -> list[str]:
    a_metrics = _metrics_map(a)
    b_metrics = _metrics_map(b)
    keys = sorted(set(a_metrics) | set(b_metrics))
    if not keys:
        return ["## Metrics diff", "", "_(no metrics on either iteration)_"]
    out = [
        "## Metrics diff",
        "",
        "| metric | A | B | Δ |",
        "|--------|---|---|---|",
    ]
    for key in keys:
        av = a_metrics.get(key)
        bv = b_metrics.get(key)
        delta = _delta_str(av, bv)
        out.append(f"| `{key}` | {_fmt_num(av)} | {_fmt_num(bv)} | {delta} |")
    return out


def _failure_modes_diff_lines(a: IterationRecord, b: IterationRecord) -> list[str]:
    a_modes = _failure_modes(a)
    b_modes = _failure_modes(b)
    a_keys = set(a_modes)
    b_keys = set(b_modes)
    only_a = sorted(a_keys - b_keys)
    only_b = sorted(b_keys - a_keys)
    common = sorted(a_keys & b_keys)
    if not (only_a or only_b or common):
        return ["## Failure modes", "", "_(no failure modes recorded on either iteration)_"]
    out = ["## Failure modes", ""]
    if only_a:
        out.append("- in **A only**: " + ", ".join(f"`{m}`({a_modes[m]})" for m in only_a))
    if only_b:
        out.append("- in **B only**: " + ", ".join(f"`{m}`({b_modes[m]})" for m in only_b))
    if common:
        out.append(
            "- in **both**: " + ", ".join(f"`{m}` A={a_modes[m]} B={b_modes[m]}" for m in common)
        )
    return out


def _recommendation_lines(a: IterationRecord, b: IterationRecord) -> list[str]:
    """One-line verdict if there's a clear winner; otherwise no header."""
    a_primary = a.metrics.primary if a.metrics else None
    b_primary = b.metrics.primary if b.metrics else None
    if a_primary is None or b_primary is None:
        return []
    if a_primary.name != b_primary.name:
        # Different metrics → no apples-to-apples comparison.
        return [
            "## Recommendation",
            "",
            f"_iterations report different primary metrics_ "
            f"(A=`{a_primary.name}` vs B=`{b_primary.name}`); no recommendation.",
        ]
    delta = b_primary.value - a_primary.value
    if abs(delta) < 1e-9:
        return [
            "## Recommendation",
            "",
            f"A and B tie on `{a_primary.name}` ({a_primary.value:.4g}); "
            "compare guardrails or failure modes to decide.",
        ]

    winner = "B" if delta > 0 else "A"
    loser_failures = _failure_modes(a if winner == "B" else b)
    winner_failures = _failure_modes(b if winner == "B" else a)
    new_failures = sorted(set(winner_failures) - set(loser_failures))
    new_note = (
        f"; new failure modes introduced: {', '.join(f'`{m}`' for m in new_failures)}"
        if new_failures
        else "; no new failure modes"
    )

    return [
        "## Recommendation",
        "",
        f"**{winner} is better:** `{a_primary.name}` {delta:+.4g} "
        f"({a_primary.value:.4g} → {b_primary.value:.4g}){new_note}.",
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


__all__ = ["render_compare"]
