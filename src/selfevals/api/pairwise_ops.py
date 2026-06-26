"""HTTP-facing pairwise verdict operations.

Wraps `runner/pairwise_ops.py` for the API: it builds `PairwiseVerdict` entities
from request bodies (assigning server-side `id`/`workspace_id`), persists them,
lists them, and shapes the calibration report. The storage + calibration math
stays in `runner.pairwise_ops` (the migration cut-point); this module only
projects to/from the HTTP envelopes.
"""

from __future__ import annotations

import asyncio

from selfevals.api.schemas import (
    IngestPairwiseVerdict,
    PairRefBody,
    PairwiseCalibrationCellResponse,
    PairwiseCalibrationResponse,
    PairwiseIngestSummaryResponse,
    PairwiseVerdictResponse,
    RankingRowResponse,
    RunTournamentRequest,
    TournamentResponse,
)
from selfevals.runner.pairwise_ops import (
    PairwiseOpError,
    compute_calibration,
    ingest_verdicts,
    list_verdicts,
)
from selfevals.schemas.pairwise_verdict import PairRef, PairwiseVerdict
from selfevals.schemas.tournament import Tournament
from selfevals.storage.interface import ListFilter, StorageInterface


class PairwiseApiError(Exception):
    """A pairwise API operation could not be completed (bad input)."""


def _to_pair_ref(body: PairRefBody) -> PairRef:
    return PairRef(
        kind=body.kind,  # type: ignore[arg-type]
        trace_id=body.trace_id,
        case_id=body.case_id,
        iteration_id=body.iteration_id,
        content_snapshot=body.content_snapshot,
    )


def _to_entity(
    body: IngestPairwiseVerdict, *, workspace_id: str, experiment_id: str
) -> PairwiseVerdict:
    try:
        return PairwiseVerdict(
            id=PairwiseVerdict.make_id(),
            workspace_id=workspace_id,
            a_ref=_to_pair_ref(body.a_ref),
            b_ref=_to_pair_ref(body.b_ref),
            preferred=body.preferred,  # type: ignore[arg-type]
            margin=body.margin,
            rationale=body.rationale,
            judge_kind=body.judge_kind,  # type: ignore[arg-type]
            judge_id=body.judge_id,
            judge_model=body.judge_model,
            rubric_version=body.rubric_version,
            position=body.position,  # type: ignore[arg-type]
            experiment_id=experiment_id,
            case_id=body.case_id,
            dataset_id=body.dataset_id,
        )
    except ValueError as exc:  # pydantic validation (bad literal, margin range…)
        raise PairwiseApiError(str(exc)) from exc


def _view(verdict: PairwiseVerdict) -> PairwiseVerdictResponse:
    return PairwiseVerdictResponse(
        id=verdict.id,
        a_ref=PairRefBody(**verdict.a_ref.model_dump()),
        b_ref=PairRefBody(**verdict.b_ref.model_dump()),
        preferred=verdict.preferred,
        margin=verdict.margin,
        rationale=verdict.rationale,
        judge_kind=verdict.judge_kind,
        judge_id=verdict.judge_id,
        rubric_version=verdict.rubric_version,
        experiment_id=verdict.experiment_id,
        case_id=verdict.case_id,
        created_at=verdict.created_at,
    )


def ingest_pairwise_verdicts(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    verdicts: list[IngestPairwiseVerdict],
) -> PairwiseIngestSummaryResponse:
    entities = [
        _to_entity(b, workspace_id=workspace_id, experiment_id=experiment_id) for b in verdicts
    ]
    with storage.open(workspace_id) as scope:
        try:
            ingest_verdicts(scope, entities)
        except PairwiseOpError as exc:
            raise PairwiseApiError(str(exc)) from exc
    return PairwiseIngestSummaryResponse(ingested=len(entities))


