"""autotune_sweep: CLI verdict on an exported log, natural-excitation FRF gates on coherent and incoherent hovers."""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from ground_station.analysis import autotune_sweep as at
from ground_station.autotune.design import read_pid_rows


def _hover(coherent: bool, seconds=60.0, fs=50.0, seed=0):
    t = np.arange(0.0, seconds, 1.0 / fs)
    rng = np.random.default_rng(seed)
    s = {"flight_phase": ((t > 1) & (t < seconds - 1)).astype(float), "DroneStatus.ARM_Status": np.ones_like(t)}
    for pid in ("gyroxPID", "gyroyPID", "gyrozPID"):
        r = lfilter([0.3], [1.0, -0.7], 20.0 * rng.standard_normal(len(t)))           # broadband setpoint
        a = np.exp(-30.0 / fs)                                                        # 30 rad/s closed loop
        x = lfilter([1 - a], [1.0, -a], r) if coherent else 20.0 * rng.standard_normal(len(t))
        s[f"Ctrler.{pid}.Des"], s[f"Ctrler.{pid}.FB"] = r, x + 0.5 * rng.standard_normal(len(t))
    return {k: (t, v) for k, v in s.items()}


def test_cli_refuses_hover_without_excitation(tmp_path):
    csv = tmp_path / "log.csv"
    assert at.export_csv(_hover(True), csv)
    v = at.cli_verdict(csv, "roll", tmp_path)
    assert v["code"] == 2 and "multisine" in v["reason"]
    assert not at.export_csv({"x": (np.arange(3.0), np.arange(3.0))}, tmp_path / "none.csv")


def test_natural_id_gates():
    flown = read_pid_rows()["gyroxPID"]
    good = at.natural_id(_hover(True), "roll", flown)
    assert good["coverage"] >= 0.5 and good["bins"] > 4 and good["coh_med"] > 0.6 and "fit" in good
    bad = at.natural_id(_hover(False), "roll", flown)
    assert bad["status"] == "refused" and bad["coh_med"] < 0.2
    assert at.natural_id({"flight_phase": (np.arange(5.0), np.zeros(5)),
                          "Ctrler.gyroxPID.Des": (np.arange(5.0), np.zeros(5)),
                          "Ctrler.gyroxPID.FB": (np.arange(5.0), np.zeros(5))}, "roll", flown)["status"] == "no airborne span"
