"""Atomic per-case claim + planner idempotency for sharded execution.

The load-bearing invariant: N workers draining the same pending frontier must
take DISJOINT rows — never double-claim a case. That needs a real second
connection (the storage holds one connection), so the concurrency test opens two
PostgresStorage instances and claims from both, then asserts the claimed sets do
not overlap and cover everything exactly once.
"""

from __future__ import annotations

import threading
from datetime import timedelta

from selfevals._internal.time import utc_now
from selfevals.api.run_jobs import plan_scenario_jobs
from selfevals.schemas.job import RunJob, ScenarioJob, ScenarioJobStatus
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _seed_run_job(db_url: str) -> RunJob:
    """Create workspace + experiment + run_job so scenario FKs resolve."""
    from tests.optimization.test_loop import _experiment  # reuse the factory

    storage = open_storage(db_url)
    exp = _experiment(max_iterations=1)
    run_job = RunJob(
        id=RunJob.make_id(),
        workspace_id=WS,
        experiment_id=exp.id,
        spec_payload={},
        reps=1,
    )
    with storage.open(WS) as scope:
        scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t", name="t"))
        scope.put_entity(exp)
        scope.put_entity(run_job)
    storage.close()
    return run_job


def _plan(db_url: str, run_job: RunJob, case_ids: list[str]) -> int:
    storage = open_storage(db_url)
    try:
        return plan_scenario_jobs(
            storage,
            run_job=run_job,
            case_ids=case_ids,
            iteration=0,
            parameter_overrides={"level": 1.0},
            reps=1,
        )
    finally:
        storage.close()


def test_plan_is_idempotent(db_url: str) -> None:
    run_job = _seed_run_job(db_url)
    case_ids = [f"case_{i}" for i in range(5)]
    assert _plan(db_url, run_job, case_ids) == 5
    # Re-planning the same iteration inserts nothing (UNIQUE conflict → no-op).
    assert _plan(db_url, run_job, case_ids) == 0


def test_claim_marks_claimed_and_bumps_attempt(db_url: str) -> None:
    run_job = _seed_run_job(db_url)
    _plan(db_url, run_job, [f"case_{i}" for i in range(3)])

    storage = open_storage(db_url)
    try:
        claimed = storage.claim_scenario_jobs(
            run_job_id=run_job.id,
            iteration=0,
            worker_id="w1",
            lease_until=utc_now() + timedelta(seconds=300),
            batch=10,
        )
        assert len(claimed) == 3
        assert all(j.status == ScenarioJobStatus.CLAIMED for j in claimed)
        assert all(j.attempt == 1 for j in claimed)
        assert all(j.worker_id == "w1" for j in claimed)
        # Nothing left to claim.
        assert storage.claim_scenario_jobs(
            run_job_id=run_job.id, iteration=0, worker_id="w1",
            lease_until=utc_now() + timedelta(seconds=300), batch=10,
        ) == []
    finally:
        storage.close()


def test_concurrent_claims_are_disjoint(db_url: str) -> None:
    run_job = _seed_run_job(db_url)
    n = 40
    _plan(db_url, run_job, [f"case_{i}" for i in range(n)])

    results: dict[str, list[ScenarioJob]] = {}
    barrier = threading.Barrier(2)

    def _worker(name: str) -> None:
        store = open_storage(db_url)
        claimed: list[ScenarioJob] = []
        try:
            barrier.wait()  # maximize the race window
            while True:
                batch = store.claim_scenario_jobs(
                    run_job_id=run_job.id, iteration=0, worker_id=name,
                    lease_until=utc_now() + timedelta(seconds=300), batch=7,
                )
                if not batch:
                    break
                claimed.extend(batch)
        finally:
            store.close()
        results[name] = claimed

    t1 = threading.Thread(target=_worker, args=("w1",))
    t2 = threading.Thread(target=_worker, args=("w2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    ids1 = {j.id for j in results["w1"]}
    ids2 = {j.id for j in results["w2"]}
    # No case claimed by both workers, and together they cover every case once.
    assert ids1.isdisjoint(ids2)
    assert len(ids1) + len(ids2) == n


def test_barrier_counts_and_finalize(db_url: str) -> None:
    run_job = _seed_run_job(db_url)
    _plan(db_url, run_job, [f"case_{i}" for i in range(3)])

    storage = open_storage(db_url)
    try:
        counts = storage.barrier_counts(run_job_id=run_job.id, iteration=0)
        assert counts == {"pending": 3}

        claimed = storage.claim_scenario_jobs(
            run_job_id=run_job.id, iteration=0, worker_id="w1",
            lease_until=utc_now() + timedelta(seconds=300), batch=10,
        )
        # Finalize one as succeeded; the barrier should reflect it.
        storage.finalize_scenario_job(
            job_id=claimed[0].id, status="succeeded", error=None, finished_at=utc_now()
        )
        counts = storage.barrier_counts(run_job_id=run_job.id, iteration=0)
        assert counts.get("succeeded") == 1
        assert counts.get("claimed") == 2
    finally:
        storage.close()
