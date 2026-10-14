"""`fly`: fly a campaign from a terminal, flight after flight, no agent and no internet. Arm by RC = go.

    fly preset_tour                       # ground_station/service/campaigns/preset_tour.fly (or .yaml)
    fly my_script.fly --pack P4000-1      # a .fly file anywhere; the pack is remembered for the next call
    fly <launch copy> --run logs/workflow-c/<run>       # a launch copy flies as is; --run adds flight_debrief
    fly <campaign> --record-only carry --rate 100       # disarmed handheld log, no drone command

At start the VOFA+ FLIGHT_VIEW variables are forwarded (FireWater 127.0.0.1:1347) and the two VOFA+ import files
are written; their paths are printed. The `p` key streams every slot to PlotJuggler (UDP Server JSON, 9870) and
opens it on a layout of the streamed variables; that stream outlives the recording.

Each round: compile the file (a .fly via fly_file, a yaml via campaign_launch), run the preflight every 2 s while
disarmed and print only what fails, then wait for the RC arm. Armed with a passing preflight: a 3 s countdown (a
disarm or Ctrl+C cancels it), then go. In flight one live line is rewritten in place at 5 Hz and phase changes
scroll above it; keys s stop the step, n next step, l land, p PlotJuggler; Ctrl+C lands, twice aborts. After the
landing: a 5-line debrief and logs/flights/<date>/<NN>_<campaign>/, then disarm and the file is read again for the
next flight. The link going quiet for 3 s and coming back (a pack swap reboots the drone) asks for the new pack.

The service runs in its own console window, so Ctrl+C or a crash here never stops it mid-flight. The RC kill switch
stays the primary stop. `fly.cmd` at the repo root runs this module.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ground_station.lab import events

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ("pack charged and plugged", "drone on the pad", "phone filming LANDSCAPE, calibration focus",
             "ch6 HIGH, flymode SDK", "thumb on ch10 kill", "area clear")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a system proxy must not see 127.0.0.1
FLIGHTS_DIR = ROOT / "logs" / "flights"                    # one folder per flight (operator Q13, 2026-10-13)
PACK_FILE = ROOT / "logs" / "campaigns" / "fly" / "last_pack.txt"
COUNTDOWN_S = 3.0     # operator Q5: RC arm -> 3 s -> takeoff; a disarm inside it cancels
PREFLIGHT_S = 2.0     # operator Q5: silent preflight period while disarmed
LIVE_S = 0.2          # operator Q12: the live line at 5 Hz
LINK_LOST_S = 3.0     # PROPOSED: every stream older than this = the drone is off (pack swap)
CONFIRMATION = "RC arm held through the 3 s fly countdown"
PJ_BINS = ("D:/PlotJuggler/plotjuggler.exe", "plotjuggler")   # camera_truth.sh pj() candidates; $PJ_BIN first
_clock, _sleep = time.monotonic, time.sleep  # tests replace both


def call(base, route, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + route, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def ensure_service(base, port, wait_s=60):
    try:
        call(base, "/api/campaign/state", timeout=2)
        print(f"service already up on {port}")
        return
    except OSError:
        pass
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, "-m", "ground_station.service", "--port", str(port)], cwd=ROOT,
                     creationflags=flags)
    print(f"service starting in its own window on {port} ...")
    events.log("service_start", port=port)
    for _ in range(wait_s):
        time.sleep(1)
        try:
            call(base, "/api/campaign/state", timeout=2)
            return
        except OSError:
            pass
    events.log("service_start_failed", port=port, wait_s=wait_s)
    sys.exit(f"service did not answer on {port} in {wait_s} s: check its window")


PRIM_NAMES = ("IDLE", "CLIMB", "HOVER", "TRAJ", "RETURN", "SETTLE", "DESCEND")   # API/wfb_prim.h order
TRAJ_NAMES = ("EMPTY", "LOADING", "READY", "EXECUTING", "DONE")                   # API/wfb_traj.h order
TEL_NAMES = ("g_wfb_status.prim_state", "g_wfb_status.traj_state", "g_wfb_status.airborne_t",
             "Ctrler.locxPID.FB", "Ctrler.locxPID.Des", "Ctrler.locyPID.FB", "Ctrler.locyPID.Des",
             "Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des")


AXES = (("x", "locxPID", 0.01), ("y", "locyPID", 0.01), ("z", "Z_posPID", 1.0))  # loc PIDs in cm, Z_posPID in m


def read_tel(base):
    """The fresh (< 2 s) TEL_NAMES values from /api/values, {} when none."""
    from urllib.parse import urlencode
    return {k: d["value"] for k, d in call(base, "/api/values?" + urlencode({"names": ",".join(TEL_NAMES)}))[1]
            .get("values", {}).items() if d.get("age_s", 99) < 2}


def _state_name(names, v, k):
    return names[int(v[k])] if k in v and 0 <= int(v[k]) < len(names) else "?"


def format_tel(v):
    """One terminal line of flight state: prim, traj, airborne s, x/y/z FB->Des in m, or '' if no value."""
    if not v:
        return ""
    out = [f"{_state_name(PRIM_NAMES, v, 'g_wfb_status.prim_state'):<7}",
           f"traj {_state_name(TRAJ_NAMES, v, 'g_wfb_status.traj_state'):<9}"]
    if "g_wfb_status.airborne_t" in v:
        out.append(f"air {v['g_wfb_status.airborne_t']:5.1f}/120s")
    for ax, pid, scale in AXES:
        fb, des = v.get(f"Ctrler.{pid}.FB"), v.get(f"Ctrler.{pid}.Des")
        if fb is not None:
            out.append(f"{ax} {fb * scale:+.2f}" + (f"->{des * scale:+.2f}" if des is not None else ""))
    if out[0].strip() == "IDLE" and "LOADING" in out[1]:   # xyz-f2: ~150 frames at 0.37 s each, measured 2026-10-13
        out.append("<- uploading the program on the ground, about 60 s: wait, motors start after it")
    return "  ".join(out)


def tel_line(base):
    return format_tel(read_tel(base))


def _key():
    """One pressed key, lower case, or '' (Windows console; elsewhere keys are off)."""
    try:
        import msvcrt
    except ImportError:
        return ""
    return msvcrt.getwch().lower() if msvcrt.kbhit() else ""


class Term:
    """One live line rewritten in place, event lines scrolling above it."""

    def __init__(self, stream=None):
        self.stream, self.live = stream or sys.stdout, ""

    def _width(self):
        return max(40, shutil.get_terminal_size((120, 20)).columns - 1)

    def event(self, text):
        w = self._width()
        self.stream.write("\r" + " " * w + "\r" + f"{time.strftime('%H:%M:%S')}  {text}\n")
        if self.live:
            self.stream.write(self.live[:w])
        self.stream.flush()

    def line(self, text):
        self.live = text
        self.stream.write("\r" + text[:self._width()].ljust(self._width()))
        self.stream.flush()

    def close(self):
        if self.live:
            self.stream.write("\n")
            self.live = ""


KEY_ROUTES = {"s": "/api/campaign/stop", "n": "/api/campaign/next", "l": "/api/campaign/land"}


def watch(base, term=None, key=None, poll_s=1.0, live_s=LIVE_S, on_pj=None):
    """Flight view until the runner stops running: (last campaign state, live samples). Phase changes are events,
    the live line updates every live_s and each fresh read is kept as a sample {t, <TEL_NAMES>} for the debrief.
    Keys s/n/l post stop/next/land, p calls on_pj; Ctrl+C lands, a second Ctrl+C aborts. While the service is down
    Ctrl+C returns ({"status": "service_down"}, samples) instead."""
    term, key = term or Term(), key or _key
    last, stops, st, samples, t_state, t_down = None, 0, {}, [], -math.inf, None
    t0 = _clock()
    while True:
        try:
            k = key()
            if k in KEY_ROUTES:
                code, res = call(base, KEY_ROUTES[k], {})
                term.event(f"key {k} -> {KEY_ROUTES[k]}: {code} {res.get('error', '')}".rstrip())
                events.log("key", key=k, route=KEY_ROUTES[k], code=code, error=res.get("error"))
            elif k == "p":
                term.event(on_pj() if on_pj else "PlotJuggler: not wired here")
                events.log("key", key="p")
            now = _clock()
            if now - t_state >= poll_s:
                t_state = now
                st = call(base, "/api/campaign/state")[1]
                if t_down is not None:
                    term.event(f"service back after {_clock() - t_down:.0f} s")
                    events.log("service_back", down_s=round(_clock() - t_down, 1))
                    t_down = None
                line = (st.get("status"), st.get("phase"), st.get("banner"), st.get("arm_refusal"))
                if line != last:
                    refusal = f"  ARM REFUSED: {line[3]}" if line[3] else ""
                    term.event(f"{line[0]}  {line[1] or ''}  {line[2] or ''}{refusal}")
                    events.log("phase", status=line[0], phase=line[1], banner=line[2], refusal=line[3])
                    last = line
                if st.get("status") != "running":
                    term.close()
                    return st, samples
            v = read_tel(base)
            if v:
                samples.append({"t": round(now - t0, 2), **v})
                term.line(format_tel(v))
            _sleep(live_s)
        except KeyboardInterrupt:
            if t_down is not None:           # nothing can take a land/abort post: leave, so fly can run again
                events.log("key", key="ctrl+c", route=None, service_down_s=round(_clock() - t_down, 1))
                term.event("Ctrl+C: service down, leaving fly. Run fly again (it restarts the service).")
                term.close()
                return {"status": "service_down"}, samples
            route = "/api/campaign/land" if stops == 0 else "/api/campaign/abort"
            stops += 1
            events.log("key", key="ctrl+c", route=route)
            try:
                term.event(f"Ctrl+C -> {route}: {call(base, route, {})}")
            except OSError as e:
                term.event(f"Ctrl+C -> {route} FAILED ({e}): use the RC kill switch")
        except OSError as e:
            # 2026-10-14: 8081 died mid-session and this printed one bare line per second, with no next step.
            now = _clock()
            if t_down is None:
                t_down = now
                events.log("service_down", error=str(e))
                term.event(f"SERVICE DOWN ({e}): fly has no control path and the flight log has stopped. "
                           f"Fly with the RC: take over and land. After landing, Ctrl+C leaves; then run fly again (it restarts the service).")
            elif int(now - t_down) // 10 != int(now - t_down - poll_s) // 10:
                term.event(f"service still down {now - t_down:.0f} s: RC has the drone")
            _sleep(poll_s)


def slot_counts(base):
    """{slot: (received, dropped)} from the bridge's stream stats ({} when the service has none)."""
    rows = (call(base, "/api/diagnostics/bundle")[1].get("transport") or {}).get("active_slots") or []
    return {int(r["slot"]): (int(r.get("received") or 0), int(r.get("dropped") or 0)) for r in rows}


