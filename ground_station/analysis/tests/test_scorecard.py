"""Scorecard on synthetic flights in the real formats (campaign_fly debrief, recorder telemetry.csv/events.jsonl,
pipeline log, 8081 service log). Expected values come from how the fixture is built, not from the module."""
import csv
import json
import math
import time

import numpy as np
import pytest

from ground_station.analysis import scorecard as sc
from ground_station.analysis.battery_model import PackRegistry

DATE = "2026-10-19"
E0 = 1_792_400_000.0          # epoch s of the first telemetry row
DT = 0.05


def _profile(t):
    """One synthetic flight: ground 0-5 s, climb 5-8, HOVER 8-20, TRAJ 20-30, descent 30-33, ground to 40."""
    if t < 5:
        z, ps = 0.0, 0
    elif t < 8:
        z, ps = 0.8 * (t - 5) / 3, 1
    elif t < 20:
        z, ps = 0.8, 2
    elif t < 30:
        z, ps = 0.8, 3
    elif t < 33:
        z, ps = 0.8 * (33 - t) / 3, 4
    else:
        z, ps = 0.0, 0
    xd = 50 * math.sin(2 * math.pi * (t - 20) / 10) if ps == 3 else 0.0      # cm
    ox, oy = (1.0, 0.0) if ps == 3 else (3.0, 4.0)                               # EKF - target, cm
    ex = 4.0 * (t - 8) / (20 - DT - 8) if ps == 2 else 0.0                       # camera - EKF, cm: 0 -> 4 ramp
    v = 16.4 if t < 5 else 16.0 if t >= 34 else 15.6 if 15 <= t < 16 else 15.9
    vz = -0.3 if 30 <= t < 33.2 else 0.0
    return {"z": z, "ps": ps, "xd": xd, "ox": ox, "oy": oy, "ex": ex, "v": v, "vz": vz}


def _session(d, stall=True):
    d.mkdir(parents=True)
    rows = []
    for i in range(int(40 / DT)):
        t = i * DT
        p = _profile(t)
        ns = int((E0 + t) * 1e9)
        for k, val in (("Ctrler.locxPID.FB", p["xd"] + p["ox"]), ("Ctrler.locxPID.Des", p["xd"]),
                       ("Ctrler.locyPID.FB", p["oy"]), ("Ctrler.locyPID.Des", 0.0),
                       ("Ctrler.Z_posPID.FB", p["z"]), ("Ctrler.Z_posPID.Des", p["z"]),
                       ("Ctrler.Z_ratePID.FB", p["vz"]), ("g_wfb_status.prim_state", p["ps"]),
                       ("real_voltage", p["v"]), ("slot1.g_ext_pos.e_x", p["ex"]), ("slot1.g_ext_pos.e_y", 0.0),
                       ("slot1.g_ext_pos.stale", 1.0 if t < 5 else 0.0)):
            rows.append((ns, 1, k, val))
    with (d / "telemetry.csv").open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["received_ns", "slot", "key", "value"])
        wr.writerows(rows)
    ev = [{"t": E0 + 1, "kind": "link", "data": {"ingest_lag_s": 0.1, "ingest_dropped": 100}},
          {"t": E0 + 2, "kind": "command", "data": {"id": 15, "idx": 10, "value": 1.0}},
          {"t": E0 + 12, "kind": "link", "data": {"ingest_lag_s": 0.4, "ingest_dropped": 103}},
          {"t": E0 + 39, "kind": "link", "data": {"ingest_lag_s": 0.2, "ingest_dropped": 105}}]
    if stall:
        ev.append({"t": E0 + 25, "kind": "stream_stall", "data": {"gap_s": 1.2}})
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev), encoding="utf-8")


DEBRIEF_OK = """complete  air 28.0 s  1 flight(s)  800 live samples
HOVER  x/y/z error cm  rms 3/4/0  max 3/4/0  (12 s)
TRAJ   x/y/z error cm  rms 1/0/0  max 1/0/0  (10 s)
landing offset x +3 y +4 cm from the takeoff point (5 cm)
camera landing x +2 y +3 cm from the pad (4 cm, ne, median of 15 fixes)
outputs {out}  flight folder {folder}
"""
DEBRIEF_ABORT = """aborted stale camera  air 12.0 s  1 flight(s)  300 live samples
ABORTED L2 figure8: camera stale 1.5 s (the safety monitor landed it; the steps after that point never ran)
HOVER  x/y/z error cm  rms 3/4/0  max 3/4/0  (4 s)
TRAJ   no samples
landing offset x +18 y +4 cm from the takeoff point (18 cm)
outputs -  flight folder {folder}
"""


