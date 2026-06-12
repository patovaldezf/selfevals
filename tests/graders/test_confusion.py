from __future__ import annotations

import pytest

from selfevals.graders._confusion import ConfusionReport, confusion_from_pairs, f1_score


def test_empty_pairs_produce_none_report() -> None:
    rep = confusion_from_pairs([])
    assert rep.n_pairs == 0
    assert rep.macro_f1 is None
    assert rep.labels == []
    assert rep.confusion == {}


def test_perfect_diagonal() -> None:
    pairs = [("a", "a"), ("b", "b"), ("a", "a")]
    rep = confusion_from_pairs(pairs)
    assert rep.n_pairs == 3
    assert rep.accuracy == 1.0
    assert rep.confusion == {("a", "a"): 2, ("b", "b"): 1}
    assert rep.per_label_precision == {"a": 1.0, "b": 1.0}
    assert rep.per_label_recall == {"a": 1.0, "b": 1.0}
    assert rep.macro_f1 == 1.0


def test_off_diagonal_directional() -> None:
    # 'a' is always predicted 'b' (a→b), 'b' predicted correctly.
    pairs = [("a", "b"), ("a", "b"), ("b", "b")]
    rep = confusion_from_pairs(pairs)
    assert rep.confusion[("a", "b")] == 2
    assert rep.confusion[("b", "b")] == 1
    # class 'a': TP=0, actual=2, predicted=0 → precision None (no predicted 'a'),
    # recall 0/2 = 0.
    assert rep.per_label_precision["a"] is None
    assert rep.per_label_recall["a"] == 0.0
    assert rep.per_label_f1["a"] is None
    # class 'b': TP=1, predicted=3, actual=1 → precision 1/3, recall 1.0.
    assert rep.per_label_precision["b"] == pytest.approx(1 / 3)
    assert rep.per_label_recall["b"] == 1.0
    assert rep.accuracy == pytest.approx(1 / 3)


def test_imbalance_guard_excludes_none_f1_from_macro() -> None:
    # 'a' has no predicted instances (precision None → f1 None), excluded from macro.
    pairs = [("a", "b"), ("b", "b")]
    rep = confusion_from_pairs(pairs)
    assert rep.per_label_f1["a"] is None
    # only 'b' contributes a real f1 (P=1/2, R=1 → 2/3), so macro == that.
    assert rep.macro_f1 == pytest.approx(2 / 3)


def test_f1_score_undefined_when_either_none_or_both_zero() -> None:
    assert f1_score(None, 1.0) is None
    assert f1_score(1.0, None) is None
    assert f1_score(0.0, 0.0) is None
    assert f1_score(1.0, 1.0) == 1.0
    assert f1_score(0.5, 0.5) == pytest.approx(0.5)


def test_to_dict_and_from_dict_round_trip() -> None:
    pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("c", "a")]
    rep = confusion_from_pairs(pairs)
    data = rep.to_dict()
    # The matrix is nested {expected: {predicted: count}} — JSON-serializable.
    assert data["matrix"] == {
        "a": {"a": 1, "b": 1},
        "b": {"b": 1},
        "c": {"a": 1},
    }
    rebuilt = ConfusionReport.from_dict(data)
    assert rebuilt.confusion == rep.confusion
    assert rebuilt.labels == rep.labels
    assert rebuilt.per_label_f1 == rep.per_label_f1
    assert rebuilt.macro_f1 == rep.macro_f1
    assert rebuilt.accuracy == rep.accuracy


def test_to_nested_only_observed_cells() -> None:
    rep = confusion_from_pairs([("a", "b")])
    nested = rep.to_nested()
    assert nested == {"a": {"b": 1}}
    # 'b'→'a', 'a'→'a' etc. are absent (count 0), not zero-filled.
    assert "a" not in nested.get("b", {})
