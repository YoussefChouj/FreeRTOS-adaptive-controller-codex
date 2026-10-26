"""Offline CF diagnostics: signal recovery, float32 replay and capture failures."""
import csv
import json

import numpy as np
import pytest

from ground_station.lab import cf_vz_check as check


def _fields(t):
    values = {key: np.zeros(len(t)) for key in check.FAST_FIELDS}
    values["xTickCount"] = np.rint(t * 1000)
    return values


def test_recovers_half_gain_and_delay_at_07_hz():
    """PROPOSED synthetic signal: known amplitude/sign/delay, no sensor model."""
    t = np.arange(0, 80, 0.005)
    omega, delay = 2 * np.pi * 0.7, 0.15
    values = _fields(t)
    values["ano_of.of2_h"] = 0.8 + 0.1 * np.sin(omega * t)
    values["alt_cf.h"] = values["ano_of.of2_h"].copy()
    values["alt_cf.vz"] = 0.5 * 0.1 * omega * np.cos(omega * (t - delay))
    values["alt_cf.a_up"] = -0.1 * omega ** 2 * np.sin(omega * t)
    report, _ = check.analyze(t, values)
    band = report["transfers"]["vz_over_d_range"]["bands"]["0.7"]
    assert band["h1_gain"] == pytest.approx(0.5, abs=0.05)
    assert band["h2_gain"] == pytest.approx(0.5, abs=0.05)
    assert band["geo_gain"] == pytest.approx(0.5, abs=0.05)
    assert band["phase_deg"] == pytest.approx(-360 * 0.7 * delay, abs=5)
    assert "phase_deg" in report["transfers"]["a_up_over_d2_range"]["band_0.2_1.2_hz"]
    assert report["of2_h_std_m"] == pytest.approx(0.1 / np.sqrt(2), abs=1e-4)
    print(f"D4 signal: geo={band['geo_gain']:.6f}, phase={band['phase_deg']:.3f} deg (expected -37.800)")


def _firmware_capture(mode):
    """Independent float32 C-style predict/correct fixture; gate already accepted.

    Models initialized armed estimator and quantized range, not vehicle dynamics,
    arm transitions, dropout resync or accelerometer clipping.
    """
    f = np.float32
    t = np.arange(0, 30, 0.005)
    values = _fields(t)
    h, v, b = f(0.8), f(0), f(0.02)
    n_corr, sensor_count, steps = (1 << 32) - 11, 250, 0
    last_dt, y_corr, error = f(0.005), f(0.8), f(0)
    for i, seconds in enumerate(t):
        raw_a = f(-0.06 * (2 * np.pi * 0.7) ** 2 * np.sin(2 * np.pi * 0.7 * seconds) + 0.025)
        a = f(raw_a - b)
        if i:
            steps += 1
            h += v * f(0.005) + f(0.5) * a * f(0.005) * f(0.005)
            v += a * f(0.005)
            if (i * 70) // 200 != ((i - 1) * 70) // 200:
                last_dt = f(steps) * f(0.005)
                steps = 0
                sensor_count = (sensor_count + 1) % 256
                n_corr = (n_corr + 1) % (1 << 32)
                y_corr = f(np.round((0.8 + 0.06 * np.sin(2 * np.pi * 0.7 * seconds)) * 100) / 100)
                error = y_corr - h
                k1, k2, k3 = f(0.21), f(0.45), f(0.1)
                if mode:
                    k1 = k1 * last_dt / f(0.05)
                    k2 = k2 * last_dt / f(0.05)
                    k3 = k3 * last_dt / f(0.05)
                h += k1 * error
                v += k2 * error
                b -= k3 * error
                b = f(np.clip(b, -1, 1))
        for key, value in (("alt_cf.h", h), ("alt_cf.vz", v), ("alt_cf.b_az", b),
                           ("alt_cf.a_up", a), ("alt_cf.e", error), ("alt_cf.dt_c", last_dt),
                           ("alt_cf.y_corr", y_corr), ("alt_cf.mode", mode),
                           ("alt_cf.n_corr", n_corr), ("ano_of.alt_update_cnt", sensor_count),
                           ("ano_of.of2_h", y_corr), ("ano_of.of_alt_cm", round(float(y_corr) * 100))):
            values[key][i] = value
    return t, values


@pytest.mark.parametrize("mode", [0, 1])
def test_firmware_equivalent_replay_correlation(mode):
    t, values = _firmware_capture(mode)
    report, _ = check.analyze(t, values)
    corr = report["replay"][str(mode)]["correlation_vz"]
    assert corr > 0.99
    assert report["replay_faithful"]
    assert report["n_corr_hz"] == pytest.approx(70, abs=0.1)
    assert report["alt_update_cnt_hz"] == pytest.approx(70, abs=0.1)
    print(f"D4 replay mode {mode}: correlation={corr:.12f}, n_corr={report['n_corr_hz']:.6f} Hz")


def _write_csv(path, t, values, every=1):
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(("timestamp_ns", "slot", "key", "value"))
        for i in range(0, len(t), every):
            for key, column in values.items():
                writer.writerow((int(1e9 + t[i] * 1e9), 2, "slot2." + key, column[i]))


def test_session_output_and_counter_wrap(tmp_path):
    t, values = _firmware_capture(1)
    # Tick wrap must use firmware elapsed time, independent of receive jitter.
    values["xTickCount"] = (values["xTickCount"] + (1 << 32) - 5000) % (1 << 32)
    _write_csv(tmp_path / "telemetry.csv", t, values)
    report = check.run(tmp_path, 1, 28)
    assert report["slot"] == 2
    assert report["firmware_sample_hz"] == pytest.approx(200)
    assert report["replay_faithful"]
    assert json.loads((tmp_path / "cf_vz_check.json").read_text())["replay_faithful"]
    assert (tmp_path / "cf_vz_check.png").stat().st_size > 1000


def test_missing_fast_ticks_cannot_claim_faithful_replay(tmp_path):
    t, values = _firmware_capture(0)
    _write_csv(tmp_path / "telemetry.csv", t, values, every=8)
    report = check.run(tmp_path)
    assert not report["replay_faithful"]
    assert report["estimator_status"] == "estimator is still not understood"
    assert any("hidden inputs" in issue for issue in report["replay_issues"])


def test_missing_fields_are_actionable(tmp_path):
    t, values = _firmware_capture(0)
    del values["alt_cf.y_corr"]
    _write_csv(tmp_path / "telemetry.csv", t, values)
    with pytest.raises(ValueError, match="no complete CF fast-slot frames"):
        check.read_fast_slot(tmp_path)
