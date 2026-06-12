"""Continuous-integration gates for selfevals experiments.

The regression gate (`regression.py`) is the pure math a CI step consumes:
given a versioned baseline and the current iteration's metrics, it decides
whether quality regressed past a configurable threshold and returns a
machine-readable verdict the CLI turns into an exit code.
"""

from __future__ import annotations

from selfevals.ci.regression import (
    RegressionFinding,
    RegressionResult,
    RegressionThresholds,
    evaluate_regression,
)

__all__ = [
    "RegressionFinding",
    "RegressionResult",
    "RegressionThresholds",
    "evaluate_regression",
]