def link_lines(before, after, slots):
    """One line per slot: frames received and dropped between two slot_counts snapshots."""
    out = []
    for k in slots:
        rx, dr = (after.get(k, (0, 0))[i] - before.get(k, (0, 0))[i] for i in (0, 1))
        pct = 100.0 * dr / (rx + dr) if rx + dr else 0.0
        out.append(f"  link slot {k}: {rx} frames, {dr} dropped ({pct:.1f} %)")
    return out


def record_only(base, campaign, rate_hz=None, label="handheld", settle_s=3.0, wait=input):
    """Disarmed handheld carry: the launch copy's log plan on the stream slots, record until Enter or Ctrl+C.

    Sends no drone command (no go, no arm, no takeoff): only /subscribe and /api/recording. Returns the session dir.
    It ends with the frames received and dropped per slot: a 10 s run of a plan is the bring-up link check.
    """
    from ground_station.livewatch.campaign_capture import MAX_SLOTS, log_plan_table, plan_capture, subscribe_steps
    from ground_station.service.campaign_schema import load_campaign

    log_plan = dict(load_campaign(Path(campaign)).experiments[0].log_plan or {})
    if rate_hz:
        log_plan["rate_hz"] = rate_hz
    plan = plan_capture(log_plan or None)
    print(log_plan_table(plan))
    steps = subscribe_steps(plan)
    for args in [s["args"] for s in steps] + [{"slot": k, "divider": 0, "ranges": []}
                                               for k in range(len(steps), MAX_SLOTS)]:
        code, res = call(base, "/subscribe", args)
        if code != 202:
            sys.exit(f"subscribe slot {args['slot']} refused ({code}): {res.get('error', res)}")
    time.sleep(settle_s)
    streams = call(base, "/api/campaign/vitals")[1].get("streams", {})
    for s in steps:
        age = (streams.get(str(s["args"]["slot"])) or {}).get("age_s")
        print(f"  slot {s['args']['slot']}: {'NOT STREAMING' if age is None or age > 1.0 else f'fresh ({age} s)'}")
    before = slot_counts(base)
    code, st = call(base, "/api/recording/start", {
        "requested_by": "operator", "label": label, "reason": "workflow C handheld carry (disarmed)",
        "notes": f"log_plan rate {plan['rate_hz']:g} Hz, groups {', '.join(plan['groups']) or 'core only'}"})
    if not st.get("recording"):
        sys.exit(f"recording did not start ({code}): {st.get('error', st)}")
    print(f"RECORDING -> {st.get('session_dir')}")
    try:
        wait("Carry the drone now (DISARMED). Enter or Ctrl+C stops the log: ")
    except (KeyboardInterrupt, EOFError):
        print()
    after = slot_counts(base)
    st = call(base, "/api/recording/stop", {})[1]
    print(f"stopped: {st.get('session_dir')}")
    for line in link_lines(before, after, [s["args"]["slot"] for s in steps]):
        print(line)
    return str(st.get("session_dir") or "")


