"""Offline route-ops copilot example.

This example is intentionally domain-generic: it models the patterns common to
route-ops assistants without naming any real product, company, or customer.
It is meant to teach how to evaluate an agentic workflow, not a toy classifier:

- read tools gather route/account/task context,
- write tools commit plan/task changes only after a safety gate,
- structured output exposes the action contract graders can inspect,
- tool calls are emitted so trajectory/funnel checks can reason about the path.

``model_params.level`` drives the improvement path. At low levels the agent gives
thin answers and misses the important tool/action contract. At high levels it
uses the right tools and returns structured, auditable artifacts.
"""

from __future__ import annotations

import json
from typing import Any

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AdapterToolUse


def run(req: AdapterRequest) -> AdapterResponse:
    """Run a deterministic route-ops copilot turn."""
    level = float(req.get_model_param("level", 0.0))
    complete = level >= 0.5
    story = str((req.context or {}).get("story", "route_read"))

    if story == "visit_prep":
        return _visit_prep(complete)
    if story == "route_edit":
        return _route_edit(complete)
    if story == "gate_reject":
        return _gate_reject(complete)
    if story == "insight_action":
        return _insight_action(complete)
    return _route_read(complete)


def judge(req: AdapterRequest) -> AdapterResponse:
    """Deterministic rubric judge for the example's judge_panel grader."""
    messages = req.input.get("messages", [])
    prompt = messages[0].get("content", "") if messages else ""
    passed = any(
        marker in prompt
        for marker in (
            "Prioritize North Ridge Supply",
            "review the stalled restock",
            "Done. I moved Harbor Parts",
            "I did not save that change",
            "source visit reports",
        )
    )
    verdict = (
        {"label": "pass", "reason": "grounded answer with safe action contract", "score": 1.0}
        if passed
        else {"label": "fail", "reason": "missing safe, auditable action contract", "score": 0.0}
    )
    return AdapterResponse(content=json.dumps(verdict), tokens_input=24, tokens_output=8)


def _route_read(complete: bool) -> AdapterResponse:
    if not complete:
        return _incomplete("route_read", "You have some visits today.", ["today_route"])
    return _response(
        content=(
            "Today you have 4 stops. Prioritize North Ridge Supply first because "
            "it is at risk, then Harbor Parts because it has an open follow-up."
        ),
        story="route_read",
        detected=["today_route", "priority_account", "open_follow_up"],
        artifact={"kind": "route_view", "stop_count": 4, "top_priority": "North Ridge Supply"},
        tools=[
            ("list_route_stops", {"date": "today"}),
            ("list_pending_tasks", {"scope": "current_rep"}),
        ],
    )


def _visit_prep(complete: bool) -> AdapterResponse:
    if not complete:
        return _incomplete("visit_prep", "North Ridge Supply is an account.", ["account_profile"])
    return _response(
        content=(
            "Before North Ridge Supply, review the stalled restock discussion, "
            "ask about their new bay expansion, and confirm Friday's follow-up."
        ),
        story="visit_prep",
        detected=["account_profile", "last_visit", "open_follow_up", "visit_brief"],
        artifact={
            "kind": "visit_brief",
            "account": "North Ridge Supply",
            "talking_points": ["restock blocker", "bay expansion", "Friday follow-up"],
        },
        tools=[
            ("search_accounts", {"query": "North Ridge Supply"}),
            ("get_account", {"account_id": "acct_north_ridge"}),
            ("get_visit_history", {"account_id": "acct_north_ridge"}),
            ("list_pending_tasks", {"account_id": "acct_north_ridge"}),
        ],
    )


def _route_edit(complete: bool) -> AdapterResponse:
    if not complete:
        return _incomplete("route_edit", "I moved the account.", ["route_change"])
    return _response(
        content=(
            "Done. I moved Harbor Parts to tomorrow at 10:30 after checking the "
            "route capacity, and the updated day remains feasible."
        ),
        story="route_edit",
        detected=["resolved_stop_id", "route_change", "feasibility_checked", "committed_change"],
        artifact={
            "kind": "route_change",
            "operation": "move",
            "status": "committed",
            "target_date": "tomorrow",
        },
        tools=[
            ("list_route_stops", {"date": "today"}),
            ("check_route_feasibility", {"operation": "move", "stop_id": "stop_harbor"}),
            ("edit_route", {"operation": "move", "stop_id": "stop_harbor", "date": "tomorrow"}),
        ],
    )


def _gate_reject(complete: bool) -> AdapterResponse:
    if not complete:
        return _incomplete("gate_reject", "I will try to add all visits.", ["route_change"])
    return _response(
        content=(
            "I did not save that change. Forty visits between 8:00 and 9:00 is "
            "infeasible; the route gate found capacity and drive-time violations."
        ),
        story="gate_reject",
        detected=["feasibility_checked", "infeasible_request", "no_write_committed"],
        artifact={
            "kind": "gate_result",
            "operation": "add_many",
            "status": "rejected",
            "violations": ["capacity", "drive_time"],
        },
        tools=[
            ("check_route_feasibility", {"operation": "add_many", "count": 40}),
        ],
    )


def _insight_action(complete: bool) -> AdapterResponse:
    if not complete:
        return _incomplete("insight_action", "Several accounts look risky.", ["account_at_risk"])
    return _response(
        content=(
            "Three accounts are at risk this month. I created recovery tasks for "
            "North Ridge Supply, Harbor Parts, and Summit Fleet, each linked back "
            "to the source visit reports."
        ),
        story="insight_action",
        detected=["account_at_risk", "source_ids", "recovery_tasks", "batch_action"],
        artifact={
            "kind": "insight_action",
            "status": "committed",
            "source_ids": ["visit_101", "visit_118", "visit_121"],
            "task_count": 3,
        },
        tools=[
            ("get_customer_sentiment", {"window": "30d"}),
            (
                "manage_task",
                {
                    "operation": "create_batch",
                    "account_ids": ["acct_north_ridge", "acct_harbor", "acct_summit"],
                },
            ),
        ],
    )


def _incomplete(story: str, content: str, detected: list[str]) -> AdapterResponse:
    return AdapterResponse(
        content=content,
        structured_output={
            "intent_class": story,
            "detected": detected,
            "artifact": {},
            "safety_status": "unknown",
        },
        tool_uses=[],
        tokens_input=10,
        tokens_output=5,
    )


def _response(
    *,
    content: str,
    story: str,
    detected: list[str],
    artifact: dict[str, Any],
    tools: list[tuple[str, dict[str, Any]]],
) -> AdapterResponse:
    return AdapterResponse(
        content=content,
        structured_output={
            "intent_class": story,
            "detected": detected,
            "artifact": artifact,
            "safety_status": "safe",
        },
        tool_uses=[
            AdapterToolUse(tool=tool, tool_use_id=f"tool_{idx}", args=args)
            for idx, (tool, args) in enumerate(tools, start=1)
        ],
        stop_reason="end_turn",
        tokens_input=42,
        tokens_output=24,
        provider_metadata={"provider": "offline", "model": "route-ops-copilot-fixture"},
    )
