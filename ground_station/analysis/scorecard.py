"""Per-flight scorecard: one dict of numbers per labday flight, a day report, and the baseline-vs-adaptive table.

    python -m ground_station.analysis.scorecard 2026-10-19            # score the day's flights, write the reports
    python -m ground_station.analysis.scorecard 2026-10-19 --compare  # also docs/lab/scorecard-compare.{md,csv}

Input: a flight folder logs/flights/<date>/<NN>_<campaign>/ (debrief.txt, outputs.txt, the .fly copy), the outputs dir
it names (metrics.json, campaign.yaml, summary.md), the recording session (telemetry.csv, events.jsonl), the pipeline
log logs/pipeline/<date>.jsonl, the 8081 service logs and the camera fixes.csv. Every number comes from those files;
a value that cannot be computed is None ("-" in the tables). How to read each metric: docs/lab/scorecard.md.
Thresholds and the pass mapping are PROPOSED (marked so in every output). labday calls score_flight after each flight.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ground_station.analysis import lab_flights
from ground_station.service import campaign_outputs as co

ROOT = Path(__file__).resolve().parents[2]
DAYS_DIR = ROOT / "docs" / "lab" / "days"
PASS_LAND_CM = 15.0       # PROPOSED: "lands on the pad" = debrief landing offset at most this
CAM_SYNC_R_MIN = 0.8      # PROPOSED: fixes.csv is used for tracking error only when the x cross-correlation reaches this
PRIM_TRAJ = 3             # API/wfb_prim.h: IDLE CLIMB HOVER TRAJ ...
MRAC_INJ = (15, 10)       # command id, idx of mrac_injection (campaign_live command events)
VOLT_KEYS = ("real_voltage", "status.vbat")
BEHIND = re.compile(r"^(\d\d:\d\d:\d\d)\.\d+ .*ingest behind: max frame age (\d+) ms")

# (key, column header): the order of the day table, the trend and the comparison
METRICS = [("status", "status"), ("air_s", "air s"), ("hold_s", "hold s"),
           ("hold_rms_cm", "hold rms cm (EKF)"), ("cam_hold_rms_cm", "hold rms cm (cam)"),
           ("cam_hold_drift_cm", "hold drift cm (cam)"), ("traj_rms_cm", "traj rms cm (EKF)"),
           ("cam_traj_rms_cm", "traj rms cm (cam)"), ("cam_ekf_rms_cm", "cam-EKF rms cm"),
           ("takeoff_osc_cm", "takeoff osc cm"), ("land_err_cm", "touch err cm"), ("land_vz_cms", "touch vz cm/s"),
           ("land_offset_cm", "land offset cm"), ("cam_land_cm", "cam land cm"),
           ("aborts", "aborts"), ("stalls", "stalls"), ("link_lost", "link lost"),
           ("ingest_lag_max_s", "ingest lag max s"), ("ingest_dropped", "ingest dropped"),
           ("ingest_behind", "ingest behind"), ("v_rest", "V rest"), ("sag_v", "sag V"),
           ("wh_used", "Wh (PROPOSED)"), ("verdict", "pass (PROPOSED)")]
NUMERIC = [k for k, _ in METRICS if k not in ("status", "verdict")]


def _r(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _rms_cm(ex, ey):
    m = np.isfinite(ex) & np.isfinite(ey)
    return _r(100 * math.sqrt(float(np.mean(ex[m] ** 2 + ey[m] ** 2)))) if m.sum() >= 5 else None


# ---------- inputs ----------

def parse_debrief(text: str) -> dict:
    """The numbers campaign_fly.debrief_lines() printed (status, airborne s, aborts, phase RMS, landing offsets)."""
    d: dict = {"aborts": []}
    lines = text.splitlines()
    if lines:
        m = re.match(r"(\S+)\s*(.*?)\s+air ([\d.]+) s", lines[0])
        if m:
            d.update(status=m.group(1), reason=m.group(2).strip(), air_s=float(m.group(3)))
    for ln in lines[1:]:
        if m := re.match(r"ABORTED L(\S+) (.+?): (.*?) \(the safety", ln):
            d["aborts"].append(f"L{m.group(1)} {m.group(3)}")
        elif m := re.match(r"(HOVER|TRAJ)\s+x/y/z error cm\s+rms (\S+)\s+max (\S+)\s+\(([\d.]+) s\)", ln):
            d[m.group(1).lower()] = {"rms": m.group(2), "max": m.group(3), "s": float(m.group(4))}
        elif m := re.match(r"(HOVER|TRAJ)\s+no samples", ln):
            d[m.group(1).lower()] = None
        elif m := re.match(r"landing offset x \S+ y \S+ cm from the takeoff point \(([\d.]+) cm\)", ln):
            d["land_offset_cm"] = float(m.group(1))
        elif m := re.match(r"camera landing x \S+ y \S+ cm from the pad \(([\d.]+) cm", ln):
            d["cam_land_cm"] = float(m.group(1))
        elif m := re.match(r"outputs (\S+)\s+flight folder", ln):
            d["outputs"] = None if m.group(1) == "-" else m.group(1)
    return d


def _resolve(p, root: Path) -> Path | None:
    if not p:
        return None
    p = Path(str(p).strip())
    return p if p.is_absolute() else root / p


def find_session(out_dir: Path | None, root: Path) -> Path | None:
    """The recording session of a campaign outputs dir: metrics.json flights[].recording, else summary.md."""
    if out_dir is None:
        return None
    try:
        for f in json.loads((out_dir / "metrics.json").read_text(encoding="utf-8")).get("flights") or []:
            if f.get("recording"):
                return _resolve(f["recording"], root)
    except (OSError, ValueError):
        pass
    try:
        m = re.search(r"recording `([^`]+)`", (out_dir / "summary.md").read_text(encoding="utf-8"))
        return _resolve(m.group(1), root) if m else None
    except OSError:
        return None


def _events(path: Path) -> list[dict]:
    out = []
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _t0_epoch(session: Path) -> float | None:
    """Wall-clock second of the first telemetry row (received_ns is time.time_ns): read_telemetry's t = 0."""
    try:
        with (session / "telemetry.csv").open(encoding="utf-8") as f:
            f.readline()
            return int(f.readline().split(",", 1)[0]) / 1e9
    except (OSError, ValueError, IndexError):
        return None


