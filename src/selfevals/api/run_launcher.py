"""HTTP launch path for `POST .../experiments/run` (F1).

Turns a `RunExperimentRequest` into a queued background run, reusing the exact
canonical wiring the CLI uses (`runner.launch.build_loop`). The handler is sync
and returns 202 immediately; the loop — which drives LLM calls and can take
minutes — runs on a daemon thread with its own SQLite connection and its own
`asyncio.run`, so it never blocks the FastAPI event loop. (The span broker
already publishes cross-thread via `call_soon_threadsafe`, so SSE keeps working.)

State follows the loop: the handler persists the experiment as soon as it
validates (so a `GET` sees it right away), then the thread drives it through
running → completed. On failure the experiment is moved to `aborted` (there is
no `failed` state for an experiment) and the exception is logged.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import HTTPException

from selfevals._errors import SelfEvalsUserError
from selfevals.api.schemas import RunExperimentRequest, RunExperimentResponse
from selfevals.cli import _friendly
from selfevals.repo.loader import ExperimentSpec, LoaderError, build_spec_from_mapping
from selfevals.runner.launch import build_loop, ensure_workspace
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

# States from which a fresh launch would collide with an in-flight run.
_ACTIVE_STATES = {
    ExperimentState.QUEUED,
    ExperimentState.RUNNING,
    ExperimentState.PAUSED,
}


def launch_experiment_run(
    *,
    db_path: str,
    workspace_id: str,
    body: RunExperimentRequest,
) -> RunExperimentResponse:
    """Validate, persist, and kick off a background run. Returns the 202 body.

    Raises `HTTPException` for the user-facing error paths:
    * 422 — spec does not validate / yields zero cases / bad source combination.
    * 409 — the target experiment already has an active run.
    """
    spec = _load_spec(workspace_id=workspace_id, body=body)
    _apply_overrides(spec, body)

    storage = SQLiteStorage(db_path)
    try:
        ensure_workspace(storage, spec)
        with storage.open(spec.workspace_id) as scope:
            if scope.exists(Experiment, spec.experiment.id):
                existing = scope.get_entity(Experiment, spec.experiment.id)
                assert isinstance(existing, Experiment)
                if existing.state in _ACTIVE_STATES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"experiment {spec.experiment.id} already has an active run",
                    )
            # Persist up front so a poll right after the 202 finds the
            # experiment in its starting state.
            scope.put_entity(spec.experiment)
    finally:
        storage.close()

    reps = body.reps if body.reps is not None else 1
    thread = threading.Thread(
        target=_run_in_thread,
        kwargs={"db_path": db_path, "spec": spec, "reps": reps},
        name=f"run-{spec.experiment.id}",
        daemon=True,
    )
    thread.start()

    return RunExperimentResponse(
        experiment_id=spec.experiment.id,
        workspace_id=spec.workspace_id,
        state=str(spec.experiment.state),
    )


def _load_spec(*, workspace_id: str, body: RunExperimentRequest) -> ExperimentSpec:
    """Build the spec from disk or inline body. Loader errors → 422.

    `workspace_id` from the path always wins over any `workspace:` in the spec,
    so a POST to `/workspaces/{ws}/...` is authoritative about where the run
    lands.
    """
    try:
        if body.spec_inline is not None:
            return build_spec_from_mapping(body.spec_inline, workspace_id=workspace_id)
        assert body.spec_path is not None  # guaranteed by the request validator
        return _friendly.load_spec(body.spec_path, workspace_id=workspace_id)
    except (SelfEvalsUserError, LoaderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _apply_overrides(spec: ExperimentSpec, body: RunExperimentRequest) -> None:
    if body.max_iterations is not None:
        spec.experiment.run.max_iterations = body.max_iterations
    if body.persist_traces is not None:
        spec.experiment.run.persist_traces = body.persist_traces


def _run_in_thread(*, db_path: str, spec: ExperimentSpec, reps: int) -> None:
    """Background worker: own storage + own event loop. Never blocks FastAPI.

    The loop drives `spec.experiment.state` (draft → … → completed) and now
    persists the experiment row at each transition via its `scope`, so a polling
    `GET` follows progress without anything extra here. On failure we still move
    it to `aborted` ourselves (the loop has no failure transition).
    """
    storage = SQLiteStorage(db_path)
    scope = None
    try:
        scope = storage.open(spec.workspace_id)
        loop = build_loop(spec, scope=scope, repetitions_per_case=reps)
        asyncio.run(loop.run())
    except Exception:
        logger.exception("experiment run failed: %s", spec.experiment.id)
        _abort_experiment(storage, spec)
    finally:
        if scope is not None:
            scope.close()
        storage.close()


def _abort_experiment(storage: SQLiteStorage, spec: ExperimentSpec) -> None:
    """Move a failed run to `aborted` (no `failed` state exists for experiments).

    Reloads from storage to avoid clobbering whatever state the loop reached,
    and tolerates an already-terminal experiment (e.g. it completed then a
    persistence step failed) by leaving it alone.
    """
    try:
        with storage.open(spec.workspace_id) as scope:
            if not scope.exists(Experiment, spec.experiment.id):
                return
            exp = scope.get_entity(Experiment, spec.experiment.id)
            assert isinstance(exp, Experiment)
            if ExperimentState.ABORTED in _legal_next(exp.state):
                exp.transition_to(ExperimentState.ABORTED)
                scope.put_entity(exp)
    except Exception:
        logger.exception("failed to mark experiment aborted: %s", spec.experiment.id)


def _legal_next(state: ExperimentState) -> set[ExperimentState]:
    from selfevals.schemas.experiment import _LEGAL_TRANSITIONS

    return _LEGAL_TRANSITIONS.get(state, set())
