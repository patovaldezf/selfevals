"""Sandbox policy: what an agent is and isn't allowed to do in a given run.

MVP implements `mock` and `dry_run` fully:
- `mock`: every tool is mocked. The adapter is given `tools_allowed = []`
  and any `tool_uses` it returns are accepted but never executed for
  side effects.
- `dry_run`: tools with `side_effects=True` are mocked; read-only tools
  are allowed.
- `live_sandboxed` and `live_canary` are accepted by the policy but the
  Runner currently raises `SandboxViolationError` if you try to actually
  run with them — they need an isolation harness we haven't built yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from bootstrap.schemas.enums import SandboxMode


class SandboxViolationError(RuntimeError):
    """The current SandboxMode does not permit the requested operation."""


@dataclass(frozen=True)
class SandboxPolicy:
    mode: SandboxMode

    def is_mvp_supported(self) -> bool:
        return self.mode in (SandboxMode.MOCK, SandboxMode.DRY_RUN)

    def should_mock_tool(self, *, side_effects: bool) -> bool:
        """Should the runner mock this tool call instead of executing it?"""
        if self.mode == SandboxMode.MOCK:
            return True
        if self.mode == SandboxMode.DRY_RUN:
            return side_effects
        # live_sandboxed / live_canary — runner gates these out elsewhere.
        return False

    def ensure_runnable(self) -> None:
        """Raise if the current mode is not supported in MVP."""
        if not self.is_mvp_supported():
            raise SandboxViolationError(
                f"sandbox mode {self.mode!r} is reserved for post-MVP; use mock or dry_run"
            )