@pytest.fixture
def lab(tmp_path):
    """root with an adaptive flight (01, complete) and a baseline one (02, aborted, outputs only in outputs.txt)."""
    root = tmp_path
    fl = root / "logs" / "flights" / DATE
    for n, (camp, inj, deb) in enumerate((("lab-m-memory-u100", 1, DEBRIEF_OK),
                                          ("lab-m-memory-u0", 0, DEBRIEF_ABORT)), 1):
        folder = fl / f"0{n}_{camp}"
        folder.mkdir(parents=True)
        out = root / "logs" / "campaigns" / f"{camp}_20261019-10{n}000"
        out.mkdir(parents=True)
        sess = root / "logs" / "sessions" / f"20261019-10{n}000"
        _session(sess, stall=n == 1)
        (out / "metrics.json").write_text(json.dumps({"flights": [{"recording": str(sess)}]}), encoding="utf-8")
        (out / "campaign.yaml").write_text(
            f"controller: pid_cascade\nexperiments:\n- scenario:\n    steps:\n    - takeoff: {{z: 0.8, "
            f"mrac_injection: {inj}}}\n", encoding="utf-8")
        (folder / f"lab_m_memory_u{100 * inj}.fly").write_text(
            f"# gains: lab_soft\n# why: memory\ntakeoff 0.8 mrac_injection={inj}\nfigure8\nland\n", encoding="utf-8")
        (folder / "debrief.txt").write_text(deb.format(out=out.relative_to(root).as_posix(),
                                                      folder=folder.relative_to(root).as_posix()), encoding="utf-8")
        (folder / "outputs.txt").write_text(str(out.relative_to(root)) + "\n", encoding="utf-8")
    (root / "logs" / "sessions" / "20261019-102000" / "events.jsonl").write_text(
        json.dumps({"t": E0 + 1, "kind": "command", "data": {"id": 15, "idx": 10, "value": 0.0}}) + "\n",
        encoding="utf-8")
    pipe = root / "pipe"
    pipe.mkdir()
    (pipe / f"{DATE}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (
        {"event": "go", "pid": 7, "pack": "1", "folder": f"logs\\flights\\{DATE}\\01_lab-m-memory-u100"},
        {"event": "link_lost", "pid": 7}, {"event": "link_lost", "pid": 9})), encoding="utf-8")
    svc = root / "logs" / "service"
    svc.mkdir(parents=True)
    hm = lambda s: time.strftime("%H:%M:%S", time.localtime(E0 + s))
    (svc / f"8081-{time.strftime('%Y%m%d', time.localtime(E0))}-090000.log").write_text(
        f"{hm(10)}.120 [wifi_bridge] ingest behind: max frame age 640 ms\n"
        f"{hm(20)}.500 [wifi_bridge] ingest behind: max frame age 910 ms\n"
        f"{hm(20)}.700 [api] something else\n"
        f"{hm(-3600)}.000 [wifi_bridge] ingest behind: max frame age 5000 ms\n", encoding="utf-8")
    return root, pipe


def _card(lab, n=1, **kw):
    root, pipe = lab
    folder = next((root / "logs" / "flights" / DATE).glob(f"0{n}_*"))
    return sc.score_flight(folder, root=root, events_dir=pipe, **kw)


def test_parse_debrief_reads_every_line_kind():
    d = sc.parse_debrief(DEBRIEF_ABORT.format(folder="f"))
    assert (d["status"], d["reason"], d["air_s"]) == ("aborted", "stale camera", 12.0)
    assert d["aborts"] == ["L2 camera stale 1.5 s"]
    assert d["hover"] == {"rms": "3/4/0", "max": "3/4/0", "s": 4.0} and d["traj"] is None
    assert d["land_offset_cm"] == 18.0 and d["outputs"] is None and "cam_land_cm" not in d
    d = sc.parse_debrief(DEBRIEF_OK.format(out="logs/campaigns/x", folder="f"))
    assert d["cam_land_cm"] == 4.0 and d["outputs"] == "logs/campaigns/x" and d["traj"]["s"] == 10.0


