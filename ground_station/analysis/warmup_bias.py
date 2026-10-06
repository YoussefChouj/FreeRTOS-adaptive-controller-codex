"""Warm-up gyro bias vs Real_Temp from a livewatch CSV (still drone, power-cycled cold).

Usage: python -m ground_station.analysis.warmup_bias <csv> [--win 60] [--mark t0,t1]
Needs columns t, ORI_Gyro{x,y,z}, Gyro_{X,Y,Z}_Offset, Real_Temp, g_gyro_z_bias_blocks.

Total bias per axis = Gyro_*_Offset (boot zero; z also moved by the still tracker) + mean of ORI_Gyro*
(residual after the offset). White noise 0.47 deg/s/rtHz (measured 10-06) makes short-window means scatter
by 0.47/sqrt(secs), so bins carry that 1-sigma and the slope is a regression over every sample.
"""
import argparse
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

WHITE = 0.47                                   # deg/s/rtHz, 200 Hz noise floor log 10-06
AX = (("x", "tab:red"), ("y", "tab:green"), ("z", "tab:blue"))


def run(csv, win=60.0, mark=None, png=True):

    d = pd.read_csv(csv)
    d = d[d.t > 0].reset_index(drop=True)
    for a, _ in AX:
        d[f"tot_{a}"] = d[f"Gyro_{a.upper()}_Offset"] + d[f"ORI_Gyro{a}"]
    secs = float(d.t.iloc[-1] - d.t.iloc[0])
    out = {"csv": csv, "rows": len(d), "secs": round(secs, 1), "rate_hz": round(len(d) / secs, 1),
           "temp_first_last": [int(d.Real_Temp.iloc[0]), int(d.Real_Temp.iloc[-1])],
           "blocks_first_last": [int(d.g_gyro_z_bias_blocks.iloc[0]), int(d.g_gyro_z_bias_blocks.iloc[-1])],
           "axes": {}, "bins": []}

    T = d.Real_Temp.values.astype(float)
    sxx = float(((T - T.mean()) ** 2).sum())
    for a, _ in AX:
        y = d[f"tot_{a}"].values
        k, c = np.polyfit(T, y, 1)
        se = float(np.std(y - (k * T + c)) / np.sqrt(sxx))   # white-noise slope standard error
        out["axes"][a] = {"slope_degps_per_C": round(float(k), 4), "slope_se": round(se, 4),
                          "fit_change_over_run": round(float(k * (T.max() - T.min())), 3),
                          "offset_first_last": [round(float(d[f"Gyro_{a.upper()}_Offset"].iloc[0]), 4),
                                                round(float(d[f"Gyro_{a.upper()}_Offset"].iloc[-1]), 4)]}

    b = d.groupby("Real_Temp")
    bins = pd.DataFrame({"secs": b.t.max() - b.t.min(), "t0": b.t.min()})
    for a, _ in AX:
        bins[a] = b[f"tot_{a}"].mean()
    bins["sigma"] = WHITE / np.sqrt(bins.secs.clip(lower=0.5))
    out["bins"] = [{"T": int(i), **{k: round(float(v), 4) for k, v in r.items()}} for i, r in bins.iterrows()]

    d["w"] = (d.t // win).astype(int)
    w = d.groupby("w").mean(numeric_only=True)

    if not png:
        json.dump(out, open(csv.replace(".csv", "_warmup.json"), "w"), indent=1)
        return out
    fig, axs = plt.subplots(3, 1, figsize=(10, 11))
    for a, col in AX:
        axs[0].plot(w.t, w[f"tot_{a}"], c=col, marker="o", ms=3, label=f"{a} total bias ({win:.0f} s means)")
    axs[0].set_ylabel("deg/s")
    axs[0].set_title("Gyro bias while warming up from cold (drone still, boot offsets taken at ~18 C)")
    t2 = axs[0].twinx()
    t2.plot(d.t[::200], d.Real_Temp[::200], c="k", lw=1)
    t2.set_ylabel("Real_Temp (C, black)")
    axs[0].legend(fontsize=7, loc="lower right")
    for a, col in AX:
        axs[1].plot(d.t[::200], d[f"Gyro_{a.upper()}_Offset"][::200], c=col, label=f"Gyro_{a.upper()}_Offset")
    axs[1].set_ylabel("applied offset (deg/s)")
    axs[1].legend(fontsize=7)
    for ax in axs[:2]:
        if mark:
            ax.axvspan(*mark, color="gold", alpha=0.35)
        ax.set_xlabel("t since capture start (s)" + ("   [gold: marked event]" if mark else ""))
    for a, col in AX:
        o = out["axes"][a]
        dx = {"x": -0.15, "y": 0.0, "z": 0.15}[a]
        axs[2].errorbar(bins.index + dx, bins[a], yerr=bins.sigma, fmt="o", c=col, ms=4, capsize=2,
                        label=f"{a}: {o['slope_degps_per_C']:+.4f} +- {o['slope_se']:.4f} deg/s per C")
    axs[2].set_xlabel("Real_Temp (C, integer)  -  error bars: 1 sigma from white noise and time spent at that C")
    axs[2].set_ylabel("total bias (deg/s)")
    axs[2].legend(fontsize=7)
    fig.tight_layout()
    png_path = csv.replace(".csv", "_warmup.png")
    fig.savefig(png_path, dpi=110)
    plt.close(fig)
    json.dump(out, open(csv.replace(".csv", "_warmup.json"), "w"), indent=1)
    out["png"] = png_path
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv")
    p.add_argument("--win", type=float, default=60.0, help="time-plot window (s)")
    p.add_argument("--mark", help="shade an event span t0,t1 (s), e.g. lights switched on")
    a = p.parse_args(argv)
    mark = tuple(float(v) for v in a.mark.split(",")) if a.mark else None
    out = run(a.csv, a.win, mark)
    for ax, o in out["axes"].items():
        print(f"{ax}: {o['slope_degps_per_C']:+.4f} +- {o['slope_se']:.4f} deg/s per C, "
              f"fit change {o['fit_change_over_run']:+.3f} deg/s over {out['temp_first_last']} C")
    print("PNG", out["png"])


if __name__ == "__main__":
    main()
