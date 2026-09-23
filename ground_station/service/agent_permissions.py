"""Agent-facing discovery documents: a permission manifest and llms.txt.

Why this exists
---------------
An agent that arrives at this dashboard without having read
``docs/dashboard-platform/AGENT_GUIDE.md`` has no way to learn that clicking
one button switches a tab and clicking another writes a flight-critical
parameter to a drone that is powered on. Everything the service knows about
that distinction already exists -- the action registry carries ``risk``, the
control layer carries ``mode``, ``allow_agent_arm`` and ``tier0_access`` -- but
only behind routes an agent has to be told about first.

These two documents are the conventional places an agent looks *before* it is
told anything:

  ``/.well-known/agent-permissions.json``
      The Lightweight Agent Standards permission-manifest shape
      (arXiv:2601.02371): ``metadata``, ``resource_rules``,
      ``action_guidelines``, ``api``. Resource rules are CSS selectors with a
      verb and an ``allowed`` flag; modifiers carry rate limits and human-
      approval requirements.

  ``/llms.txt``
      The llms.txt convention: a short Markdown map pointing at the documents
      and routes that are worth reading, so an agent spends its context on the
      right four files instead of crawling the tree.

Design note
-----------
Both documents are *generated*, never hand-maintained. The manifest reads the
live action registry and the live control state on every request, so it cannot
drift from what the service will actually permit -- and it tells the truth
about the current mode rather than a mode someone documented once. The only
hand-written part is SELECTOR_RULES, because risk lives on actions and the DOM
does not; every selector in it was counted against the running dashboard
before being added (see the comment on each).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# DOM-level rules.
#
# The action registry knows that ``set_param`` is critical; it does not know
# which button on the page performs one. This table is that bridge, and it is
# the one part of the manifest that can go stale, so keep it short and keep
# every entry verified.
#
# Match counts below were measured against the running dashboard on
# 2026-09-23 by walking all 11 workspace tabs and taking the maximum count
# per selector. A selector that matched nothing was left out rather than
# guessed at -- an entry claiming to govern a control that does not exist is
# worse than no entry, because an agent will believe it.
# ---------------------------------------------------------------------------
SELECTOR_RULES: list[dict[str, Any]] = [
    {
        "verb": "click_element",
        "selector": ".ws-tab",
        "allowed": True,
        "description": (
            "Workspace tabs (11). Navigation only -- switching a tab sends "
            "nothing to the drone. Prefer role=tab in the accessibility tree; "
            "aria-selected carries which one is active."
        ),
    },
    {
        "verb": "click_element",
        "selector": ".cp-params-row-apply",
        "allowed": False,
        "modifiers": {"requires_human_approval": True},
        "description": (
            "Parameters panel Apply buttons (85). Each transmits one "
            "parameter write to a powered-on drone. The service enforces its "
            "own arm/precondition interlock regardless of what an agent does "
            "here; this rule exists so an agent does not attempt the click in "
            "the first place."
        ),
    },
    {
        "verb": "click_element",
        "selector": ".cp-submit",
        "allowed": False,
        "modifiers": {"requires_human_approval": True},
        "description": (
            "Command Panel submit buttons (18). Same class of effect as "
            "Apply: these transmit commands to the flight controller."
        ),
    },
    {
        "verb": "submit_form",
        "selector": "[data-cmdid]",
        "allowed": False,
        "modifiers": {"requires_human_approval": True},
        "description": (
            "Any control carrying a firmware command id (94). Treat the "
            "attribute itself as the marker for a transmitting control."
        ),
    },
    {
        "verb": "fill_input",
        "selector": "#cp-params-search",
        "allowed": True,
        "description": (
            "Parameter filter box. Local filtering only; sends nothing."
        ),
    },
    {
        "verb": "fill_input",
        "selector": ".cp-params-row-input, .cp-params-row-slider",
        "allowed": True,
        "modifiers": {"note": "staging only"},
        "description": (
            "Parameter value entry (85 inputs, 53 sliders). Typing or "
            "dragging stages a value and transmits nothing -- only Apply "
            "transmits. Out-of-range values are flagged, not clamped."
        ),
    },
]

ACTION_GUIDELINES = [
    "Agents MUST NOT arm this aircraft, command motors, or command throttle "
    "unless the operator has asked for it in the current session. The "
    "allow_agent_arm flag being true is a capability, not an instruction.",
    "Agents MUST NOT POST to this service directly. Drive it through the "
    "dashboard MCP server, or through the plan/approval queue at "
    "POST /api/agent/plans, so every action lands in the activity journal.",
    "Agents SHOULD read GET /api/routes and GET /api/agent/actions before "
    "acting. Both are self-describing and current; this manifest is a "
    "summary of them, not a replacement.",
    "Agents SHOULD read the page through the accessibility tree. Panels are "
    "role=region with accessible names, gated panels carry their "
    "preconditions in that name, and the gate bar is an aria-live status "
    "region.",
    "Agents SHOULD NOT treat a telemetry slot index as stable. Slot layout "
    "is a subscribe-time choice; resolve values by name across all slots.",
]


def _risk_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the live action registry into per-risk name lists."""
    out: dict[str, list[str]] = {}
    for spec in actions or []:
        name = spec.get("name")
        if not name:
            continue
        out.setdefault(str(spec.get("risk") or "unknown"), []).append(str(name))
    for names in out.values():
        names.sort()
    return out