def test_score_flight_numbers_match_the_fixture(lab):
    c = _card(lab, day_flight={"id": "mem1", "fly": "lab_m_memory_u100", "pass": "figure-8 completes; the dot "
                               "stays on the drone in the tracker window"})
    assert (c["id"], c["date"], c["status"], c["air_s"]) == ("mem1", DATE, "complete", 28.0)
    assert c["session"].endswith("20261019-101000")
    # EKF: offset 3/4 cm in HOVER, 1/0 cm in TRAJ
    assert c["hold_rms_cm"] == 5.0 and c["traj_rms_cm"] == 1.0
    assert c["hold_s"] == pytest.approx(12 - DT, abs=1e-6)
    assert c["traj_s"] == pytest.approx(10 - DT, abs=0.06)
    # camera = EKF + e_x ramp 0..4 cm in HOVER: x error 3..7 around a median of 5, so the drift is 2 cm
    ramp = np.linspace(3, 7, int(12 / DT))
    assert c["cam_source"] == "telemetry e_x/e_y"
    assert c["cam_hold_rms_cm"] == round(math.sqrt(np.mean(ramp ** 2 + 16)), 1)
    assert c["cam_hold_drift_cm"] == pytest.approx(2.0, abs=0.05)
    assert c["cam_traj_rms_cm"] == 1.0
    assert c["takeoff_osc_cm"] == 5.0
    assert c["land_err_cm"] == 5.0 and c["land_err_source"] == "ekf" and c["land_vz_cms"] == -30.0
    assert (c["land_offset_cm"], c["cam_land_cm"]) == (5.0, 4.0)
    # link: 3 link snapshots, dropped counter 100 -> 105, one stall, link_lost of pid 7 only, 2 behind lines in span
    assert (c["ingest_lag_max_s"], c["ingest_dropped"], c["stalls"], c["link_lost"]) == (0.4, 5, 1, 1)
    assert (c["ingest_behind"], c["ingest_behind_max_ms"]) == (2, 910)
    assert c["aborts"] == 0
    # battery: rest 16.4, lowest airborne 15.6, after landing 16.0; SoC/Wh through the real packs.yaml
    assert (c["v_rest"], c["v_min"], c["v_end"], c["sag_v"]) == (16.4, 15.6, 16.0, 0.8)
    reg = PackRegistry.load()
    s0, s1 = reg.predict_soc("type1", 16.4), reg.predict_soc("type1", 16.0)
    assert c["pack"] == "type1" and c["soc_start"] == round(s0, 1)
    assert c["wh_used"] == pytest.approx((s0 - s1) / 100 * reg._packs["type1"].capacity_mah / 1000 * 15.9, abs=0.02)
    # config: campaign controller, .fly gains, injection from the session command
    assert c["config"]["key"] == "pid_cascade / gains lab_soft / mrac_injection 1" and c["config"]["arm"] == "adaptive"
    # pass: figure-8 clause checked (complete, no abort, TRAJ samples), tracker clause manual
    assert c["verdict"] == "pass+manual"
    assert [r for _, r, _ in c["checks"]] == ["pass", "manual", "manual"]      # last: operator verdict
    line = sc.one_line(c)
    assert line.startswith("score mem1: pass+manual (PROPOSED checks; your call from the flight decides); hold cam rms ")
    assert "aborts 0" in line and "ingest lag max 0.4 s" in line and "sag 0.8 V" in line and "Wh (PROPOSED)" in line


def test_aborted_baseline_flight(lab):
    c = _card(lab, 2, day_flight={"id": "mem2", "fly": "lab_m_memory_u0", "pass": "figure-8 completes; lands on the pad"})
    assert c["outputs"].endswith("lab-m-memory-u0_20261019-102000")      # from outputs.txt (debrief says "-")
    assert c["aborts"] == 1 and c["abort_reasons"] == ["L2 camera stale 1.5 s"]
    assert c["config"]["arm"] == "baseline" and c["config"]["key"].endswith("mrac_injection 0")
    assert c["link_lost"] is None and c["pack"] is None and c.get("wh_used") is None    # no go event for this folder
    assert c["stalls"] == 0
    assert c["verdict"] == "fail" and [r for _, r, _ in c["checks"]] == ["fail", "fail", "manual"]


