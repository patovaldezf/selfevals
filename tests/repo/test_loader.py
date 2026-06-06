from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfevals.repo.loader import (
    AgentEntrypoint,
    CliAgentSpec,
    EmbeddedAgentSpec,
    HttpAgentSpec,
    InlineDatasetSource,
    LoaderError,
    RefDatasetSource,
    load_experiment_spec,
    resolve_agent_callable,
)
from selfevals.schemas.enums import DatasetType

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _experiment_block() -> dict:
    return {
        "name": "optimize prompt v2",
        "goal": "lift pass@1 on commerce.product_resolution",
        "mode": "handoff",
        "taxonomy": {
            "target_features": ["commerce.product_resolution"],
            "dataset_types": ["capability"],
        },
        "datasets": {"optimization": {"id": "ds_x", "version": 1}},
        "target": {"primary": {"name": "pass@1", "operator": ">=", "value": 0.5}},
        "editable": {"prompt": True, "model_params": True},
        "frozen": {
            "fleet": {"id": "flt_x"},
            "agents": [{"id": "ag_x"}],
            "datasets": [{"id": "ds_y"}],
        },
        "proposer": {"strategy": "grid"},
        "run": {
            "sandbox": "mock",
            "max_iterations": 3,
            "convergence": {"min_delta": 1e-6, "patience": 10},
        },
        "search_space": {"model_params": {"level": [0.0, 1.0]}},
        "reliability": {"metrics": ["pass@1"]},
    }


def _inline_case() -> dict:
    return {
        "name": "t",
        "task_type": "x",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "taxonomy": {
            "level": "final_response",
            "feature": {"primary": "commerce.product_resolution"},
            "source": {"type": "handcrafted"},
            "ground_truth": {"methods": ["exact_match"]},
            "dataset_type": "capability",
        },
        "expected": {"must_include": ["pong"]},
    }


def _write_yaml(tmp_path: Path, body: dict) -> Path:
    import yaml as pyyaml

    p = tmp_path / "experiment.yaml"
    p.write_text(pyyaml.safe_dump(body))
    return p


