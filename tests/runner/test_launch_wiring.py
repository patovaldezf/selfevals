"""Wiring point: transport-tagged agent specs → concrete adapters.

The loader only parses the `agent:` block into a typed spec; `runner.launch`
dispatches that spec to an adapter. These tests pin the dispatch (one adapter per
variant) and the judge-fallback rule (only embedded agents expose an in-process
callable to reuse as a judge). They live alongside `runner/launch.py` because
this wiring is now shared by both the CLI and the HTTP `experiments/run` path.
"""

from __future__ import annotations

import pytest

from selfevals._errors import SelfEvalsUserError
from selfevals.repo.loader import (
    AgentEntrypoint,
    CliAgentSpec,
    EmbeddedAgentSpec,
    HttpAgentSpec,
)
from selfevals.runner.adapters import (
    AdapterError,
    AdapterRequest,
    AdapterResponse,
    CliCommandAdapter,
    EmbeddedAdapter,
    HttpEndpointAdapter,
)
from selfevals.runner.launch import (
    _agent_entrypoint_for_judge,
    _wrap_user_callable,
    build_adapter,
    trace_sampling_override,
)


def _req() -> AdapterRequest:
    return AdapterRequest(workspace_id="ws_test", case_id="c1", input={"messages": []})


def test_build_adapter_embedded() -> None:
    ep = AgentEntrypoint(
        raw="selfevals.repo.loader:resolve_agent_callable",
        module="selfevals.repo.loader",
        attribute="resolve_agent_callable",
    )
    adapter = build_adapter(EmbeddedAgentSpec(entrypoint=ep))
    assert isinstance(adapter, EmbeddedAdapter)


def test_build_adapter_embedded_bad_entrypoint_is_user_error() -> None:
    ep = AgentEntrypoint(raw="not.a.real.mod:x", module="not.a.real.mod", attribute="x")
    with pytest.raises(SelfEvalsUserError, match="could not be imported"):
        build_adapter(EmbeddedAgentSpec(entrypoint=ep))


def test_build_loop_persists_cases_stamped_with_experiment_id(tmp_path: object) -> None:
    """build_loop must write the run's eval cases, stamped with experiment_id,
    so `GET .../experiments/{id}/cases` has something to list. Authoring leaves
    experiment_id None; persistence is where the link gets made."""
    import json as _json
    from pathlib import Path

    import yaml

    from selfevals.repo.loader import build_spec_from_mapping
    from selfevals.runner.launch import build_loop, ensure_workspace
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.storage.interface import ListFilter
    from selfevals.storage.sqlite import SQLiteStorage

    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo_root / "evals/experiments/example_pingpong.yaml").read_text())
    rows = [
        _json.loads(line)
        for line in (repo_root / "evals/datasets/pingpong.jsonl").read_text().splitlines()
        if line.strip()
    ]
    raw["dataset"] = {"cases_inline": rows}
    ws = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    spec = build_spec_from_mapping(raw, workspace_id=ws)

    # Authoring: cases carry no experiment link yet.
    assert all(c.experiment_id is None for c in spec.cases)

    db = Path(str(tmp_path)) / "cases.sqlite"
    storage = SQLiteStorage(str(db))
    try:
        ensure_workspace(storage, spec)
        with storage.open(ws) as scope:
            build_loop(spec, scope=scope, repetitions_per_case=1)
        with storage.open(ws) as scope:
            persisted = [
                c
                for c in scope.list_entities(
                    EvalCase, ListFilter(where={"experiment_id": spec.experiment.id})
                )
                if isinstance(c, EvalCase)
            ]
    finally:
        storage.close()

    assert len(persisted) == len(spec.cases) == 2
    assert {c.experiment_id for c in persisted} == {spec.experiment.id}
    # The in-memory spec cases were stamped in place too (same objects).
    assert all(c.experiment_id == spec.experiment.id for c in spec.cases)


def test_build_loop_without_scope_does_not_persist_cases(tmp_path: object) -> None:
    """An ephemeral run (`--no-persist`, scope=None) writes nothing — the cases
    stay authoring-only and no storage is touched."""
    import json as _json
    from pathlib import Path

    import yaml

    from selfevals.repo.loader import build_spec_from_mapping
    from selfevals.runner.launch import build_loop

    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo_root / "evals/experiments/example_pingpong.yaml").read_text())
    rows = [
        _json.loads(line)
        for line in (repo_root / "evals/datasets/pingpong.jsonl").read_text().splitlines()
        if line.strip()
    ]
    raw["dataset"] = {"cases_inline": rows}
    spec = build_spec_from_mapping(raw, workspace_id="ws_01HZZZZZZZZZZZZZZZZZZZZZZZ")

    build_loop(spec, scope=None, repetitions_per_case=1)
    # No scope → no stamping, no writes.
    assert all(c.experiment_id is None for c in spec.cases)


