"""CI gates built on top of selfevals runs.

`regression` holds the pure regression-gate math: given a baseline's metrics and
the current run's metrics, decide whether the agent regressed. The function is
agnostic of *what* the baseline is anchored to (dataset, experiment, anything) —
the caller loads the baseline and passes the numbers in.
"""

from selfevals.ci.regression import (
    BaselineMetrics,
    RegressionFinding,
    RegressionResult,
    RegressionThresholds,
    evaluate_regression,
)

__all__ = [
    "BaselineMetrics",
    "RegressionFinding",
    "RegressionResult",
    "RegressionThresholds",
    "evaluate_regression",
]
