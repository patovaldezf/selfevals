"""CLI handlers for `selfevals dataset` — standalone dataset management.

Datasets are first-class, experiment-independent resources: you create one from
a JSONL file, list/inspect what's stored, and freeze it for immutability — none
of which requires (or launches) an experiment. Every write goes through the same
`repo.datasets.persist_dataset` the API and the run path use, so a dataset is
created identically regardless of who asks.
"""

from __future__ import annotations

import argparse

from selfevals.cli.commands import CommandError, _require_entity, _storage
from selfevals.repo.datasets import (
    DatasetImportError,
    compute_manifest_hash,
    compute_statistics,
    load_cases_from_jsonl,
    persist_dataset,
)
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas.dataset import Dataset
from selfevals.schemas.enums import DatasetStatus, DatasetType
from selfevals.schemas.eval_case import EvalCase
from selfevals.storage.interface import ListFilter


def cmd_dataset_create(args: argparse.Namespace) -> int:
    """Create a dataset from a JSONL file of cases. Does not run anything."""
    from pathlib import Path

    try:
        dataset_type = DatasetType(args.type)
    except ValueError as exc:
        raise CommandError(f"unknown dataset type {args.type!r}: {exc}") from exc

    storage = _storage(args)
    try:
        cases = load_cases_from_jsonl(Path(args.from_path), workspace_id=args.workspace_id)
    except DatasetImportError as exc:
        storage.close()
        raise CommandError(str(exc)) from exc

    try:
        ensure_workspace_by_id(storage, args.workspace_id)
        with storage.open(args.workspace_id) as scope:
            dataset = persist_dataset(
                scope,
                name=args.name,
                dataset_type=dataset_type,
                cases=cases,
                description=args.description,
            )
    finally:
        storage.close()

    print(f"created dataset id={dataset.id}")
    print(f"  name:   {dataset.name}")
    print(f"  type:   {dataset.dataset_type}")
    print(f"  status: {dataset.status}")
    print(f"  cases:  {len(dataset.cases)}")
    return 0


def cmd_dataset_list(args: argparse.Namespace) -> int:
    status_filter: DatasetStatus | None = None
    if args.status is not None:
        try:
            status_filter = DatasetStatus(args.status)
        except ValueError as exc:
            raise CommandError(f"unknown status {args.status!r}: {exc}") from exc

    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            datasets = [
                d
                for d in scope.list_entities(Dataset, ListFilter())
                if isinstance(d, Dataset)
            ]
    finally:
        storage.close()

    if status_filter is not None:
        datasets = [d for d in datasets if d.status == status_filter]
    if not datasets:
        print("(no datasets)")
        return 0
    for ds in datasets:
        print(
            f"{ds.id}  type={ds.dataset_type}  status={ds.status}  "
            f"cases={len(ds.cases)}  name={ds.name}"
        )
    return 0


def cmd_dataset_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            ds = _require_entity(scope, Dataset, args.dataset_id)
            assert isinstance(ds, Dataset)
    finally:
        storage.close()

    print(f"dataset id={ds.id}")
    print(f"  name:          {ds.name}")
    print(f"  description:   {ds.description or '-'}")
    print(f"  type:          {ds.dataset_type}")
    print(f"  status:        {ds.status}")
    print(f"  cases:         {len(ds.cases)}")
    print(f"  manifest_hash: {ds.manifest_hash or '-'}")
    sa = ds.split_allocation
    print(
        f"  split:         optimization={sa.optimization:g} "
        f"holdout={sa.holdout:g} reliability={sa.reliability:g}"
    )
    if ds.statistics is not None:
        stats = ds.statistics
        print(f"  total_cases:   {stats.total_cases}")
        print(f"  holdout_count: {stats.holdout_count}")
        if stats.by_feature:
            features = ", ".join(f"{k}={v}" for k, v in sorted(stats.by_feature.items()))
            print(f"  by_feature:    {features}")
    return 0


def cmd_dataset_freeze(args: argparse.Namespace) -> int:
    """Freeze a dataset: recompute hash/statistics and set status=FROZEN.

    FROZEN demands a non-empty manifest_hash; we recompute it from the current
    cases so the frozen manifest is self-consistent. Regression datasets become
    content-immutable at this point (enforced by the schema).
    """
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            ds = _require_entity(scope, Dataset, args.dataset_id)
            assert isinstance(ds, Dataset)
            ref_ids = {ref.id for ref in ds.cases}
            cases = [
                c
                for c in scope.list_entities(EvalCase, ListFilter())
                if isinstance(c, EvalCase) and c.id in ref_ids
            ]
            ds.manifest_hash = compute_manifest_hash(cases) if cases else ds.manifest_hash
            ds.statistics = compute_statistics(cases) if cases else ds.statistics
            if not ds.manifest_hash:
                raise CommandError(
                    f"cannot freeze {ds.id}: no cases resolved and no manifest_hash present"
                )
            ds.status = DatasetStatus.FROZEN
            scope.put_entity(ds)
    finally:
        storage.close()

    print(f"froze dataset {args.dataset_id} (status=frozen)")
    return 0
