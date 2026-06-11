"""A tiny path selector over JSON-like structures (the funnel/set_match lens).

A grader frontier reads *one slice* of an agent's `structured_output`: the
detected set, a resolved id, a list of products. Hard-coding which key a grader
reads (as `set_match` did with `"detected"`) couples the grader to one contract
shape. This module makes that slice **declarative**: a level/grader names a
short path, and `select` walks it.

The grammar is deliberately minimal — three step kinds, no positional indexing:

    ""            → the root unchanged
    "foo"         → dict key lookup
    "foo.bar"     → nested key lookup
    "foo[]"       → the list at `foo` (as-is)
    "foo[].bar"   → project `bar` over each dict element of list `foo`

Positional indexing (`foo[0]`) is intentionally absent: indexing *into* a list
to compare one element is a matching concern (the funnel's `by_index` match),
not a lensing concern. Keeping the selector a pure lens leaves no overlap with
the match taxonomy.

Resolution never raises: any step that lands on `None`, a missing key, or a
type that cannot be walked yields `None` for the whole expression. This lets a
caller distinguish "absent or malformed" (`None`) from "present but empty"
(`[]`). Syntax errors in the path string are a *construction-time* concern —
`validate_path` raises `ValueError` so a YAML typo surfaces at load, not at
grade time.
"""

from __future__ import annotations

import re

# A path is a dot-separated sequence of segments. Each segment is a bare key,
# optionally suffixed with `[]` to mean "the list at this key" / "project over
# this list". The empty string (or ".") selects the root.
_SEGMENT_RE = re.compile(r"^[^.\[\]]+(\[\])?$")


def _split(path: str) -> list[tuple[str, bool]]:
    """Parse `path` into `(key, is_projection)` steps. Assumes a valid path.

    `validate_path` is the gate that guarantees validity; this helper trusts it.
    """
    stripped = path.strip()
    if stripped in ("", "."):
        return []
    steps: list[tuple[str, bool]] = []
    for raw in stripped.split("."):
        if raw.endswith("[]"):
            steps.append((raw[:-2], True))
        else:
            steps.append((raw, False))
    return steps


def validate_path(path: str) -> None:
    """Raise `ValueError` if `path` is not a well-formed selector expression.

    Called when a consumer is *constructed* (e.g. a `set_match`/`funnel` grader
    built from YAML), so malformed paths fail at load time with a clear message
    rather than silently returning `None` at grade time.
    """
    stripped = path.strip()
    if stripped in ("", "."):
        return
    for segment in stripped.split("."):
        if not segment:
            raise ValueError(
                f"path {path!r} has an empty segment (a stray or leading/trailing '.')"
            )
        if not _SEGMENT_RE.match(segment):
            raise ValueError(
                f"path {path!r}: segment {segment!r} is malformed; a segment is a bare "
                f"key optionally suffixed with '[]' (e.g. 'items' or 'items[]')"
            )


def select(root: object, path: str) -> object | None:
    """Walk `path` over `root`, returning the selected value or `None`.

    Never raises on data shape: a missing key, a `None`, or a wrong-typed step
    collapses the whole expression to `None`. `validate_path` should have been
    called on `path` already (at construction); a malformed path here is a
    programmer error, not a data error.

    A `[]` step turns the walk into *projection mode*: once we are iterating a
    list, every subsequent plain-key step maps that key over the list elements,
    so `"foo[].bar"` returns the `bar` of each element of `foo`. Elements that
    are not dicts, lack the key, or whose value is `None` are dropped from the
    projection — so `candidates[].id` over `[{"id":"a"},{"id":None},{"id":"b"}]`
    yields `["a","b"]`. This is intended: a null/absent field is not a value, and
    callers extracting an id set (set_match) want only the real ids.
    """
    current: object | None = root
    projecting = False
    for key, is_projection in _split(path):
        if projecting:
            # `current` is a list; map this step over each dict element.
            assert isinstance(current, list)
            mapped: list[object] = []
            for element in current:
                if not isinstance(element, dict):
                    continue
                value = element.get(key) if key else element
                if value is not None:
                    mapped.append(value)
            current = mapped
            # A `[]` on a projection step (`foo[].bar[]`) flattens one level.
            if is_projection:
                flat: list[object] = []
                for item in current:
                    if isinstance(item, list):
                        flat.extend(item)
                current = flat
            continue
        if not is_projection:
            current = current.get(key) if isinstance(current, dict) else None
            continue
        # Entering projection mode: descend into the list at `key` (or `current`
        # itself when `key` is empty, i.e. a bare `[]`).
        container = current.get(key) if (key and isinstance(current, dict)) else current
        if not isinstance(container, list):
            return None
        current = container
        projecting = True
    return current


def select_str_list(root: object, path: str) -> list[str] | None:
    """Select a `list[str]` at `path`, or `None` if it is not exactly that.

    The strict contract `set_match` needs: the selected value must be a `list`
    whose every element is a `str`. A non-list, or any non-`str` element, yields
    `None` (the "no usable detected set" signal). A projection (`foo[].bar`)
    that yields a list of strings satisfies this.
    """
    value = select(root, path)
    if not isinstance(value, list):
        return None
    if not all(isinstance(x, str) for x in value):
        return None
    return [x for x in value if isinstance(x, str)]
