"""WP-20 replay on a synthetic handheld log: scale, vertical gate, release hysteresis."""
import numpy as np
import pandas as pd

from ground_station.research import of_gate_replay as R


def _write_log(tmp_path, name="synth"):
    """50 Hz slot1 (flow, quality, Z) + 10 Hz slot0 (yaw), z 0.8 m, yaw 0, no bias.

    t 1-6 s: flow 8 cm/s sideways (40 cm unscaled).
    t 8-11 s: climb 0.5 m/s; flow 8 cm/s during t 8.5-10.5 s (must be gated).
    t 12.00 s: one row with Z below 0.40 m (release-hysteresis probe).
    """
    t = np.arange(0, 15, 0.02)
    dy = np.where((t >= 1) & (t < 6) | (t >= 8.5) & (t < 10.5), 8.0, 0.0)
    zrate = np.where((t >= 8) & (t < 11), 0.5, 0.0)
    zpos = np.where(np.isclose(t, 12.0), 0.30, 0.8)
    slot1 = pd.DataFrame({
        "t_src_ms": np.round(t * 1000).astype(int), "ano_of.of2_dx_fix": 0.0, "ano_of.of2_dy_fix": dy,
        "ano_of.of_quality": 100, "Ctrler.Z_posPID.FB": zpos, "Ctrler.Z_ratePID.FB": zrate,
    })
    t0 = np.arange(0, 15, 0.1)
    slot0 = pd.DataFrame({"t_src_ms": np.round(t0 * 1000).astype(int), "imu_data.yaw": 0.0})
    slot0.to_csv(tmp_path / f"{name}.slot0.csv", index=False)
    slot1.to_csv(tmp_path / f"{name}.slot1.csv", index=False)
    return R.load_flight(tmp_path, name)


def _at(df, pos, t_s):
    return float(pos[np.searchsorted(df["t"].to_numpy(), t_s)])


def test_scale_and_vertical_gate(tmp_path):
    df = _write_log(tmp_path)
    gate, mask = R.vgate(df), R.base_mask(df)
    old = R.integrate(df, 1.0, mask)
    new = R.integrate(df, R.OF_VEL_SCALE, mask & ~gate)
    # Horizontal move: locxPID.FB = earth_y; 8 cm/s for 5 s.
    assert abs(_at(df, old[0], 7.0) - _at(df, old[0], 0.5) - 40.0) < 0.5
    assert abs(_at(df, new[0], 7.0) - _at(df, new[0], 0.5) - 50.0) < 0.5
    # Vertical segment: ungated it fakes 16 cm, gated it must not move.
    assert _at(df, old[0], 11.5) - _at(df, old[0], 7.5) > 15.0
    assert abs(_at(df, new[0], 11.5) - _at(df, new[0], 7.5)) < 0.1
    assert np.allclose(new[1], 0.0)


def test_gate_release_hysteresis(tmp_path):
    df = _write_log(tmp_path)
    gate, t = R.vgate(df), df["t"].to_numpy()
    assert gate[0]                                   # firmware init: gated
    assert not gate[np.searchsorted(t, 0.5)]         # released after 0.3 s clear
    assert gate[np.searchsorted(t, 12.0)]            # one low-Z row gates at once
    assert gate[np.searchsorted(t, 12.2)]            # held through the release time
    assert not gate[np.searchsorted(t, 12.4)]        # released 0.3 s after it cleared