# ---------- metric groups ----------

def phase_masks(w) -> dict:
    """Per-row masks of the primitive state (HOVER = hold, TRAJ = trajectory) on the wide frame; None when absent."""
    ps = np.round(lab_flights.col(w, co.PRIM_STATE)) if len(w) else np.array([])
    return {n: (m if m.any() else None) for n, m in (("hold", ps == co.PRIM_HOVER), ("traj", ps == PRIM_TRAJ))}


def ekf_tracking(w, win) -> dict:
    """EKF position minus the x/y target over the HOVER and TRAJ rows."""
    ex = lab_flights.col(w, "Ctrler.locxPID.FB") - lab_flights.col(w, "Ctrler.locxPID.Des")
    ey = lab_flights.col(w, "Ctrler.locyPID.FB") - lab_flights.col(w, "Ctrler.locyPID.Des")
    r = {}
    for name in ("hold", "traj"):
        if win[name] is not None:
            m = win[name]
            r[f"{name}_rms_cm"] = _rms_cm(ex[m], ey[m])
    return r


def camera_tracking(w, win, fixes: Path | None) -> dict:
    """Camera truth vs the x/y target. In telemetry the fusion innovation g_ext_pos.e_x/e_y (cm) is camera minus EKF,
    so camera = 100*FB + e with no clock sync. Without it, fixes.csv is synced to the EKF x track by cross-correlation
    (accepted when r >= CAM_SYNC_R_MIN, PROPOSED)."""
    t = w.index.to_numpy(float) if len(w) else np.array([])
    fbx, fby = (lab_flights.col(w, f"Ctrler.loc{a}PID.FB") * 100 for a in "xy")
    dsx, dsy = (lab_flights.col(w, f"Ctrler.loc{a}PID.Des") * 100 for a in "xy")
    e_x, e_y = lab_flights.col(w, "g_ext_pos.e_x"), lab_flights.col(w, "g_ext_pos.e_y")
    fresh = lab_flights.col(w, "g_ext_pos.stale") < 0.5 if "g_ext_pos.stale" in w else np.isfinite(e_x)
    r: dict = {"cam_source": None}
    if np.isfinite(e_x).sum() >= 5:
        cx, cy, ok, r["cam_source"] = fbx + e_x, fby + e_y, fresh & np.isfinite(e_x), "telemetry e_x/e_y"
        air = lab_flights.windows(w)
        if air:
            m = ok & (t >= air["ta"]) & (t <= air["tb"])
            r["cam_ekf_rms_cm"] = _rms_cm(e_x[m] / 100, e_y[m] / 100)
    elif fixes is not None and len(t):
        cx, cy, ok = _fixes_on_grid(t, fbx, fixes, r)
        if cx is None:
            return r
    else:
        return r
    for name in ("hold", "traj"):
        if win[name] is not None:
            m = ok & win[name]
            r[f"cam_{name}_rms_cm"] = _rms_cm((cx[m] - dsx[m]) / 100, (cy[m] - dsy[m]) / 100)
            if name == "hold" and m.sum() >= 5:
                d = np.hypot(cx[m] - np.median(cx[m]), cy[m] - np.median(cy[m]))
                r["cam_hold_drift_cm"] = _r(np.max(d))
    return r


