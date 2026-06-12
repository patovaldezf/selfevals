"""Markdown rendering of an OptimizationResult."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from selfevals.reporter._metrics import CostTimeSummary, compute_cost_time_summary
from selfevals.schemas.enums import DecisionOutcome

if TYPE_CHECKING:
    from selfevals.optimization.aggregator import FunnelNode, IterationAggregate
    from selfevals.optimization.loop import IterationOutcome, OptimizationResult


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
    lines.append(f"**Target:** `{target.name}` {target.operator} {target.value:g}")
    if exp.target.guardrails:
        guardrail_summary = ", ".join(
            f"`{g.name}` {g.operator} {g.value:g}" for g in exp.target.guardrails
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

    summary = compute_cost_time_summary(result)
    cost_time_lines = _cost_time_lines(summary)
    if cost_time_lines:
        lines.append("## Cost & Time")
        lines.append("")
        lines.extend(cost_time_lines)
        lines.append("")

    funnel_source = best if best is not None else result.iterations[-1]
    funnel_lines = _funnel_lines(funnel_source.aggregate)
    if funnel_lines:
        lines.append(f"## Funnel (iteration #{funnel_source.iteration})")
        lines.append("")
        lines.extend(funnel_lines)
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

    next_steps = _next_steps_lines(result)
    if next_steps:
        lines.append("## Next steps")
        lines.append("")
        lines.extend(next_steps)
        lines.append("")

    return "\n".join(lines)


def _iteration_table(iterations: list[IterationOutcome]) -> list[str]:
    header = "| # | primary | Δ | consistency | outcome | rationale |"
    sep = "|---|---------|---|-------------|---------|-----------|"
    rows: list[str] = [header, sep]
    baseline: float | None = None
    for it in iterations:
        primary = it.aggregate.primary_value
        delta = "—" if baseline is None else f"{primary - baseline:+.4g}"
        consistency = _fmt_consistency(it.aggregate.reliability.get("consistency_rate"))
        outcome = _OUTCOME_GLYPH[it.decision_record.outcome]
        decision = it.iteration_record.decision
        rationale = decision.rationale if decision is not None else ""
        rationale_short = rationale if len(rationale) <= 80 else rationale[:77] + "…"
        rationale_escaped = rationale_short.replace("|", "\\|")
        rows.append(
            f"| {it.iteration} | {primary:.4g} | {delta} | {consistency} | "
            f"{outcome} | {rationale_escaped} |"
        )
        if baseline is None or primary > baseline:
            baseline = primary
    return rows


def _fmt_consistency(rate: float | None) -> str:
    """Render reliability's consistency_rate as a percentage, or em dash.

    `consistency_rate` is only present when the experiment listed it among its
    reliability metrics (and survives rehydration from storage). When absent —
    e.g. an iteration that never measured it — we show "—" rather than a
    fabricated 0%."""
    if rate is None:
        return "—"
    return f"{rate * 100:.0f}%"


def _funnel_lines(aggregate: IterationAggregate) -> list[str]:
    """Render the rolled-up grader funnel as an indented bullet tree.

    Returns `[]` when the iteration has no funnel data, so the caller omits
    the whole section (mirroring Cost & Time / Top failure modes). Each node
    shows its score, contribution count, weight, and any failure-mode tags;
    children nest under their parent for the drill-down.
    """
    if not aggregate.funnel:
        return []
    out: list[str] = []
    for key in sorted(aggregate.funnel):
        _funnel_node_lines(aggregate.funnel[key], depth=0, out=out)
    return out


def _funnel_node_lines(node: FunnelNode, *, depth: int, out: list[str]) -> None:
    indent = "  " * depth
    score = "—" if node.mean_score is None else f"{node.mean_score:.4g}"
    parts = [f"score={score}", f"n={node.count}", f"weight={node.total_weight:.4g}"]
    if node.failure_mode_counts:
        ranked = sorted(node.failure_mode_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        modes = ", ".join(f"{mode} x{count}" for mode, count in ranked)
        parts.append(f"failures: {modes}")
    out.append(f"{indent}- `{node.key}` — {' · '.join(parts)}")
    for child_key in sorted(node.children):
        _funnel_node_lines(node.children[child_key], depth=depth + 1, out=out)


def _failure_mode_lines(aggregates: Iterable[IterationAggregate], *, top_n: int) -> list[str]:
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


def _cost_time_lines(summary: CostTimeSummary) -> list[str]:
    """Render the Cost & Time bullet list.

    Returns `[]` when neither cost nor time data exists — the caller
    omits the whole section so we never show "$0.00" placeholders.
    """
    if not summary.has_any:
        return []
    out: list[str] = []
    if summary.has_cost:
        out.append(f"- **Total cost:** ${_fmt_cost(summary.cost_total_usd)}")
        if summary.cost_per_iteration_usd is not None:
            out.append(
                f"  - per iteration: ${_fmt_cost(summary.cost_per_iteration_usd)} "
                f"({summary.iterations} iter)"
            )
        if summary.cost_per_case_usd is not None:
            out.append(
                f"  - per case: ${_fmt_cost(summary.cost_per_case_usd)} "
                f"({summary.cases_run} case run{'s' if summary.cases_run != 1 else ''})"
            )
    if summary.has_time:
        out.append(f"- **Total time:** {_fmt_time(summary.time_total_seconds)}")
        if summary.time_per_iteration_seconds is not None:
            out.append(f"  - per iteration: {_fmt_time(summary.time_per_iteration_seconds)}")
        if summary.time_per_case_seconds is not None:
            out.append(f"  - per case: {_fmt_time(summary.time_per_case_seconds)}")
    return out


def _fmt_cost(usd: float | None) -> str:
    if usd is None:
        return "—"
    if usd < 0.01:
        return f"{usd:.4f}"
    return f"{usd:.2f}"


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"
    minutes, rest = divmod(seconds, 60.0)
    return f"{int(minutes)}m{rest:04.1f}s"


def _next_steps_lines(result: OptimizationResult) -> list[str]:
    """Suggest follow-up commands the reader can copy-paste.

    Always emit the commands. They no-op gracefully when the run
    wasn't persisted (CLI prints `(no iterations)` etc.), so we don't
    need to guess persistence from the result alone — and guessing
    would lie when wrong.
    """
    exp = result.experiment
    ws_id = exp.workspace_id
    exp_id = exp.id

    out: list[str] = []
    out.append("Inspect this experiment (requires the run to be persisted to SQLite):")
    out.append("")
    out.append("```bash")
    out.append(f"selfevals iteration list {ws_id} {exp_id}")
    out.append(f"selfevals experiment show {ws_id} {exp_id}")
    if len(result.iterations) >= 2:
        a = result.iterations[0].iteration_record.id
        b = result.iterations[-1].iteration_record.id
        out.append(f"selfevals compare {ws_id} {a} {b}")
    out.append(f"selfevals report {ws_id} {exp_id} --format json")
    out.append("```")
    return out
