"""Parse and serialize the `graders:` block into a list of `GraderSpec`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from selfevals.repo.loader.models import AgentEntrypoint, GraderSpec, LoaderError
from selfevals.schemas.enums import SpanKind

_SUPPORTED_GRADER_TYPES = {
    "deterministic",
    "llm_judge",
    "judge_panel",
    "pairwise",
    "set_match",
    "funnel",
    "confusion",
}
_PAIRWISE_COMPARE_AGAINST = {"reference"}
_SET_MATCH_GATINGS = {"completeness", "precision", "recall", "f1"}
_CONSENSUS_RULES = {"majority", "unanimous", "weighted"}
_FUNNEL_MATCH_KINDS = {
    "exists",
    "equals",
    "set_match",
    "by_key",
    "by_index",
    "tool_called",
    "span_exists",
}
_SPAN_KINDS = {k.value for k in SpanKind}


def build_grader_specs(spec_path: Path, raw: dict[str, Any]) -> list[GraderSpec]:
    section = raw.get("graders")
    if section is None:
        return []
    if not isinstance(section, list):
        raise LoaderError(f"{spec_path}: `graders:` must be a list of mappings")
    specs: list[GraderSpec] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(section):
        if not isinstance(entry, dict):
            raise LoaderError(
                f"{spec_path}: graders[{i}] must be a mapping, got {type(entry).__name__}"
            )
        type_ = entry.get("type")
        if not isinstance(type_, str) or type_ not in _SUPPORTED_GRADER_TYPES:
            raise LoaderError(
                f"{spec_path}: graders[{i}].type must be one of "
                f"{sorted(_SUPPORTED_GRADER_TYPES)}; got {type_!r}"
            )
        name = entry.get("name") or type_
        if not isinstance(name, str) or not name:
            raise LoaderError(f"{spec_path}: graders[{i}].name must be a non-empty string")
        if name in seen_names:
            raise LoaderError(f"{spec_path}: graders[{i}].name {name!r} is duplicated")
        seen_names.add(name)

        rubric: str | None = None
        judge_entry: AgentEntrypoint | None = None
        n_judges: int | None = None
        consensus: str | None = None
        params: dict[str, Any] = {}

        if type_ in ("llm_judge", "judge_panel", "pairwise"):
            rubric_raw = entry.get("rubric")
            if not isinstance(rubric_raw, str) or not rubric_raw.strip():
                raise LoaderError(
                    f"{spec_path}: graders[{i}] ({type_}) requires a non-empty `rubric`"
                )
            rubric = rubric_raw
            judge_entry = _parse_judge_entrypoint(spec_path, i, entry)

        if type_ == "judge_panel":
            n_judges = _parse_n_judges(spec_path, i, entry)
            consensus = _parse_consensus(spec_path, i, entry)

        if type_ == "pairwise":
            params = _parse_pairwise_params(spec_path, i, entry)

        if type_ == "set_match":
            params = _parse_set_match_params(spec_path, i, entry)

        if type_ == "funnel":
            params = _parse_funnel_params(spec_path, i, entry)

        if type_ == "confusion":
            params = _parse_confusion_params(spec_path, i, entry)

        specs.append(
            GraderSpec(
                type=type_,
                name=name,
                rubric=rubric,
                judge_entrypoint=judge_entry,
                n_judges=n_judges,
                consensus=consensus,
                params=params,
            )
        )
    return specs


def _parse_judge_entrypoint(
    spec_path: Path, i: int, entry: dict[str, Any]
) -> AgentEntrypoint | None:
    judge_raw = entry.get("judge_entrypoint")
    if judge_raw is None:
        return None
    if not isinstance(judge_raw, str) or ":" not in judge_raw:
        raise LoaderError(
            f"{spec_path}: graders[{i}].judge_entrypoint must be "
            f"'module:callable', got {judge_raw!r}"
        )
    module, _, attribute = judge_raw.partition(":")
    return AgentEntrypoint(raw=judge_raw, module=module, attribute=attribute)


def _parse_n_judges(spec_path: Path, i: int, entry: dict[str, Any]) -> int:
    raw = entry.get("n_judges", 3)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise LoaderError(
            f"{spec_path}: graders[{i}].n_judges must be an integer >= 1, got {raw!r}"
        )
    return raw


def _parse_consensus(spec_path: Path, i: int, entry: dict[str, Any]) -> str:
    raw = entry.get("consensus", "majority")
    if raw not in _CONSENSUS_RULES:
        raise LoaderError(
            f"{spec_path}: graders[{i}].consensus must be one of "
            f"{sorted(_CONSENSUS_RULES)}, got {raw!r}"
        )
    return cast(str, raw)


def _parse_set_match_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    gating = raw.get("gating")
    if gating is not None:
        if gating not in _SET_MATCH_GATINGS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.gating must be one of "
                f"{sorted(_SET_MATCH_GATINGS)}, got {gating!r}"
            )
        params["gating"] = gating
    threshold = raw.get("threshold")
    if threshold is not None:
        if not isinstance(threshold, int | float) or isinstance(threshold, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.threshold must be a number, got {threshold!r}"
            )
        if not 0.0 <= float(threshold) <= 1.0:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.threshold must be in [0, 1], got {threshold!r}"
            )
        params["threshold"] = float(threshold)
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.case_sensitive must be a boolean, "
                f"got {case_sensitive!r}"
            )
        params["case_sensitive"] = case_sensitive
    return params


def _parse_pairwise_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate a `pairwise` grader's params.

    `compare_against` selects what B is (MVP: only `reference`). `tie_is_pass`
    maps a tie verdict to PASS (default true). `swap_and_average` runs A/B and
    B/A and averages to neutralize the judge's position bias (default false).
    """
    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    compare_against = raw.get("compare_against")
    if compare_against is not None:
        if compare_against not in _PAIRWISE_COMPARE_AGAINST:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.compare_against must be one of "
                f"{sorted(_PAIRWISE_COMPARE_AGAINST)}, got {compare_against!r}"
            )
        params["compare_against"] = compare_against
    for flag in ("tie_is_pass", "swap_and_average"):
        value = raw.get(flag)
        if value is not None:
            if not isinstance(value, bool):
                raise LoaderError(
                    f"{spec_path}: graders[{i}].params.{flag} must be a boolean, got {value!r}"
                )
            params[flag] = value
    return params