def build_permission_manifest(
    actions: list[dict[str, Any]] | None,
    control: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build /.well-known/agent-permissions.json from live service state.

    ``actions`` is the output of ``Agent.action_specs()`` and ``control`` the
    output of ``Agent.control_state()``. Both are passed in rather than
    imported so this stays a pure function and the caller decides what a
    missing agent layer means.
    """
    control = control or {}
    actions = actions or []
    by_risk = _risk_summary(actions)
    mode = control.get("mode")

    return {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _dt.datetime.now(_dt.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "author": "ground_station.service",
            "subject": "UAV adaptive-controller ground station dashboard",
            "note": (
                "Generated per request from the live action registry and "
                "control state. Never hand-edited, so it cannot describe a "
                "permission the service does not actually enforce."
            ),
        },
        # The honest headline: this page commands real hardware.
        "safety_context": {
            "controls_physical_hardware": True,
            "hardware": "quadrotor UAV, powered on, tethered to this host",
            "control_mode": mode,
            "allow_agent_arm": control.get("allow_agent_arm"),
            "tier0_access": control.get("tier0_access"),
            "enforcement": (
                "Server-side. This manifest is advisory; the service refuses "
                "critical actions on its own regardless of whether an agent "
                "read this file."
            ),
        },
        "resource_rules": SELECTOR_RULES,
        "action_guidelines": ACTION_GUIDELINES,
        "api": {
            "routes": "/api/routes",
            "actions": "/api/agent/actions",
            "action_names_by_risk": by_risk,
            "state": "/api/agent/state",
            "submit_plan": "POST /api/agent/plans",
            "approvals": "/api/agent/approvals",
            "events": "/api/agent/stream",
            "guide": "/docs/dashboard-platform/AGENT_GUIDE.md",
        },
    }


def build_llms_txt(control: dict[str, Any] | None) -> str:
    """Build /llms.txt: a short Markdown map for an arriving agent."""
    control = control or {}
    mode = control.get("mode") or "unknown"
    return f"""# UAV Ground Station Dashboard

> Live telemetry, command and tuning surface for a six-degree-of-freedom
> adaptive flight controller. This service talks to a real quadrotor that is
> powered on. Read before acting; it is not a demo.

Agent control mode right now: **{mode}**.

## Read these first

- [Agent guide](/docs/dashboard-platform/AGENT_GUIDE.md): ground rules, stable
  UI selectors, how to drive the dashboard programmatically, and the traps
  that have cost real work.
- [Permission manifest](/.well-known/agent-permissions.json): which controls
  an agent may operate, which need a human, and the current safety context.
- [Route map](/api/routes): every GET and POST this service answers, with a
  one-line description each. Self-describing and current.
- [Action registry](/api/agent/actions): the actions an agent can request,
  each with a risk class and a JSON Schema for its arguments.
- [Telemetry protocol](/docs/telemetry-protocol.md): the subscribe wire format.

## How to act

Do not POST to this service directly. Submit a plan to
`POST /api/agent/plans` and let the approval queue run it, or use the
dashboard MCP server. Both route every action through the activity journal at
`/api/agent/history`.

## How to read the page

The shell exposes its structure through the accessibility tree: the workspace
bar is a `tablist`, each panel is a `region` with an accessible name, gated
panels name their preconditions in that name, and the gate bar is an
`aria-live` status region. Read that in preference to scraping the DOM.

## Do not

Arm the aircraft, command motors or command throttle unless the operator asked
for it in this session. Flashing firmware is blocked while armed.
"""
