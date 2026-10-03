"""campaign_logplan CLI: the launch-approval table and the saved plan JSON for a campaign file."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ground_station.livewatch.campaign_capture import CAMPAIGN_SET, OPTICAL_FLOW, VELOCITY_LOOPS
from ground_station.service.campaign_logplan import main
from ground_station.service.campaign_schema import load_campaign

HOVER_LADDER = Path(__file__).resolve().parents[1] / "campaigns" / "hover_ladder.yaml"


def test_hover_ladder_is_a_three_rung_fly_campaign():
    c = load_campaign(HOVER_LADDER)
    assert c.mode == "fly" and c.max_flights == 3
    assert [e.scenario_args["z"] for e in c.experiments] == [0.5, 0.7, 1.3]
    assert all(e.scenario.name == "hover" and e.log_plan for e in c.experiments)
    assert [s.duration_s for e in c.experiments for s in e.scenario.steps if s.kind == "hold"] == [20.0] * 3


def test_cli_prints_one_table_per_experiment_and_writes_json(tmp_path, capsys):
    out = tmp_path / "log_plan.json"
    assert main([str(HOVER_LADDER), "--max-rate", "25", "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert text.count("| core (always on) |") == 3 and "requested 50 Hz" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["max_rate_hz"] == 25 and len(data["experiments"]) == 3
    first = data["experiments"][0]
    assert first["plan"]["rate_hz"] == 25.0
    assert first["subscribe_steps"][0]["args"]["divider"] == 4
    assert first["subscribe_steps"][0]["args"]["ranges"] == list(CAMPAIGN_SET["needed"] + VELOCITY_LOOPS + OPTICAL_FLOW)


def test_cli_reports_a_bad_log_plan(tmp_path, capsys):
    data = yaml.safe_load(HOVER_LADDER.read_text(encoding="utf-8"))
    data["experiments"][1]["log_plan"] = {"rate_hz": 50, "groups": ["gyro"]}
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert main([str(bad)]) == 2
    assert "experiments.1.log_plan: groups: unknown group 'gyro'" in capsys.readouterr().err