def list_pairwise_verdicts(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    case_id: str | None = None,
    judge_kind: str | None = None,
) -> list[PairwiseVerdictResponse]:
    with storage.open(workspace_id) as scope:
        verdicts = list_verdicts(
            scope, experiment_id=experiment_id, case_id=case_id, judge_kind=judge_kind
        )
    return [_view(v) for v in verdicts]


def get_pairwise_calibration(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
) -> PairwiseCalibrationResponse:
    with storage.open(workspace_id) as scope:
        report = compute_calibration(scope, experiment_id=experiment_id)
    return PairwiseCalibrationResponse(
        compared_pairs=report.compared_pairs,
        agreements=report.agreements,
        disagreements=report.disagreements,
        agreement_rate=report.agreement_rate,
        by_rubric_version=[
            PairwiseCalibrationCellResponse(
                rubric_version=c.rubric_version,
                compared_pairs=c.compared_pairs,
                agreements=c.agreements,
                disagreements=c.disagreements,
                agreement_rate=c.agreement_rate,
            )
            for c in report.by_rubric_version
        ],
    )


def _tournament_view(tournament: Tournament) -> TournamentResponse:
    return TournamentResponse(
        id=tournament.id,
        experiment_id=tournament.experiment_id,
        strategy=tournament.strategy,
        method=tournament.method,
        candidate_ids=list(tournament.candidate_ids),
        baseline_id=tournament.baseline_id,
        n_comparisons=tournament.n_comparisons,
        swap_and_average=tournament.swap_and_average,
        ranking=[
            RankingRowResponse(
                candidate_id=r.candidate_id,
                rank=r.rank,
                score=r.score,
                wins=r.wins,
                losses=r.losses,
                ties=r.ties,
                n_comparisons=r.n_comparisons,
            )
            for r in tournament.ranking
        ],
        created_at=tournament.created_at,
    )


def run_pairwise_tournament(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    request: RunTournamentRequest,
) -> TournamentResponse:
    """Resolve the judge callable, run the tournament, persist + return ranking."""
    from selfevals._errors import SelfEvalsUserError
    from selfevals.repo.loader import (
        AgentEntrypoint,
        LoaderError,
        resolve_agent_callable,
    )
    from selfevals.runner.launch import _wrap_user_callable
    from selfevals.runner.pairwise_tournament import Candidate, run_tournament

    raw = request.judge_entrypoint
    if ":" not in raw:
        raise PairwiseApiError(f"judge_entrypoint must be 'module:fn', got {raw!r}")
    module, _, attribute = raw.partition(":")
    entry = AgentEntrypoint(raw=raw, module=module, attribute=attribute)
    try:
        judge_callable = resolve_agent_callable(entry)
    except (LoaderError, SelfEvalsUserError) as exc:
        raise PairwiseApiError(str(exc)) from exc
    judge_adapter = _wrap_user_callable(judge_callable, entry)

    candidates = [
        Candidate(id=c.id, output_text=c.output_text, trace_id=c.trace_id)
        for c in request.candidates
    ]

    with storage.open(workspace_id) as scope:
        try:
            result = asyncio.run(
                run_tournament(
                    scope,
                    candidates=candidates,
                    judge_adapter=judge_adapter,
                    rubric=request.rubric,
                    case_input=request.case_input,
                    strategy=request.strategy,
                    method=request.method,  # type: ignore[arg-type]
                    baseline_id=request.baseline_id,
                    comparisons_per_candidate=request.comparisons_per_candidate,
                    swiss_rounds=request.swiss_rounds,
                    swap_and_average=request.swap_and_average,
                    experiment_id=experiment_id,
                )
            )
        except ValueError as exc:  # bad strategy/method/baseline
            raise PairwiseApiError(str(exc)) from exc
        return _tournament_view(result.tournament)


def list_pairwise_tournaments(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
) -> list[TournamentResponse]:
    with storage.open(workspace_id) as scope:
        entities = scope.list_entities(
            Tournament, ListFilter(where={"experiment_id": experiment_id})
        )
    return [_tournament_view(t) for t in entities if isinstance(t, Tournament)]