def _parse_confusion_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate a `confusion` grader's params (`extract`/`expected_from`/`case_sensitive`).

    `extract` is the path selector into `structured_output` for the predicted
    class (default `"label"`); `expected_from`, when set, is the path into
    `Expected.structured_output` for the ground-truth class (default: read
    `Expected.outcome`). Paths are validated here so a YAML typo fails at load.
    """
    from selfevals.graders._select import validate_path

    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    extract = raw.get("extract")
    if extract is not None:
        if not isinstance(extract, str):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.extract must be a string, got {extract!r}"
            )
        try:
            validate_path(extract)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].params.extract: {exc}") from exc
        params["extract"] = extract
    expected_from = raw.get("expected_from")
    if expected_from is not None:
        if not isinstance(expected_from, str):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.expected_from must be a string, "
                f"got {expected_from!r}"
            )
        try:
            validate_path(expected_from)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].params.expected_from: {exc}") from exc
        params["expected_from"] = expected_from
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.case_sensitive must be a boolean, "
                f"got {case_sensitive!r}"
            )
        params["case_sensitive"] = case_sensitive
    return params


def _parse_funnel_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Parse a funnel's `params.levels` tree into validated nested dicts.

    Stored as plain JSON-friendly dicts (not built `_Level`s) because building
    a level's match — especially a nested `llm_judge` — needs adapter
    resolution, which lives in `runner.launch`, not here. The loader stays
    import-side-effect-free. `key` uniqueness is checked across the whole tree
    (the aggregator rolls up by key).
    """
    from selfevals.graders._select import validate_path

    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    levels_raw = raw.get("levels")
    if not isinstance(levels_raw, list) or not levels_raw:
        raise LoaderError(
            f"{spec_path}: graders[{i}] (funnel) requires a non-empty `params.levels` list"
        )
    seen_keys: set[str] = set()

    def _level(node: Any, path: str) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise LoaderError(f"{spec_path}: graders[{i}].levels{path} must be a mapping")
        key = node.get("key")
        if not isinstance(key, str) or not key:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels{path}.key must be a non-empty string"
            )
        if key in seen_keys:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels key {key!r} is duplicated (keys must be "
                f"unique across the funnel tree)"
            )
        seen_keys.add(key)

        extract = node.get("extract", "")
        if not isinstance(extract, str):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].extract must be a string")
        try:
            validate_path(extract)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].extract: {exc}") from exc

        gate = node.get("gate", False)
        if not isinstance(gate, bool):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].gate must be a boolean")
        feeds = node.get("feeds_extract", False)
        if not isinstance(feeds, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].feeds_extract must be a boolean"
            )
        failure_mode = node.get("failure_mode")
        if failure_mode is not None and not (isinstance(failure_mode, str) and failure_mode):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].failure_mode must be a non-empty string"
            )

        match = _funnel_match(spec_path, i, key, node.get("match"))

        children_raw = node.get("children", [])
        if not isinstance(children_raw, list):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].children must be a list")
        children = [_level(c, f"{path}[{key}].children[{j}]") for j, c in enumerate(children_raw)]

        out: dict[str, Any] = {
            "key": key,
            "extract": extract,
            "gate": gate,
            "feeds_extract": feeds,
            "match": match,
            "children": children,
        }
        if failure_mode is not None:
            out["failure_mode"] = failure_mode
        return out

    levels = [_level(node, f"[{j}]") for j, node in enumerate(levels_raw)]
    return {"levels": levels}


