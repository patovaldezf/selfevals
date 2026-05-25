from __future__ import annotations

from bootstrap.graders.base import GradeLabel
from bootstrap.graders.calibration import (
    HumanLabel,
    PredictedLabel,
    compute_classification_metrics,
)


def test_empty_inputs_produce_zero_pair_report() -> None:
    rep = compute_classification_metrics([], [])
    assert rep.n_pairs == 0
    assert rep.precision is None
    assert rep.recall is None
    assert rep.f1 is None


def test_perfect_predictions() -> None:
    cases = [f"ec_{i}" for i in range(5)]
    preds = [PredictedLabel(case_id=c, label=GradeLabel.PASS) for c in cases]
    humans = [HumanLabel(case_id=c, label=GradeLabel.PASS) for c in cases]
    rep = compute_classification_metrics(preds, humans)
    assert rep.n_pairs == 5
    assert rep.precision == 1.0
    assert rep.recall == 1.0
    assert rep.f1 == 1.0
    assert rep.accuracy == 1.0


def test_split_labels_compute_metrics() -> None:
    preds = [
        PredictedLabel("ec_1", GradeLabel.PASS),
        PredictedLabel("ec_2", GradeLabel.PASS),
        PredictedLabel("ec_3", GradeLabel.FAIL),
        PredictedLabel("ec_4", GradeLabel.PASS),
    ]
    humans = [
        HumanLabel("ec_1", GradeLabel.PASS),
        HumanLabel("ec_2", GradeLabel.FAIL),
        HumanLabel("ec_3", GradeLabel.FAIL),
        HumanLabel("ec_4", GradeLabel.PASS),
    ]
    rep = compute_classification_metrics(preds, humans)
    # pass class: TP=2, FP=1, FN=0 → precision 2/3, recall 1
    assert rep.precision == 2 / 3
    assert rep.recall == 1.0
    assert rep.accuracy == 3 / 4


def test_high_risk_false_negatives_counted() -> None:
    preds = [
        PredictedLabel("ec_1", GradeLabel.FAIL),  # missed a high-risk pass
        PredictedLabel("ec_2", GradeLabel.PASS),
    ]
    humans = [
        HumanLabel("ec_1", GradeLabel.PASS, high_risk=True),
        HumanLabel("ec_2", GradeLabel.PASS, high_risk=False),
    ]
    rep = compute_classification_metrics(preds, humans)
    assert rep.high_risk_false_negatives == 1


def test_unpaired_cases_dropped() -> None:
    preds = [
        PredictedLabel("ec_only_predicted", GradeLabel.PASS),
        PredictedLabel("ec_both", GradeLabel.FAIL),
    ]
    humans = [
        HumanLabel("ec_only_human", GradeLabel.PASS),
        HumanLabel("ec_both", GradeLabel.FAIL),
    ]
    rep = compute_classification_metrics(preds, humans)
    assert rep.n_pairs == 1
    assert rep.accuracy == 1.0


def test_macro_f1_across_observed_labels() -> None:
    # 3 classes, all perfectly classified.
    preds = [
        PredictedLabel(f"ec_{i}", label)
        for i, label in enumerate([GradeLabel.PASS, GradeLabel.FAIL, GradeLabel.PARTIAL])
    ]
    humans = [
        HumanLabel(f"ec_{i}", label)
        for i, label in enumerate([GradeLabel.PASS, GradeLabel.FAIL, GradeLabel.PARTIAL])
    ]
    rep = compute_classification_metrics(preds, humans)
    assert rep.macro_f1 == 1.0


def test_zero_predicted_positives_returns_none_precision() -> None:
    # Predictor never says "pass"; precision is undefined for that class.
    preds = [PredictedLabel(f"ec_{i}", GradeLabel.FAIL) for i in range(3)]
    humans = [HumanLabel(f"ec_{i}", GradeLabel.PASS) for i in range(3)]
    rep = compute_classification_metrics(preds, humans)
    assert rep.precision is None  # no predicted positives
    assert rep.recall == 0.0
