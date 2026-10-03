"""WP-23 B: one launch command writes a validated launch copy and prints the log plan; errors are one line."""

from __future__ import annotations

import pytest

from ground_station.service import campaign_launch
from ground_station.service.campaign_schema import load_campaign


@pytest.fixture
def launch_dir(tmp_path, monkeypatch):
    out = tmp_path / "launch"
    monkeypatch.setattr(campaign_launch, "LAUNCH_DIR", out)
    return out


def _one_line_error(capsys, argv):
    assert campaign_launch.main(argv) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("error: ") and "\n" not in err, err
    return err


def test_launch_writes_copy_and_prints_table(launch_dir, capsys):
    assert campaign_launch.main(["hover_ladder", "--pack", "P4000-2"]) == 0
    out = capsys.readouterr().out
    copies = list(launch_dir.glob("hover_ladder_*.yaml"))
    assert len(copies) == 1
    c = load_campaign(copies[0])
    assert c.packs == ("P4000-2",) and [e.name for e in c.experiments] == ["hover_z050", "hover_z070", "hover_z130"]
    assert "# Log plan: hover_ladder" in out and "| core (always on) |" in out
    assert out.strip().splitlines()[-1].startswith("launch copy: ") and copies[0].name in out
    assert "warning" not in out


def test_rate_overrides_every_experiment_and_unknown_pack_warns(launch_dir, capsys):
    assert campaign_launch.main(["ground_station/service/campaigns/hover_ladder.yaml", "--pack", "TEST-1",
                                 "--rate", "25"]) == 0
    out = capsys.readouterr().out
    c = load_campaign(next(launch_dir.glob("*.yaml")))
    assert [e.log_plan["rate_hz"] for e in c.experiments] == [25, 25, 25]
    assert [e.log_plan["groups"] for e in c.experiments][0] == ["velocity_loops", "optical_flow"]
    assert "warning: pack TEST-1 is not in packs.yaml" in out


def test_errors_are_one_line_and_write_nothing(launch_dir, capsys, tmp_path):
    assert "unknown campaign 'nope'" in _one_line_error(capsys, ["nope", "--pack", "P4000-1"])
    assert "no campaign file" in _one_line_error(capsys, ["missing.yaml", "--pack", "P4000-1"])
    bad = tmp_path / "bad.yaml"
    bad.write_text("campaign: Bad Name\nmode: fly\n", encoding="utf-8")
    assert "campaign invalid" in _one_line_error(capsys, [str(bad), "--pack", "P4000-1"])
    assert "--rate must be > 0" in _one_line_error(capsys, ["hover_ladder", "--pack", "P4000-1", "--rate", "0"])
    assert not launch_dir.exists() or not list(launch_dir.iterdir())


def test_list_campaigns(launch_dir, capsys):
    campaign_launch.main(["hover_ladder", "--pack", "P4000-1"])
    listing = campaign_launch.list_campaigns()
    saved = {c["name"]: c for c in listing["saved"]}
    assert saved["hover_ladder"]["mode"] == "fly" and saved["hover_ladder"]["path"].endswith("hover_ladder.yaml")
    assert listing["launch"][0]["name"].startswith("hover_ladder_") and listing["launch"][0]["packs"] == ["P4000-1"]
    assert {"P4000-1", "P4000-2", "P5300-1"} <= set(listing["packs"])