def resolve_campaign(arg, pack, rate=None, launch_dir=None, fly_dir=None):
    """(launch copy, source file) for a .fly file, a campaign yaml or a saved name (campaigns/<name>.fly, then .yaml),
    compiled and written fresh for `pack`; a file already in the launch folder flies as is (with its own pack).
    Raises fly_file.FlyFileError or campaign_launch.LaunchError (both ValueError) with the problems."""
    from ground_station.service import fly_file
    from ground_station.service.campaign_launch import (LAUNCH_DIR, SAVED_DIR, LaunchError, resolve_campaign_path,
                                                        write_launch_copy)
    p = resolve_campaign_path(arg)
    if not p.is_file():
        p = next((c for c in (SAVED_DIR / f"{arg}.fly", SAVED_DIR / f"{arg}.yaml") if c.is_file()), None)
        if p is None:
            names = sorted({c.stem for c in SAVED_DIR.glob("*.fly")} | {c.stem for c in SAVED_DIR.glob("*.yaml")})
            raise LaunchError(f"no campaign {arg!r}: give a .fly/.yaml path or one of {', '.join(names)}")
    if p.suffix == ".fly":
        return fly_file.launch_fly(p, pack, rate, fly_dir, launch_dir)[0], p
    if p.resolve().parent == Path(launch_dir or LAUNCH_DIR).resolve():
        return p, p
    return write_launch_copy(p, pack, rate, launch_dir)[0], p