def _fixes_on_grid(t, fbx, fixes: Path, r: dict):
    from ground_station.analysis.video_truth import sync_xcorr
    try:
        f = pd.read_csv(fixes)
        ft, fx, fy = (f[c].to_numpy(float) for c in ("t_cap", "earth_x_cm", "earth_y_cm"))
    except (OSError, ValueError, KeyError):
        return None, None, None
    good = np.isfinite(ft) & np.isfinite(fx) & np.isfinite(fy)
    ft, fx, fy = ft[good], fx[good], fy[good]
    gm = np.isfinite(fbx)
    if len(ft) < 50 or gm.sum() < 50:
        return None, None, None
    o, rb, _ = sync_xcorr(t[gm], fbx[gm], ft, fx, max_lag=30.0, dt=0.05, centre=ft[0] - t[0])
    r["cam_sync_r"] = _r(rb, 3)
    if not np.isfinite(rb) or rb < CAM_SYNC_R_MIN:
        r["cam_source"] = f"fixes.csv, sync r {rb:.2f} < {CAM_SYNC_R_MIN} (PROPOSED): not used"
        return None, None, None
    r["cam_source"] = f"fixes.csv synced by x cross-correlation (r {rb:.2f})"
    tt = t + o
    ok = (tt >= ft[0]) & (tt <= ft[-1])
    return np.interp(tt, ft, fx), np.interp(tt, ft, fy), ok


def takeoff_landing(w, session, cam_name) -> dict:
    r = {}
    xm = lab_flights.xy_metrics(w) if len(w) else None
    if xm and xm.get("takeoff_hold"):
        th = xm["takeoff_hold"]
        if th["x"] and th["y"]:
            r["takeoff_osc_cm"] = _r(math.hypot(th["x"]["rms_cm"], th["y"]["rms_cm"]))
    try:
        lm = lab_flights.land_metrics(w, session, cam_name) if len(w) else None
    except ValueError:          # no rows in the touchdown window
        lm = None
    if lm:
        r.update(land_err_cm=_r(lm["primary_err_at_touch_cm"]), land_err_source=lm["primary_source"],
                 land_vz_cms=_r(lm["vz_at_touch_cms"]), cam_touch_err_cm=_r(lm["cam_err_at_touch_cm"]),
                 cam_touch_source=lm["cam_source"])
    return r


