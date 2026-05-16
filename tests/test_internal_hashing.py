from bootstrap._internal.hashing import bytes_hash, content_hash


def test_content_hash_is_order_independent() -> None:
    a = {"x": 1, "y": [1, 2, 3], "nested": {"a": 1, "b": 2}}
    b = {"nested": {"b": 2, "a": 1}, "y": [1, 2, 3], "x": 1}
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_on_value_change() -> None:
    h1 = content_hash({"x": 1})
    h2 = content_hash({"x": 2})
    assert h1 != h2


def test_content_hash_prefix() -> None:
    assert content_hash({"x": 1}).startswith("sha256:")


def test_bytes_hash_prefix_and_stable() -> None:
    h = bytes_hash(b"hello")
    assert h.startswith("sha256:")
    assert h == bytes_hash(b"hello")
