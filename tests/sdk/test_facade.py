"""Unit tests for selfeval.sdk.facade."""

from __future__ import annotations

import pytest

from selfeval.sdk import facade


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Make every test start from a clean facade module state."""
    facade._reset_for_tests()
    yield
    facade._reset_for_tests()


def test_init_returns_result_and_marks_initialized() -> None:
    result = facade.init(project="proj1", instrument=[])
    assert result.project == "proj1"
    assert facade.is_initialized()


def test_init_idempotent_same_project() -> None:
    a = facade.init(project="proj1", instrument=[])
    b = facade.init(project="proj1", instrument=[])
    assert a is b


def test_init_raises_on_different_project() -> None:
    facade.init(project="proj1", instrument=[])
    with pytest.raises(facade.SelfEvalAlreadyInitialized):
        facade.init(project="proj2", instrument=[])


def test_invalid_project_rejected() -> None:
    with pytest.raises(ValueError):
        facade.init(project="", instrument=[])


def test_invalid_sample_rate_rejected() -> None:
    with pytest.raises(ValueError):
        facade.init(project="p", sample_rate=2.0, instrument=[])


def test_endpoint_resolution_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELFEVAL_OTLP_ENDPOINT", "http://from-env:1234")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-env:9999")
    result = facade.init(project="p", endpoint="http://explicit:4318", instrument=[])
    assert result.endpoint == "http://explicit:4318"


def test_endpoint_resolution_selfeval_env_wins_over_otel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELFEVAL_OTLP_ENDPOINT", "http://from-env:1234")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-env:9999")
    result = facade.init(project="p", instrument=[])
    assert result.endpoint == "http://from-env:1234"


def test_endpoint_resolution_otel_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVAL_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-env:9999")
    result = facade.init(project="p", instrument=[])
    assert result.endpoint == "http://otel-env:9999"


def test_no_endpoint_emits_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVAL_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    result = facade.init(project="p", instrument=[])
    assert result.endpoint is None
    assert any("OTLP endpoint" in w for w in result.warnings)


def test_explicit_disable_skips_instrumentor() -> None:
    result = facade.init(project="p", disable=["anthropic"], instrument=[])
    assert "anthropic" not in result.instrumentors_installed


def test_shutdown_resets_state() -> None:
    facade.init(project="p", instrument=[])
    facade.shutdown()
    assert not facade.is_initialized()
    # Re-init after shutdown should succeed with any project.
    facade.init(project="other", instrument=[])


def test_top_level_init_and_shutdown_proxies() -> None:
    import selfeval

    result = selfeval.init(project="proj1", instrument=[])
    assert result.project == "proj1"
    assert selfeval.is_initialized()
    selfeval.shutdown()
    assert not selfeval.is_initialized()