def link(session: Path, t0: float | None, pipe: list[dict], go: dict | None, service_dir: Path) -> dict:
    """Session events (aborts, stream stalls, 1 Hz link snapshots), pipeline link_lost of this fly run, and the
    8081 "ingest behind" lines inside the session's wall-clock span."""
    ev = _events(session / "events.jsonl") if session else []
    lk = [e.get("data") or {} for e in ev if e.get("kind") == "link"]
    lags = [d["ingest_lag_s"] for d in lk if isinstance(d.get("ingest_lag_s"), (int, float))]
    drop = [d["ingest_dropped"] for d in lk if isinstance(d.get("ingest_dropped"), (int, float))]
    r = {"abort_reasons": [f"{(e.get('data') or {}).get('reason')} L{(e.get('data') or {}).get('level')}"
                           for e in ev if e.get("kind") == "abort"],
         "stalls": sum(e.get("kind") == "stream_stall" for e in ev) if session else None,
         "ingest_lag_max_s": max(lags) if lags else None,
         "ingest_dropped": int(max(drop) - min(drop)) if drop else None}   # a bridge counter since 8081 start
    r["link_lost"] = sum(e.get("event") == "link_lost" and e.get("pid") == go.get("pid") for e in pipe) if go else None
    ts = [e["t"] for e in ev if isinstance(e.get("t"), (int, float))]
    if t0 is not None:
        ts.append(t0)
    r["ingest_behind"], r["ingest_behind_max_ms"] = _behind(service_dir, min(ts), max(ts)) if ts else (None, None)
    return r


def _behind(service_dir: Path, a: float, b: float):
    """Count and worst age of the bridge's "ingest behind" lines between wall-clock a and b (local time)."""
    day, lo, hi = time.strftime("%Y%m%d", time.localtime(a)), time.strftime("%H:%M:%S", time.localtime(a)), \
        time.strftime("%H:%M:%S", time.localtime(b))
    logs = sorted(service_dir.glob(f"*-{day}-*.log")) if service_dir.is_dir() else []
    if not logs:
        return None, None
    n, worst = 0, None
    for p in logs:
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = BEHIND.match(ln)
            if m and lo <= m.group(1) <= hi:
                n, worst = n + 1, max(worst or 0, int(m.group(2)))
    return n, worst


def battery(series, w, pack: str | None) -> dict:
    """Rest V = median before airborne, sag = rest - lowest airborne V, end = median after landing. SoC from the
    packs.yaml curve and Wh = SoC drop x capacity x mean V are PROPOSED estimates (no current sensor, PROPOSED curve)."""
    key = next((k for k in VOLT_KEYS if series.get(k, ([], []))[0]), None)
    W = lab_flights.windows(w) if len(w) else None
    if key is None or W is None:
        return {}
    t, v = (np.asarray(a, float) for a in series[key])
    pre, air, post = v[t < W["ta"]], v[(t >= W["ta"]) & (t <= W["tb"])], v[t > W["tb"] + 1]
    r = {"v_rest": _r(np.median(pre), 2) if len(pre) else None, "v_min": _r(air.min(), 2) if len(air) else None,
         "v_end": _r(np.median(post), 2) if len(post) else None, "pack": pack}
    if r["v_rest"] is not None and r["v_min"] is not None:
        r["sag_v"] = _r(r["v_rest"] - r["v_min"], 2)
    from ground_station.analysis.battery_model import PackError, PackRegistry
    try:
        reg = PackRegistry.load()
        pid = pack if pack in reg.pack_ids() else f"type{pack}" if f"type{pack}" in reg.pack_ids() else None
        if pid and r["v_rest"] is not None and r["v_end"] is not None:
            s0, s1 = reg.predict_soc(pid, r["v_rest"]), reg.predict_soc(pid, r["v_end"])
            p = reg._packs[pid]
            r.update(pack=pid, soc_start=_r(s0), soc_end=_r(s1),
                     wh_used=_r((s0 - s1) / 100 * p.capacity_mah / 1000 * float(np.mean(air)), 2))
    except (PackError, OSError):      # no packs.yaml or a curve it refuses: no SoC, the volts stand
        r["soc_note"] = "packs.yaml not usable"
    return r