def remember_pack(pack=None, path=None):
    """`pack` saved as the default for the next call, or the saved one when `pack` is empty ('' if none)."""
    path = Path(path or PACK_FILE)
    if pack and pack.strip():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pack.strip() + "\n", encoding="utf-8")
        return pack.strip()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pack_problem(pack):
    """Why `pack` cannot fly ('' when it can): the preflight pack gate takes only packs.yaml ids (2026-10-14:
    'P1' was saved, then refused at every preflight)."""
    from ground_station.service.campaign_launch import _pack_ids
    known = _pack_ids()
    ids = ", ".join(known) or "see ground_station/analysis/packs.yaml"
    if not pack:
        return f"no pack yet: give --pack <id> once, it is remembered ({ids})"
    if known and pack not in known:
        return f"unknown pack '{pack}': give --pack with one of {ids}"
    return ""


def quiet_preflight(base, campaign, pack, shown):
    """(ok, failing rows) of one preflight; prints only when the failing set differs from `shown`."""
    from urllib.parse import urlencode
    _, res = call(base, "/api/campaign/preflight?" + urlencode({"campaign": campaign, "pack": pack}), timeout=30)
    fails = tuple(f"{c.get('name')}: {c.get('value')}" + (f"  -> {c['fix']}" if c.get("fix") else "")
                  for c in res.get("checks", []) if c.get("pass") is False)
    if "error" in res:
        fails += (f"preflight error: {res['error']}",)
    if fails != shown:
        events.log("preflight", ok=not fails, fails=list(fails), campaign=campaign, pack=pack)
        print("preflight " + ("FAIL:\n  " + "\n  ".join(fails) if fails else "OK: arm by RC to fly"), flush=True)
    return bool(res.get("ok")) and not fails, fails