def _inline_spec(ws: str) -> object:
    """A pingpong spec with cases inlined, ready for build_loop."""
    import json as _json
    from pathlib import Path

    import yaml

    from selfevals.repo.loader import build_spec_from_mapping

    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo_root / "evals/experiments/example_pingpong.yaml").read_text())
    rows = [
        _json.loads(line)
        for line in (repo_root / "evals/datasets/pingpong.jsonl").read_text().splitlines()
        if line.strip()
    ]
    raw["dataset"] = {"cases_inline": rows, "name": "pingpong inline", "dataset_type": "capability"}
    return build_spec_from_mapping(raw, workspace_id=ws)


def test_build_loop_materializes_inline_dataset(tmp_path: object) -> None:
    """An inline run materializes a real Dataset over its cases and rewrites the
    experiment's dataset refs to point at it — no more dangling placeholder."""
    from pathlib import Path

    from selfevals.runner.launch import build_loop, ensure_workspace
    from selfevals.schemas.dataset import Dataset, DatasetStatus
    from selfevals.storage.interface import ListFilter
    from selfevals.storage.sqlite import SQLiteStorage

    ws = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    spec = _inline_spec(ws)
    db = Path(str(tmp_path)) / "ds.sqlite"
    storage = SQLiteStorage(str(db))
    try:
        ensure_workspace(storage, spec)  # type: ignore[arg-type]
        with storage.open(ws) as scope:
            build_loop(spec, scope=scope, repetitions_per_case=1)  # type: ignore[arg-type]
        with storage.open(ws) as scope:
            datasets = [
                d for d in scope.list_entities(Dataset, ListFilter()) if isinstance(d, Dataset)
            ]
    finally:
        storage.close()

    assert len(datasets) == 1
    ds = datasets[0]
    assert ds.status == DatasetStatus.ACTIVE
    assert ds.name == "pingpong inline"
    assert len(ds.cases) == len(spec.cases)  # type: ignore[attr-defined]
    assert ds.manifest_hash is not None
    assert ds.statistics is not None and ds.statistics.total_cases == len(ds.cases)
    # The experiment now references the materialized dataset, not a placeholder.
    assert spec.experiment.datasets.optimization.id == ds.id  # type: ignore[attr-defined]
    assert [r.id for r in spec.experiment.frozen.datasets] == [ds.id]  # type: ignore[attr-defined]


def test_inline_dataset_materialization_is_idempotent(tmp_path: object) -> None:
    """Relaunching the same experiment updates the same Dataset row in place
    (id derived from the experiment id) rather than spawning a duplicate."""
    from pathlib import Path

    from selfevals.runner.launch import build_loop, ensure_workspace
    from selfevals.schemas.dataset import Dataset
    from selfevals.storage.interface import ListFilter
    from selfevals.storage.sqlite import SQLiteStorage

    ws = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    spec = _inline_spec(ws)
    db = Path(str(tmp_path)) / "ds.sqlite"
    storage = SQLiteStorage(str(db))
    try:
        ensure_workspace(storage, spec)  # type: ignore[arg-type]
        for _ in range(2):
            with storage.open(ws) as scope:
                build_loop(spec, scope=scope, repetitions_per_case=1)  # type: ignore[arg-type]
        with storage.open(ws) as scope:
            datasets = [
                d for d in scope.list_entities(Dataset, ListFilter()) if isinstance(d, Dataset)
            ]
    finally:
        storage.close()

    assert len(datasets) == 1


def test_ref_dataset_resolution_hydrates_cases_and_split(tmp_path: object) -> None:
    """A `dataset: {ref: ds_x}` spec resolves the persisted dataset at launch:
    its cases hydrate `spec.cases` and its split allocation reaches the loop."""
    from pathlib import Path

    import yaml as _yaml

    from selfevals.repo.datasets import load_cases_from_jsonl, persist_dataset
    from selfevals.repo.loader import RefDatasetSource, build_spec_from_mapping
    from selfevals.runner.launch import _resolve_dataset_source, ensure_workspace_by_id
    from selfevals.schemas.dataset import SplitAllocation
    from selfevals.schemas.enums import DatasetType
    from selfevals.storage.sqlite import SQLiteStorage

    ws = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    repo_root = Path(__file__).resolve().parents[2]
    db = Path(str(tmp_path)) / "ref.sqlite"
    storage = SQLiteStorage(str(db))
    try:
        ensure_workspace_by_id(storage, ws)
        cases = load_cases_from_jsonl(
            repo_root / "evals/datasets/pingpong.jsonl", workspace_id=ws
        )
        with storage.open(ws) as scope:
            ds = persist_dataset(
                scope,
                name="shared",
                dataset_type=DatasetType.CAPABILITY,
                cases=cases,
                split_allocation=SplitAllocation(
                    optimization=0.5, holdout=0.5, reliability=0.0
                ),
            )

        raw = _yaml.safe_load(
            (repo_root / "evals/experiments/example_pingpong.yaml").read_text()
        )
        raw["dataset"] = {"ref": ds.id}
        spec = build_spec_from_mapping(raw, workspace_id=ws)
        assert isinstance(spec.dataset_source, RefDatasetSource)
        assert spec.cases == []  # not hydrated until launch

        with storage.open(ws) as scope:
            split = _resolve_dataset_source(scope, spec)
    finally:
        storage.close()

    # Cases hydrated in place from the dataset; split adopted from it.
    assert len(spec.cases) == len(cases)
    assert split is not None and split.optimization == 0.5
    assert spec.experiment.datasets.optimization.id == ds.id