def config(out_dir: Path | None, session: Path | None, fly: Path | None) -> dict:
    """Controller configuration the flight ran with: the campaign controller, the .fly gains preset and the MRAC
    injection (the session's command events first, else the campaign.yaml takeoff step, else the .fly text)."""
    c = {"controller": None, "gains": None, "mrac_injection": None}
    try:
        camp = yaml.safe_load((out_dir / "campaign.yaml").read_text(encoding="utf-8")) if out_dir else None
    except (OSError, yaml.YAMLError):
        camp = None
    if isinstance(camp, dict):
        c["controller"] = camp.get("controller")
        for ex in camp.get("experiments") or []:
            for st in ((ex.get("scenario") or {}).get("steps") or []):
                if isinstance(st, dict) and isinstance(st.get("takeoff"), dict) and "mrac_injection" in st["takeoff"]:
                    c["mrac_injection"] = c["mrac_injection"] if c["mrac_injection"] is not None \
                        else float(st["takeoff"]["mrac_injection"])
    for e in _events(session / "events.jsonl") if session else []:
        d = e.get("data") or {}
        if e.get("kind") == "command" and (d.get("id"), d.get("idx")) == MRAC_INJ and d.get("value") is not None:
            c["mrac_injection"] = float(d["value"])          # the last write wins
    if fly is not None and fly.is_file():
        from ground_station.service.campaign_fly import extract_fly_directives
        c["gains"] = extract_fly_directives(fly)[0]
        if c["mrac_injection"] is None:
            m = re.search(r"mrac_injection=([\d.]+)", fly.read_text(encoding="utf-8", errors="replace"))
            c["mrac_injection"] = float(m.group(1)) if m else None
    inj = c["mrac_injection"]
    c["arm"] = None if inj is None else "adaptive" if inj > 0 else "baseline"
    c["key"] = f"{c['controller'] or '?'} / gains {c['gains'] or 'default'} / mrac_injection " \
               f"{'?' if inj is None else f'{inj:g}'}"
    return c


def verdict(pass_text: str | None, card: dict) -> dict:
    """PROPOSED mapping of the day file's pass text to checks on this card. Clauses split on ';'; a clause the
    mapping does not know stays manual."""
    checks = []
    for clause in [s.strip() for s in (pass_text or "").split(";") if s.strip()]:
        low = clause.lower()
        if "figure-8" in low or "complete" in low:
            ok = card.get("status") == "complete" and not card.get("aborts") and bool(card.get("traj_s"))
            checks.append((clause, "pass" if ok else "fail",
                           f"status {card.get('status')}, aborts {card.get('aborts') or 0}, TRAJ {card.get('traj_s') or 0} s"))
        elif "pad" in low and "land" in low:
            lo = card.get("land_offset_cm")
            checks.append((clause, "manual" if lo is None else "pass" if lo <= PASS_LAND_CM else "fail",
                           f"land offset {lo} cm vs {PASS_LAND_CM} (PROPOSED)"))
        elif "hold" in low or "hover" in low:
            ok = bool(card.get("hold_s")) and not card.get("aborts")
            checks.append((clause, "pass" if ok else "fail", f"hold {card.get('hold_s')} s"))
        else:
            checks.append((clause, "manual", "not checked from the logs"))
    res = [c[1] for c in checks]
    v = "fail" if "fail" in res else "manual" if not res or set(res) == {"manual"} else \
        "pass+manual" if "manual" in res else "pass"
    return {"verdict": v, "checks": checks}


# ---------- one flight ----------

