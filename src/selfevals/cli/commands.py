"""CLI command implementations: lifecycle (init, run) and reporting.

Each `cmd_*` takes the parsed argparse Namespace and returns an int exit
code. Errors that should produce a clean `error: <msg>` line raise
`CommandError`; anything else escapes as a traceback (a real bug).

Workspace/experiment/iteration inspection lives in `experiment_commands.py`;
skills/examples/serve/worker live in `ops_commands.py`. Shared helpers
(`_storage`, `_require_entity`, entity listing/reconstruction) live in
`_common.py`, which all handler modules — including this one — import from
directly.
"""

from __future__ import annotations

import argparse

from selfevals.cli import _friendly
from selfevals.cli._common import (
    CommandError,
    _ensure_cwd_on_path,
    _experiment_decisions,
    _experiment_iterations,
    _reconstruct_result,
    _require_entity,
    _storage,
)
from selfevals.optimization.loop import OptimizationResult
from selfevals.reporter import render_json, render_markdown
from selfevals.reporter.compare import render_compare
from selfevals.runner.launch import (
    build_loop,
    ensure_workspace,
    payload_router_for_db,
    trace_sampling_override,
)
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import IterationRecord
from selfevals.storage.factory import resolve_storage_url
from selfevals.storage.interface import StorageInterface
from selfevals.storage.seed import seed_failure_taxonomy, seed_workspace


