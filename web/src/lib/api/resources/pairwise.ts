import { qs, request } from '$lib/api/http';
import type {
  IngestPairwiseVerdict,
  PairwiseCalibration,
  PairwiseVerdict,
  RunTournamentRequest,
  Tournament
} from '$lib/api/types';

export const pairwiseApi = {
  /** Pairwise verdicts for an experiment, optionally filtered by case or
   * judge kind (LLM vs human). */
  listVerdicts: (
    workspaceId: string,
    experimentId: string,
    opts: { caseId?: string; judgeKind?: 'llm' | 'human' } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<PairwiseVerdict[]>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/verdicts${qs({
        case_id: opts.caseId,
        judge_kind: opts.judgeKind
      })}`,
      { fetch }
    ),

  /** LLM-vs-human agreement report (overall + per rubric version). */
  verdictCalibration: (
    workspaceId: string,
    experimentId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<PairwiseCalibration>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/verdicts/calibration`,
      { fetch }
    ),

  /** Ingest a batch of LLM/human verdicts. Returns the count persisted. */
  ingestVerdicts: (
    workspaceId: string,
    experimentId: string,
    verdicts: IngestPairwiseVerdict[],
    fetch?: typeof globalThis.fetch
  ) =>
    request<{ ingested: number }>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/verdicts/ingest`,
      { method: 'POST', json: { verdicts }, fetch }
    ),

  /** Past tournaments for an experiment, newest first. */
  listTournaments: (workspaceId: string, experimentId: string, fetch?: typeof globalThis.fetch) =>
    request<Tournament[]>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/tournaments`,
      { fetch }
    ),

  /** Run a pairwise tournament: rank N candidates via Elo / Bradley-Terry. */
  runTournament: (
    workspaceId: string,
    experimentId: string,
    body: RunTournamentRequest,
    fetch?: typeof globalThis.fetch
  ) =>
    request<Tournament>(`/api/workspaces/${workspaceId}/experiments/${experimentId}/tournaments`, {
      method: 'POST',
      json: body,
      fetch
    })
};
