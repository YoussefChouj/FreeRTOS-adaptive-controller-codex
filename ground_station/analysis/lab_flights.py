"""Lab flight analysis from the 8081 recordings: step response, oscillation, path tracking and landing.

    python -m ground_station.analysis.lab_flights 2026-10-15            # every flown lab flight of the day
    python -m ground_station.analysis.lab_flights 2026-10-15 --only xy-ctrl-screen pad-landing

Flights are found through logs/flights/<date>/NN_<card>-<variant>/debrief.txt -> campaign summary.md -> the
recording in logs/sessions/ (telemetry.csv, long format: received_ns, slot, key, value). Refused takeoffs have
no recording and are listed as such. Output: logs/analysis/<date>/ (PNG plots, metrics.json, report.html).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FS = 25.0                                       # resample rate for spectra (the lab plan slot runs at 25 Hz)
BAND = (0.2, 3.0)                               # Hz: where position oscillation lives (slower is drift, faster is noise)


# ---------- loading ----------

def flights_of_day(date: str) -> list[dict]:
    out = []
    for d in sorted((ROOT / "logs" / "flights" / date).glob("[0-9][0-9]_*")):
        rec = {"n": int(d.name[:2]), "name": d.name, "card": d.name[3:], "session": None}
        deb = (d / "debrief.txt").read_text(encoding="utf-8", errors="replace") if (d / "debrief.txt").exists() else ""
        rec["debrief"] = deb
        m = re.search(r"outputs (\S+)", deb)
        summ = Path(m.group(1).replace("\\", "/")) / "summary.md" if m else None
        if summ and summ.exists():
            s = re.search(r"recording `([^`]+)`", summ.read_text(encoding="utf-8", errors="replace"))
            if s:
                p = ROOT / s.group(1).replace("\\", "/")
                if (p / "telemetry.csv").exists():
                    rec["session"] = p
        out.append(rec)
    return out


def load(session: Path) -> pd.DataFrame:
    df = pd.read_csv(session / "telemetry.csv", usecols=["received_ns", "key", "value"])
    imu = df.key.isin(["slot0.imu_data.rol", "slot0.imu_data.pit"])
    df = df[~df.key.str.startswith("slot") | imu]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    w = df.pivot_table(index="received_ns", columns="key", values="value", aggfunc="last").sort_index().ffill()
    w.index = (w.index - w.index[0]) / 1e9
    xy = [c for c in w.columns if re.fullmatch(r"Ctrler\.loc[xy]s?PID\.(Des|FB|U)", c)]
    w[xy] = w[xy] / 100.0                       # firmware xy loops run in cm and cm/s; z in m: analyse everything in m
    return w.rename(columns={"slot0.imu_data.rol": "imu.rol", "slot0.imu_data.pit": "imu.pit"})


def col(w, k):
    return w[k].to_numpy(float) if k in w else np.full(len(w), np.nan)


# ---------- metrics ----------

def uniform(t, y, t0, t1):
    tu = np.arange(t0, t1, 1 / FS)
    ok = np.isfinite(y)
    return tu, np.interp(tu, t[ok], y[ok]) if ok.sum() > 2 else np.full(len(tu), np.nan)


def spectrum(t, e, t0, t1):
    tu, eu = uniform(t, e, t0, t1)
    if len(eu) < 2 * FS or not np.isfinite(eu).all():
        return np.array([]), np.array([])
    eu = eu - np.polyval(np.polyfit(tu, eu, 1), tu)
    f = np.fft.rfftfreq(len(eu), 1 / FS)
    p = np.abs(np.fft.rfft(eu * np.hanning(len(eu)))) ** 2
    return f, p / max(p.sum(), 1e-12)


def osc(t, e, t0, t1):
    """Oscillation of error e in [t0, t1]: rms of the part that swings around its 2 s running mean,
    peak-to-peak, and the dominant frequency in BAND."""
    m = (t >= t0) & (t <= t1)
    if m.sum() < 10:
        return {}
    tu, eu = uniform(t, e, t0, t1)
    hp = eu - pd.Series(eu).rolling(int(2 * FS), center=True, min_periods=1).mean().to_numpy()
    f, p = spectrum(t, e, t0, t1)
    b = (f >= BAND[0]) & (f <= BAND[1])
    return {"rms_cm": 100 * float(np.sqrt(np.nanmean(eu ** 2))), "osc_rms_cm": 100 * float(np.sqrt(np.nanmean(hp ** 2))),
            "p2p_cm": 100 * float(np.nanmax(eu) - np.nanmin(eu)),
            "f_dom_hz": float(f[b][np.argmax(p[b])]) if b.any() else None,
            "band_frac": float(p[b].sum()) if b.any() else None}


def windows(w):
    t = w.index.to_numpy(float)
    z, zd = col(w, "Ctrler.Z_posPID.FB"), col(w, "Ctrler.Z_posPID.Des")
    xd, yd = col(w, "Ctrler.locxPID.Des"), col(w, "Ctrler.locyPID.Des")
    air = np.where(z > 0.25)[0]
    if not len(air):
        return None
    ta, tb = t[air[0]], t[air[-1]]
    # landing starts where the z target last leaves the hover height
    hov = np.nanmedian(zd[(t > ta) & (t < tb)])
    above = np.where((zd > hov - 0.03) & (t < tb + 5))[0]
    t_land = t[above[-1]] if len(above) else tb
    spd = np.hypot(np.gradient(pd.Series(xd).rolling(5, min_periods=1).mean(), t),
                   np.gradient(pd.Series(yd).rolling(5, min_periods=1).mean(), t))
    return {"t": t, "ta": ta, "tb": tb, "t_land": t_land, "hover_z": float(hov), "spd": spd}


def xy_metrics(w):
    W = windows(w)
    if W is None:
        return None
    t = W["t"]
    x, xd = col(w, "Ctrler.locxPID.FB"), col(w, "Ctrler.locxPID.Des")
    y, yd = col(w, "Ctrler.locyPID.FB"), col(w, "Ctrler.locyPID.Des")
    r = {"hover_z": W["hover_z"], "airborne_s": W["tb"] - W["ta"]}
    cross = np.where((t > W["ta"]) & (xd > 0.2))[0]
    if len(cross):
        ts = t[cross[0]]
        leave = np.where((t > ts + 2) & (np.abs(xd - 0.4) > 0.01))[0]
        te = min(t[leave[0]] if len(leave) else ts + 10, ts + 10)
        m = (t >= ts - 0.5) & (t <= te)
        fin = float(np.nanmedian(xd[(t > ts + 1) & (t < te)]))
        x0 = float(np.nanmedian(x[(t > ts - 2) & (t < ts - 0.2)]))
        out = np.where(m & (np.abs(x - fin) > 0.05))[0]
        r.update(t_step=ts, step_end=te, overshoot_x_cm=100 * float(np.nanmax(x[m]) - fin),
                 rise_s=float(t[m][np.argmax(x[m] >= x0 + 0.9 * (fin - x0))] - ts),
                 settle5_s=float(t[out[-1]] - ts) if len(out) else 0.0,
                 hold_x=osc(t, x - xd, ts + 4, te), hold_y=osc(t, y - yd, ts + 4, te))
        mv = np.where((t > te) & (t < W["t_land"]) & (W["spd"] > 0.02))[0]
        if len(mv):
            p0, p1 = t[mv[0]], t[mv[-1]]
            ex, ey = x - xd, y - yd
            pm = (t >= p0) & (t <= p1)
            r.update(path_t=(p0, p1), path_x=osc(t, ex, p0, p1), path_y=osc(t, ey, p0, p1),
                     path_xy_rms_cm=100 * float(np.sqrt(np.nanmean(ex[pm] ** 2 + ey[pm] ** 2))),
                     path_xy_max_cm=100 * float(np.nanmax(np.hypot(ex[pm], ey[pm]))))
            for k in ("Ctrler.rollPID.Des", "Ctrler.pitchPID.Des"):
                r["att_" + k.split(".")[1][:-3] + "_cmd"] = osc(t, col(w, k) / 100, p0, p1)   # deg: osc() scales by 100
    r["takeoff_hold"] = {"x": osc(t, x - xd, W["ta"] + 1, W["ta"] + 4), "y": osc(t, y - yd, W["ta"] + 1, W["ta"] + 4)}
    return r


def land_metrics(w):
    W = windows(w)
    if W is None:
        return None
    t = W["t"]
    x, xd, y, yd = (col(w, k) for k in ("Ctrler.locxPID.FB", "Ctrler.locxPID.Des", "Ctrler.locyPID.FB", "Ctrler.locyPID.Des"))
    z, zd, vz = col(w, "Ctrler.Z_posPID.FB"), col(w, "Ctrler.Z_posPID.Des"), col(w, "Ctrler.Z_ratePID.FB")
    tl = W["t_land"]
    low = np.where((t > tl) & (z < 0.06))[0]
    td = t[low[0]] if len(low) else W["tb"]
    i_l, i_t = np.searchsorted(t, tl), min(np.searchsorted(t, td), len(t) - 1)
    pre = (t > tl - 3) & (t < tl)
    dsc = (t >= tl) & (t <= td)
    after = (t > td) & (t < td + 3)
    r = {"t_land": tl, "t_touch": td, "descent_s": td - tl, "hover_z": W["hover_z"],
         "target_xy_cm": [100 * xd[i_l], 100 * yd[i_l]],
         "err_at_land_start_cm": 100 * float(np.hypot(x[i_l] - xd[i_l], y[i_l] - yd[i_l])),
         "err_rms_3s_before_cm": 100 * float(np.sqrt(np.nanmean((x[pre] - xd[pre]) ** 2 + (y[pre] - yd[pre]) ** 2))),
         "drift_during_descent_cm": 100 * float(np.hypot(x[i_t] - x[i_l], y[i_t] - y[i_l])),
         "err_max_during_descent_cm": 100 * float(np.nanmax(np.hypot(x[dsc] - xd[dsc], y[dsc] - yd[dsc]))) if dsc.any() else None,
         "err_at_touch_cm": 100 * float(np.hypot(x[i_t] - xd[i_t], y[i_t] - yd[i_t])),
         "touch_xy_cm": [100 * x[i_t], 100 * y[i_t]],
         "vz_at_touch_cms": 100 * float(np.nanmin(vz[(t > td - 0.5) & (t <= td + 0.1)])) if np.isfinite(vz).any() else None,
         "slide_after_touch_cm": 100 * float(np.nanmax(np.hypot(x[after] - x[i_t], y[after] - y[i_t]))) if after.any() else None}
    return r


# ---------- plots ----------

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": .3, "font.size": 9})
    return plt


def plot_xy(flights, out: Path) -> list[Path]:
    plt = _plt()
    files = []
    fl = [f for f in flights if f.get("xy") and "t_step" in f["xy"]]
    if not fl:
        return files
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for f in fl:
        w, ts = f["w"], f["xy"]["t_step"]
        t = w.index.to_numpy(float) - ts
        m = (t > -2) & (t < 10)
        l, = ax[0].plot(t[m], 100 * col(w, "Ctrler.locxPID.FB")[m], label=f["label"])
        ax[0].plot(t[m], 100 * col(w, "Ctrler.locxPID.Des")[m], "--", c=l.get_color(), lw=.8)
        ax[1].plot(t[m], 100 * col(w, "Ctrler.locyPID.FB")[m], c=l.get_color())
    ax[0].axhspan(35, 45, color="g", alpha=.08)
    ax[0].set_ylabel("x cm (dashed = target)")
    ax[1].set_ylabel("y cm (target 0)")
    ax[1].set_xlabel("s after the 40 cm x step")
    ax[0].legend(ncol=4)
    ax[0].set_title("40 cm step in x: response (green band = within 5 cm)")
    files.append(_save(fig, out / "xy_step.png"))

    pf = [f for f in fl if "path_t" in f["xy"]]
    if pf:
        fig, ax = plt.subplots(1, len(pf), figsize=(3.2 * len(pf), 3.6), squeeze=False)
        for a, f in zip(ax[0], pf):
            w, (p0, p1) = f["w"], f["xy"]["path_t"]
            t = w.index.to_numpy(float)
            m = (t >= p0) & (t <= p1)
            a.plot(100 * col(w, "Ctrler.locxPID.Des")[m], 100 * col(w, "Ctrler.locyPID.Des")[m], "k--", lw=.8)
            a.plot(100 * col(w, "Ctrler.locxPID.FB")[m], 100 * col(w, "Ctrler.locyPID.FB")[m], lw=.9)
            a.set_aspect("equal")
            a.set_title(f"{f['label']}  rms {f['xy']['path_xy_rms_cm']:.1f} cm", fontsize=8)
        fig.suptitle("Circle + figure-8 (dashed = target)")
        files.append(_save(fig, out / "xy_paths.png"))

        fig, ax = plt.subplots(3, 1, figsize=(10, 8))
        for f in pf:
            w, (p0, p1) = f["w"], f["xy"]["path_t"]
            t = w.index.to_numpy(float)
            m = (t >= p0) & (t <= p1)
            l, = ax[0].plot(t[m] - p0, 100 * (col(w, "Ctrler.locxPID.FB") - col(w, "Ctrler.locxPID.Des"))[m], lw=.8, label=f["label"])
            ax[1].plot(t[m] - p0, col(w, "Ctrler.rollPID.Des")[m], lw=.7, c=l.get_color())
            fr, p = spectrum(t, col(w, "Ctrler.locxPID.FB") - col(w, "Ctrler.locxPID.Des"), p0, p1)
            ax[2].semilogy(fr, p, lw=.9, c=l.get_color())
        ax[0].set_ylabel("x error cm")
        ax[0].legend(ncol=4)
        ax[1].set_ylabel("roll command")
        ax[1].set_xlabel("s into the path")
        ax[2].axvspan(*BAND, color="orange", alpha=.08)
        ax[2].set_xlim(0, 5)
        ax[2].set_xlabel("Hz (x error spectrum; orange = oscillation band)")
        fig.suptitle("Path tracking: error, attitude command, spectrum")
        files.append(_save(fig, out / "xy_path_error.png"))

    keys = [("overshoot_x_cm", "step overshoot cm"), ("settle5_s", "settle to 5 cm, s"),
            ("hold_osc", "hold oscillation rms cm"), ("path_xy_rms_cm", "path rms cm"), ("path_osc", "path oscillation rms cm")]
    fig, ax = plt.subplots(1, len(keys), figsize=(14, 3))
    for a, (k, lab) in zip(ax, keys):
        vals = [_v(f["xy"], k) for f in fl]
        a.bar(range(len(fl)), vals, color=[f"C{i}" for i in range(len(fl))])
        a.set_xticks(range(len(fl)), [f["label"] for f in fl], rotation=45, ha="right", fontsize=7)
        a.set_title(lab, fontsize=8)
    files.append(_save(fig, out / "xy_bars.png"))
    return files


def _v(r, k):
    if k == "hold_osc":
        return np.hypot(r.get("hold_x", {}).get("osc_rms_cm", np.nan), r.get("hold_y", {}).get("osc_rms_cm", np.nan))
    if k == "path_osc":
        return np.hypot(r.get("path_x", {}).get("osc_rms_cm", np.nan), r.get("path_y", {}).get("osc_rms_cm", np.nan))
    return r.get(k, np.nan)


def plot_land(flights, out: Path) -> list[Path]:
    plt = _plt()
    fl = [f for f in flights if f.get("land")]
    if not fl:
        return []
    fig, ax = plt.subplots(len(fl), 3, figsize=(13, 3.2 * len(fl)), squeeze=False)
    for row, f in zip(ax, fl):
        w, L = f["w"], f["land"]
        t = w.index.to_numpy(float) - L["t_land"]
        m = (t > -12) & (t < L["descent_s"] + 4)
        row[0].plot(t[m], 100 * col(w, "Ctrler.Z_posPID.FB")[m], label="z")
        row[0].plot(t[m], 100 * col(w, "Ctrler.Z_posPID.Des")[m], "k--", lw=.8, label="z target")
        row[0].axvline(L["descent_s"], c="r", lw=.8)
        row[0].set_ylabel("cm")
        row[0].set_title(f"{f['label']}: height (red = touchdown)", fontsize=8)
        for k, c in (("locx", "C0"), ("locy", "C1")):
            row[1].plot(t[m], 100 * col(w, f"Ctrler.{k}PID.FB")[m], c=c, label=k[-1])
            row[1].plot(t[m], 100 * col(w, f"Ctrler.{k}PID.Des")[m], "--", c=c, lw=.8)
        row[1].axvline(0, c="k", lw=.6)
        row[1].axvline(L["descent_s"], c="r", lw=.8)
        row[1].set_title("x, y (dashed = target; black = descent starts)", fontsize=8)
        row[1].legend()
        d = (t >= -3) & (t <= L["descent_s"] + 2)
        row[2].plot(100 * col(w, "Ctrler.locxPID.FB")[d], 100 * col(w, "Ctrler.locyPID.FB")[d], lw=.9)
        row[2].plot(*L["target_xy_cm"], "k+", ms=12)
        row[2].plot(*L["touch_xy_cm"], "rx", ms=9)
        row[2].add_patch(plt.Circle(L["target_xy_cm"], 10, fill=False, ls=":"))
        row[2].set_aspect("equal")
        row[2].set_title(f"top view, last 3 s + descent: touch {L['err_at_touch_cm']:.0f} cm off target", fontsize=8)
    for a in ax[-1][:2]:
        a.set_xlabel("s after descent start")
    files = [_save(fig, out / "landing.png")]
    return files


def _save(fig, p: Path) -> Path:
    fig.tight_layout()
    fig.savefig(p)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return p


# ---------- report ----------

def _f(v, n=1):
    return "" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{n}f}"


def report(date, flights, pngs, out: Path) -> Path:
    rows = ["<tr><th>#</th><th>flight</th><th>recording</th><th>debrief</th></tr>"]
    for f in flights:
        first = next((ln for ln in f["debrief"].splitlines() if ln.strip()), "")
        rows.append(f"<tr><td>{f['n']}</td><td>{f['card']}</td><td>{'yes' if f['session'] else 'none (refused)'}</td>"
                    f"<td>{first[:110]}</td></tr>")
    xy = [f for f in flights if f.get("xy") and "t_step" in f["xy"]]
    xrows = ["<tr><th>#</th><th>ctrl</th><th>overshoot cm</th><th>rise s</th><th>settle 5 cm s</th><th>hold osc cm</th>"
             "<th>path rms cm</th><th>path max cm</th><th>path osc cm</th><th>osc Hz x/y</th><th>roll cmd osc deg</th></tr>"]
    for f in xy:
        r = f["xy"]
        xrows.append(f"<tr><td>{f['n']}</td><td>{f['label']}</td><td>{_f(r.get('overshoot_x_cm'))}</td><td>{_f(r.get('rise_s'), 2)}</td>"
                     f"<td>{_f(r.get('settle5_s'))}</td><td>{_f(_v(r, 'hold_osc'))}</td><td>{_f(r.get('path_xy_rms_cm'))}</td>"
                     f"<td>{_f(r.get('path_xy_max_cm'))}</td><td>{_f(_v(r, 'path_osc'))}</td>"
                     f"<td>{_f(r.get('path_x', {}).get('f_dom_hz'), 2)} / {_f(r.get('path_y', {}).get('f_dom_hz'), 2)}</td>"
                     f"<td>{_f(r.get('att_roll_cmd', {}).get('osc_rms_cm'), 2)}</td></tr>")
    lrows = ["<tr><th>#</th><th>target cm</th><th>err at descent start</th><th>rms 3 s before</th><th>max in descent</th>"
             "<th>drift in descent</th><th>off target at touch</th><th>descent s</th><th>vz at touch cm/s</th><th>slide after</th></tr>"]
    for f in flights:
        L = f.get("land")
        if L:
            lrows.append(f"<tr><td>{f['n']}</td><td>{_f(L['target_xy_cm'][0], 0)}, {_f(L['target_xy_cm'][1], 0)}</td>"
                         f"<td>{_f(L['err_at_land_start_cm'])}</td><td>{_f(L['err_rms_3s_before_cm'])}</td>"
                         f"<td>{_f(L['err_max_during_descent_cm'])}</td><td>{_f(L['drift_during_descent_cm'])}</td>"
                         f"<td>{_f(L['err_at_touch_cm'])}</td><td>{_f(L['descent_s'])}</td><td>{_f(L['vz_at_touch_cms'])}</td>"
                         f"<td>{_f(L['slide_after_touch_cm'])}</td></tr>")
    imgs = "".join(f"<h3>{p.stem}</h3><img src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}'>" for p in pngs)
    html = f"""<!doctype html><meta charset=utf-8><title>Lab flights {date}</title>