def cmd_init(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        seeded = seed_workspace(
            storage,
            slug=args.slug,
            name=args.name or args.slug,
            user_id=args.user,
        )
        modes = seed_failure_taxonomy(storage, workspace_id=seeded.workspace.id)
    finally:
        storage.close()
    ws = seeded.workspace
    print(f"workspace id={ws.id} slug={ws.slug} name={ws.name}")
    print(f"members: {len(seeded.members)} role(s)")
    print(f"failure-mode taxonomy: {len(modes)} canonical mode(s) seeded")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
            decisions = _experiment_decisions(scope, exp.id)
            # Build the result while the scope is open — _reconstruct_result
            # reloads persisted Traces to repopulate case_runs / failure_reasons.
            result = _reconstruct_result(scope, exp, iterations, decisions)
    finally:
        storage.close()

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            a = _require_entity(scope, IterationRecord, args.iter_a_id)
            b = _require_entity(scope, IterationRecord, args.iter_b_id)
    finally:
        storage.close()
    assert isinstance(a, IterationRecord)
    assert isinstance(b, IterationRecord)
    if a.experiment_id != b.experiment_id:
        raise CommandError(
            f"iterations belong to different experiments ({a.experiment_id} vs {b.experiment_id})"
        )
    if a.metrics is None or b.metrics is None:
        raise CommandError("one of the iterations has no metrics")

    print(render_compare(a, b))
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    if args.cases < 1 or args.space_size < 1 or args.reps < 1:
        raise CommandError("cases, space-size, and reps must all be >= 1")
    if args.cost_per_call < 0:
        raise CommandError("cost-per-call must be >= 0")
    total_calls = args.cases * args.reps * args.space_size
    total_cost = total_calls * args.cost_per_call
    print(f"cases x reps x proposals = {args.cases} x {args.reps} x {args.space_size}")
    print(f"agent calls (upper bound): {total_calls}")
    print(f"estimated cost (USD):      ${total_cost:.2f}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _ensure_cwd_on_path()
    spec = _friendly.load_spec(args.spec, workspace_id=args.workspace)

    dataset_id = getattr(args, "dataset", None)
    if dataset_id is not None:
        from dataclasses import replace

        from selfevals.repo.loader import RefDatasetSource
        from selfevals.schemas._base import EntityRef

        # Override the spec's dataset source with a reference; build_loop resolves
        # its cases + split from storage (sharded runs always persist).
        spec = replace(
            spec,
            dataset_source=RefDatasetSource(ref=EntityRef(id=dataset_id)),
            cases=[],
        )

    if args.max_iterations is not None:
        if args.max_iterations < 1:
            raise CommandError("--max-iterations must be >= 1")
        spec.experiment.run.max_iterations = args.max_iterations

    # Precedence: explicit --persist-traces flag > SELFEVALS_TRACE_SAMPLING env
    # > spec default.
    if args.persist_traces is not None:
        spec.experiment.run.persist_traces = args.persist_traces
    else:
        env_policy = trace_sampling_override()
        if env_policy is not None:
            spec.experiment.run.persist_traces = env_policy

    # Sharded execution is the only path: `run` enqueues a coordinator run-job
    # and a worker (or N workers) drains its scenario jobs.
    from selfevals.api.run_jobs import create_run_job
    from selfevals.api.run_queue import configured_run_queue

    storage = _storage(args)
    try:
        ensure_workspace(storage, spec)
        with storage.open(spec.workspace_id) as scope:
            # Build the loop first: it validates the spec (unknown graders, bad
            # adapters) and persists the experiment + eval_cases. A spec error is
            # a spec error whether or not Redis is up, so it must surface before
            # the infra requirement below.
            payload_router = payload_router_for_db(resolve_storage_url(args.db), spec.workspace_id)
            build_loop(
                spec, scope=scope, repetitions_per_case=args.reps, payload_router=payload_router
            ).close_executor()

        # Only now require the queue: a valid spec still needs Redis + a worker.
        queue = configured_run_queue()
        if queue is None:
            raise CommandError(
                "`selfevals run` shards execution and needs Redis + a worker. "
                "Set SELFEVALS_REDIS_URL and start one with `selfevals worker runs` "
                "(locally: `docker compose up -d` brings up Postgres, Redis, and a worker)."
            )
        # Preflight: fail in milliseconds instead of hanging until --timeout.
        # `active_consumers()` returns None when Redis can't be probed (don't
        # block the launch over an observability check) and 0 when the group
        # exists with no live worker — the exact "queued, nobody consuming"
        # state that otherwise surfaces as N seconds of silence.
        if queue.active_consumers() == 0:
            raise CommandError(
                "no worker is consuming the run queue, so this run would hang.",
                hint=(
                    "start one with `selfevals worker runs` (or `docker compose up -d worker`) "
                    "and make sure its SELFEVALS_REDIS_URL matches yours — including the "
                    "database number after the port."
                ),
            )
        job = create_run_job(storage, spec=spec, reps=args.reps)
        queue.enqueue(job)

        # Block until the worker drives the run to a terminal state, then rebuild
        # the result from persisted records (same path as `cmd_report`).
        result = _await_run_result(storage, spec, job.id, timeout_s=args.timeout)
        with storage.open(spec.workspace_id) as scope:
            from selfevals.runner.baseline import maybe_autoset_baseline

            maybe_autoset_baseline(scope, spec, result)
    finally:
        storage.close()

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


def _await_run_result(
    storage: StorageInterface,
    spec: object,
    job_id: str,
    *,
    timeout_s: float,
    poll_s: float = 0.5,
) -> OptimizationResult:
    """Poll the run-job until terminal, then reconstruct the OptimizationResult.

    Raises CommandError if the job fails/dead-letters or the timeout elapses —
    the CLI's contract is a clean one-line error, never a hang or a traceback.
    """
    import time

    from selfevals.api.run_jobs import get_run_job
    from selfevals.repo.loader import ExperimentSpec
    from selfevals.schemas.job import RunJobStatus

    assert isinstance(spec, ExperimentSpec)
    deadline = time.monotonic() + timeout_s
    while True:
        job = get_run_job(storage, workspace_id=spec.workspace_id, job_id=job_id)
        if job is None:
            raise CommandError(f"run job {job_id} vanished from storage")
        if job.status in (RunJobStatus.SUCCEEDED, RunJobStatus.CANCELLED):
            break
        if job.status in (RunJobStatus.FAILED, RunJobStatus.DEAD_LETTERED):
            raise CommandError(f"run failed: {job.last_error or 'unknown error'}")
        if time.monotonic() > deadline:
            raise CommandError(
                f"run did not finish within {timeout_s:g}s (status={job.status}); "
                "is a worker consuming the queue?"
            )
        time.sleep(poll_s)

    with storage.open(spec.workspace_id) as scope:
        exp = _require_entity(scope, Experiment, spec.experiment.id)
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, exp.id)
        decisions = _experiment_decisions(scope, exp.id)
        return _reconstruct_result(scope, exp, iterations, decisions)
