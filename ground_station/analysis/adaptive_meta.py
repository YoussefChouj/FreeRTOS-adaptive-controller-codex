"""Meta table over many adaptive_review runs: load x variant x PID, one row per airborne segment plus pooled rows.

    python -m ground_station.analysis.adaptive_review logs/<stem> --out <dir>/<stem> --no-plots   (per log)
    python -m ground_station.analysis.adaptive_meta <dir> [--labels labels.csv]

Reads <dir>/*/adaptive_summary.json and writes <dir>/meta.md. The variant and the load come from the stem
(vpNN, pid, ARM/arm -> arm 293 g, doll -> doll, else rope 570 g) unless labels.csv (stem,variant,load) overrides.
A segment flown with injection off is a PID segment whatever the variant; its cancel numbers are the shadow u_ad.
"""
import argparse
import csv
import glob
import json
import os
import re

import numpy as np

AXES = ("pitch", "roll", "yaw", "z_rate")


def label(stem, over):
    if stem in over:
        return over[stem]
    m = re.search(r"vp(\d+)", stem)
    var = "vp%s" % m.group(1) if m else ("pid" if "pid" in stem.lower() else "?")
    s = stem.lower()
    load = "arm 293 g" if "arm" in s else ("doll" if "doll" in s else "rope 570 g")
    return var, load + (" circle" if "circle" in s else "")


def rows(d):
    out = []
    for seg, v in d["segs"].items():
        if "dur_s" not in v:
            continue
        kind = seg.split()[1]
        out.append((seg, kind, v))
    return out


def fmt(x, f="%.2f"):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f % x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--labels", help="csv stem,variant,load overriding the stem-derived labels")
    a = ap.parse_args()
    over = {}
    if a.labels:
        with open(a.labels, encoding="utf-8") as fh:
            over = {r["stem"]: (r["variant"], r["load"]) for r in csv.DictReader(fh)}
    runs = []
    for p in sorted(glob.glob(os.path.join(a.dir, "*", "adaptive_summary.json")), key=os.path.getmtime):
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        runs.append((d["stem"],) + label(d["stem"], over) + (d,))
    ax_get = lambda v, ax, k, i=None: (v.get(ax, {}).get(k) if i is None else
                                       (v.get(ax, {}).get(k) or [None] * 3)[i])
    md = ["# Lab-day meta table (%d logs)" % len(runs), "",
          "Measured from the logs by `adaptive_review --no-plots` and `adaptive_meta`. Controller = PID for "
          "segments flown with injection off, else the variant. Attitude sd in deg, band PSD of the body rate in "
          "0.25-0.9 Hz ((deg/s)^2), cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat) at the fitted b (1 = cancels "
          "the disturbance, < 0 = adds to it), phase of u_ad against -Delta_hat in the band (ideal 0 deg). "
          "ideal16 = cancel a perfect estimator behind a 16 rad/s filter would reach (the ceiling).", "",
          "## Every airborne segment", "",
          "| log | variant | load | seg | ctrl | s | pitch sd | roll sd | band PSD p / r | peak Hz p / r | "
          "z err sd (m) | sat % | cancel p / r / yaw / z | phase p / r | ideal16 p / r |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    pool = {}
    for stem, var, load, d in runs:
        for seg, kind, v in rows(d):
            ctrl = "pid" if kind == "PID" else var
            inj = kind == "MRAC"
            c = [ax_get(v, ax, "cancel", 1) if inj else None for ax in AXES]
            ph = [ax_get(v, ax, "phase") if inj else None for ax in ("pitch", "roll")]
            i16 = [v.get(ax, {}).get("ideal", {}).get("16") for ax in ("pitch", "roll")]
            pk = v.get("peak_hz", [None, None])
            md.append("| %s | %s | %s | %s | %s | %.0f | %.2f | %.2f | %.0f / %.0f | %s / %s | %s | %.1f | %s | %s | %s |" % (
                stem, var, load, seg.split()[0], ctrl, v["dur_s"], v["pitch_sd"], v["roll_sd"], v["band_psd"][0],
                v["band_psd"][1], fmt(pk[0]), fmt(pk[1]), fmt(v.get("z_err_sd"), "%.3f"), v["motor_sat_pct"],
                " / ".join(fmt(x, "%+.2f") for x in c), " / ".join(fmt(x, "%+.0f") for x in ph),
                " / ".join(fmt(x, "%+.2f") for x in i16)))
            pool.setdefault((load.replace(" circle", ""), ctrl), []).append((v, c, ph))
    md += ["", "## Pooled by load and controller (duration-weighted means over segments)", "",
           "| load | ctrl | segs | s | pitch sd | roll sd | band PSD p / r | cancel p / r / yaw / z | phase p / r |",
           "|---|---|---|---|---|---|---|---|---|"]
    for (load, ctrl), lst in sorted(pool.items()):
        w = np.array([v["dur_s"] for v, _, _ in lst])
        wm = lambda xs: (float(np.average([x for x in xs if x is not None],
                                          weights=[wi for x, wi in zip(xs, w) if x is not None]))
                         if any(x is not None for x in xs) else None)
        md.append("| %s | %s | %d | %.0f | %.2f | %.2f | %.0f / %.0f | %s | %s |" % (
            load, ctrl, len(lst), w.sum(), wm([v["pitch_sd"] for v, _, _ in lst]), wm([v["roll_sd"] for v, _, _ in lst]),
            wm([v["band_psd"][0] for v, _, _ in lst]), wm([v["band_psd"][1] for v, _, _ in lst]),
            " / ".join(fmt(wm([c[i] for _, c, _ in lst]), "%+.2f") for i in range(4)),
            " / ".join(fmt(wm([p[i] for _, _, p in lst]), "%+.0f") for i in range(2))))
    md += ["", "## Disturbance Delta_hat per load (all segments, control units; -Delta static = trim needed)", "",
           "| load | axis | segs | -Delta static mean (min..max) | Delta dyn RMS mean | ideal cancel w 4 / 8 / 16 |",
           "|---|---|---|---|---|---|"]
    by_load = {}
    for (load, _), lst in pool.items():
        by_load.setdefault(load, []).extend(lst)
    for load, lst in sorted(by_load.items()):
        for ax in AXES:
            vs = [v[ax] for v, _, _ in lst if ax in v]
            if not vs:
                continue
            st = np.array([x["delta_static"] for x in vs])
            md.append("| %s | %s | %d | %+.4f (%+.4f..%+.4f) | %.4f | %s |" % (
                load, ax, len(vs), st.mean(), st.min(), st.max(), np.mean([x["delta_dyn"] for x in vs]),
                " / ".join("%+.2f" % np.mean([x["ideal"][w] for x in vs]) for w in ("4", "8", "16"))))
    with open(os.path.join(a.dir, "meta.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print("wrote %s (%d logs)" % (os.path.join(a.dir, "meta.md"), len(runs)))


if __name__ == "__main__":
    main()
