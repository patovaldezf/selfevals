"""Markdown rendering of an OptimizationResult."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from bootstrap.schemas.enums import DecisionOutcome

if TYPE_CHECKING:
    from bootstrap.optimization.aggregator import IterationAggregate
    from bootstrap.optimization.loop import IterationOutcome, OptimizationResult


_OUTCOME_GLYPH: dict[DecisionOutcome, str] = {
    DecisionOutcome.KEEP_CANDIDATE: "keep",
    DecisionOutcome.REJECT: "reject",
    DecisionOutcome.REVERT: "revert",
    DecisionOutcome.FEATURE_FLAG: "flag",
    DecisionOutcome.INVESTIGATE: "investigate",
    DecisionOutcome.REQUIRE_TRADEOFF_REVIEW: "review",
    DecisionOutcome.SPAWN_SUBEXPERIMENT: "spawn",
}


def render_markdown(
    result: OptimizationResult,
    *,
    top_failure_modes: int = 5,
) -> str:
    """Return a self-contained markdown report for one OptimizationResult.

    The report is designed to drop straight into a PR comment: ATX
    headings, no HTML, and tables narrow enough to read in a 100-col
    diff view.
    """
    exp = result.experiment
    lines: list[str] = []

    lines.append(f"# Experiment: {exp.name}")
    lines.append("")
    lines.append(f"_{exp.goal}_")
    lines.append("")
    lines.append(
        f"- **State:** `{exp.state}`  "
        f"- **Mode:** `{exp.mode}`  "
        f"- **Proposer:** `{exp.proposer.strategy}`  "
        f"- **Iterations:** {len(result.iterations)}/{exp.run.max_iterations}  "
        f"- **Terminated:** `{result.terminated_reason or 'n/a'}`"
    )
    lines.append("")

    target = exp.target.primary
    lines.append(
        f"**Target:** `{target.name}` {target.operator} {target.value:g}"
    )
    if exp.target.guardrails:
        guardrail_summary = ", ".join(
            f"`{g.name}` {g.operator} {g.value:g}"
            for g in exp.target.guardrails
        )
        lines.append(f"**Guardrails:** {guardrail_summary}")
    lines.append("")

    if not result.iterations:
        lines.append("> No iterations were executed.")
        lines.append("")
        return "\n".join(lines)

    best = result.best_iteration
    if best is not None:
        primary = best.aggregate.primary_metric
        value = best.aggregate.primary_value
        lines.append(
            f"## Best iteration: #{best.iteration} — "
            f"`{primary} = {value:.4g}` ({_OUTCOME_GLYPH[best.decision_record.outcome]})"
        )
        lines.append("")
        lines.append(f"> {best.proposal.hypothesis}")
        lines.append("")
        if best.proposal.parameters:
            lines.append("**Parameters:**")
            lines.append("")
            for key, val in sorted(best.proposal.parameters.items()):
                lines.append(f"- `{key}` = `{_fmt_value(val)}`")
            lines.append("")

    lines.append("## Iterations")
    lines.append("")
    lines.extend(_iteration_table(result.iterations))
    lines.append("")

    failure_lines = _failure_mode_lines(
        (it.aggregate for it in result.iterations),
        top_n=top_failure_modes,
    )
    if failure_lines:
        lines.append("## Top failure modes")
        lines.append("")
        lines.extend(failure_lines)
        lines.append("")

    return "\n".join(lines)


def _iteration_table(iterations: list[IterationOutcome]) -> list[str]:
    header = "| # | primary | Δ | outcome | rationale |"
    sep = "|---|---------|---|---------|-----------|"
    rows: list[str] = [header, sep]
    baseline: float | None = None
    for it in iterations:
        primary = it.aggregate.primary_value
        delta = "—" if baseline is None else f"{primary - baseline:+.4g}"
        outcome = _OUTCOME_GLYPH[it.decision_record.outcome]
        decision = it.iteration_record.decision
        rationale = decision.rationale if decision is not None else ""
        rationale_short = rationale if len(rationale) <= 80 else rationale[:77] + "…"
        rationale_escaped = rationale_short.replace("|", "\\|")
        rows.append(
            f"| {it.iteration} | {primary:.4g} | {delta} | "
            f"{outcome} | {rationale_escaped} |"
        )
        if baseline is None or primary > baseline:
            baseline = primary
    return rows


def _failure_mode_lines(
    aggregates: Iterable[IterationAggregate], *, top_n: int
) -> list[str]:
    totals: dict[str, int] = {}
    for agg in aggregates:
        for mode, count in agg.failure_mode_counts.items():
            totals[mode] = totals.get(mode, 0) + count
    if not totals:
        return []
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [f"- `{mode}` — {count}" for mode, count in ranked]


def _fmt_value(val: object) -> str:
    if isinstance(val, float):
        return f"{val:.4g}"
    if isinstance(val, str):
        return val
    return repr(val)
