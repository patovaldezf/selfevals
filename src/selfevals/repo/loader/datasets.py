"""Parse and serialize the `dataset:` block into a tagged `DatasetSpec`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from selfevals.repo.loader.models import (
    DatasetSpec,
    ExperimentSpec,
    InlineDatasetSource,
    LoaderError,
    RefDatasetSource,
)
from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import SplitAllocation
from selfevals.schemas.enums import DatasetType
from selfevals.schemas.eval_case import EvalCase


def build_dataset_source(spec_path: Path, raw: dict[str, Any], workspace_id: str) -> DatasetSpec:
    """Classify the `dataset:` block into a tagged source (no storage access).

    Three shapes, mutually exclusive:
    - `cases_inline:` / `cases_path:` → `InlineDatasetSource` (cases declared
      here; optional `name`/`dataset_type`/`split_allocation`/`description`
      describe the Dataset that launch will materialize).
    - `ref:` → `RefDatasetSource` (reuse a persisted Dataset; resolved at launch).
    Mixing `ref:` with inline cases is rejected, mirroring the inline XOR.
    """
    dataset = raw.get("dataset", {})
    if not isinstance(dataset, dict):
        raise LoaderError(f"{spec_path}: `dataset:` must be a mapping")

    ref = dataset.get("ref")
    inline = dataset.get("cases_inline")
    cases_path = dataset.get("cases_path")
    has_inline = inline is not None or cases_path is not None

    if ref is not None and has_inline:
        raise LoaderError(
            f"{spec_path}: dataset cannot mix `ref:` with `cases_inline:`/`cases_path:` "
            "— a ref reuses a persisted dataset, inline declares its own cases"
        )
    if ref is not None:
        return _build_ref_dataset_source(spec_path, dataset, ref)

    cases = build_cases(spec_path, dataset, workspace_id)
    return InlineDatasetSource(
        cases=cases,
        name=_opt_str(spec_path, dataset, "name"),
        dataset_type=_opt_dataset_type(spec_path, dataset),
        split_allocation=_opt_split_allocation(spec_path, dataset),
        description=_opt_str(spec_path, dataset, "description"),
    )


def _build_ref_dataset_source(
    spec_path: Path, dataset: dict[str, Any], ref: Any
) -> RefDatasetSource:
    if not isinstance(ref, str) or not ref:
        raise LoaderError(f"{spec_path}: `dataset.ref:` must be a non-empty dataset id string")
    version = dataset.get("version")
    if version is not None and not isinstance(version, int):
        raise LoaderError(f"{spec_path}: `dataset.version:` must be an integer when given")
    try:
        return RefDatasetSource(ref=EntityRef(id=ref, version=version))
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap any build error into a spec-located LoaderError
        raise LoaderError(f"{spec_path}: invalid `dataset.ref:`: {exc}") from exc


def _opt_str(spec_path: Path, dataset: dict[str, Any], key: str) -> str | None:
    value = dataset.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise LoaderError(f"{spec_path}: `dataset.{key}:` must be a non-empty string when given")
    return value


def _opt_dataset_type(spec_path: Path, dataset: dict[str, Any]) -> DatasetType | None:
    value = dataset.get("dataset_type")
    if value is None:
        return None
    try:
        return DatasetType(value)
    except ValueError as exc:
        raise LoaderError(f"{spec_path}: invalid `dataset.dataset_type:` {value!r}: {exc}") from exc


def _opt_split_allocation(spec_path: Path, dataset: dict[str, Any]) -> SplitAllocation | None:
    value = dataset.get("split_allocation")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LoaderError(f"{spec_path}: `dataset.split_allocation:` must be a mapping when given")
    try:
        return SplitAllocation(**value)
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap any build error into a spec-located LoaderError
        raise LoaderError(f"{spec_path}: invalid `dataset.split_allocation:`: {exc}") from exc


def build_cases(spec_path: Path, dataset: dict[str, Any], workspace_id: str) -> list[EvalCase]:
    inline = dataset.get("cases_inline")
    cases_path = dataset.get("cases_path")
    if inline is None and cases_path is None:
        raise LoaderError(
            f"{spec_path}: dataset must provide `cases_inline:`, `cases_path:`, or `ref:`"
        )
    if inline is not None and cases_path is not None:
        raise LoaderError(
            f"{spec_path}: dataset cannot have both `cases_inline:` and `cases_path:`"
        )

    if inline is not None:
        if not isinstance(inline, list):
            raise LoaderError(f"{spec_path}: `cases_inline:` must be a list")
        rows = [cast(dict[str, Any], row) for row in inline]
    else:
        rows = _read_jsonl(spec_path.parent / str(cases_path))

    if not rows:
        raise LoaderError(f"{spec_path}: dataset yielded zero cases")

    cases: list[EvalCase] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise LoaderError(f"{spec_path}: case #{i} must be a mapping, got {type(row).__name__}")
        case_payload = dict(row)
        case_payload.setdefault("id", EvalCase.make_id())
        case_payload.setdefault("workspace_id", workspace_id)
        try:
            cases.append(EvalCase(**case_payload))
        except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap any Pydantic build error into a spec-located LoaderError
            raise LoaderError(f"{spec_path}: invalid case #{i}: {exc}") from exc
    return cases


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise LoaderError(f"dataset file not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise LoaderError(f"{path}:{line_no}: expected an object, got {type(row).__name__}")
        rows.append(row)
    return rows


def dump_dataset_source(spec: ExperimentSpec) -> dict[str, Any]:
    """Serialize the spec's dataset source for `serialize_experiment_spec`."""
    if isinstance(spec.dataset_source, RefDatasetSource):
        ref = spec.dataset_source.ref
        block: dict[str, Any] = {"ref": ref.id}
        if ref.version is not None:
            block["version"] = ref.version
        return block
    return {"cases_inline": [case.model_dump(mode="json") for case in spec.cases]}
