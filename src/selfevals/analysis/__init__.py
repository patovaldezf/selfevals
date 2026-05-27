"""Error-analysis handshake: build bundles, ingest results.

selfevals owns the data, the contract, the persistence, and the verification.
The intelligence (open/axial coding) lives in an external coding agent that
honours the `AnalysisBundle` / `AnalysisResult` contract defined in `schemas`.
selfevals never calls an LLM here. See docs/spec/error_analysis_design.md.
"""

from __future__ import annotations

from selfevals.analysis.bundle import build_bundle
from selfevals.analysis.ingest import IngestSummary, ingest_result
from selfevals.analysis.schemas import AnalysisBundle, AnalysisResult
from selfevals.analysis.staging import AnalysisStagingRecord

__all__ = [
    "AnalysisBundle",
    "AnalysisResult",
    "AnalysisStagingRecord",
    "IngestSummary",
    "build_bundle",
    "ingest_result",
]
