"""Rename VOFA+ channels (I0..In) and lay out its tabs by patching its config while it is closed.

VOFA+ rewrites vofa+.config.json and vofa+.tabviews.json on exit, so a patch made while it runs is lost.
Sequence: graceful close -> wait -> force after a timeout -> backup -> patch -> relaunch.

``python -m ground_station.vofa_studio.vofa_config --frames F`` applies FLIGHT_VIEW (the live flight
monitor that camera_truth.sh fusion opens); stream_log --vofa-view sends the same channels in the same order.
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# Joined part by part: the one-string Windows form is a single relative name off Windows, where parents[2] (DATAS)
# raised IndexError at import and stopped fly before its first flight (the Linux soak, docs/lab/soak.md).
CONFIG = (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
          / "vofa+" / "100" / "context" / "vofa+.config.json")
TABVIEWS = CONFIG.with_name("vofa+.tabviews.json")
# Last session's samples; VOFA+ reloads them on start and takes channel names from this header, not settings_ctx.
DATAS = CONFIG.parents[2] / "vofa+.datas.csv"
SHORTCUT =r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\VOFA+\x64\vofa+.lnk"
EXE = "vofa+.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Live flight view: one VOFA+ tab per row, one full-screen chart per tab, <= 6 short-named channels.
#   tab             (short name, variable) ...
FLIGHT_VIEW = (
    ("pos xy",      (("x", "Ctrler.locxPID.FB"), ("x_des", "Ctrler.locxPID.Des"),
                     ("y", "Ctrler.locyPID.FB"), ("y_des", "Ctrler.locyPID.Des"))),
    ("vel xy",      (("vx", "mrac_state.x_vel.x"), ("vx_ref", "mrac_state.x_vel.r"),
                     ("vy", "mrac_state.y_vel.x"), ("vy_ref", "mrac_state.y_vel.r"))),
    ("camera",      (("cam_ex", "g_ext_pos.e_x"), ("cam_ey", "g_ext_pos.e_y"),
                     ("corr_x", "g_ext_pos.c_x"), ("corr_y", "g_ext_pos.c_y"))),
    ("ekf innov",   (("innov_x", "s_ekf_of.innov_x"), ("innov_y", "s_ekf_of.innov_y"))),
    ("attitude",    (("roll", "imu_data.rol"), ("pitch", "imu_data.pit"), ("yaw", "imu_data.yaw"))),
    ("pitch mrac",  (("pit_x", "mrac_state.pitch.x"), ("pit_xm", "mrac_state.pitch.xm"),
                     ("pit_r", "mrac_state.pitch.r"), ("pit_uad", "mrac_state.pitch.u_ad"))),
    ("roll mrac",   (("rol_x", "mrac_state.roll.x"), ("rol_xm", "mrac_state.roll.xm"),
                     ("rol_r", "mrac_state.roll.r"), ("rol_uad", "mrac_state.roll.u_ad"))),
    ("yaw mrac",    (("yaw_x", "mrac_state.yaw.x"), ("yaw_xm", "mrac_state.yaw.xm"),
                     ("yaw_r", "mrac_state.yaw.r"), ("yaw_uad", "mrac_state.yaw.u_ad"))),
    ("height",      (("alt_cm", "ano_of.of_alt_cm"), ("z_fb", "Ctrler.Z_posPID.FB"),
                     ("vz", "mrac_state.z_rate.x"), ("vz_ref", "mrac_state.z_rate.r"))),
    ("rc",          (("rc_thr", "Remoter.ThrCtrler"), ("rc_pit", "Remoter.PitCtrler"),
                     ("rc_rol", "Remoter.RolCtrler"), ("rc_yaw", "Remoter.YawCtrler"))),
)
# Side-panel numbers only (no chart): sent after the charted channels.
MONITOR_ROW = (("cam_on", "g_ext_pos_on"), ("cam_age_ms", "g_ext_pos.age_ms"), ("cam_stale", "g_ext_pos.stale"),
               ("cam_ok", "g_ext_pos.n_ok"), ("cam_rej", "g_ext_pos.n_rej"), ("armed", "DroneStatus.ARM_Status"),
               ("fly_mode", "DroneStatus.FlyMode"), ("volt", "real_voltage"), ("of_q", "ano_of.of_quality"),
               ("ctrl_sel", "g_ctrl_select"))
# Distinct within a chart, readable on dark and light backgrounds; channel k of a tab gets VIEW_COLORS[k].
VIEW_COLORS = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4")
MONITOR_COLOR = "#a0a0a0"


def view_channels(view=FLIGHT_VIEW, monitor=MONITOR_ROW, available=None):
    """Channel list for a view: {"vars", "names", "colors", "tabs": [{"name", "lines"}], "missing"}.

    Variables not in ``available`` (when given) are dropped and listed in "missing"; empty tabs are dropped.
    """
    keep = (lambda v: True) if available is None else set(available).__contains__
    out = {"vars": [], "names": [], "colors": [], "tabs": [], "missing": []}
    for tab, channels in view:
        lines = []
        for name, var in channels:
            if not keep(var):
                out["missing"].append(var)
                continue
            out["colors"].append(VIEW_COLORS[len(lines) % len(VIEW_COLORS)])
            lines.append(len(out["vars"]))
            out["vars"].append(var)
            out["names"].append(name)
        if lines:
            out["tabs"].append({"name": tab, "lines": lines})
    for name, var in monitor:
        if not keep(var):
            out["missing"].append(var)
            continue
        out["vars"].append(var)
        out["names"].append(name)
        out["colors"].append(MONITOR_COLOR)
    return out


def _find_settings(node):
    """Return the settings_ctx list (per-channel entries) anywhere in the tree."""
    if isinstance(node, dict):
        val = node.get("settings_ctx")
        if isinstance(val, list):
            return val
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_settings(child)
        if found is not None:
            return found
    return None


def read_names(path=CONFIG):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ctx = _find_settings(data)
    return [e.get("name", "") for e in ctx] if ctx is not None else []


def read_tabs(path=TABVIEWS):
    """Tabs and the 0-based channel indices their WaveCharts plot (rbw.lines).

    VOFA+ rewrites this file when it exits, so live edits show after a close.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tabs = []
    for group in data.get("ctx", []):
        for tab in group.get("tabs", []):
            lines = set()
            for w in tab.get("widgets", []):
                rbw = w.get("ctx", {}).get("rbw", {}).get("ctx", {}).get(".", {})
                lines.update(i for i in rbw.get("lines", []) if isinstance(i, int))
            tabs.append({"name": tab.get("name", ""), "lines": sorted(lines)})
    return tabs