def test_ref_dataset_without_scope_is_user_error() -> None:
    """Resolving a ref needs storage — an ephemeral run over a ref is an error."""
    from pathlib import Path

    import yaml as _yaml

    from selfevals.repo.loader import build_spec_from_mapping
    from selfevals.runner.launch import build_loop

    repo_root = Path(__file__).resolve().parents[2]
    raw = _yaml.safe_load(
        (repo_root / "evals/experiments/example_pingpong.yaml").read_text()
    )
    raw["dataset"] = {"ref": "ds_01HZZZZZZZZZZZZZZZZZZZZZZZ"}
    spec = build_spec_from_mapping(raw, workspace_id="ws_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    with pytest.raises(SelfEvalsUserError, match="not persisting"):
        build_loop(spec, scope=None, repetitions_per_case=1)


def test_ref_dataset_missing_is_user_error(tmp_path: object) -> None:
    """A ref to a dataset that isn't in storage fails with a clear message."""
    from pathlib import Path

    import yaml as _yaml

    from selfevals.repo.loader import build_spec_from_mapping
    from selfevals.runner.launch import build_loop, ensure_workspace_by_id
    from selfevals.storage.sqlite import SQLiteStorage

    ws = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    repo_root = Path(__file__).resolve().parents[2]
    raw = _yaml.safe_load(
        (repo_root / "evals/experiments/example_pingpong.yaml").read_text()
    )
    raw["dataset"] = {"ref": "ds_01HZZZZZZZZZZZZZZZZZZZZZZZ"}
    spec = build_spec_from_mapping(raw, workspace_id=ws)
    db = Path(str(tmp_path)) / "missing.sqlite"
    storage = SQLiteStorage(str(db))
    try:
        ensure_workspace_by_id(storage, ws)
        with storage.open(ws) as scope, pytest.raises(SelfEvalsUserError, match="not found"):
            build_loop(spec, scope=scope, repetitions_per_case=1)
    finally:
        storage.close()


def test_build_adapter_cli() -> None:
    spec = CliAgentSpec(command=["./bin/agent"], env={"TOKEN": "x"}, timeout_seconds=30.0)
    adapter = build_adapter(spec)
    assert isinstance(adapter, CliCommandAdapter)
    assert adapter._command == ["./bin/agent"]
    assert adapter._env == {"TOKEN": "x"}
    assert adapter._timeout == 30.0


def test_build_adapter_cli_default_timeout() -> None:
    adapter = build_adapter(CliAgentSpec(command=["./agent"]))
    assert isinstance(adapter, CliCommandAdapter)
    # When the spec omits timeout_seconds, the adapter default applies.
    assert adapter._timeout == 60.0


def test_build_adapter_http() -> None:
    spec = HttpAgentSpec(
        url="https://agent.example.com/eval",
        headers={"Authorization": "Bearer x"},
        timeout_seconds=12.5,
    )
    adapter = build_adapter(spec)
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter._url == "https://agent.example.com/eval"
    assert adapter._headers["Authorization"] == "Bearer x"
    assert adapter._timeout == 12.5


def test_build_adapter_http_default_timeout() -> None:
    adapter = build_adapter(HttpAgentSpec(url="https://x/eval"))
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter._timeout == 60.0


def test_judge_fallback_returns_embedded_entrypoint() -> None:
    ep = AgentEntrypoint(raw="m:f", module="m", attribute="f")
    assert _agent_entrypoint_for_judge("rubric", EmbeddedAgentSpec(entrypoint=ep)) is ep


def test_judge_fallback_rejects_cli_agent() -> None:
    with pytest.raises(SelfEvalsUserError, match="not embedded"):
        _agent_entrypoint_for_judge("rubric", CliAgentSpec(command=["./a"]))


def test_judge_fallback_rejects_http_agent() -> None:
    with pytest.raises(SelfEvalsUserError, match="not embedded"):
        _agent_entrypoint_for_judge("rubric", HttpAgentSpec(url="https://x"))


# --- async entrypoint support (regression for the YAML-discovered async bug) ---
#
# `_wrap_user_callable` used to install a sync `_adapt` that called the user
# callable without awaiting it. An `async def` entrypoint then handed
# EmbeddedAdapter a bare coroutine — never awaited (RuntimeWarning) and rejected
# as "returned coroutine; expected str or AdapterResponse". These pin both the
# async and the (regression-guarded) sync path.

_EP = AgentEntrypoint(raw="m:f", module="m", attribute="f")


@pytest.mark.asyncio
async def test_wrap_user_callable_awaits_async_entrypoint() -> None:
    async def run(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=f"async:{req.case_id}")

    adapter = _wrap_user_callable(run, _EP)
    resp = await adapter.invoke(_req())
    assert resp.content == "async:c1"


@pytest.mark.asyncio
async def test_wrap_user_callable_handles_sync_entrypoint() -> None:
    def run(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=f"sync:{req.case_id}")

    adapter = _wrap_user_callable(run, _EP)
    resp = await adapter.invoke(_req())
    assert resp.content == "sync:c1"


@pytest.mark.asyncio
async def test_wrap_user_callable_coerces_str_return_async() -> None:
    async def run(req: AdapterRequest) -> str:
        return "hello"

    adapter = _wrap_user_callable(run, _EP)
    resp = await adapter.invoke(_req())
    assert resp.content == "hello"


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # the dangling coroutine IS the symptom
async def test_async_entrypoint_returning_coroutine_hints_await() -> None:
    # Reachable case the hint targets: an `async def` entrypoint that forgets to
    # await an inner async call, so it resolves to a *coroutine* (not an
    # AdapterResponse). _coerce sees a coroutine and must now say "did you forget
    # to await?" instead of the bare "returned coroutine" that sent brain_os
    # chasing the wrong cause. The never-awaited inner coroutine raises a
    # RuntimeWarning — which is precisely the bug we're making legible.
    async def _inner() -> AdapterResponse:
        return AdapterResponse(content="x")

    async def run(req: AdapterRequest):  # type: ignore[no-untyped-def]
        return _inner()  # returns a coroutine — the inner await is missing

    adapter = _wrap_user_callable(run, _EP)
    with pytest.raises((AdapterError, TypeError), match="forget to await"):
        await adapter.invoke(_req())


def test_plain_wrong_type_has_no_await_hint() -> None:
    # A sync entrypoint returning a plain non-coercible value (int) must NOT get
    # the await hint — only awaitables do. The TypeError from _coerce propagates
    # wrapped in AdapterError ("embedded callable raised: ...").
    import asyncio

    def run(req: AdapterRequest):  # type: ignore[no-untyped-def]
        return 123

    adapter = _wrap_user_callable(run, _EP)
    with pytest.raises(AdapterError) as exc:
        asyncio.run(adapter.invoke(_req()))
    assert "forget to await" not in str(exc.value)
    assert "returned int" in str(exc.value)


def test_trace_sampling_override_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVALS_TRACE_SAMPLING", raising=False)
    assert trace_sampling_override() is None


def test_trace_sampling_override_maps_fe_and_spec_vocabularies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # FE vocabulary
    monkeypatch.setenv("SELFEVALS_TRACE_SAMPLING", "all")
    assert trace_sampling_override() == "all"
    monkeypatch.setenv("SELFEVALS_TRACE_SAMPLING", "failures-only")
    assert trace_sampling_override() == "failed"
    # Spec vocabulary + case-insensitive / whitespace-tolerant
    monkeypatch.setenv("SELFEVALS_TRACE_SAMPLING", "  FAILED ")
    assert trace_sampling_override() == "failed"
    monkeypatch.setenv("SELFEVALS_TRACE_SAMPLING", "none")
    assert trace_sampling_override() == "none"


def test_trace_sampling_override_unknown_value_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unrecognized value falls through to the spec default, not an error.
    monkeypatch.setenv("SELFEVALS_TRACE_SAMPLING", "sometimes")
    assert trace_sampling_override() is None


def test_build_adapter_http_passes_declared_model() -> None:
    from selfevals.repo.loader import AgentModelDecl

    spec = HttpAgentSpec(
        url="https://x/eval", model=AgentModelDecl(provider="openai", name="gpt-5")
    )
    adapter = build_adapter(spec)
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter.model is not None
    assert adapter.model.provider == "openai"
    assert adapter.model.name == "gpt-5"


def test_build_adapter_http_without_model_is_none() -> None:
    adapter = build_adapter(HttpAgentSpec(url="https://x/eval"))
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter.model is None
