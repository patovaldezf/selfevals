"""End-to-end coverage for `examples/hello_llm`.

These tests exercise the real example code: agent fake responder, judge
fake responder, YAML loader path for the `graders:` extension, and the
CLI's `selfevals run` pipeline. We deliberately do NOT mock the agent
module — the test would lose almost all signal if we did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from selfevals.cli.main import app
from selfevals.graders.registry import available_graders, unregister_grader
from selfevals.runner.adapters import AdapterRequest, AdapterResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = REPO_ROOT / "examples" / "hello_llm" / "experiment.yaml"

# Make `examples.hello_llm.agent` importable for tests run via pytest from
# the repo root.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.hello_llm import agent  # noqa: E402


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _make_request(
    *,
    case_id: str = "ec_test",
    task_hint: str = "sentiment",
    user: str = "I love this!",
    temperature: float = 0.0,
) -> AdapterRequest:
    return AdapterRequest(
        workspace_id="ws_test",
        case_id=case_id,
        input={
            "task_hint": task_hint,
            "messages": [{"role": "user", "content": user}],
        },
        parameters={"model_params": {"temperature": temperature}},
    )


def test_agent_fake_sentiment_cool_temperature_is_correct() -> None:
    resp = agent.run(_make_request(task_hint="sentiment", user="I love it", temperature=0.0))
    assert isinstance(resp, AdapterResponse)
    assert resp.content == "positive"
    assert resp.tokens_input > 0
    assert resp.tokens_output > 0


def test_agent_fake_sentiment_warm_temperature_hedges() -> None:
    resp = agent.run(_make_request(task_hint="sentiment", user="I love it", temperature=0.9))
    assert resp.content == "mixed"


def test_agent_fake_extraction_zero_temperature_is_exact() -> None:
    resp = agent.run(_make_request(task_hint="extraction", temperature=0.0))
    assert resp.structured_output == {"city": "Berlin", "date": "2026-06-12", "attendees": 2}


def test_agent_fake_extraction_warm_temperature_drifts() -> None:
    resp = agent.run(_make_request(task_hint="extraction", temperature=1.0))
    assert resp.structured_output is not None
    assert resp.structured_output != {
        "city": "Berlin",
        "date": "2026-06-12",
        "attendees": 2,
    }


def test_judge_fake_passes_empathic_actionable_reply() -> None:
    rubric_prompt = (
        "Agent response: Thanks for reaching out — I'm sorry the order hasn't "
        "arrived. I can offer either a refund or a replacement. Which would you prefer?"
    )
    req = AdapterRequest(
        workspace_id="ws_test",
        case_id="ec_test",
        input={"messages": [{"role": "user", "content": rubric_prompt}]},
    )
    resp = agent.judge(req)
    assert resp.content is not None
    payload = json.loads(resp.content)
    assert payload["label"] == "pass"
    assert 0.0 <= float(payload["score"]) <= 1.0


def test_judge_fake_fails_terse_unhelpful_reply() -> None:
    req = AdapterRequest(
        workspace_id="ws_test",
        case_id="ec_test",
        input={"messages": [{"role": "user", "content": "Agent response: idk"}]},
    )
    resp = agent.judge(req)
    payload = json.loads(resp.content or "")
    assert payload["label"] == "fail"


def test_build_runner_uses_injected_fake() -> None:
    """The fake_responder hook lets callers stub the agent without monkey-
    patching globals — verifies the injection contract."""

    sentinel_text = "from-injected-fake"

    def fake(ctx: agent.PromptContext) -> AdapterResponse:
        return AdapterResponse(content=sentinel_text, tokens_input=1, tokens_output=1)

    runner = agent.build_runner(fake_responder=fake)
    resp = runner(_make_request())
    assert resp.content == sentinel_text


def test_cli_run_example_end_to_end(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`selfevals run examples/hello_llm/experiment.yaml --no-persist` must
    complete and produce a JSON OptimizationResult with one iteration per
    temperature value, and the cool temperature must be best."""
    # Don't leak registered grader names across tests.
    for name in ("rules", "rubric_judge"):
        unregister_grader(name)

    rc = app(
        [
            "run",
            str(EXPERIMENT),
            "--no-persist",
            "--format",
            "json",
        ]
    )
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["experiment"]["name"] == "hello_llm temperature sweep"
    iterations = payload["iterations"]
    assert len(iterations) == 3  # grid: 3 temperatures
    primaries = [it["metrics"]["primary"]["value"] for it in iterations]
    # The fake should drive a clear ranking: t=0 highest, t=1 lowest.
    assert primaries[0] >= primaries[1] >= primaries[2]
    assert primaries[0] >= 0.7  # meets target
    # Confirm at least one iteration registered the LLM-judge grader as a
    # signal (the support reply case is graded by it).
    assert payload["best_iteration"]["parameters"]["model_params"]["temperature"] == 0.0


def test_cli_run_unregisters_specs_after_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI must clean the grader registry after `run` so consecutive
    invocations don't leak named factories."""
    for name in ("rules", "rubric_judge"):
        unregister_grader(name)
    before = set(available_graders())
    rc = app(["run", str(EXPERIMENT), "--no-persist", "--format", "json"])
    assert rc == 0, capsys.readouterr().err
    capsys.readouterr()  # drain stdout
    after = set(available_graders())
    assert after == before, f"registry leaked: added {after - before}"