def score_flight(folder, day_flight: dict | None = None, camera: str | None = None, root: Path | None = None,
                 events_dir: Path | None = None) -> dict:
    """The scorecard of one flight folder (logs/flights/<date>/<NN>_<campaign>). day_flight is the day-file entry
    (id, fly, pass); camera is the video_truth dir name (<id>-<fly>) when the camera ran."""
    root = Path(root or ROOT)
    folder = Path(folder)
    df = day_flight or {}
    card: dict = {"id": df.get("id") or folder.name, "fly": df.get("fly"), "folder": str(folder),
                  "date": folder.parent.name, "pass_text": df.get("pass")}
    try:
        dbf = parse_debrief((folder / "debrief.txt").read_text(encoding="utf-8"))
    except OSError:
        dbf = {"aborts": []}
    out = _resolve(dbf.get("outputs"), root)
    if out is None:
        try:
            out = _resolve((folder / "outputs.txt").read_text(encoding="utf-8").strip(), root)
        except OSError:
            out = None
    session = find_session(out, root)
    session = session if session and (session / "telemetry.csv").is_file() else None
    pipe = _events(Path(events_dir or root / "logs" / "pipeline") / f"{card['date']}.jsonl")
    rel = folder.relative_to(root).as_posix() if folder.is_relative_to(root) else str(folder)
    go = next((e for e in reversed(pipe) if e.get("event") == "go" and str(e.get("folder", "")).replace("\\", "/")
               == rel), None)
    fly = next(iter(sorted(folder.glob("*.fly"))), None)
    card.update(status=dbf.get("status"), reason=dbf.get("reason"), air_s=dbf.get("air_s"),
                land_offset_cm=dbf.get("land_offset_cm"), cam_land_cm=dbf.get("cam_land_cm"),
                session=str(session) if session else None, outputs=str(out) if out else None,
                config=config(out, session, fly))
    series = co.read_telemetry(session) if session else {}
    try:
        w = lab_flights.load(session) if session else pd.DataFrame()
    except (OSError, ValueError, KeyError, IndexError):
        w = pd.DataFrame()
    win = phase_masks(w)
    fm = co.flight_metrics(series) if series else {}
    card["hold_s"] = fm.get("hold_s")
    tt = w.index.to_numpy(float)
    card["traj_s"] = _r(tt[win["traj"]][-1] - tt[win["traj"]][0]) if win["traj"] is not None \
        else (dbf.get("traj") or {}).get("s")
    if len(w):
        card.update(ekf_tracking(w, win))
        fixes = None
        if camera:
            saved, lab_flights.ROOT = lab_flights.ROOT, root
            try:
                fixes = lab_flights._find_fixes_csv(None, camera)
            finally:
                lab_flights.ROOT = saved
        card.update(camera_tracking(w, win, fixes))
        saved, lab_flights.ROOT = lab_flights.ROOT, root
        try:
            card.update(takeoff_landing(w, session, camera))
        finally:
            lab_flights.ROOT = saved
    lk = link(session, _t0_epoch(session) if session else None, pipe, go, root / "logs" / "service") if session \
        else {"abort_reasons": []}
    card.update(lk)
    card["abort_reasons"] = dbf["aborts"] or lk["abort_reasons"]
    card["aborts"] = len(card["abort_reasons"])
    card.update(battery(series, w, (go or {}).get("pack")))
    card.update(verdict(df.get("pass"), card))
    return card


def _f(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def one_line(card: dict) -> str:
    """The labday line after '--- <id> done': the few numbers worth a glance, then where the full card is."""
    parts = [f"score {card['id']}: pass {card.get('verdict')} (PROPOSED)"]
    for k, label in (("cam_hold_rms_cm", "hold cam rms"), ("hold_rms_cm", "hold EKF rms"),
                     ("cam_traj_rms_cm", "traj cam rms"), ("traj_rms_cm", "traj EKF rms"),
                     ("land_offset_cm", "land offset")):
        if card.get(k) is not None:
            parts.append(f"{label} {_f(card[k])} cm")
    parts.append(f"aborts {card.get('aborts') or 0}")
    if card.get("ingest_lag_max_s") is not None:
        parts.append(f"ingest lag max {_f(card['ingest_lag_max_s'])} s")
    if card.get("sag_v") is not None:
        parts.append(f"sag {_f(card['sag_v'])} V")
    if card.get("wh_used") is not None:
        parts.append(f"{_f(card['wh_used'])} Wh (PROPOSED)")
    return "; ".join(parts)


# ---------- reports ----------

def _table(rows: list[dict], lead: list[tuple[str, str]]) -> list[str]:
    cols = lead + METRICS
    out = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_f(r.get(k)) for k, _ in cols) + " |" for r in rows]
    return out


def _row(card: dict) -> dict:
    return {**{k: card.get(k) for k, _ in METRICS}, "id": card["id"], "fly": card.get("fly"),
            "date": card.get("date"), "config": (card.get("config") or {}).get("key"),
            "arm": (card.get("config") or {}).get("arm")}


