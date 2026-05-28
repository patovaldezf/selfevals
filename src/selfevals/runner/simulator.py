"""UserSimulator: an AgentAdapter that plays the *user* side of a conversation.

The Executor and MultiTurnExecutor drive the system-under-test (SUT) — the
agent under evaluation. For source=`simulation` cases (canon §4 / spec §4)
we also need a counterparty: a user. The UserSimulator fills that role.

Two operating modes ship out of the box and stay narrow on purpose:

- ``scripted`` — deterministic, offline. The simulator is constructed with a
  pre-recorded list of replies and returns them in order. This is what powers
  the test suite and the bundled `example_pingpong` walkthrough; it needs no
  API key and produces byte-identical runs.

- ``judge`` — LLM-backed. The simulator delegates to a real AgentAdapter
  (typically an LLM) and seeds it with persona/goal/success_criteria plus the
  running history. We deliberately reuse `AgentAdapter` instead of inventing
  a new client surface — that keeps the simulator agnostic to the provider.

The simulator is itself an `AgentAdapter`, so callers can swap it in anywhere
one fits (including running it through the very same Executor for debugging).
But the production path is: `MultiTurnExecutor` calls the simulator to
generate the *next user turn* once the case's scripted user turns are
exhausted, then the SUT replies. Costs and traces stay separate from the
SUT's, so `trajectory` and `cost` graders that read the SUT's trace exclude
the simulator naturally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from selfevals.runner.adapters import (
    AdapterError,
    AdapterRequest,
    AdapterResponse,
    AgentAdapter,
)

if TYPE_CHECKING:
    from selfevals.schemas.fleet import Agent


ROLE_TAG_USER_SIMULATOR = "user_simulator"
"""The `provider_metadata["role_tag"]` value the simulator stamps on every
response. Downstream code that needs to *exclude* simulator turns from
SUT-only accounting reads this tag. Kept as a constant so callers do not
hand-roll the string."""


@dataclass(frozen=True)
class SimulatorSpec:
    """Declarative description of the simulated user.

    All fields are passed verbatim into the judge prompt (when running in
    ``judge`` mode) and used to gate the simulation loop. They are
    intentionally string-based: the simulator stays provider-agnostic and
    does not impose a schema on the persona description.

    Attributes:
        persona: One-paragraph description of who the user is and how they
            talk. Example: "An impatient customer who has been waiting 20
            minutes for a refund and wants escalation."
        goal: What the user is trying to accomplish. Example: "Get a full
            refund processed today."
        success_criteria: Strings the *agent*'s reply must surface for the
            user to declare success. Matched case-insensitively as
            substrings against the latest agent reply at each step (see
            `is_success`). Empty list disables success-based termination.
        stop_condition: Optional free-text stop signal. The simulator
            treats it as a case-insensitive substring; when present in the
            latest agent reply, the simulation ends. Disjoint from
            ``success_criteria`` — that one means "agent satisfied the
            user", this one means "user gave up / got bored / hit an out".
            None disables condition-based termination.
        max_turns: Hard cap on *user-simulator* turns produced. Reached
            first, the simulator yields an empty response with
            ``stop_reason="end_turn"`` and the MultiTurnExecutor stops the
            thread. Must be >= 1.
    """

    persona: str
    goal: str
    success_criteria: list[str] = field(default_factory=list)
    stop_condition: str | None = None
    max_turns: int = 8

    def __post_init__(self) -> None:
        if not self.persona:
            raise ValueError("SimulatorSpec.persona must be non-empty")
        if not self.goal:
            raise ValueError("SimulatorSpec.goal must be non-empty")
        if self.max_turns < 1:
            raise ValueError("SimulatorSpec.max_turns must be >= 1")

    def is_success(self, agent_reply: str | None) -> bool:
        """True when the agent's reply contains every success criterion.

        Empty ``success_criteria`` returns False (success is opt-in): a
        case with no criteria can still terminate via ``stop_condition`` or
        ``max_turns``.
        """
        if not self.success_criteria or agent_reply is None:
            return False
        reply = agent_reply.lower()
        return all(c.lower() in reply for c in self.success_criteria)

    def hit_stop_condition(self, agent_reply: str | None) -> bool:
        """True when ``stop_condition`` is set and appears in the reply."""
        if not self.stop_condition or agent_reply is None:
            return False
        return self.stop_condition.lower() in agent_reply.lower()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulatorSpec:
        """Parse a `case.input["simulator"]` payload defensively.

        Accepts only the documented keys; ignores extras so a future spec
        bump (e.g. seed) does not silently break older runners.
        """
        if not isinstance(data, dict):
            raise ValueError("simulator spec must be a mapping")
        try:
            return cls(
                persona=str(data["persona"]),
                goal=str(data["goal"]),
                success_criteria=list(data.get("success_criteria") or []),
                stop_condition=data.get("stop_condition"),
                max_turns=int(data.get("max_turns", 8)),
            )
        except KeyError as exc:
            raise ValueError(f"simulator spec missing required field: {exc}") from exc


class UserSimulator(AgentAdapter):
    """AgentAdapter that produces the next *user* turn given a conversation.

    Construction picks the mode:

    - Pass ``scripted_replies`` (and no ``judge_adapter``) for deterministic
      offline behavior. Index N of `scripted_replies` is returned on the
      simulator's N-th invocation; running past the list yields an empty
      response with ``stop_reason="end_turn"`` so the caller terminates.
    - Pass ``judge_adapter`` for LLM-driven simulation. The simulator
      forwards an AdapterRequest containing the persona/goal/criteria and
      the running history, and uses the wrapped adapter's reply as the
      next user turn.
    - Pass both: ``scripted_replies`` is consumed first, then the simulator
      falls through to the judge_adapter once they run out. Useful for
      hybrid tests where the opening turns are deterministic but the tail
      is open-ended.
    - Pass neither: ValueError. We do not want a silent no-op simulator.

    Every response carries ``provider_metadata={"role_tag":
    "user_simulator"}`` so MultiTurnExecutor (and any future SUT-only
    accounting layer) can recognize and segregate simulator output.
    """

    def __init__(
        self,
        spec: SimulatorSpec,
        *,
        scripted_replies: list[str] | None = None,
        judge_adapter: AgentAdapter | None = None,
        agent: Agent | None = None,
    ) -> None:
        if scripted_replies is None and judge_adapter is None:
            raise ValueError("UserSimulator requires either scripted_replies or a judge_adapter")
        self._spec = spec
        self._scripted = list(scripted_replies or [])
        self._judge = judge_adapter
        self.agent = agent
        # Independent counter so the simulator can be invoked across
        # multiple cases / threads without the caller having to reset
        # state. MultiTurnExecutor constructs one per thread (see
        # multiturn.py), so the counter mirrors that thread's turns.
        self._turns_emitted = 0

    @property
    def spec(self) -> SimulatorSpec:
        return self._spec

    @property
    def turns_emitted(self) -> int:
        return self._turns_emitted

    def reset(self) -> None:
        """Reset the per-thread turn counter.

        MultiTurnExecutor uses one UserSimulator per thread today, but
        callers driving the simulator directly (tests, debug shells) may
        want to reuse one instance across threads — `reset()` is the seam.
        """
        self._turns_emitted = 0

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        # max_turns cap: once we've emitted N turns we stop, regardless of
        # mode. MultiTurnExecutor reads stop_reason="end_turn" with empty
        # content as the termination signal.
        if self._turns_emitted >= self._spec.max_turns:
            return AdapterResponse(
                content="",
                stop_reason="end_turn",
                provider_metadata={
                    "role_tag": ROLE_TAG_USER_SIMULATOR,
                    "termination": "max_turns",
                },
            )

        history = list(request.input.get("messages") or [])

        # Scripted mode first (deterministic, offline). Fall through to
        # the judge once we've exhausted the canned replies.
        if self._turns_emitted < len(self._scripted):
            reply = self._scripted[self._turns_emitted]
            self._turns_emitted += 1
            return AdapterResponse(
                content=reply,
                stop_reason="end_turn",
                provider_metadata={
                    "role_tag": ROLE_TAG_USER_SIMULATOR,
                    "source": "scripted",
                },
            )

        if self._judge is None:
            # Scripted-only and we ran past the script: signal termination
            # rather than fabricating a turn.
            return AdapterResponse(
                content="",
                stop_reason="end_turn",
                provider_metadata={
                    "role_tag": ROLE_TAG_USER_SIMULATOR,
                    "termination": "scripted_exhausted",
                },
            )

        # Judge mode: hand the persona + history to a wrapped adapter and
        # use its content as the next user turn. We re-shape the request
        # so the judge sees a self-contained prompt rather than the SUT's
        # raw input (which may carry SUT-specific keys like task_hint).
        judge_request = self._build_judge_request(request, history)
        try:
            judge_response = await self._judge.invoke(judge_request)
        except AdapterError:
            # Surface adapter failures unchanged: the MultiTurnExecutor's
            # error path records the failed turn and stops the thread.
            raise
        next_turn = (judge_response.content or "").strip()
        self._turns_emitted += 1
        return AdapterResponse(
            content=next_turn,
            stop_reason="end_turn",
            # Carry token counts through so a caller that wants to attribute
            # simulator-side cost has the data — the SUT trace still does
            # not see them.
            tokens_input=judge_response.tokens_input,
            tokens_output=judge_response.tokens_output,
            tokens_reasoning=judge_response.tokens_reasoning,
            tokens_cache_read=judge_response.tokens_cache_read,
            tokens_cache_creation=judge_response.tokens_cache_creation,
            cost_usd=judge_response.cost_usd,
            provider_metadata={
                "role_tag": ROLE_TAG_USER_SIMULATOR,
                "source": "judge",
            },
        )

    def _build_judge_request(
        self,
        request: AdapterRequest,
        history: list[dict[str, Any]],
    ) -> AdapterRequest:
        """Construct the AdapterRequest passed to the judge adapter.

        We forward workspace_id / case_id (so the judge's own trace, if it
        builds one, attributes to the right scope) and embed the persona
        prompt under `system_prompt` plus the running history under
        `messages`. Anything else on the original request is dropped: the
        judge adapter should not see SUT-internal context.
        """
        system_prompt = self._render_system_prompt()
        return AdapterRequest(
            workspace_id=request.workspace_id,
            case_id=request.case_id,
            input={
                "system_prompt": system_prompt,
                "messages": history,
            },
            context=None,
            tools_allowed=[],
            parameters={},
            metadata={"role_tag": ROLE_TAG_USER_SIMULATOR},
        )

    def _render_system_prompt(self) -> str:
        """Plain-text rendering of the spec for the judge adapter.

        We keep this dumb on purpose: the simulator does not own prompt
        engineering. Callers who want a fancier template can subclass and
        override.
        """
        lines = [
            "You are role-playing a user in a conversation with an AI agent.",
            f"Persona: {self._spec.persona}",
            f"Goal: {self._spec.goal}",
        ]
        if self._spec.success_criteria:
            joined = "; ".join(self._spec.success_criteria)
            lines.append(f"You succeed when the agent's reply includes: {joined}")
        if self._spec.stop_condition:
            lines.append(
                "You give up and stop the conversation if the agent's reply "
                f"contains: {self._spec.stop_condition}"
            )
        lines.append(
            "Reply with ONLY the next user message — no narration, no quotes, no role labels."
        )
        return "\n".join(lines)