def test_missing_data_gives_none_not_numbers(tmp_path):
    folder = tmp_path / "logs" / "flights" / DATE / "03_x"
    folder.mkdir(parents=True)
    c = sc.score_flight(folder, {"id": "z1", "pass": "hold at 0.3 m"}, root=tmp_path, events_dir=tmp_path)
    for k in sc.NUMERIC:
        assert c.get(k) is None or (k == "aborts" and c[k] == 0), k
    assert c["verdict"] == "fail" and c["config"]["arm"] is None
    assert "aborts 0" in sc.one_line(c) and "rms" not in sc.one_line(c)


def test_verdict_mapping():
    ok = {"status": "complete", "aborts": 0, "traj_s": 9.0, "land_offset_cm": 12.0, "hold_s": 5.0}
    assert sc.verdict("figure-8 completes", ok)["verdict"] == "pass+manual"     # never a bare pass
    assert sc.verdict("lands on the pad", {**ok, "land_offset_cm": 16.0})["verdict"] == "fail"
    assert sc.verdict("lands on the pad", {**ok, "land_offset_cm": None})["verdict"] == "manual"
    assert sc.verdict("hover 10 s; bounce under 5 cm", ok)["verdict"] == "pass+manual"
    assert sc.verdict(None, ok)["verdict"] == "manual"
    assert sc.verdict("figure-8 completes", {**ok, "traj_s": None})["verdict"] == "fail"


def test_sysid_complete_clause_reads_the_running_state_not_traj():
    """10-28: every sysid card failed "both multisine bands complete" on TRAJ 0 s; a sysid flight has no TRAJ phase."""
    card = {"status": "complete", "aborts": 0, "traj_s": None,
            "excite_s": sc.excite_s({"id.sysid_state": ([0, 1, 2, 30], [1, 2, 2, 4])})}
    assert card["excite_s"] == 1.0
    assert sc.verdict("both multisine bands complete; fits with residual <= 0.35", card)["verdict"] == "pass+manual"
    assert sc.verdict("excite completes", {**card, "excite_s": 0.0})["verdict"] == "fail"     # never ran
    assert sc.verdict("excite completes", {**card, "excite_s": None})["verdict"] == "manual"  # not streamed
    assert sc.verdict("excite completes", {**card, "aborts": 1})["verdict"] == "fail"
    assert sc.excite_s({}) is None


def test_day_report_trend_and_compare(lab, tmp_path):
    days = tmp_path / "days"
    c1 = _card(lab, day_flight={"id": "mem1", "fly": "lab_m_memory_u100", "pass": "figure-8 completes"})
    c2 = _card(lab, 2, day_flight={"id": "mem2", "fly": "lab_m_memory_u0", "pass": "figure-8 completes"})
    old = {**c1, "date": "2026-10-18", "hold_rms_cm": 7.5}
    sc.write_day_report("2026-10-18", [old], days)
    p = sc.write_day_report(DATE, [c1, c2], days)
    md = p.read_text(encoding="utf-8")
    assert p.name == f"{DATE}-report.md" and (days / f"{DATE}-report.json").is_file()
    flights = md.split("## Flights")[1].split("##")[0]
    assert flights.count("| mem1 |") == 1 and flights.count("| mem2 |") == 1
    tr = md.split("## Trend (same flight id over days)")[1].strip().splitlines()[2:]
    assert [ln.split(" | ")[0:2] for ln in tr] == [["| 2026-10-18", "mem1"], [f"| {DATE}", "mem1"],
                                                   [f"| {DATE}", "mem2"]]
    assert "| 7.5 |" in tr[0] and "| 5 |" in tr[1]
    assert "PROPOSED" in md and "figure-8 completes -> fail" in md
    # comparison from cards and from the report rows gives the same table; baseline first
    rows = json.loads((days / "2026-10-18-report.json").read_text()) + \
        json.loads((days / f"{DATE}-report.json").read_text())
    md_c, csv_c = sc.compare_configs([old, c1, c2])
    assert (md_c, csv_c) == sc.compare_configs(rows)
    tab = list(csv.DictReader(csv_c.splitlines()))
    assert [(r["arm"], r["n"]) for r in tab] == [("baseline", "1"), ("adaptive", "2")]
    assert tab[1]["hold_rms_cm mean"] == "6.25" and tab[1]["hold_rms_cm sd"] == str(round(np.std([7.5, 5], ddof=1), 2))
    assert tab[0]["hold_rms_cm sd"] == "" and (tab[0]["fail"], tab[1]["pass"]) == ("1", "2")
    assert "| hold rms cm (EKF) | 5 | 6.25 (1.77) |" in md_c