<style>body{{font:14px system-ui;margin:16px;max-width:1300px}}table{{border-collapse:collapse;margin:8px 0 20px}}
td,th{{border:1px solid #ccc;padding:3px 7px;text-align:right}}img{{max-width:100%}}</style>
<h1>Lab flights {date}</h1><p>Generated by <code>python -m ground_station.analysis.lab_flights {date}</code>.
Oscillation = rms of the error around its 2 s running mean (lag removed); band {BAND[0]}-{BAND[1]} Hz.</p>
<h2>Flights</h2><table>{''.join(rows)}</table><h2>xy controllers</h2><table>{''.join(xrows)}</table>
<h2>Landings</h2><table>{''.join(lrows)}</table>{imgs}"""
    p = out / "report.html"
    p.write_text(html, encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ground_station.analysis.lab_flights")
    ap.add_argument("date")
    ap.add_argument("--only", nargs="*", default=None, help="card prefixes to keep")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else ROOT / "logs" / "analysis" / a.date
    out.mkdir(parents=True, exist_ok=True)
    flights = [f for f in flights_of_day(a.date) if not a.only or any(f["card"].startswith(o) for o in a.only)]
    for f in flights:
        f["label"] = f"{f['n']:02d} {f['card'].rsplit('-', 1)[-1]}"
        if not f["session"]:
            continue
        f["w"] = load(f["session"])
        if f["card"].startswith("xy-ctrl"):
            f["xy"] = xy_metrics(f["w"])
        f["land"] = land_metrics(f["w"])
    pngs = plot_xy(flights, out) + plot_land([f for f in flights if f["card"].startswith("pad-landing")], out)
    keep = {k: v for k, v in ((f["name"], {"session": str(f["session"]), "xy": f.get("xy"), "land": f.get("land")})
                               for f in flights)}
    (out / "metrics.json").write_text(json.dumps(keep, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
    print(report(a.date, flights, pngs, out))


if __name__ == "__main__":
    main()
