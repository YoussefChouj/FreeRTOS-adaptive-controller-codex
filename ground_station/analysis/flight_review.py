"""Flight review (WP-42 P5): one self-contained HTML page per recorder session.

    python -m ground_station.analysis.flight_review <session_dir> [--out FILE] [--no-sat]

Sections:
  header           manifest (label, reason, duration) and the session_schema build/contracts when the recording has one
                   (WP-42 P3), the HOVER hold window, and the prim_state / safety_trip timeline
  setpoint/actual  x / y / z position (campaign_capture.POSITION_AXES) and every logged ``<loop>.Des`` / ``<loop>.FB``
                   pair, with error RMS and max over the hold (the whole flight span without one)
  spectra          attitude and rate feedback (Hann rFFT on a uniform resample) with the largest line of each
  saturation       motor commands against the bench limits; share of flight-span samples at the high / low limit
  sample interval  histogram of the frame interval per telemetry slot, with median / p99 / max and the gap count

Plots are PNGs inside the page (data URIs), so the page is one file that opens anywhere; the numbers are also in the
page as JSON (``<script id="review-data">``). The loader, hold window and timeline are campaign_outputs' and
flight_debrief's; the controller replays of the same session are the WP-34 scripts (mrac_log_replay, refmodel_replay).
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping

from ground_station.analysis import flight_debrief as fd
from ground_station.livewatch.campaign_capture import ATTITUDE, MOTORS, POSITION_AXES
from ground_station.service.campaign_outputs import _SLOT_PREFIX, PRIM_STATE, Series, hold_window

GAP_FACTOR = 3.0        # an interval above GAP_FACTOR x the slot median counts as a gap (as flight_debrief)
SPECTRUM_SYMS = (ATTITUDE[0], ATTITUDE[1], fd.RATE_FB["roll"], fd.RATE_FB["pitch"])


def read_stream(slot0: Path) -> tuple[Series, dict[int, list[float]]]:
    """A stream_log --frames capture (``<stem>.slot0.csv`` plus its sibling slots) as ``read_session`` returns it,
    loaded by log_corpus (board clock, t = 0 at the first slot-0 frame)."""
    from ground_station.analysis import log_corpus as lc
    raw = lc._load_stream(slot0)
    series: Series = {k: (t.tolist(), v.tolist()) for k, (t, v) in raw.items()}
    frames: dict[int, list[float]] = {}
    for i, f in enumerate(lc._stream_slots(slot0)):
        with f.open(newline="", encoding="utf-8") as fh:
            cols = [c for c in next(csv.reader(fh), []) if c not in ("t_src_ms", "t_host_s", "seq")]
        hit = next((series[c][0] for c in cols if c in series), None)
        if hit:
            frames[i] = hit
    return series, frames


def read_session(session_dir: str | Path) -> tuple[Series, dict[int, list[float]]]:
    """``campaign_outputs.read_telemetry``'s series plus the frame times (s) per slot, in one pass over the CSV.
    A ``<stem>.slot0.csv`` path reads a stream_log capture instead (``read_stream``)."""
    if str(session_dir).endswith(".slot0.csv"):
        return read_stream(Path(session_dir))
    series: Series = {}
    frames: dict[int, list[float]] = {}
    t0 = None
    with (Path(session_dir) / "telemetry.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ns, value = int(row["received_ns"]), float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            t0 = ns if t0 is None else t0
            t = (ns - t0) / 1e9
            ts, vs = series.setdefault(_SLOT_PREFIX.sub("", row.get("key") or ""), ([], []))
            ts.append(t)
            vs.append(value)
            try:
                slot = int(row.get("slot") or 0)
            except ValueError:
                slot = -1
            ft = frames.setdefault(slot, [])
            if not ft or ft[-1] != t:
                ft.append(t)
    return series, frames


def flight_span(series: Series) -> tuple[float, float] | None:
    """First and last sample with prim_state not IDLE; the whole recording without a prim_state stream."""
    ts, vs = series.get(PRIM_STATE, ([], []))
    if ts:
        busy = [t for t, v in zip(ts, vs) if int(round(v)) != 0]
        return (busy[0], busy[-1]) if busy else None
    all_t = [t for s in series.values() for t in (s[0][0], s[0][-1]) if s[0]]
    return (min(all_t), max(all_t)) if all_t else None


def _in(ts: list[float], vs: list[float], w: tuple[float, float] | None) -> tuple[list[float], list[float]]:
    if w is None:
        return ts, vs
    pts = [(t, v) for t, v in zip(ts, vs) if w[0] <= t <= w[1]]
    return [t for t, _ in pts], [v for _, v in pts]


def tracking_pairs(series: Series) -> list[dict[str, Any]]:
    """Position axes first (in m), then every other ``<loop>.Des`` / ``<loop>.FB`` pair in the log (raw units)."""
    out, used = [], set()
    for name, ax in zip(("x", "y", "z"), POSITION_AXES):
        if ax.feedback in series and ax.reference in series:
            out.append({"label": f"{name} position", "fb": ax.feedback, "des": ax.reference, "scale": ax.to_m,
                        "unit": "m"})
            used |= {ax.feedback, ax.reference}
    for sym in sorted(series):
        if sym.endswith(".FB") and sym not in used and (des := sym[:-3] + ".Des") in series:
            out.append({"label": sym[:-3], "fb": sym, "des": des, "scale": 1.0, "unit": ""})
    return out


def _err(series: Series, pair: Mapping[str, Any], w: tuple[float, float] | None) -> list[float]:
    import bisect
    ts, vs = _in(*series[pair["fb"]], w)
    rt, rv = series[pair["des"]]
    return [(v - rv[max(0, bisect.bisect_right(rt, t) - 1)]) * pair["scale"] for t, v in zip(ts, vs)]


def tracking_stats(series: Series, pairs: list[dict[str, Any]], w: tuple[float, float] | None) -> list[dict[str, Any]]:
    rows = []
    for p in pairs:
        e = _err(series, p, w)
        rows.append({"label": p["label"], "unit": p["unit"], "n": len(e),
                     "err_rms": round(math.sqrt(sum(x * x for x in e) / len(e)), 4) if e else None,
                     "err_max": round(max(abs(x) for x in e), 4) if e else None})
    return rows


def spectrum(ts: list[float], vs: list[float]) -> tuple[Any, Any] | None:
    """(freqs Hz, amplitude) of a Hann rFFT on a uniform resample at the median interval; None if too short."""
    import numpy as np
    if len(ts) < 16 or ts[-1] - ts[0] < fd.MIN_SPECTRUM_S:
        return None
    dt = statistics.median(b - a for a, b in zip(ts, ts[1:]))
    if dt <= 0:
        return None
    y = np.interp(np.arange(ts[0], ts[-1], dt), ts, vs)
    win = np.hanning(len(y))
    return np.fft.rfftfreq(len(y), dt), 2.0 * np.abs(np.fft.rfft((y - y.mean()) * win)) / win.sum()


def saturation(series: Series, sat: tuple[float, float] | None, w: tuple[float, float] | None) -> dict[str, Any]:
    """Per motor over the window: mean, min, max and (with limits) the share of samples at or past each limit."""
    out: dict[str, Any] = {}
    for sym in MOTORS:
        _, vs = _in(*series.get(sym, ([], [])), w)
        if not vs:
            continue
        m = {"n": len(vs), "mean": round(sum(vs) / len(vs), 1), "min": min(vs), "max": max(vs)}
        if sat is not None:
            hi, lo = sat
            m["frac_hi"] = round(sum(v >= hi for v in vs) / len(vs), 4)
            m["frac_lo"] = round(sum(v <= lo for v in vs) / len(vs), 4)
        out[sym.split(".")[-1]] = m
    return out


def interval_stats(frames: Mapping[int, list[float]]) -> dict[int, dict[str, Any]]:
    out = {}
    for slot, ts in sorted(frames.items()):
        dts = sorted(b - a for a, b in zip(ts, ts[1:]))
        if not dts:
            continue
        med = statistics.median(dts)
        out[slot] = {"frames": len(ts), "median_ms": round(1e3 * med, 2),
                     "p99_ms": round(1e3 * dts[int(0.99 * (len(dts) - 1))], 2), "max_ms": round(1e3 * dts[-1], 1),
                     "gaps": sum(d > GAP_FACTOR * med for d in dts),
                     "rate_hz": round((len(ts) - 1) / (ts[-1] - ts[0]), 1) if ts[-1] > ts[0] else None}
    return out


# ---- plots -----------------------------------------------------------------------------------------------------

def _png(fig) -> str:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _shade(axes, w: tuple[float, float] | None) -> None:
    if w:
        for ax in axes:
            ax.axvspan(*w, color="0.92", zorder=0)


def plot_tracking(series: Series, pairs: list[dict[str, Any]], hold: tuple[float, float] | None) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not pairs:
        return None
    fig, axes = plt.subplots(len(pairs), 1, figsize=(10, 1.9 * len(pairs) + 0.6), sharex=True, squeeze=False)
    axes = axes[:, 0]
    for ax, p in zip(axes, pairs):
        for sym, style, lab in ((p["des"], "--", "setpoint"), (p["fb"], "-", "actual")):
            ts, vs = series[sym]
            ax.plot(ts, [v * p["scale"] for v in vs], style, linewidth=0.8, label=lab)
        ax.set_ylabel(f"{p['label']} {p['unit']}".strip(), fontsize=8)
        ax.legend(loc="upper right", fontsize=7)
    axes[-1].set_xlabel("t s (from recording start; grey: HOVER hold)")
    _shade(axes, hold)
    return _png(fig)


def plot_spectra(series: Series, w: tuple[float, float] | None) -> tuple[str | None, list[dict[str, Any]]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    fig, ax = plt.subplots(figsize=(10, 3.6))
    peaks, drawn = [], False
    for sym in SPECTRUM_SYMS:
        ts, vs = _in(*series.get(sym, ([], [])), w)
        sp = spectrum(ts, vs)
        if sp is None:
            continue
        freqs, amp = sp
        ax.semilogy(freqs, amp + 1e-6, linewidth=0.8, label=sym)
        band = freqs >= 0.5
        if band.any():
            i = int(np.argmax(np.where(band, amp, -1.0)))
            peaks.append({"symbol": sym, "f_hz": round(float(freqs[i]), 2), "amp": round(float(amp[i]), 3),
                          "fs_hz": round(1.0 / statistics.median(b - a for a, b in zip(ts, ts[1:])), 1)})
        drawn = True
    if not drawn:
        plt.close(fig)
        return None, peaks
    ax.axvspan(1, 6, color="0.92", zorder=0)
    ax.axvline(8, color="0.6", linestyle=":")
    ax.set_xlabel("Hz (grey: angle-loop band 1-6 Hz; dotted: rate band from 8 Hz)")
    ax.set_ylabel("amplitude (deg, deg/s)")
    ax.legend(fontsize=7)
    return _png(fig), peaks


def plot_motors(series: Series, sat: tuple[float, float] | None, span: tuple[float, float] | None) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    syms = [s for s in MOTORS if s in series]
    if not syms:
        return None
    fig, ax = plt.subplots(figsize=(10, 3.2))
    for sym in syms:
        ax.plot(*series[sym], linewidth=0.7, label=sym.split(".")[-1])
    for v in sat or ():
        ax.axhline(v, color="r", linestyle="--", linewidth=0.8)
    _shade([ax], span)
    ax.set_xlabel("t s (grey: flight span; red: bench saturation limits)")
    ax.set_ylabel("motor cmd")
    ax.legend(fontsize=7, ncol=4)
    return _png(fig)


def plot_intervals(frames: Mapping[int, list[float]]) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    slots = [s for s, ts in sorted(frames.items()) if len(ts) > 1]
    if not slots:
        return None
    fig, axes = plt.subplots(1, len(slots), figsize=(10, 3.0), squeeze=False)
    for ax, s in zip(axes[0], slots):
        ts = frames[s]
        ax.hist([round(1e3 * (b - a), 3) for a, b in zip(ts, ts[1:])], bins=60, log=True)  # float-eps spread breaks bins
        ax.set_title(f"slot {s}", fontsize=8)
        ax.set_xlabel("frame interval ms", fontsize=8)
    axes[0][0].set_ylabel("count")
    return _png(fig)


# ---- page ------------------------------------------------------------------------------------------------------

def _table(rows: list[Mapping[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "<p class=dim>none</p>"
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if r.get(c) is None else str(r.get(c)))}</td>"
                                    for c in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _img(src: str | None, alt: str) -> str:
    return f'<img alt="{html.escape(alt)}" src="{src}">' if src else f"<p class=dim>no data for {html.escape(alt)}</p>"


CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:16px auto;max-width:1060px;padding:0 16px;background:#fff;color:#111}
h1{font-size:20px}h2{font-size:16px;margin-top:28px;border-bottom:1px solid #ddd}img{max-width:100%}
table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #ddd;padding:2px 8px;text-align:left;font-size:13px}
.dim{color:#777}code{background:#f4f4f4;padding:0 3px}"""