def _hover_frame(xd):
    """A HOVER-only (prim 2) frame at 20 Hz over 20 s whose x target (m) is xd(t)."""
    t = np.arange(0, 20, DT)
    import pandas as pd
    return pd.DataFrame({"g_wfb_status.prim_state": 2.0, "Ctrler.locxPID.Des": [xd(s) for s in t],
                         "Ctrler.locyPID.Des": 0.0}, index=t)


def test_preset_trajectory_in_hover_is_traj_and_a_static_target_stays_hold():
    # `preset figure8` moves the target from AutoflyTask while the primitive stays HOVER: 0.3 m amplitude, 10 s
    # period (peak 0.19 m/s) from t = 5 s to 15 s, static before and after
    w = _hover_frame(lambda s: 0.3 * math.sin(2 * math.pi * (s - 5) / 10) if 5 <= s < 15 else 0.0)
    m = sc.phase_masks(w)
    t = w.index.to_numpy(float)
    assert m["traj"] is not None and m["hold"] is not None
    assert not m["traj"][t < 4.7].any() and not m["traj"][t > 15.3].any()
    # the target crosses zero speed at the lobe tips (t = 7.5, 12.5 s): only those few rows fall back to hold
    assert m["traj"][(t >= 5) & (t < 15)].mean() > 0.85
    assert m["hold"][t < 4.7].all() and m["hold"][t > 15.3].all()
    assert not (m["hold"] & m["traj"]).any()
    # a static target, or one hold setpoint step (0.3 m at 10 s: 0.6 m/s for the 0.5 s the +-0.25 s window spans
    # it, under PRESET_TRAJ_MIN_S), gives no traj rows
    for xd in (lambda s: 0.0, lambda s: 0.3 * (s >= 10)):
        m = sc.phase_masks(_hover_frame(xd))
        assert m["traj"] is None and m["hold"].all()
    # real TRAJ rows (prim 3) win: a moving HOVER target is then not counted as trajectory
    w = _hover_frame(lambda s: 0.01 * s)
    w.loc[w.index >= 15, "g_wfb_status.prim_state"] = 3.0
    m = sc.phase_masks(w)
    assert m["traj"].sum() == (w.index >= 15).sum() and m["hold"].sum() == (w.index < 15).sum()


