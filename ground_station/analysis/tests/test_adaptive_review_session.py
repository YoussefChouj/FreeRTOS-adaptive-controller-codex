"""adaptive_review on a dashboard / campaign session dir: telemetry.csv rows become the same 100 Hz frame as the
stream logs, and the injection flag (mrac_3l group) splits a flight into PID and MRAC segments; without it the
segments are unlabelled instead of failing."""
from __future__ import annotations

import numpy as np

from ground_station.analysis import adaptive_review as ar


def _session(tmp_path, with_flag):
    t = np.arange(0.0, 20.0, 0.02)                          # 50 Hz, airborne 2..16 s, MRAC on from 9 s
    sig = {"Ctrler.Z_posPID.FB": np.where((t > 2) & (t < 16), 0.8, 0.0),
           "mrac_state.pitch.u_nom": 0.01 * np.sin(t)}
    sig.update({m: np.full_like(t, 1600.0) for m in ar.MOTORS})
    if with_flag:
        sig["mrac_flags.output_injection_on"] = (t >= 9).astype(float)
    rows = ["received_ns,slot,key,value"]
    rows += ["%d,0,%s,%r" % (int(ti * 1e9), k, float(v[i])) for i, ti in enumerate(t) for k, v in sig.items()]
    d = tmp_path / ("sess%d" % with_flag)
    d.mkdir()
    (d / "telemetry.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return d


def test_session_dir_loads_on_the_100hz_grid_and_splits_pid_from_mrac(tmp_path):
    df = ar.load(str(_session(tmp_path, True)))
    assert np.allclose(np.diff(df.t.to_numpy()), 1.0 / ar.FS)
    assert df.t.iloc[0] == 0.0 and abs(df.t.iloc[-1] - 19.98) < 0.02
    assert np.isclose(df["mrac_state.pitch.u_nom"].iloc[-1], 0.01 * np.sin(19.98), atol=1e-3)
    segs, _, inj = ar.segments(df)
    assert [s[0] for s in segs] == ["F1 PID", "F1 MRAC"]
    assert inj.max() == 1


def test_session_without_the_injection_flag_gives_unlabelled_segments(tmp_path):
    segs, _, inj = ar.segments(ar.load(str(_session(tmp_path, False))))
    assert [s[0] for s in segs] == ["F1 ?"]
    assert inj.max() == 0                                   # u_ad read as shadow