def review(session_dir: str | Path, out: str | Path | None = None,
           sat: tuple[float, float] | None = None) -> Path:
    """Write the review page (default ``<session_dir>/flight_review.html``) and return its path."""
    d = Path(session_dir)
    series, frames = read_session(d)
    manifest: dict[str, Any] = {}
    if (d / "manifest.json").is_file():
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest.get("session_schema") or {}
    hold, span = hold_window(series), flight_span(series)
    w = hold or span
    pairs = tracking_pairs(series)
    spec_src, peaks = plot_spectra(series, w)
    data = {
        "session": d.name, "hold_window_s": hold, "flight_span_s": span, "sat_limits": sat,
        "timeline": fd.timeline(series),
        "tracking": tracking_stats(series, pairs, w),
        "spectral_peaks": peaks,
        "saturation": saturation(series, sat, span),
        "intervals": interval_stats(frames),
        "build": schema.get("build"), "contracts": schema.get("contracts"),
    }
    win_txt = (f"HOVER hold {hold[0]:.1f}-{hold[1]:.1f} s" if hold else
               f"no HOVER hold; flight span {span[0]:.1f}-{span[1]:.1f} s" if span else "no flight span")
    git = (schema.get("build") or {}).get("git") or {}
    meta = [{"field": "session", "value": d.name},
            {"field": "label / reason", "value": f"{manifest.get('label') or ''} {manifest.get('reason') or ''}".strip()},
            {"field": "window", "value": win_txt},
            {"field": "git", "value": f"{git.get('commit', '')[:12]}{' (dirty)' if git.get('dirty') else ''}" or None},
            {"field": "contracts", "value": json.dumps(schema.get("contracts")) if schema.get("contracts") else None},
            {"field": "variables / tunables", "value": f"{len(schema.get('variables') or [])} / "
                                                       f"{len(schema.get('tunables') or [])}" if schema else None}]
    sat_rows = [{"motor": k, **v} for k, v in data["saturation"].items()]
    iv_rows = [{"slot": k, **v} for k, v in data["intervals"].items()]
    blob = json.dumps(data).replace("</", "<\\/")
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Flight review {html.escape(d.name)}</title>
<style>{CSS}</style></head><body>
<h1>Flight review: {html.escape(d.name)}</h1>
{_table(meta, ["field", "value"])}
<h2>Timeline</h2>{_table(data["timeline"], ["t_s", "what", "to"])}
<h2>Setpoint vs actual</h2>
<p class=dim>Error = actual - setpoint over the {"HOVER hold" if hold else "flight span"}.</p>
{_table(data["tracking"], ["label", "unit", "n", "err_rms", "err_max"])}
{_img(plot_tracking(series, pairs, hold), "setpoint vs actual")}
<h2>Spectra</h2>{_table(peaks, ["symbol", "f_hz", "amp", "fs_hz"])}{_img(spec_src, "spectra")}
<h2>Motor saturation</h2>
<p class=dim>Over the flight span; limits {html.escape(str(sat)) if sat else "not given (--no-sat)"}.</p>
{_table(sat_rows, ["motor", "n", "mean", "min", "max", "frac_hi", "frac_lo"])}
{_img(plot_motors(series, sat, span), "motor commands")}
<h2>Sample interval</h2>
<p class=dim>A gap is an interval above {GAP_FACTOR:g} x the slot median.</p>
{_table(iv_rows, ["slot", "frames", "rate_hz", "median_ms", "p99_ms", "max_ms", "gaps"])}
{_img(plot_intervals(frames), "sample interval histogram")}
<script type="application/json" id="review-data">{blob}</script>
</body></html>
"""
    path = Path(out) if out else (d.parent / (d.name[: -len(".slot0.csv")] + ".flight_review.html") if d.is_file()
                                  else d / "flight_review.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def page_data(path: str | Path) -> dict[str, Any]:
    """The numbers a review page carries (its ``review-data`` JSON)."""
    text = Path(path).read_text(encoding="utf-8")
    start = text.index('<script type="application/json" id="review-data">') + len(
        '<script type="application/json" id="review-data">')
    return json.loads(text[start:text.index("</script>", start)].replace("<\\/", "</"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("session", help="a recorder session dir (telemetry.csv) or a stream_log <stem>.slot0.csv")
    p.add_argument("--out", help="output HTML (default <session>/flight_review.html)")
    p.add_argument("--no-sat", action="store_true", help="skip the bench saturation limits")
    a = p.parse_args(argv)
    sat = None
    if not a.no_sat:
        try:
            from ground_station.service.campaign_live import _sat_hi_lo
            sat = _sat_hi_lo()
        except Exception:
            sat = None
    print(f"flight review: {review(a.session, a.out, sat)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
