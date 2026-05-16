from bootstrap._internal.ids import is_prefixed_id, is_ulid, new_prefixed_id, new_ulid


def test_new_ulid_is_26_chars_crockford() -> None:
    ulid = new_ulid()
    assert len(ulid) == 26
    assert is_ulid(ulid)


def test_ulids_are_unique() -> None:
    seen = {new_ulid() for _ in range(1000)}
    assert len(seen) == 1000


def test_ulids_are_lexicographically_sortable_by_time() -> None:
    earlier = new_ulid()
    later = new_ulid()
    assert earlier < later or earlier[:10] == later[:10]


def test_new_prefixed_id() -> None:
    value = new_prefixed_id("ws")
    assert value.startswith("ws_")
    assert is_prefixed_id(value, prefix="ws")
    assert not is_prefixed_id(value, prefix="ag")


def test_invalid_prefix_rejected() -> None:
    import pytest

    for bad in ["", "x", "TOO_LONG", "ABC", "a1", "ñe"]:
        with pytest.raises(ValueError):
            new_prefixed_id(bad)


def test_is_ulid_rejects_lowercase_and_invalid_chars() -> None:
    assert not is_ulid("01h" + "0" * 23)  # lowercase h
    assert not is_ulid("I" + "0" * 25)  # Crockford excludes I
    assert not is_ulid("L" + "0" * 25)  # Crockford excludes L
    assert not is_ulid("O" + "0" * 25)  # Crockford excludes O
    assert not is_ulid("U" + "0" * 25)  # Crockford excludes U
    assert not is_ulid("0" * 25)  # too short
