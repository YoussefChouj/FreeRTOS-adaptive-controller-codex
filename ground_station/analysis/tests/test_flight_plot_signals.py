"""Regression coverage for current recorder symbols and moved subscribe slots."""
import csv
import math

import pytest

from ground_station.analysis import flight_report as report
from ground_station.lab import labday


@pytest.mark.parametrize("prefix", ["", "slot0.", "slot3."])
def test_resolve_current_symbols_and_rate_roles(tmp_path, prefix):
    names = ["imu_data.rol", "Ctrler.rollPID.Des", "Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.Des",
             "mymotor.motor1", "u_gyrox"]
    data = {prefix + k: ([0, 100_000_000], [0.0, 1.0]) for k in names}
    configured = report._normalize_signal_map({"gyro_roll": "slot1.Ctrler.gyroxPID.FB",
                                               "roll_rate_sp": "slot1.Ctrler.gyroxPID.Des"})
    signals = report._resolve_signal_map({**report.DEFAULT_SIGNALS, **configured}, data)
    assert signals["roll"] == [prefix + "imu_data.rol"]
    assert signals["gyro_x"] == [prefix + "Ctrler.gyroxPID.FB"]
    assert signals["gyro_x_sp"] == [prefix + "Ctrler.gyroxPID.Des"]
    assert signals["motor1"] == [prefix + "mymotor.motor1"]
    assert signals["control_out_roll"] == [prefix + "u_gyrox"]
    segmentation = {"armed_start_ns": None, "armed_end_ns": None, "used_whole_log": True}
    metrics = report.compute_metrics(data, segmentation, signals, [])
    assert metrics["rate"]["roll"]["rmse"] == 0


def test_fallback_resolution_does_not_guess_between_slots():
    data = {"slot1.a": ([], []), "slot2.a": ([], []), "second": ([], [])}
    assert report._resolve_signal_map({"test": ["missing", "second"]}, data)["test"] == ["second"]
    assert report._resolve_signal_map({"test": ["a"]}, data)["test"] == [""]


def test_labday_plots_current_recorder_csv(tmp_path, monkeypatch):
    # Synthetic PROPOSED excitation, using the names recorded by smoke-hover.
    folder = tmp_path / "smoke-hover"
    folder.mkdir()
    with (folder / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["received_ns", "slot", "key", "value"])
        for i in range(120):
            t = i / 25
            vals = {"g_wfb_status.prim_state": 2, "g_wfb_status.safety_trip": 0,
                    "Ctrler.Z_posPID.FB": .8 + .02 * math.sin(t), "Ctrler.Z_posPID.Des": .8,
                    "Ctrler.locxPID.FB": math.sin(t), "Ctrler.locxPID.Des": 0,
                    "Ctrler.locyPID.FB": math.cos(t), "Ctrler.locyPID.Des": 0,
                    "imu_data.rol": math.sin(t), "imu_data.pit": math.cos(t), "imu_data.yaw": 0,
                    "Ctrler.rollPID.Des": 0, "Ctrler.pitchPID.Des": 0, "yaw_sp": 0,
                    "Ctrler.gyroxPID.FB": math.cos(t), "Ctrler.gyroxPID.Des": 0,
                    "Ctrler.gyroyPID.FB": math.sin(t), "Ctrler.gyroyPID.Des": 0,
                    "Ctrler.gyrozPID.FB": math.cos(t), "Ctrler.gyrozPID.Des": 0,
                    "u_gyrox": math.sin(t), "u_gyroy": math.cos(t), "u_gyroz": 0}
            vals.update({f"mymotor.motor{j}": 3000 + i + j for j in range(1, 5)})
            for k, v in vals.items():
                # Feedback in a moved slot, commands bare, as in the real CSV.
                key = "slot0." + k if k.endswith(".FB") or k.startswith("imu_data.") else k
                writer.writerow([1_000_000_000 + int(t * 1e9), 0, key, v])

    def no_placeholder(*args, **kwargs):
        pytest.fail("valid telemetry must reach the tracking and analysis plotters")

    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
    drawn = {}
    close = plt.close

    def capture(fig=None):
        if isinstance(fig, Figure) and fig._suptitle:
            drawn[fig._suptitle.get_text()] = [len(ax.lines) for ax in fig.axes]
        close(fig)

    monkeypatch.setattr(plt, "close", capture)
    monkeypatch.setattr(labday, "_save_fallback_png", no_placeholder)
    plots = labday.generate_flight_plots(folder, flight_id="smoke-hover", root=tmp_path)
    assert plots >= 8
    assert all(n >= 2 for n in drawn["Rate Tracking"])
    assert all(n >= 2 for n in drawn["Attitude Tracking"][:3])
    assert drawn["Control Effort & Motor Outputs"] == [3, 4]
    import json
    metrics = json.loads((folder / "metrics.json").read_text())
    assert set(metrics["rate"]) == {"roll", "pitch", "yaw"}
    assert metrics["control_effort"]["motor_count"] == 4
    for name in ("tracking", "analysis", "attitude_tracking", "rate_tracking", "control_effort",
                 "error_distribution", "psd_error"):
        assert (folder / "plots" / f"{name}.png").stat().st_size > 1000


def test_tracking_aligns_different_rates_without_stretching_time():
    data = {"measured": ([0, 500, 1000, 1500, 2000], [0, .5, 1, 1.5, 2]),
            "reference": ([0, 1000, 2000], [0, 1, 2])}
    t, actual, desired = report._aligned_tracking(data, "measured", "reference")
    assert t == data["measured"][0]
    assert actual == desired
