"""Two stand-in "providers" for the `search_space.agents` example.

Both answer the same pingpong cases, offline and with no API key, but they
differ the way real providers do: one is more accurate, the other is cheaper.
That is the whole point of a provider bake-off — the winner is not obvious from
either number alone, and the run has to surface both.

Swap these for real integrations (AssemblyAI vs ElevenLabs, Twilio vs Retell,
LangChain vs Mastra) and the spec does not change shape: each entry under
`search_space.agents` is just an `agent:` block.
"""

from __future__ import annotations

from selfevals.runner.adapters import AdapterRequest, AdapterResponse


def accurate(req: AdapterRequest) -> AdapterResponse:
    """The premium provider: always right, and priced like it."""
    return AdapterResponse(
        content="pong",
        tokens_input=4,
        tokens_output=2,
        cost_usd=0.010,
        provider_metadata={"provider": "acme-premium"},
    )


def cheap(req: AdapterRequest) -> AdapterResponse:
    """The budget provider: ~10x cheaper, and it misses the harder case.

    It only answers correctly when the prompt literally contains "ping", so the
    dataset's second case exposes the accuracy gap you are paying to avoid.
    """
    text = str(req.input)
    hit = "ping" in text.lower()
    return AdapterResponse(
        content="pong" if hit else "…",
        tokens_input=4,
        tokens_output=2,
        cost_usd=0.001,
        provider_metadata={"provider": "budget-co"},
    )
