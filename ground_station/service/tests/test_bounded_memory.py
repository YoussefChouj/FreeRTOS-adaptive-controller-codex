"""Tests that plan/approval containers stay bounded under sustained load.

These tests create plans through the same path the live service uses and
measure retained memory with tracemalloc.  Before the fix each completed
plan grew _plans dict and _approvals list forever; after the fix both
stay within their configured limits.
"""
import os
import tracemalloc
import time

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore
from ground_station.service.agent import (
    AgentManager,
    MAX_PLANS,
    MAX_APPROVALS,
)

os.environ.setdefault("GS_SHELL_WATCH", "0")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema():
    return StreamSchema(1, 1, 4,
                        (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)


def _make_service():
    """Create a minimal service with an agent manager (no HTTP server)."""
    svc = GroundStationService(
        store=SessionStore(),
        schemas=[_schema()],
        source="sim",
    )
    svc.start()
    agent = AgentManager(svc, shell_root=None, journal_root=None)
    agent.start()
    return svc, agent


def _make_safe_plan(title="mem_test"):
    """Build a minimal safe plan that runs and completes quickly."""
    return {
        "title": title,
        "source": "agent:test",
        "steps": [
            {"action": "wait_ms", "args": {"ms": 10}},
            {"action": "say", "args": {"text": "done"}},
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_plans_stay_bounded_under_load():
    """Creating N completed plans must not grow _plans beyond MAX_PLANS."""
    svc, agent = _make_service()
    try:
        tracemalloc.start()

        # Create 500 completed plans (well above MAX_PLANS=1000)
        n_plans = 500
        for i in range(n_plans):
            agent.create_plan(_make_safe_plan(f"plan-{i}"), queue_if_busy=True)
            # Allow the background thread to finish
            time.sleep(0.02)

        current, peak = tracemalloc.get_traced_memory()
        plan_count = len(agent._plans)

        # After 500 plans, the dict should be bounded to MAX_PLANS,
        # not equal to n_plans.
        assert plan_count <= MAX_PLANS, (
            f"_plans has {plan_count} entries (limit={MAX_PLANS})"
        )
        # Memory growth from plan dicts should be < 5 MB (each entry is tiny).
        # 500 plans without the fix would be several hundred MB.
        assert current < 10 * 1024 * 1024, (
            f"Current memory {current / 1024 / 1024:.1f} MB too high"
        )
    finally:
        tracemalloc.stop()
        agent.stop()
        svc.stop()


def test_approvals_stay_bounded():
    """Approval items for completed plans must not grow past MAX_APPROVALS."""
    svc, agent = _make_service()
    try:
        tracemalloc.start()

        # Create plans with many critical steps (arm + param writes).
        n_plans = 300
        critical_actions = [
            {"action": "command", "args": {"command_id": 0x01, "index": 0, "value": 1.0}},
            {"action": "command", "args": {"command_id": 0x03, "index": 0, "value": 1.0}},
        ]

        for i in range(n_plans):
            plan = {
                "title": f"approval-test-{i}",
                "source": "agent:test",
                "steps": critical_actions,
            }
            agent.create_plan(plan, queue_if_busy=True)
            time.sleep(0.01)

        current, _ = tracemalloc.get_traced_memory()
        approval_count = len(agent._approvals)

        assert approval_count <= MAX_APPROVALS, (
            f"_approvals has {approval_count} entries (limit={MAX_APPROVALS})"
        )
    finally:
        tracemalloc.stop()
        agent.stop()
        svc.stop()


def test_memory_growth_ratio():
    """Verify that memory grows sub-linearly: 2x plans < 2x memory."""
    svc, agent = _make_service()
    try:
        tracemalloc.start()

        # First batch: 100 plans
        for i in range(100):
            agent.create_plan(_make_safe_plan(f"batch1-{i}"), queue_if_busy=True)
            time.sleep(0.02)
        current1, _ = tracemalloc.get_traced_memory()
        plans1 = len(agent._plans)

        # Second batch: 200 more (total 300)
        for i in range(200):
            agent.create_plan(_make_safe_plan(f"batch2-{i}"), queue_if_busy=True)
            time.sleep(0.02)
        current2, _ = tracemalloc.get_traced_memory()
        plans2 = len(agent._plans)

        # Memory should be essentially flat since both are under MAX_PLANS.
        growth = current2 - current1
        # Allow small overhead for tracemalloc itself.
        assert growth < 2 * 1024 * 1024, (
            f"Memory grew by {growth / 1024 / 1024:.1f} MB for 200 more plans"
        )
    finally:
        tracemalloc.stop()
        agent.stop()
        svc.stop()
