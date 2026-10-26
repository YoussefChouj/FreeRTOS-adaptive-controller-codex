"""Acceptance tests for n26-sysid-chain: multi-loop (rate, angle, vel, pos) recovery within 5%,
0.2-40 Hz rate run with 200 Hz sampling + tick jitter + delay, directory --out fix, and short-log refusal."""

from __future__ import annotations

import json

import pytest

from ground_station.autotune import cli, excitation as ex, synth
from ground_station.autotune.design import Plant


@pytest.mark.parametrize("loop,axis,plant,excite", [
    ("rate", "roll", Plant(k=9.0, tau_s=0.05, delay_s=0.015),
     {"signal": "multisine", "f0": 0.2, "f1": 40.0, "amp": 60.0, "duration_s": 40.0}),
    ("angle", "roll", Plant(k=1.0, tau_s=0.06, delay_s=0.020),
     {"signal": "multisine", "f0": 0.2, "f1": 6.0, "amp": 8.0, "duration_s": 35.0}),
    ("vel", "x", Plant(k=0.70, tau_s=0.08, delay_s=0.025),
     {"signal": "multisine", "f0": 0.2, "f1": 6.0, "amp": 25.0, "duration_s": 40.0}),
    ("pos", "x", Plant(k=1.0, tau_s=0.15, delay_s=0.030),
     {"signal": "multisine", "f0": 0.15, "f1": 4.0, "amp": 15.0, "duration_s": 45.0}),
])
def test_recover_known_plant_per_loop_within_5pct(tmp_path, loop, axis, plant, excite):
    log_dir = tmp_path / f"sim_{loop}_{axis}"
    out_dir = tmp_path / f"out_{loop}_{axis}"
    out_dir.mkdir(parents=True, exist_ok=True)  # pre-existing dir: verifies Windows PermissionError fix

    df = synth.simulate_loop(loop, axis, plant, excite=excite, noise_std=0.01, seed=7,
                             log_hz=200, tick_jitter_ms=0.25)
    synth.write_session(log_dir, df)

    argv = [
        str(log_dir), "--loop", loop, "--axis", axis,
        "--f0", str(excite["f0"]), "--f1", str(excite["f1"]),
        "--amp", str(excite["amp"]), "--duration", str(excite["duration_s"]),
        "--out", str(out_dir), "--stem", f"{loop}_{axis}",
    ]
    rc = cli.main(argv)
    assert rc in (0, 2)  # fit must succeed; gain design may propose or refuse if current already optimal
    model_path = out_dir / f"model_{loop}_{axis}.json"
    assert model_path.is_file()
    assert (out_dir / f"{loop}_{axis}.png").is_file()
    assert (out_dir / f"{loop}_{axis}.csv").is_file()
    assert (out_dir / f"{loop}_{axis}.caption.txt").is_file()

    data = json.loads(model_path.read_text(encoding="utf-8"))
    fit = data["fit"]
    assert fit is not None
    assert abs(fit["k"] - plant.k) / plant.k <= 0.05, f"{loop} k: got {fit['k']}, want {plant.k}"
    assert abs(fit["tau_s"] - plant.tau_s) / plant.tau_s <= 0.05, f"{loop} tau: got {fit['tau_s']}, want {plant.tau_s}"
    assert abs(fit["delay_s"] - plant.delay_s) <= max(0.05 * plant.delay_s, 0.002), (
        f"{loop} delay: got {fit['delay_s']}, want {plant.delay_s}"
    )


def test_too_short_log_is_refused(tmp_path, capsys):
    log_dir = tmp_path / "short_log"
    out_dir = tmp_path / "short_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Simulate only 8 s total while CLI expects 30 s excitation
    short_ex = {"signal": "multisine", "f0": 0.5, "f1": 15.0, "amp": 60.0, "duration_s": 5.0}
    df = synth.simulate_loop("rate", "yaw", Plant(k=1.25, tau_s=0.01, delay_s=0.012),
                             excite=short_ex, pre_s=1.0, post_s=1.0, seed=1)
    synth.write_session(log_dir, df)

    rc = cli.main([str(log_dir), "--loop", "rate", "--axis", "yaw", "--duration", "30.0", "--out", str(out_dir)])
    assert rc == 2
    out_text = capsys.readouterr().out
    assert "REFUSED:" in out_text and "shorter than half the excitation" in out_text
    data = json.loads((out_dir / "model_rate_yaw.json").read_text(encoding="utf-8"))
    assert data["status"] == "refused"


def test_2026_10_27_deck_loads_and_all_fly_files_exist():
    from ground_station.lab import labday
    d = labday.load_day(labday.day_path("2026-10-27"))
    ids = [f["id"] for f in d["flights"]]
    for expected in (
        "smoke-hover", "rate-sysid-roll-wide", "rate-sysid-pitch-wide", "rate-sysid-yaw-wide",
        "angle-sysid-roll-pitch", "vel-sysid-xy", "vel-sysid-z", "pos-sysid-xy", "pos-sysid-z",
        "model-based-pid", "model-based-lqr", "model-based-mpc", "model-based-adrc",
    ):
        assert expected in ids, f"missing {expected} in 2026-10-27.yaml"