def test_load_inline_cases(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case(), _inline_case()]},
        "agent": {"entrypoint": "tests.repo.fixtures.fake_agent:run"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert spec.workspace_id == WS
    assert spec.experiment.name == "optimize prompt v2"
    assert len(spec.cases) == 2
    assert isinstance(spec.agent, EmbeddedAgentSpec)
    assert spec.agent.entrypoint.module == "tests.repo.fixtures.fake_agent"
    assert spec.agent.entrypoint.attribute == "run"
    assert spec.agent.entrypoint.raw == "tests.repo.fixtures.fake_agent:run"


def test_load_cases_from_jsonl(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text("\n".join(json.dumps(_inline_case()) for _ in range(3)) + "\n")
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_path": "cases.jsonl"},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert len(spec.cases) == 3


def test_error_analysis_block_hydrates_onto_experiment(tmp_path: Path) -> None:
    # The YAML keys are 1:1 with Experiment fields, so the opt-in block needs
    # no loader code — Pydantic validates it. This guards that contract (§9).
    exp = _experiment_block()
    exp["error_analysis"] = {
        "enabled": True,
        "trigger": {"when": "fail_rate_above", "threshold": 0.2},
        "scope": "all",
    }
    body = {
        "workspace": WS,
        "experiment": exp,
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    ea = spec.experiment.error_analysis
    assert ea.enabled is True
    assert ea.scope == "all"
    assert ea.trigger.threshold == 0.2
    assert ea.should_stage(fail_rate=0.5) is True


def test_error_analysis_defaults_off_when_omitted(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert spec.experiment.error_analysis.enabled is False


def test_workspace_override_takes_precedence(tmp_path: Path) -> None:
    body = {
        "workspace": "ws_01YYYYYYYYYYYYYYYYYYYYYYYY",
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body), workspace_id=WS)
    assert spec.workspace_id == WS
    assert spec.experiment.workspace_id == WS


def test_missing_workspace_raises(tmp_path: Path) -> None:
    body = {
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match="workspace_id missing"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LoaderError, match="not found"):
        load_experiment_spec(tmp_path / "nope.yaml")


def test_inline_and_path_both_rejected(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {
            "cases_inline": [_inline_case()],
            "cases_path": "cases.jsonl",
        },
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match="cannot have both"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_zero_cases_rejected(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": []},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match="zero cases"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_bad_entrypoint_format(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "no_colon_here"},
    }
    with pytest.raises(LoaderError, match="entrypoint"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_invalid_experiment_payload_surfaces(tmp_path: Path) -> None:
    exp = _experiment_block()
    exp["mode"] = "not_a_mode"
    body = {
        "workspace": WS,
        "experiment": exp,
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match="invalid experiment payload"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_invalid_yaml_surfaces(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text(":\n:\nfoo: [unclosed")
    with pytest.raises(LoaderError, match="could not parse YAML"):
        load_experiment_spec(p)


def test_top_level_not_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- one\n- two\n")
    with pytest.raises(LoaderError, match="expected a mapping"):
        load_experiment_spec(p)


def test_resolve_agent_callable_finds_function() -> None:
    ep = AgentEntrypoint(
        raw="selfevals.repo.loader:resolve_agent_callable",
        module="selfevals.repo.loader",
        attribute="resolve_agent_callable",
    )
    fn = resolve_agent_callable(ep)
    assert callable(fn)


def test_resolve_agent_callable_unknown_module() -> None:
    ep = AgentEntrypoint(raw="not.a.real.mod:x", module="not.a.real.mod", attribute="x")
    with pytest.raises(LoaderError, match="could not be imported"):
        resolve_agent_callable(ep)


def _body_with_agent(agent: dict) -> dict:
    return {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": agent,
    }


def test_agent_type_embedded(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "embedded", "entrypoint": "pkg.mod:run"})
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, EmbeddedAgentSpec)
    assert spec.agent.entrypoint.raw == "pkg.mod:run"


def test_agent_type_cli(tmp_path: Path) -> None:
    body = _body_with_agent(
        {
            "type": "cli",
            "command": ["./bin/agent", "--flag"],
            "env": {"TOKEN": "x"},
            "timeout_seconds": 30,
        }
    )
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, CliAgentSpec)
    assert spec.agent.command == ["./bin/agent", "--flag"]
    assert spec.agent.env == {"TOKEN": "x"}
    assert spec.agent.timeout_seconds == 30.0


def test_agent_type_cli_minimal(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "cli", "command": ["./agent"]})
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, CliAgentSpec)
    assert spec.agent.env is None
    assert spec.agent.timeout_seconds is None


def test_agent_type_http(tmp_path: Path) -> None:
    body = _body_with_agent(
        {
            "type": "http",
            "url": "https://agent.example.com/eval",
            "headers": {"Authorization": "Bearer x"},
            "timeout_seconds": 12.5,
        }
    )
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, HttpAgentSpec)
    assert spec.agent.url == "https://agent.example.com/eval"
    assert spec.agent.headers == {"Authorization": "Bearer x"}
    assert spec.agent.timeout_seconds == 12.5


def test_agent_http_with_model_parsed(tmp_path: Path) -> None:
    body = _body_with_agent(
        {
            "type": "http",
            "url": "https://x/eval",
            "model": {"provider": "openai", "name": "gpt-5"},
        }
    )
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, HttpAgentSpec)
    assert spec.agent.model is not None
    assert spec.agent.model.provider == "openai"
    assert spec.agent.model.name == "gpt-5"


def test_agent_http_without_model_is_none(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "http", "url": "https://x/eval"})
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.agent, HttpAgentSpec)
    assert spec.agent.model is None


