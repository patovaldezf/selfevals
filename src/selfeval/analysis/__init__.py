"""Error-analysis handshake: build bundles, ingest results.

selfeval owns the data, the contract, the persistence, and the verification.
The intelligence (open/axial coding) lives in an external coding agent that
honours the `AnalysisBundle` / `AnalysisResult` contract defined in `schemas`.
selfeval never calls an LLM here. See docs/spec/error_analysis_design.md.
"""

from __future__ import annotations

from selfeval.analysis.bundle import build_bundle
from selfeval.analysis.ingest import IngestSummary, ingest_result
from selfeval.analysis.schemas import AnalysisBundle, AnalysisResult
from selfeval.analysis.staging import AnalysisStagingRecord

__all__ = [
    "AnalysisBundle",
    "AnalysisResult",
    "AnalysisStagingRecord",
    "IngestSummary",
    "build_bundle",
    "ingest_result",
]