def _vitals(base):
    try:
        return call(base, "/api/campaign/vitals", timeout=5)[1]
    except OSError:
        return {}


def _link_age(v):
    ages = [s.get("age_s") for s in (v.get("streams") or {}).values() if s.get("age_s") is not None]
    return min(ages) if ages else math.inf


def wait_for_arm(base, campaign, pack, key=None, poll_s=0.25, preflight=True):
    """'go' after an RC arm held for COUNTDOWN_S with the preflight passing, 'quit' on q or on Ctrl+C outside the
    countdown, 'link' when the link comes back after LINK_LOST_S of silence (a pack swap). Only a disarmed -> armed
    edge counts: a drone still armed after a landing never takes off again. `preflight` False skips the preflight
    (the runner already holds the campaign and waits for its next flight)."""
    key = key or _key
    shown, pf_ok, t_pf = None, not preflight, -math.inf
    seen_disarmed, warned, armed_t, count = False, False, None, None
    t_link, lost = _clock(), False
    while True:
        try:
            if key() == "q":
                return "quit"
            v, now = _vitals(base), _clock()
            if _link_age(v) <= LINK_LOST_S:
                t_link = now
                if lost:
                    print("link back", flush=True)
                    events.log("link_back")
                    return "link"
            elif not lost and now - t_link > LINK_LOST_S:
                lost = True
                print("link lost: waiting for the drone (a new pack asks for its id)", flush=True)
                events.log("link_lost")
            state = (v.get("arm") or {}).get("state")
            if state != "armed":
                if armed_t is not None:
                    print("disarmed: countdown cancelled", flush=True)
                seen_disarmed, warned, armed_t = seen_disarmed or state == "disarmed", False, None
                if preflight and state == "disarmed" and now - t_pf >= PREFLIGHT_S:
                    t_pf = now
                    pf_ok, shown = quiet_preflight(base, campaign, pack, shown)
            elif armed_t is None:
                if not seen_disarmed or not pf_ok:
                    if not warned:
                        print("ARMED " + ("with the preflight failing" if seen_disarmed else "already") +
                              ": disarm by RC, then arm again", flush=True)
                        warned = True
                        events.log("arm_refused", seen_disarmed=seen_disarmed, preflight_ok=pf_ok)
                    seen_disarmed = False
                else:
                    armed_t, count = now, None
                    print(f"ARMED: takeoff in {COUNTDOWN_S:g} s (disarm or Ctrl+C cancels)", flush=True)
            else:
                left = COUNTDOWN_S - (now - armed_t)
                if left <= 0:
                    return "go"
                if math.ceil(left) != count:
                    count = math.ceil(left)
                    print(f"  {count}", flush=True)
            _sleep(poll_s)
        except KeyboardInterrupt:
            if armed_t is None:
                return "quit"
            print("countdown cancelled: disarm by RC, then arm again", flush=True)
            armed_t, seen_disarmed, warned = None, False, True


def _err_cm(samples, axis):
    _, pid, scale = next(a for a in AXES if a[0] == axis)
    fb, des = f"Ctrler.{pid}.FB", f"Ctrler.{pid}.Des"
    return [(s[fb] - s[des]) * scale * 100 for s in samples if fb in s and des in s]


def _pos_cm(sample):
    return tuple(sample.get(f"Ctrler.{pid}.FB", math.nan) * scale * 100 for _, pid, scale in AXES[:2])


