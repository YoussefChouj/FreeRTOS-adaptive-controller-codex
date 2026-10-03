"""Print a campaign's per-experiment log plan for the operator's launch approval (workflow B launch Q3).

    python -m ground_station.service.campaign_logplan <campaign.yaml> [--max-rate HZ] [--json OUT]

Each experiment's ``log_plan`` (rate_hz + groups) goes through campaign_capture.plan_capture, capped at
``--max-rate`` (probe_max_rate on the live link). ``--json`` writes the plans and their agent subscribe
steps; that file is saved with the campaign logs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ground_station.livewatch.campaign_capture import CaptureError, log_plan_table, plan_capture, subscribe_steps
from ground_station.service.campaign_schema import Campaign, CampaignError, load_campaign


def campaign_log_plans(campaign: Campaign, max_rate_hz: float | None = None) -> list[dict[str, Any]]:
    """One entry per experiment: name, plan_capture() plan and the subscribe steps that apply it."""
    out = []
    for exp in campaign.experiments:
        plan = plan_capture(exp.log_plan or None, max_rate_hz=max_rate_hz)
        out.append({"experiment": exp.name, "log_plan": dict(exp.log_plan), "plan": plan,
                    "subscribe_steps": subscribe_steps(plan)})
    return out


def render(campaign: Campaign, plans: list[dict[str, Any]]) -> str:
    parts = [f"# Log plan: {campaign.campaign}"]
    for entry in plans:
        parts += ["", f"## {entry['experiment']}", log_plan_table(entry["plan"])]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign", type=Path)
    ap.add_argument("--max-rate", type=float, default=None, help="probed loss-free stream rate, Hz")
    ap.add_argument("--json", type=Path, default=None, help="write plans + subscribe steps here")
    args = ap.parse_args(argv)
    try:
        campaign = load_campaign(args.campaign)
        plans = campaign_log_plans(campaign, args.max_rate)
    except (CampaignError, CaptureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        for p in getattr(exc, "problems", ()):
            print(f"  {p}", file=sys.stderr)
        return 2
    print(render(campaign, plans))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"campaign": campaign.campaign, "max_rate_hz": args.max_rate,
                                         "experiments": plans}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