def test_day_cards_takes_ids_from_the_state_and_carried_flights_from_load_day(lab, tmp_path, monkeypatch):
    from ground_station.lab import labday
    root, _ = lab
    days = tmp_path / "days"
    days.mkdir()
    (days / "2026-10-17.yaml").write_text("day: 2026-10-17\nflights:\n- {id: E1, fly: lab_m_memory_u0, pass: p-e1}\n",
                                          encoding="utf-8")
    (days / f"{DATE}.yaml").write_text(f"day: {DATE}\nflights:\n- {{id: mem1, fly: lab_m_memory_u100, pass: p-m1}}\n",
                                       encoding="utf-8")
    monkeypatch.setattr(labday, "LAB_DIR", tmp_path / "lab")      # no earlier state: E1 is carried as 1017-E1
    seen = []
    monkeypatch.setattr(sc, "score_flight", lambda folder, f, cam, root=None: seen.append(
        (folder.name, (f or {}).get("id"), (f or {}).get("pass"), cam)) or {})
    (root / "logs" / "video_truth" / "1017-e1-lab-m-memory-u0").mkdir(parents=True)
    # no state file: both by .fly name, the carried flight included (the raw yaml has no 1017-E1)
    sc.day_cards(DATE, root, days)
    assert seen == [("01_lab-m-memory-u100", "mem1", "p-m1", None),
                    ("02_lab-m-memory-u0", "1017-E1", "p-e1", "1017-e1-lab-m-memory-u0")]
    # the state names the folder of each id and its camera: a re-fly (03) of mem1 takes the id, the first try (01)
    # matches no unused entry and keeps its folder name
    third = root / "logs" / "flights" / DATE / "03_lab-m-memory-u100"
    third.mkdir()
    (third / "lab_m_memory_u100.fly").write_text("takeoff 0.8\nland\n", encoding="utf-8")
    (root / "logs" / "video_truth" / "cam-m1").mkdir()
    st = root / "logs" / "lab" / DATE / "labday_state.json"
    st.parent.mkdir(parents=True)
    st.write_text(json.dumps({"flown": {
        "mem1": {"ok": True, "folder": f"logs/flights/{DATE}/03_lab-m-memory-u100", "camera": "cam-m1"},
        "1017-E1": {"ok": False, "folder": f"logs\\flights\\{DATE}\\02_lab-m-memory-u0", "camera": "gone"},
        "1017-F1": {"ok": False, "folder": None}}, "skipped": []}), encoding="utf-8")
    seen.clear()
    sc.day_cards(DATE, root, days)
    assert seen == [("01_lab-m-memory-u100", None, None, None),
                    ("02_lab-m-memory-u0", "1017-E1", "p-e1", None),
                    ("03_lab-m-memory-u100", "mem1", "p-m1", "cam-m1")]


def test_verdict_checks_hold_peak_to_peak_and_path_swing():
    # 10-24 lqr-alone: completed, but wandered 29 cm in hold and swung 1.75x the path; the operator saw it fail
    lqr = {"status": "complete", "aborts": 0, "traj_s": 9.0, "hold_s": 29.3, "hold_pp_cm": 29.0, "traj_amp_ratio": 1.75}
    v = sc.verdict("no sustained xy oscillation in hold (peak-to-peak < 10cm, PROPOSED); figure-8 completes", lqr)
    assert v["verdict"] == "fail" and [r for _, r, _ in v["checks"]] == ["fail", "fail", "manual"]
    good = {**lqr, "hold_pp_cm": 8.0, "traj_amp_ratio": 1.1}
    v = sc.verdict("hold peak-to-peak < 10 cm; figure-8 completes", good)
    assert v["verdict"] == "pass+manual" and [r for _, r, _ in v["checks"]] == ["pass", "pass", "manual"]
    assert sc.verdict("hold peak-to-peak small", good)["checks"][0][1] == "manual"          # no number to check
    assert sc.verdict("hold peak-to-peak < 10 cm", {**good, "hold_pp_cm": None})["verdict"] == "manual"
    assert sc.verdict("figure-8 completes", {**good, "traj_amp_ratio": None})["verdict"] == "pass+manual"


def test_ekf_tracking_peak_to_peak_and_amp_ratio():
    import pandas as pd
    t = np.arange(200) * 0.04
    ref = 0.15 * np.sin(1.37 * t)                                     # path: 30 cm peak-to-peak on x
    w = pd.DataFrame({"Ctrler.locxPID.Des": ref, "Ctrler.locxPID.FB": 2.0 * ref,      # drone swings 2x the path
                      "Ctrler.locyPID.Des": np.zeros_like(t), "Ctrler.locyPID.FB": 0.01 * np.sin(3 * t)})
    hold, traj = t < 2.0, t >= 2.0
    r = sc.ekf_tracking(w, {"hold": hold, "traj": traj})
    assert r["traj_amp_ratio"] == 2.0                                 # y target is still: left out of the ratio
    exp = 100 * max(np.ptp((2.0 * ref - ref)[hold]), np.ptp((0.01 * np.sin(3 * t))[hold]))
    assert r["hold_pp_cm"] == round(exp, 1)
    assert sc.ekf_tracking(w, {"hold": None, "traj": None}) == {}
