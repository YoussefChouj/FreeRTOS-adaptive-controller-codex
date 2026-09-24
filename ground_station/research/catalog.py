"""Catalog generator: write CATALOG.md from workflow library + action registry.

Run as ``python -m ground_station.research catalog``.  Writes
``docs/research-platform/CATALOG.md`` deterministically from:
- All YAML workflows in ``research/workflows/``
- Step type signatures from ``ground_station.research.workflow``
- The action registry from ``ground_station.service.agent``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

def generate_catalog() -> str:
    lines: list[str] = []
    lines.append("# Research Workflow Catalog")
    lines.append("")
    lines.append("Auto-generated from the workflow library and action registry.")
    lines.append("")

    # --- Workflows -------------------------------------------------------
    lines.append("## Workflows")
    lines.append("")
    wf_dir = ROOT / "ground_station" / "research" / "workflows"
    if wf_dir.exists():
        for wf in sorted(wf_dir.glob("*.yaml")):
            lines.append(f"### `{wf.stem}`")
            lines.append("")
            lines.append(f"File: `workflows/{wf.name}`")
            lines.append("")
            try:
                text = wf.read_text(encoding="utf-8")
                # Print first few lines as content preview
                for i, line in enumerate(text.splitlines()[:15]):
                    lines.append(f"  {line}")
                if len(text.splitlines()) > 15:
                    lines.append("  ...")
            except Exception:
                lines.append("  (could not read)")
            lines.append("")
    else:
        lines.append("  (no workflows directory)")
        lines.append("")

    # --- Step signatures -------------------------------------------------
    lines.append("## Step Signatures")
    lines.append("")
    lines.append("| Step | Purpose | Args |")
    lines.append("|------|---------|------|")

    steps = [
        ("set_params", "Write firmware parameters",
         "params: dict[str, float]"),
        ("fly_trajectory", "Fly a trajectory preset",
         "trajectory: str, args?: dict"),
        ("capture", "Start telemetry recording",
         "label?: str"),
        ("wait_until", "Wait for a telemetry predicate",
         "key: str, op: str, value: float, timeout_s: int"),
        ("analyze", "Run core metrics",
         "axis?: str"),
        ("revert", "Restore parameters (try/finally)",
         "params: dict[str, float]"),
        ("note", "Leave an operator note",
         "text: str, kind: str"),
        ("call", "Invoke another workflow",
         "workflow: str, params?: dict"),
    ]
    for name, purpose, args in steps:
        lines.append(f"| `{name}` | {purpose} | {args} |")
    lines.append("")

    # --- Action registry -------------------------------------------------
    lines.append("## Action Registry")
    lines.append("")
    try:
        from ground_station.service.agent import (
            UI_ACTION_SPECS, SERVICE_ACTION_SPECS,
        )
        all_actions = {}
        all_actions.update(UI_ACTION_SPECS)
        all_actions.update(SERVICE_ACTION_SPECS)
        lines.append("| Action | Risk | Where | Description |")
        lines.append("|--------|------|-------|-------------|")
        for name in sorted(all_actions):
            spec = all_actions[name]
            risk = spec.get("risk", "?")
            where = spec.get("where", "?")
            desc = spec.get("description", "")
            lines.append(f"| `{name}` | {risk} | {where} | {desc} |")
        lines.append("")
    except Exception as exc:
        lines.append(f"  (action registry unavailable: {exc})")
        lines.append("")

    # --- MCP tools -------------------------------------------------------
    lines.append("## MCP Tools (dashboard)")
    lines.append("")
    lines.append("| Tool | Description |")
    lines.append("|------|-------------|")
    tools = [
        ("get_state", "Agent-facing dashboard snapshot"),
        ("list_actions", "Action registry"),
        ("run_plan", "Submit a multi-step plan"),
        ("get_plan", "Fetch plan detail"),
        ("cancel_plan", "Cancel a plan"),
        ("say", "Agent-to-operator message"),
        ("wait_for_operator", "Long-poll for operator notes"),
        ("get_recording", "Active recording state"),
        ("list_sessions", "List session directories"),
        ("analyze_session", "Session analysis"),
        ("explain_symbol", "Firmware symbol + live value"),
        ("ui_navigate", "Switch dashboard tab"),
        ("ui_highlight", "Highlight a panel"),
        ("file_finding", "Create/list research findings"),
    ]
    for name, desc in tools:
        lines.append(f"| `{name}` | {desc} |")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    out_path = ROOT / "docs" / "research-platform" / "CATALOG.md"
    catalog = generate_catalog()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(catalog, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