def park_datas(path=DATAS):
    """Move VOFA+'s saved samples aside (timestamped .bak) so its next start uses the patched names."""
    if not path.exists():
        return None
    bak = path.with_name(path.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
    os.replace(path, bak)
    return bak


def _write(path, data, backup):
    bak = None
    if backup:
        bak = path.with_name(path.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
        shutil.copy2(path, bak)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return bak


def patch_names(names, path=CONFIG, backup=True, colors=None):
    """Name the first len(names) channels (and color them, when given); reset the rest to I<i>.

    Returns the backup path."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    ctx = _find_settings(data)
    if ctx is None:
        raise ValueError("settings_ctx not found in %s" % path)
    template = ctx[-1] if ctx else {"is_draw": True, "color": "#ffffff", "scale": 1,
                                    "yoffset": 0, "xoffset": 0, "decimal": -7,
                                    "value": 0, "name": ""}
    while len(ctx) < len(names):
        ctx.append(copy.deepcopy(template))
    for i, entry in enumerate(ctx):
        if i < len(names):
            entry["name"] = str(names[i])
            entry["is_draw"] = True
            if colors is not None and i < len(colors):
                entry["color"] = colors[i]
        else:
            entry["name"] = "I%d" % i
    return _write(path, data, backup)


def patch_tabs(tabs, path=TABVIEWS, backup=True):
    """Replace the tabs with one full-screen WaveChart each, plotting tabs[k]["lines"] (0-based channels).

    The first existing WaveChart (and its tab) is the template, so VOFA+'s own geometry and theme carry over.
    Returns the backup path."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data.get("ctx", [])
    found = next(((t, w) for g in groups for t in g.get("tabs", []) for w in t.get("widgets", [])
                  if w.get("path") == "WaveChart"), None)
    if found is None:
        raise ValueError("no WaveChart in %s to use as a template; add one chart in VOFA+ first" % path)
    tab_tpl, chart_tpl = found
    new_tabs = []
    for t in tabs:
        chart = copy.deepcopy(chart_tpl)
        ctx = chart.setdefault("ctx", {})
        ctx.setdefault("rbw", {}).setdefault("ctx", {}).setdefault(".", {})["lines"] = list(t["lines"])
        border = ctx.setdefault(".", {}).setdefault("ctx", {}).setdefault("resize_border", {}).setdefault("ctx", {})
        border.setdefault(".", {})["full_screen"] = True
        tab = copy.deepcopy(tab_tpl)
        tab["name"] = t["name"]
        tab["widgets"] = [chart]
        new_tabs.append(tab)
    group = groups[0]
    del groups[1:]
    group["tabs"] = new_tabs
    group["currentIndex"] = group["lastIndex"] = 0
    return _write(path, data, backup)


def is_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % EXE, "/NH"],
                         capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
    return EXE.lower() in out.lower()


def _close(timeout=8.0):
    """Ask VOFA+ to close (it saves its config), force-kill if it hangs."""
    if not is_running():
        return "not running"
    subprocess.run(["taskkill", "/IM", EXE], capture_output=True,
                   creationflags=_NO_WINDOW)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not is_running():
            return "closed"
        time.sleep(0.3)
    subprocess.run(["taskkill", "/F", "/IM", EXE], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(1.0)
    return "forced"


def apply_names(names, relaunch=True):
    """Close VOFA+, patch names, relaunch. Returns a summary dict."""
    closed = _close()
    if is_running():
        raise RuntimeError("VOFA+ is still running; close it and retry")
    bak = patch_names(names)
    park_datas()
    if relaunch:
        os.startfile(SHORTCUT)
    return {"closed": closed, "backup": str(bak), "names": list(names),
            "relaunched": relaunch}


def apply_view(view=FLIGHT_VIEW, monitor=MONITOR_ROW, available=None, relaunch=True,
               config=CONFIG, tabviews=TABVIEWS, datas=DATAS):
    """Close VOFA+, name + color the view's channels, lay out its tabs, relaunch. Returns view_channels(...)."""
    ch = view_channels(view, monitor, available)
    ch["closed"] = _close()
    if is_running():
        raise RuntimeError("VOFA+ is still running; close it and retry")
    patch_names(ch["names"], path=config, colors=ch["colors"])
    patch_tabs(ch["tabs"], path=tabviews)
    park_datas(datas)
    if relaunch:
        os.startfile(SHORTCUT)
    ch["relaunched"] = relaunch
    return ch


TAB_TEMPLATE = Path(__file__).with_name("vofa_tab_template.tabviews.json")   # VOFA+'s own one-tab export
# Off Windows "C:\tmp\vofa" is one relative name, which made a stray dir in the working directory.
EXPORT_DIR = Path(r"C:\tmp\vofa") if os.name == "nt" else Path(tempfile.gettempdir()) / "vofa"


def export_view(out_dir=EXPORT_DIR, view=FLIGHT_VIEW, monitor=MONITOR_ROW, available=None, template=TAB_TEMPLATE):
    """Write the view as VOFA+'s native imports, VOFA+ untouched:
    File > Load widget windows and tabs <- flight_view.tabviews.json (one WaveChart tab per view tab),
    File > Load datas <- flight_names.csv (header row = channel names, renames every channel)."""
    ch = view_channels(view, monitor, available)
    t = json.loads(Path(template).read_text(encoding="utf-8"))
    tpl, tabs = t["ctx"][0]["tabs"][0], []
    for tab in ch["tabs"]:
        n = copy.deepcopy(tpl)
        n["name"] = tab["name"]
        n["widgets"][0]["ctx"]["rbw"]["ctx"]["."]["lines"] = tab["lines"]
        tabs.append(n)
    t["ctx"][0]["tabs"] = tabs
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ch["tabviews"], ch["names_csv"] = out / "flight_view.tabviews.json", out / "flight_names.csv"
    ch["tabviews"].write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    ch["names_csv"].write_text(",".join(ch["names"]) + "\n" + ",".join(["0"] * len(ch["names"])) + "\n",
                               encoding="utf-8")
    return ch


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lay out VOFA+ for the live flight view (FLIGHT_VIEW).")
    ap.add_argument("--frames", default=None, help="frames .md; channels it does not stream are dropped")
    ap.add_argument("--out", default=str(EXPORT_DIR), help="where the two import files go (default mode)")
    ap.add_argument("--patch", action="store_true", help="old mode: close VOFA+ and patch its context files")
    ap.add_argument("--no-relaunch", action="store_true", help="with --patch: leave VOFA+ closed")
    args = ap.parse_args(argv)
    available = None
    if args.frames:
        from ground_station.livewatch.stream_log import load_frames
        available = [v for _, symbols in load_frames(Path(args.frames)) for v in symbols]
    if not args.patch:
        ch = export_view(args.out, available=available)
        print("vofa+: %d channels, %d tabs. VOFA+ File > Load widget windows and tabs: %s ; File > Load datas: %s"
              % (len(ch["vars"]), len(ch["tabs"]), ch["tabviews"], ch["names_csv"]))
        if ch["missing"]:
            print("vofa+: not streamed, dropped: %s" % ", ".join(ch["missing"]))
        return 0
    ch = apply_view(available=available, relaunch=not args.no_relaunch)
    print("vofa+: %d channels, %d tabs (%s), VOFA+ %s%s"
          % (len(ch["vars"]), len(ch["tabs"]), ", ".join(t["name"] for t in ch["tabs"]), ch["closed"],
             ", relaunched" if ch["relaunched"] else ""))
    if ch["missing"]:
        print("vofa+: not streamed, dropped: %s" % ", ".join(ch["missing"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