def write_day_report(date: str, cards: list[dict], days_dir: Path | None = None) -> Path:
    """docs/lab/days/<date>-report.md: one row per flight, the PROPOSED pass checks, notes, and the trend of each flight
    id over every day that has a <day>-report.json sidecar (written here too)."""
    days_dir = Path(days_dir or DAYS_DIR)
    days_dir.mkdir(parents=True, exist_ok=True)
    rows = [_row(c) for c in cards]
    (days_dir / f"{date}-report.json").write_text(json.dumps(rows, indent=1, default=str) + "\n", encoding="utf-8")
    md = [f"# {date} scorecard", "",
          ("Written by `ground_station/analysis/scorecard.py` (labday after each flight). How to read it: "
           "`docs/lab/scorecard.md`. `-` = not computable from the logs. Pass marks and Wh are PROPOSED."), "",
          "## Flights", ""] + _table(rows, [("id", "id"), ("fly", "fly"), ("config", "config")])
    md += ["", "## Pass checks (PROPOSED mapping of the day file's pass text)", ""]
    for c in cards:
        md.append(f"- **{c['id']}** ({c.get('pass_text') or 'no pass text'}): " + ("; ".join(
            f"{t} -> {res} ({why})" for t, res, why in c.get("checks") or []) or "nothing to check"))
    md += ["", "## Sources", ""]
    for c in cards:
        md.append(f"- **{c['id']}**: folder `{c.get('folder')}`, session `{c.get('session') or '-'}`, camera "
                  f"{c.get('cam_source') or '-'}, touch err from {c.get('land_err_source') or '-'}, pack "
                  f"{c.get('pack') or '-'}, camera touch err {_f(c.get('cam_touch_err_cm'))} cm from "
                  f"{c.get('cam_touch_source') or '-'}, aborts {', '.join(c.get('abort_reasons') or []) or '-'}")
    md += ["", "## Trend (same flight id over days)", ""] + trend(days_dir)
    p = days_dir / f"{date}-report.md"
    p.write_text("\n".join(md) + "\n", encoding="utf-8")
    return p


def trend(days_dir: Path) -> list[str]:
    rows = []
    for p in sorted(days_dir.glob("*-report.json")):
        try:
            rows += [{**r, "day": p.name[:-len("-report.json")]} for r in json.loads(p.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: (str(r.get("id")), r["day"]))
    keys = [("day", "day"), ("id", "id"), ("status", "status"), ("hold_rms_cm", "hold rms cm (EKF)"),
            ("cam_hold_rms_cm", "hold rms cm (cam)"), ("cam_traj_rms_cm", "traj rms cm (cam)"),
            ("land_offset_cm", "land offset cm"), ("aborts", "aborts"), ("ingest_lag_max_s", "ingest lag max s"),
            ("verdict", "pass (PROPOSED)")]
    out = ["| " + " | ".join(h for _, h in keys) + " |", "|" + "---|" * len(keys)]
    return out + ["| " + " | ".join(_f(r.get(k)) for k, _ in keys) + " |" for r in rows]


def compare_configs(cards: list[dict]) -> tuple[str, str]:
    """Cards (or <day>-report.json rows) grouped by config. Baseline (mrac_injection 0) vs adaptive (> 0) per controller and gains preset: n, then mean and spread (sample
    SD, n >= 2) of each metric over the flights that have it. Returns (markdown, csv)."""
    cards = [_row(c) if isinstance(c.get("config"), dict) else c for c in cards]   # cards or report.json rows
    groups: dict[str, list[dict]] = {}
    for c in cards:
        groups.setdefault(c.get("config") or "?", []).append(c)
    head = ["config", "arm", "n", "flights"] + [f"{k} mean" for k in NUMERIC] + [f"{k} sd" for k in NUMERIC] + \
           ["pass", "fail", "manual"]
    rows = []
    for key in sorted(groups, key=lambda k: (groups[k][0].get("arm") != "baseline", k)):
        g = groups[key]
        row = {"config": key, "arm": g[0].get("arm") or "?", "n": len(g),
               "flights": " ".join(f"{c.get('date')}/{c['id']}" for c in g)}
        for k in NUMERIC:
            vals = [float(c[k]) for c in g if isinstance(c.get(k), (int, float))]
            row[f"{k} mean"] = _r(np.mean(vals), 2) if vals else None
            row[f"{k} sd"] = _r(np.std(vals, ddof=1), 2) if len(vals) >= 2 else None
        for v in ("pass", "fail", "manual"):
            row[v] = sum(str(c.get("verdict", "")).startswith(v) for c in g)
        rows.append(row)
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=head, lineterminator="\n")
    wr.writeheader()
    wr.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
    md = ["# Controller configurations: baseline vs adaptive", "",
          ("Baseline = mrac_injection 0, adaptive = mrac_injection > 0, same controller and gains preset otherwise. "
           "Cells: mean (sample SD over the flights that have the metric, n >= 2). Pass counts use the PROPOSED "
           "mapping; Wh is a PROPOSED estimate. Written by `ground_station/analysis/scorecard.py`."), "",
          "| metric | " + " | ".join(f"{r['arm']}: {r['config']} (n {r['n']})" for r in rows) + " |",
          "|---|" + "---|" * len(rows)]
    for k, h in METRICS:
        if k in NUMERIC:
            md.append(f"| {h} | " + " | ".join(
                "-" if r[f"{k} mean"] is None else f"{r[f'{k} mean']:g}" + (
                    f" ({r[f'{k} sd']:g})" if r[f"{k} sd"] is not None else "") for r in rows) + " |")
    md.append("| pass / fail / manual | " + " | ".join(f"{r['pass']} / {r['fail']} / {r['manual']}" for r in rows) + " |")
    md += ["", "Flights: " + "; ".join(f"{r['config']}: {r['flights']}" for r in rows)]
    return "\n".join(md) + "\n", buf.getvalue()