def test_agent_model_missing_provider_rejected(tmp_path: Path) -> None:
    body = _body_with_agent(
        {"type": "http", "url": "https://x/eval", "model": {"name": "gpt-5"}}
    )
    with pytest.raises(LoaderError, match=r"agent\.model\.provider"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_unknown_type_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "grpc", "url": "x"})
    with pytest.raises(LoaderError, match=r"agent\.type must be one of"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_cli_missing_command_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "cli"})
    with pytest.raises(LoaderError, match="requires `command:`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_cli_empty_command_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "cli", "command": []})
    with pytest.raises(LoaderError, match="requires `command:`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_http_missing_url_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "http"})
    with pytest.raises(LoaderError, match="requires `url:`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_cli_with_entrypoint_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "cli", "command": ["./a"], "entrypoint": "m:f"})
    with pytest.raises(LoaderError, match="does not take an `entrypoint`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_http_with_entrypoint_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "http", "url": "x", "entrypoint": "m:f"})
    with pytest.raises(LoaderError, match="does not take an `entrypoint`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_bad_timeout_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "http", "url": "x", "timeout_seconds": -1})
    with pytest.raises(LoaderError, match="timeout_seconds must be a positive number"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_agent_cli_bad_env_rejected(tmp_path: Path) -> None:
    body = _body_with_agent({"type": "cli", "command": ["./a"], "env": {"K": 1}})
    with pytest.raises(LoaderError, match=r"agent\.env must be a mapping"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_resolve_agent_callable_unknown_attribute() -> None:
    ep = AgentEntrypoint(
        raw="selfevals.repo.loader:does_not_exist",
        module="selfevals.repo.loader",
        attribute="does_not_exist",
    )
    with pytest.raises(LoaderError, match="has no attribute"):
        resolve_agent_callable(ep)


# --- dataset_source classification (F2) ------------------------------------


def test_inline_dataset_source_carries_cases_and_metadata(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {
            "cases_inline": [_inline_case(), _inline_case()],
            "name": "smoke-suite",
            "dataset_type": "smoke",
            "split_allocation": {"optimization": 0.5, "holdout": 0.5, "reliability": 0.0},
            "description": "two-case warmup",
        },
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.dataset_source, InlineDatasetSource)
    assert spec.dataset_source.name == "smoke-suite"
    assert spec.dataset_source.dataset_type == DatasetType.SMOKE
    assert spec.dataset_source.split_allocation is not None
    assert spec.dataset_source.split_allocation.optimization == 0.5
    # Inline cases also populate spec.cases (back-compat with the run path).
    assert len(spec.cases) == 2
    assert len(spec.dataset_source.cases) == 2


def test_inline_dataset_source_defaults_metadata_to_none(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.dataset_source, InlineDatasetSource)
    assert spec.dataset_source.name is None
    assert spec.dataset_source.dataset_type is None
    assert spec.dataset_source.split_allocation is None


def test_ref_dataset_source_resolves_to_ref(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"ref": "ds_01HZZZZZZZZZZZZZZZZZZZZZZZ", "version": 2},
        "agent": {"entrypoint": "mod:fn"},
    }
    spec = load_experiment_spec(_write_yaml(tmp_path, body))
    assert isinstance(spec.dataset_source, RefDatasetSource)
    assert spec.dataset_source.ref.id == "ds_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    assert spec.dataset_source.ref.version == 2
    # A ref declares no cases inline — they resolve from storage at launch.
    assert spec.cases == []


def test_ref_and_inline_are_mutually_exclusive(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"ref": "ds_x", "cases_inline": [_inline_case()]},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match="cannot mix `ref:`"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_dataset_without_any_source_errors(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match=r"cases_inline.*cases_path.*ref"):
        load_experiment_spec(_write_yaml(tmp_path, body))


def test_invalid_dataset_type_in_block_errors(tmp_path: Path) -> None:
    body = {
        "workspace": WS,
        "experiment": _experiment_block(),
        "dataset": {"cases_inline": [_inline_case()], "dataset_type": "not_a_type"},
        "agent": {"entrypoint": "mod:fn"},
    }
    with pytest.raises(LoaderError, match=r"invalid `dataset\.dataset_type:`"):
        load_experiment_spec(_write_yaml(tmp_path, body))
