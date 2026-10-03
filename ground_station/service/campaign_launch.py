"""Workflow B launch in one command: validate a campaign, write its dated launch copy, print the log plan.

    python -m ground_station.service.campaign_launch <campaign-name-or-yaml> --pack <id> [--rate N]

<campaign> is a saved campaign name (ground_station/service/campaigns/<name>.yaml) or a yaml path. The launch copy
is logs/campaigns/launch/<campaign>_<YYYYmmdd-HHMM>.yaml with ``packs: [<id>]`` and, with --rate, every
experiment's log_plan rate_hz set to N. Everything is validated before the copy is written. The last stdout line
is ``launch copy: <path>``, the campaign_path for campaign_preflight and campaign_go. Any error: exit 2 with one
line on stderr.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SAVED_DIR = ROOT / "ground_station" / "service" / "campaigns"
LAUNCH_DIR = ROOT / "logs" / "campaigns" / "launch"


class LaunchError(ValueError):
    pass


def rel(p: Path) -> str:
    """Repo-relative posix path (8081 runs from the repo root), else the path as given."""
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def resolve_campaign_path(path: str | Path) -> Path:
    """A relative path is tried from the working directory, then from the repo root."""
    p = Path(path)
    return p if p.is_absolute() or p.exists() else ROOT / p


def _pack_ids() -> list[str]:
    try:
        from ground_station.analysis.battery_model import PackRegistry
        return PackRegistry.load(None).pack_ids()
    except Exception:
        return []


def find_campaign(name_or_path: str) -> Path:
    if name_or_path.endswith((".yaml", ".yml")):
        p = resolve_campaign_path(name_or_path)
        if not p.is_file():
            raise LaunchError(f"no campaign file {name_or_path}")
        return p
    saved = SAVED_DIR / f"{name_or_path}.yaml"
    if saved.is_file():
        return saved
    names = ", ".join(p.stem for p in sorted(SAVED_DIR.glob("*.yaml")))
    raise LaunchError(f"unknown campaign {name_or_path!r}; saved campaigns: {names}")


def _head(p: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"mode": "invalid"}
    if not isinstance(data, dict):
        return {"mode": "invalid"}
    return {"mode": data.get("mode", "tune"), "packs": data.get("packs") or []}


def list_campaigns() -> dict[str, Any]:
    """Saved campaigns, launch copies (newest first) and pack ids, for the panel pickers (GET /api/campaign/list)."""
    saved = [{"name": p.stem, "path": rel(p), **_head(p)} for p in sorted(SAVED_DIR.glob("*.yaml"))]
    copies = sorted(LAUNCH_DIR.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if LAUNCH_DIR.is_dir() else []
    launch = [{"name": p.stem, "path": rel(p), "mtime": p.stat().st_mtime, **_head(p)} for p in copies]
    return {"saved": saved, "launch": launch, "packs": _pack_ids()}


def build_launch(src: Path, pack: str, rate: float | None = None) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    """(launch yaml data, Campaign, log plans), all validated; nothing written."""
    from ground_station.service.campaign_logplan import campaign_log_plans
    from ground_station.service.campaign_schema import parse_campaign

    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LaunchError(f"{rel(src)}: not a YAML mapping")
    if not pack or not pack.strip():
        raise LaunchError("--pack is empty")
    data["packs"] = [pack.strip()]
    if rate is not None:
        if rate <= 0:
            raise LaunchError(f"--rate must be > 0, got {rate:g}")
        rate_val: float | int = int(rate) if float(rate).is_integer() else rate
        for exp in data.get("experiments") or []:
            if isinstance(exp, dict):
                exp["log_plan"] = {**(exp.get("log_plan") or {}), "rate_hz": rate_val}
    campaign = parse_campaign(data)
    return data, campaign, campaign_log_plans(campaign)


def write_launch_copy(src: Path, pack: str, rate: float | None = None, out_dir: Path | None = None,
                      now: float | None = None) -> tuple[Path, Any, list[dict[str, Any]]]:
    from ground_station.service.campaign_schema import load_campaign

    data, campaign, plans = build_launch(src, pack, rate)
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(now))
    out = Path(out_dir or LAUNCH_DIR) / f"{campaign.campaign}_{stamp}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# launch copy of {rel(src)} for pack {pack}" + (f", log rate {rate:g} Hz" if rate else "")
              + f"; written {stamp} by campaign_launch. Fly this file, never the template.\n")
    out.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_campaign(out)  # the written copy itself must load
    return out, campaign, plans


def main(argv: list[str] | None = None) -> int:
    from ground_station.livewatch.campaign_capture import CaptureError
    from ground_station.service.campaign_logplan import render
    from ground_station.service.campaign_schema import CampaignError

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign", help="saved campaign name or campaign yaml path")
    ap.add_argument("--pack", required=True, help="battery pack on the drone (packs.yaml id)")
    ap.add_argument("--rate", type=float, default=None, help="log rate for every experiment, Hz")
    args = ap.parse_args(argv)
    try:
        src = find_campaign(args.campaign)
        out, campaign, plans = write_launch_copy(src, args.pack, args.rate)
    except CampaignError as exc:
        print("error: campaign invalid: " + "; ".join(exc.problems), file=sys.stderr)
        return 2
    except (LaunchError, CaptureError, OSError, yaml.YAMLError) as exc:
        print("error: " + " ".join(str(exc).split()), file=sys.stderr)
        return 2
    print(render(campaign, plans))
    print()
    print(f"{campaign.campaign}: {campaign.mode} mode, {len(campaign.experiments)} experiments, pack {args.pack}")
    known = _pack_ids()
    if known and args.pack not in known:
        print(f"warning: pack {args.pack} is not in packs.yaml ({', '.join(known)}): the pack gate will refuse it")
    print(f"launch copy: {rel(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
