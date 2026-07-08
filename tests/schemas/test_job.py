"""State transitions for ScenarioJob (sharded per-case work unit).

The retry semantics differ from RunJob on purpose: a retryable ScenarioJob goes
back to PENDING (the claimable frontier the SKIP-LOCKED claim scans), not FAILED
— there is no Redis requeue for scenario jobs, the partial index is the queue.
"""

from __future__ import annotations

from datetime import timedelta

from selfevals._internal.time import utc_now
from selfevals.schemas.job import ScenarioJob, ScenarioJobStatus

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _job(**overrides: object) -> ScenarioJob:
    base: dict[str, object] = {
        "id": ScenarioJob.make_id(),
        "workspace_id": WS,
        "run_job_id": "job_01H",
        "experiment_id": "exp_01H",
        "iteration": 0,
        "case_id": "case_01H",
    }
    base.update(overrides)
    return ScenarioJob(**base)  # type: ignore[arg-type]


def test_id_prefix_and_defaults() -> None:
    job = _job()
    assert job.id.startswith("scj_")
    assert job.status == ScenarioJobStatus.PENDING
    assert job.attempt == 0
    assert not job.is_terminal


def test_claim_then_run_then_succeed() -> None:
    job = _job()
    until = utc_now() + timedelta(seconds=300)
    job.mark_claimed(worker_id="w1", lease_until=until)
    assert job.status == ScenarioJobStatus.CLAIMED
    assert job.worker_id == "w1"

    started = utc_now()
    job.mark_running(worker_id="w1", lease_until=until, started_at=started)
    assert job.status == ScenarioJobStatus.RUNNING
    assert job.started_at == started

    when = utc_now()
    job.mark_succeeded(when)
    assert job.status == ScenarioJobStatus.SUCCEEDED
    assert job.is_terminal
    assert job.finished_at == when
    assert job.worker_id is None and job.lease_until is None and job.error is None


def test_started_at_is_sticky_across_running() -> None:
    job = _job()
    first = utc_now()
    job.mark_running(worker_id="w1", lease_until=utc_now(), started_at=first)
    later = utc_now() + timedelta(seconds=5)
    job.mark_running(worker_id="w2", lease_until=utc_now(), started_at=later)
    assert job.started_at == first  # retained from the first run


def test_retry_returns_to_pending_frontier() -> None:
    job = _job(attempt=1, max_attempts=3, worker_id="w1")
    outcome = job.mark_failed_or_dead_lettered(error="boom", when=utc_now())
    assert outcome == "retry"
    # Back to PENDING so the SKIP-LOCKED claim can pick it up again.
    assert job.status == ScenarioJobStatus.PENDING
    assert not job.is_terminal
    assert job.error == "boom"
    assert job.worker_id is None and job.lease_until is None


def test_dead_letters_when_attempts_exhausted() -> None:
    job = _job(attempt=3, max_attempts=3, worker_id="w1")
    when = utc_now()
    outcome = job.mark_failed_or_dead_lettered(error="boom", when=when)
    assert outcome == "dead"
    assert job.status == ScenarioJobStatus.DEAD_LETTERED
    assert job.is_terminal
    assert job.finished_at == when


def test_cancel_is_terminal() -> None:
    job = _job()
    job.mark_cancelled(utc_now())
    assert job.status == ScenarioJobStatus.CANCELLED
    assert job.is_terminal