# ---------- day ----------

def day_cards(date: str, root: Path | None = None, days_dir: Path | None = None) -> list[dict]:
    """Cards for every flight folder of the date, matched to the day file's entries by .fly name in flight order."""
    root = Path(root or ROOT)
    days_dir = Path(days_dir or DAYS_DIR)
    try:
        plan = (yaml.safe_load((days_dir / f"{date}.yaml").read_text(encoding="utf-8")) or {}).get("flights") or []
    except (OSError, yaml.YAMLError):
        plan = []
    cards, used = [], set()
    for folder in sorted((root / "logs" / "flights" / date).glob("*_*")):
        fly = next(iter(sorted(folder.glob("*.fly"))), None)
        stem = fly.stem.lower() if fly else ""
        # the first planned entry of this .fly not yet matched (flight order; a re-fly shifts the later ids)
        f = next((p for p in plan if str(p.get("fly", "")).lower() == stem and p["id"] not in used), None)
        if f:
            used.add(f["id"])
        cam = f"{f['id']}-{f['fly']}".lower().replace("_", "-") if f else None
        cards.append(score_flight(folder, f, cam if cam and (root / "logs" / "video_truth" / cam).is_dir() else None,
                                  root=root))
    return cards


def main(argv=None):
    ap = argparse.ArgumentParser(description="score a lab day's flights and write docs/lab/days/<date>-report.md")
    ap.add_argument("date")
    ap.add_argument("--compare", action="store_true", help="also write docs/lab/scorecard-compare.{md,csv} "
                    "from every day with a report")
    a = ap.parse_args(argv)
    cards = day_cards(a.date)
    for c in cards:
        print(one_line(c))
    print(f"report: {write_day_report(a.date, cards)}")
    if a.compare:
        rows = []
        for p in sorted(DAYS_DIR.glob("*-report.json")):
            rows += json.loads(p.read_text(encoding="utf-8"))
        md, csv_text = compare_configs(rows)
        (DAYS_DIR.parent / "scorecard-compare.md").write_text(md, encoding="utf-8")
        (DAYS_DIR.parent / "scorecard-compare.csv").write_text(csv_text, encoding="utf-8")
        print(f"compare: {DAYS_DIR.parent / 'scorecard-compare.md'} (+ .csv)")


if __name__ == "__main__":
    main()
