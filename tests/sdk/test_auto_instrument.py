"""Unit tests for SDK auto-detection of OpenInference Instrumentors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfeval.sdk import auto_instrument


def test_detect_installed_returns_known_subset() -> None:
    # We don't assert specific provider names because the test env may or
    # may not have anthropic/openai installed; the contract is "filtered subset."
    specs = auto_instrument.detect_installed_sdks()
    for s in specs:
        assert s in auto_instrument.KNOWN_INSTRUMENTORS


def test_install_with_empty_request_installs_nothing() -> None:
    provider = MagicMock()
    report = auto_instrument.install_instrumentors(
        tracer_provider=provider,
        requested=[],
    )
    assert report.installed == []
    assert report.skipped_missing_extra == []


def test_install_missing_instrumentor_is_non_fatal() -> None:
    # Ask for "anthropic" but pretend the instrumentor module is missing.
    provider = MagicMock()
    with patch(
        "selfeval.sdk.auto_instrument.importlib.import_module",
        side_effect=ImportError("no module"),
    ):
        report = auto_instrument.install_instrumentors(
            tracer_provider=provider,
            requested=["anthropic"],
        )
    assert "anthropic" not in report.installed
    assert "anthropic" in report.skipped_missing_extra
    assert any("anthropic" in w for w in report.warnings)


def test_install_disabled_overrides_requested() -> None:
    provider = MagicMock()
    report = auto_instrument.install_instrumentors(
        tracer_provider=provider,
        requested=["anthropic", "openai"],
        disabled=["anthropic"],
    )
    # anthropic should be filtered out before we even try to import it.
    assert "anthropic" not in report.installed
    assert "anthropic" not in report.skipped_missing_extra
    assert "anthropic" in report.skipped_explicit


def test_install_with_working_instrumentor_records_success() -> None:
    provider = MagicMock()
    fake_module = MagicMock()
    fake_instance = MagicMock()
    fake_class = MagicMock(return_value=fake_instance)
    fake_module.AnthropicInstrumentor = fake_class

    with patch(
        "selfeval.sdk.auto_instrument.importlib.import_module",
        return_value=fake_module,
    ):
        report = auto_instrument.install_instrumentors(
            tracer_provider=provider,
            requested=["anthropic"],
        )

    assert report.installed == ["anthropic"]
    fake_instance.instrument.assert_called_once_with(tracer_provider=provider)


def test_instrument_failure_caught_as_warning() -> None:
    provider = MagicMock()
    fake_module = MagicMock()
    fake_instance = MagicMock()
    fake_instance.instrument.side_effect = RuntimeError("kaboom")
    fake_class = MagicMock(return_value=fake_instance)
    fake_module.AnthropicInstrumentor = fake_class

    with patch(
        "selfeval.sdk.auto_instrument.importlib.import_module",
        return_value=fake_module,
    ):
        report = auto_instrument.install_instrumentors(
            tracer_provider=provider,
            requested=["anthropic"],
        )

    assert report.installed == []
    assert "anthropic" in report.skipped_missing_extra
    assert any("kaboom" in w for w in report.warnings)
