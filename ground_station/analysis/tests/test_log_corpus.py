"""log_corpus: session + VOFA discovery and loading, torn session rows, airborne spans, pid.c at a commit."""
from __future__ import annotations

import json

import numpy as np

from ground_station.analysis import log_corpus as lc


def _session(root, name="20260101-000000-x", n=500, dt=0.02, phase_on=(100, 400)):
    d = root / "logs" / "sessions" / name
    d.mkdir(parents=True)
    rows = ["received_ns,slot,key,value"]
    for i in range(n):
        ns = int(1e18 + i * dt * 1e9)
        rows.append(f"{ns},0,slot0.flight_phase,{1 if phase_on[0] <= i < phase_on[1] else 0}")
        rows.append(f"{ns},0,slot0.DroneStatus.ARM_Status,1")
        rows.append(f"{ns},0,slot0.Ctrler.gyroxPID.FB,{np.sin(i * dt):.6f}")
    rows.insert(50, "garbage-row-torn,0,slot0.flight_phase,1")        # a torn line: dropped, not fatal
    rows.insert(60, "99999999999999999999999,0,slot0.flight_phase,1")  # a torn stamp far from the rest
    (d / "telemetry.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"context": {"started_commit": "abc1234"}}), encoding="utf-8")
    return d


def _vofa(root, stem="flightX", n=300):
    d = root / "logs" / "vofa"
    d.mkdir(parents=True)
    names = ["flight_phase", "Ctrler.gyroxPID.FB"]
    (d / f"{stem}.meta.json").write_text(json.dumps({"slots": {"0": {"vars": names, "rate": 50}}}), encoding="utf-8")
    lines = ["t_src_ms,t_host_s,seq," + ",".join(names)]
    lines += [f"{i * 20},{i * 0.02},{i},1,{0.1 * i}" for i in range(n)]
    (d / f"{stem}.slot0.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_find_load_keys(tmp_path):
    _session(tmp_path)
    _vofa(tmp_path)
    refs = lc.find_logs(tmp_path)
    assert [(r.name, r.kind) for r in refs] == [("20260101-000000-x", "session"), ("vofa/flightX", "vofa")]
    s = lc.load(refs[0])
    t, v = s["flight_phase"]
    assert len(t) == 500 and t[-1] < 20.0 and np.all(np.diff(t) >= 0)     # both torn rows dropped
    assert lc.keys(refs[0]) >= {"flight_phase", "Ctrler.gyroxPID.FB"}
    assert lc.keys(refs[1]) == {"flight_phase", "Ctrler.gyroxPID.FB"}
    sv = lc.load(refs[1])
    assert np.isclose(sv["Ctrler.gyroxPID.FB"][0][-1], 299 * 0.02)
    assert lc.log_commit(refs[0], tmp_path) == "abc1234"


def test_airborne_spans_and_hold(tmp_path):
    s = lc.load(lc.find_logs(_session(tmp_path).parents[2])[0])
    t = np.arange(0.0, 10.0, 0.02)
    sp = lc.spans(lc.airborne(s, t), t, 3.0)
    assert len(sp) == 1 and abs(sp[0][0] - 2.0) < 0.05 and abs(sp[0][1] - 7.98) < 0.05
    assert lc.spans(lc.airborne(s, t), t, 7.0) == []
    assert np.all(lc.hold(s, "missing", t, 5.0) == 5.0)
    assert lc.airborne({"x": (t, t)}, t).sum() == 0                     # no flight_phase: never airborne


def test_grid_common_span():
    t1, t2 = np.arange(0, 2, 0.01), np.arange(0.5, 3, 0.02)
    t, g = lc.grid({"a": (t1, t1), "b": (t2, 2 * t2)}, ["a", "b"], 0.005)
    assert t[0] == 0.5 and t[-1] < 1.99
    assert np.allclose(g["a"], t) and np.allclose(g["b"], 2 * t)


def test_pid_rows_at_head():
    rows = lc.pid_rows_at("HEAD")
    assert rows and rows["gyroxPID"].kp > 0
    assert lc.pid_rows_at("0000000") is None and lc.pid_rows_at(None) is None
