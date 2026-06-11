from __future__ import annotations

import pytest

from selfevals.graders._select import select, select_str_list, validate_path


class TestSelect:
    def test_root_for_empty_path(self) -> None:
        root = {"a": 1}
        assert select(root, "") is root
        assert select(root, ".") is root

    def test_simple_key(self) -> None:
        assert select({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        assert select({"a": {"b": {"c": 7}}}, "a.b.c") == 7

    def test_list_as_is(self) -> None:
        assert select({"xs": [1, 2, 3]}, "xs[]") == [1, 2, 3]

    def test_projection_over_list(self) -> None:
        data = {"items": [{"k": "x"}, {"k": "y"}]}
        assert select(data, "items[].k") == ["x", "y"]

    def test_projection_skips_non_dict_and_missing(self) -> None:
        data = {"items": [{"k": "x"}, {"other": 1}, 5, {"k": "z"}]}
        assert select(data, "items[].k") == ["x", "z"]

    def test_projection_nested_into_then_key(self) -> None:
        data = {"a": {"items": [{"id": "p1"}, {"id": "p2"}]}}
        assert select(data, "a.items[].id") == ["p1", "p2"]

    def test_missing_key_is_none(self) -> None:
        assert select({"a": 1}, "b") is None

    def test_nested_missing_is_none(self) -> None:
        assert select({"a": {"b": 1}}, "a.c.d") is None

    def test_none_root_is_none(self) -> None:
        assert select(None, "a") is None

    def test_scalar_where_dict_expected_is_none(self) -> None:
        assert select({"a": 5}, "a.b") is None

    def test_projection_on_non_list_is_none(self) -> None:
        assert select({"a": {"b": 1}}, "a[]") is None

    def test_present_but_empty_list(self) -> None:
        # distinct from None: the key exists and is an empty list
        assert select({"xs": []}, "xs[]") == []


class TestSelectStrList:
    def test_accepts_list_of_str(self) -> None:
        assert select_str_list({"detected": ["a", "b"]}, "detected") == ["a", "b"]

    def test_projection_yields_str_list(self) -> None:
        data = {"items": [{"id": "p1"}, {"id": "p2"}]}
        assert select_str_list(data, "items[].id") == ["p1", "p2"]

    def test_rejects_non_list(self) -> None:
        assert select_str_list({"detected": "a"}, "detected") is None

    def test_rejects_mixed_types(self) -> None:
        assert select_str_list({"detected": ["a", 2]}, "detected") is None

    def test_missing_is_none(self) -> None:
        assert select_str_list({}, "detected") is None

    def test_empty_list_is_empty_not_none(self) -> None:
        assert select_str_list({"detected": []}, "detected") == []


class TestValidatePath:
    @pytest.mark.parametrize("path", ["", ".", "foo", "foo.bar", "foo[]", "foo[].bar"])
    def test_valid_paths_accepted(self, path: str) -> None:
        validate_path(path)  # no raise

    @pytest.mark.parametrize("path", ["a..b", "a.", ".a", "a]b", "a[].", "[]x", "a[b]"])
    def test_malformed_paths_rejected(self, path: str) -> None:
        with pytest.raises(ValueError):
            validate_path(path)
