from __future__ import annotations

import pytest

from bootstrap.runner.sandbox import SandboxPolicy, SandboxViolationError
from bootstrap.schemas.enums import SandboxMode


def test_mock_policy_mocks_everything() -> None:
    p = SandboxPolicy(SandboxMode.MOCK)
    assert p.is_mvp_supported()
    assert p.should_mock_tool(side_effects=True)
    assert p.should_mock_tool(side_effects=False)
    p.ensure_runnable()


def test_dry_run_only_mocks_side_effects() -> None:
    p = SandboxPolicy(SandboxMode.DRY_RUN)
    assert p.is_mvp_supported()
    assert p.should_mock_tool(side_effects=True)
    assert not p.should_mock_tool(side_effects=False)


@pytest.mark.parametrize("mode", [SandboxMode.LIVE_SANDBOXED, SandboxMode.LIVE_CANARY])
def test_live_modes_blocked_in_mvp(mode: SandboxMode) -> None:
    p = SandboxPolicy(mode)
    assert not p.is_mvp_supported()
    assert not p.should_mock_tool(side_effects=False)
    with pytest.raises(SandboxViolationError, match="post-MVP"):
        p.ensure_runnable()