def camera_landing(root=Path("logs/video_truth"), now=None, n=15, fresh_s=10.0):
    """Where the drone stands by the camera: the median pad_x/pad_y (cm) of the last n fixes of the running
    `camera_truth.sh fusion` session (a `fusion` file, no `stop`, fixes.csv written in the last fresh_s). The EKF
    landing offset cannot see drift (the EKF drifted with the drone); this can. The tracker's pad origin is where the
    drone stood before the session's first takeoff. If the ring is lost on the ground the last fixes are from the
    descent. Returns (x_cm, y_cm, session, fixes) or None without a running fusion session."""
    now = time.time() if now is None else now
    root = Path(root)
    live = [d for d in (root.iterdir() if root.is_dir() else []) if (d / "fusion").exists()
            and not (d / "stop").exists() and (d / "fixes.csv").exists()
            and now - (d / "fixes.csv").stat().st_mtime < fresh_s]
    if not live:
        return None
    d = max(live, key=lambda d: (d / "fixes.csv").stat().st_mtime)
    xy = []
    for row in (d / "fixes.csv").read_text(encoding="utf-8").splitlines()[1:][-n:]:
        try:
            xy.append((float(row.split(",")[2]) * 100, float(row.split(",")[3]) * 100))
        except (IndexError, ValueError):
            continue
    if not xy:
        return None
    med = [sorted(c)[len(c) // 2] for c in zip(*xy)]
    return med[0], med[1], d.name, len(xy)


def debrief_lines(samples, st, folder, cam=None):
    """Five lines: result, HOVER and TRAJ error (RMS/max per axis in cm, from the live samples), landing offset from
    the takeoff point, where the files are. `cam` (camera_landing()) adds the camera's landing offset from the pad."""
    def prim(s):
        return _state_name(PRIM_NAMES, s, "g_wfb_status.prim_state")
    air = max((s.get("g_wfb_status.airborne_t", 0.0) for s in samples), default=0.0)
    lines = [f"{st.get('status')} {st.get('reason') or ''}".strip()
             + f"  air {air:.1f} s  {len(st.get('flights') or [])} flight(s)  {len(samples)} live samples"]
    for phase in ("HOVER", "TRAJ"):
        part = [s for s in samples if prim(s) == phase]
        errs = {ax: _err_cm(part, ax) for ax in "xyz"}
        if not any(errs.values()):
            lines.append(f"{phase:<6} no samples")
            continue
        rms = "/".join(f"{math.sqrt(sum(e * e for e in v) / len(v)):.0f}" if v else "-" for v in errs.values())
        mx = "/".join(f"{max(abs(e) for e in v):.0f}" if v else "-" for v in errs.values())
        lines.append(f"{phase:<6} x/y/z error cm  rms {rms}  max {mx}  ({part[-1]['t'] - part[0]['t']:.0f} s)")
    pad = next((s for s in samples if prim(s) in ("IDLE", "CLIMB")), None)
    end = samples[-1] if samples else None
    if pad and end and not any(math.isnan(c) for c in _pos_cm(pad) + _pos_cm(end)):
        dx, dy = (e - p for e, p in zip(_pos_cm(end), _pos_cm(pad)))
        lines.append(f"landing offset x {dx:+.0f} y {dy:+.0f} cm from the takeoff point ({math.hypot(dx, dy):.0f} cm)")
    else:
        lines.append("landing offset: no position before takeoff and after landing")
    if cam:
        lines.append(f"camera landing x {cam[0]:+.0f} y {cam[1]:+.0f} cm from the pad ({math.hypot(cam[0], cam[1]):.0f} cm, "
                     f"{cam[2]}, median of {cam[3]} fixes)")
    lines.append(f"outputs {st.get('outputs_dir') or '-'}  flight folder {folder}")
    return lines


def flight_folder(campaign_name, root=None, now=None):
    """A new <root>/<date>/<NN>_<campaign>/ (NN counts the day's flights from 01)."""
    day = Path(root or FLIGHTS_DIR) / time.strftime("%Y-%m-%d", time.localtime(now))
    nums = [int(p.name[:2]) for p in day.glob("[0-9][0-9]_*") if p.is_dir()]
    out = day / f"{max(nums, default=0) + 1:02d}_{campaign_name}"
    out.mkdir(parents=True)
    return out


def write_samples(path, samples):
    """The live samples as CSV, one column per name seen."""
    names = list(dict.fromkeys(k for s in samples for k in s))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(names) + "\n")
        for s in samples:
            f.write(",".join("" if s.get(k) is None else f"{s[k]:g}" for k in names) + "\n")


def _ask_pack(pack):
    while True:
        try:
            said = input(f"new pack? [Enter = {pack}] ").strip()
        except EOFError:
            said = ""
        if not said:
            return pack
        why = pack_problem(said)
        if not why:
            return remember_pack(said)
        print(why, flush=True)


def vofa_setup(base, out_dir=None):
    """VOFA+ FLIGHT_VIEW (operator Q3): forward its streamed variables (FireWater, 8081 /api/streams/forward) and
    write VOFA+'s two native import files in the same channel order. Returns the lines to print. UNVERIFIED in the
    lab: VOFA+ loading both files against the live forward."""
    from ground_station.vofa_studio import vofa_config
    code, st = call(base, "/api/streams/forward", {"enable": True, "channels": vofa_config.view_channels()["vars"]})
    if code != 200:
        return [f"VOFA+: forward refused ({code}): {st.get('error', st)}"]
    ch = vofa_config.export_view(**({"out_dir": out_dir} if out_dir else {}), available=st["channels"])
    lines = [f"VOFA+ FireWater UDP {st['addr']}, {len(st['channels'])} channels"
             + (f" ({len(ch['missing'])} not streamed)" if ch["missing"] else ""),
             f"  File > Load widget windows and tabs <- {ch['tabviews']}",
             f"  File > Load datas <- {ch['names_csv']}"]
    if ch["vars"] != st["channels"]:
        lines.append("  channel order differs from the forward: the names will be wrong, report it")
    return lines


def _pj_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq plotjuggler.exe", "/NH"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "plotjuggler.exe" in out.lower()


def plotjuggler(base, out_dir=None, popen=subprocess.Popen):
    """`p` key (operator Q14): every streamed slot to PlotJuggler (8081 /api/streams/plot, UDP Server JSON), then
    PlotJuggler opened on a layout of the streamed variables unless one already runs. The stream is independent of
    the recording: it keeps plotting after a flight. Returns one status line. UNVERIFIED in the lab: the layout curves
    against the streamed series names, packed ranges above all."""
    code, st = call(base, "/api/streams/plot", {"enable": True})
    if code != 200:
        return f"PlotJuggler: forward refused ({code}): {st.get('error', st)}"
    if _pj_running():
        return f"PlotJuggler: already open; its UDP Server (JSON) must listen on {st['addr']}"
    exe = next((c for c in (os.environ.get("PJ_BIN"), *PJ_BINS) if c and (Path(c).is_file() or shutil.which(c))),
               None)
    if not exe:
        return f"PlotJuggler: streaming to {st['addr']}, but no plotjuggler.exe found (set PJ_BIN)"
    args = [exe]
    if st.get("vars"):
        from ground_station.livewatch.pj_layout import generate_layout_xml
        layout = Path(out_dir or FLIGHTS_DIR) / "plotjuggler_live.xml"
        layout.parent.mkdir(parents=True, exist_ok=True)
        layout.write_text(generate_layout_xml(st["vars"]), encoding="utf-8")
        args += ["-l", str(layout)]
    popen(args + ["--start_streamer", "UDP Server", "--buffer_size", "400", "--nosplash"],
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"PlotJuggler: opening on {len(st.get('vars') or [])} streamed variables, UDP Server {st['addr']}"


def fly_loop(base, arg, pack, rate=None, run=None, once=False):
    """Compile -> wait for the RC arm -> go -> watch -> debrief, flight after flight until q."""
    from ground_station.service.campaign_launch import rel
    from ground_station.service.campaign_schema import load_campaign

    print("Arming confirms the checklist:\n" + "\n".join(f"  [ ] {c}" for c in CHECKLIST))
    held = None   # the runner waits for the next flight of a multi-flight campaign: (launch, src, name, its pack)
    while True:
        if held:
            launch, src, name, go_pack = held
            print(f"{name}: next flight, pack {go_pack}. Arm by RC to fly it (q quits).", flush=True)
        else:
            try:
                launch, src = resolve_campaign(arg, pack, rate)
                c = load_campaign(launch)
            except (ValueError, RuntimeError) as e:
                print(f"{arg}: cannot fly it:\n  " + str(e).replace("\n", "\n  "))
                if input("Fix the file, then Enter to read it again (q quits): ").strip().lower() == "q":
                    return 1
                continue
            name, go_pack = c.campaign, c.packs[0]
            print(f"{name}  {rel(src)}  pack {go_pack}  -> {rel(launch)}\nWaiting for the RC arm (q quits).",
                  flush=True)
        t_wait = _clock()
        r = wait_for_arm(base, str(launch), go_pack, preflight=not held)
        events.log("arm_wait", result=r, campaign=name, pack=go_pack, s=round(_clock() - t_wait, 1))
        if r == "quit":
            return 0
        if r == "link":
            pack, held = _ask_pack(pack), None
            continue
        folder = flight_folder(name)
        for f in dict.fromkeys((Path(src), Path(launch))):
            shutil.copy2(f, folder / f.name)
        code, res = call(base, "/api/campaign/go", {
            "campaign_path": str(launch), "pack_id": go_pack, "checklist": {c: True for c in CHECKLIST},
            "source": "operator:cli", "confirmation": CONFIRMATION}, timeout=30)
        events.log("go", code=code, campaign=name, pack=go_pack, folder=rel(folder), error=res.get("error"))
        if code != 200:
            print(f"GO refused ({code}): {res.get('error', res)}. Disarm by RC.", flush=True)
            held = None
            continue
        print("GO. keys: s stop step, n next, l land, p PlotJuggler; Ctrl+C land, twice abort. RC kill stays first.")
        st, samples = watch(base, on_pj=lambda: plotjuggler(base))
        t_land = _clock()
        lines = debrief_lines(samples, st, rel(folder), cam=camera_landing())
        print("\n".join(lines))
        events.log("debrief", ms=round((_clock() - t_land) * 1000), folder=rel(folder), status=st.get("status"),
                   phase=st.get("phase"), samples=len(samples))
        (folder / "debrief.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (folder / "outputs.txt").write_text(f"{st.get('outputs_dir') or ''}\n", encoding="utf-8")
        write_samples(folder / "live_5hz.csv", samples)
        if st.get("status") == "service_down":
            return 1
        if run and st.get("outputs_dir"):
            subprocess.run([sys.executable, "-m", "ground_station.analysis.flight_debrief", st["outputs_dir"],
                            "--run", run], cwd=ROOT)
        waiting = st.get("status") == "waiting_for_go" and st.get("waiting_pack")
        held = (launch, src, name, st["waiting_pack"]) if waiting else None
        if once and not held:
            return 0
        print("Disarm by RC; " + ("the next flight of this campaign" if held else f"then {rel(src)} is read again")
              + " and the next arm flies it.", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign", help=".fly file, campaign yaml, launch copy, or a saved campaign name")
    ap.add_argument("--pack", help="pack id (default: the last one given, logs/campaigns/fly/last_pack.txt)")
    ap.add_argument("--run", help="workflow-c run folder: flight_debrief after each landing")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--once", action="store_true", help="one flight, then exit")
    ap.add_argument("--record-only", metavar="LABEL", help="no flight: record the log plan (drone disarmed, carried "
                                                            "by hand) until Enter; sends no drone command")
    ap.add_argument("--rate", type=float, help="override the log plan rate, Hz")
    a = ap.parse_args(argv)
    base = f"http://127.0.0.1:{a.port}"
    pack = (a.pack or "").strip() or remember_pack()
    why = pack_problem(pack)
    if why:
        sys.exit(why)
    remember_pack(pack)
    events.log("fly_start", campaign=a.campaign, pack=pack, record_only=a.record_only, once=a.once)
    ensure_service(base, a.port)
    print("\n".join(vofa_setup(base)), flush=True)
    if a.record_only:
        try:
            launch, _ = resolve_campaign(a.campaign, pack, a.rate)
        except (ValueError, RuntimeError) as e:
            sys.exit(str(e))
        record_only(base, str(launch), a.rate, a.record_only)
        rc = 0
    else:
        rc = fly_loop(base, a.campaign, pack, a.rate, a.run, a.once)
    events.log("fly_end", rc=rc)
    print("Stop the video. The service window stays open; close it when you are done.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