def _funnel_match(spec_path: Path, i: int, key: str, match_raw: Any) -> dict[str, Any]:
    """Validate one level's `match`: exactly one of `kind` / `grader`."""
    if not isinstance(match_raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].match must be a mapping")
    has_kind = "kind" in match_raw
    has_grader = "grader" in match_raw
    if has_kind == has_grader:
        raise LoaderError(
            f"{spec_path}: graders[{i}].levels[{key}].match must have exactly one of "
            f"`kind` (builtin) or `grader` (a declared grader name)"
        )
    if has_grader:
        ref = match_raw.get("grader")
        if not isinstance(ref, str) or not ref:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.grader must be a non-empty string"
            )
        # Existence deferred to launch.py (registry resolution gives the friendly
        # unknown-grader error and avoids a declaration-ordering constraint).
        return {"grader": ref}

    kind = match_raw.get("kind")
    if kind not in _FUNNEL_MATCH_KINDS:
        raise LoaderError(
            f"{spec_path}: graders[{i}].levels[{key}].match.kind must be one of "
            f"{sorted(_FUNNEL_MATCH_KINDS)}; got {kind!r}"
        )
    out: dict[str, Any] = {"kind": kind}
    # Carry through kind-specific params, validated where it matters.
    if kind == "equals":
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (equals) requires `value`"
            )
        out["value"] = match_raw["value"]
    elif kind == "by_key":
        bkey = match_raw.get("key")
        if not isinstance(bkey, str) or not bkey:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_key) requires a string `key`"
            )
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_key) requires `value`"
            )
        out["key"] = bkey
        out["value"] = match_raw["value"]
    elif kind == "by_index":
        index = match_raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_index) requires an int `index`"
            )
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_index) requires `value`"
            )
        out["index"] = index
        out["value"] = match_raw["value"]
    elif kind == "tool_called":
        tool = match_raw.get("tool")
        if not isinstance(tool, str) or not tool:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (tool_called) requires a "
                f"string `tool`"
            )
        out["tool"] = tool
    elif kind == "span_exists":
        span_kind = match_raw.get("span_kind")
        if span_kind not in _SPAN_KINDS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (span_exists) requires "
                f"`span_kind` in {sorted(_SPAN_KINDS)}; got {span_kind!r}"
            )
        out["span_kind"] = span_kind
    elif kind == "set_match":
        # Validate the same way the standalone set_match grader does, so a funnel
        # level can't smuggle a bad gating/threshold/case_sensitive past the loader
        # only to crash (uncaught) when the grader is instantiated at launch.
        gating = match_raw.get("gating")
        if gating is not None and gating not in _SET_MATCH_GATINGS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.gating must be one of "
                f"{sorted(_SET_MATCH_GATINGS)}; got {gating!r}"
            )
        if gating is not None:
            out["gating"] = gating
        threshold = match_raw.get("threshold")
        if threshold is not None:
            if not isinstance(threshold, int | float) or isinstance(threshold, bool):
                raise LoaderError(
                    f"{spec_path}: graders[{i}].levels[{key}].match.threshold must be a number; "
                    f"got {threshold!r}"
                )
            if not 0.0 <= float(threshold) <= 1.0:
                raise LoaderError(
                    f"{spec_path}: graders[{i}].levels[{key}].match.threshold must be in [0, 1]; "
                    f"got {threshold!r}"
                )
            out["threshold"] = float(threshold)
    case_sensitive = match_raw.get("case_sensitive")
    if kind in ("equals", "by_key", "by_index", "set_match") and case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.case_sensitive must be a boolean"
            )
        out["case_sensitive"] = case_sensitive
    return out


def dump_grader_spec(grader: GraderSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": grader.type, "name": grader.name}
    if grader.rubric is not None:
        payload["rubric"] = grader.rubric
    if grader.judge_entrypoint is not None:
        from selfevals.repo.loader.agent import dump_entrypoint

        payload["judge_entrypoint"] = dump_entrypoint(grader.judge_entrypoint)
    if grader.n_judges is not None:
        payload["n_judges"] = grader.n_judges
    if grader.consensus is not None:
        payload["consensus"] = grader.consensus
    if grader.params:
        payload["params"] = dict(grader.params)
    return payload
