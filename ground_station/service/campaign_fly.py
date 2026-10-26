"""`fly`: fly a campaign from a terminal, flight after flight, no agent and no internet. Arm by RC = go.

    fly preset_tour                       # ground_station/service/campaigns/preset_tour.fly (or .yaml)
    fly my_script.fly --pack 2            # a .fly file anywhere; the pack type is remembered for the next call
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
import re
import shutil
import signal
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
NO_ARM_S = 3.0        # link up but no arm flag this long = say so (10-18 mem1 hung silently on "unknown")
NO_ARM_MSG = ("NO ARM SIGNAL: telemetry arrives but the arm flag (DroneStatus.ARM_Status, slot 0) does not, "
              "so an RC arm cannot be seen. Fix: restart the 8081 service, then run this again (q quits).")
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


def _service_stale(base):
    """(pid, newest .py) when 8081 runs code older than the newest service-side .py, else None (10-18: a limit change
    sat unused until a hand restart). The X-GS-Instance header is "<pid>-<start ms>"."""
    with urllib.request.urlopen(base + "/api/campaign/state", timeout=2) as r:
        pid, _, ms = (r.headers.get("X-GS-Instance") or "").partition("-")
    if not (pid.isdigit() and ms.isdigit()):
        return None
    # the service never imports this CLI or ground_station/lab: an edit there must not restart it
    newest = max((q for q in (ROOT / "ground_station").rglob("*.py") if "tests" not in q.parts and "lab" not in q.parts
                  and q.name != "campaign_fly.py"), key=lambda q: q.stat().st_mtime)
    return (int(pid), newest) if newest.stat().st_mtime > int(ms) / 1000 else None


def _safe_to_restart(base):
    """Runner idle, and the drone disarmed or out of contact: a restart cannot cut a flight."""
    _, pf = call(base, "/api/campaign/preflight", timeout=5)
    rows = {c.get("name"): c for c in pf.get("checks") or []}
    runner = rows.get("runner") or {}
    idle = runner.get("pass") is True and not str(runner.get("value", "")).startswith("paused")
    return idle and ((rows.get("arm_state") or {}).get("pass") is True or (rows.get("link") or {}).get("pass") is False)


def _stream_logs():
    """PIDs of running livewatch.stream_log processes, by command line (a .pid file's PID can be reused)."""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any("livewatch.stream_log" in c for c in p.info["cmdline"] or []):
                out.append(p.info["pid"])
        except (psutil.Error, TypeError):
            pass
    return out


def reap_stream_logs(wait_s=10):
    """End a stream_log left from a camera session started without 8081: it holds udp 14550 (link) and 47800 (camera
    fixes), so the next 8081 dies on WinError 10013 or runs without its fix relay (lab 10-18, ~45 min lost). 8081 is
    the only link owner while `fly` runs; the stop file lets it close its CSVs first."""
    pids = _stream_logs()
    if not pids:
        return []
    end, pidf = ROOT / "logs" / "plot_live.end", ROOT / "logs" / "plot_live.pid"
    end.parent.mkdir(exist_ok=True)
    end.touch()
    for _ in range(wait_s):
        if not _stream_logs():
            break
        time.sleep(1)
    left = _stream_logs()
    for pid in left:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for f in (end, pidf):
        f.unlink(missing_ok=True)
    print(f"ended a leftover live logger (pid {', '.join(map(str, pids))}): 8081 owns the link and camera fixes",
          flush=True)
    events.log("stream_log_reaped", pids=pids, killed=left)
    return pids


def _service_pid(base):
    with urllib.request.urlopen(base + "/api/campaign/state", timeout=2) as r:
        pid = (r.headers.get("X-GS-Instance") or "").partition("-")[0]
    return int(pid) if pid.isdigit() else None


def _service_lab_mode(base):
    """The running service's lab mode (preflight lab_mode), None when it does not report one."""
    _, pf = call(base, "/api/campaign/preflight", timeout=5)
    v = pf.get("lab_mode")
    return v if isinstance(v, bool) else None


def ensure_service(base, port, wait_s=60, lab=False):
    """lab=True: a running service must be in lab mode too. Lab 10-24: labday reused an 8081 started without --lab; its
    uncapped plan and dashboard stream layer buried the command replies and takeoff failed at set_hover_z."""
    reap_stream_logs()
    try:
        stale = _service_stale(base)
        wrong_mode = lab and _service_lab_mode(base) is False
        if wrong_mode and port != 8081:
            # Soak 10-25: fly restarted the soak's plain service on its spare port; the restart lost the service's
            # own link ports and the soak died. Only the lab's 8081 is restarted in lab mode.
            sys.exit(f"service on {port} runs without --lab and is not the lab's 8081: it is not restarted here "
                     "(it may run on its own link ports; the soak passes --plain-service)")
        if wrong_mode and not _safe_to_restart(base):
            sys.exit(f"service on {port} runs without --lab and a flight may be on: land, then run this again")
        if (stale or wrong_mode) and _safe_to_restart(base):
            pid = stale[0] if stale else _service_pid(base)
            if pid is None:
                sys.exit(f"service on {port} runs without --lab and gives no pid: stop it, then run this again")
            why = f"runs code older than {stale[1].relative_to(ROOT)}" if stale else "runs without --lab"
            print(f"service on {port} {why}: restarting it (disarmed, idle)")
            events.log("service_restart_stale" if stale else "service_restart_not_lab", port=port, pid=pid,
                       newest=str(stale[1].relative_to(ROOT)) if stale else None)
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.5)
                try:
                    call(base, "/api/campaign/state", timeout=1)
                except OSError:
                    break
        elif stale:
            print(f"service on {port} runs old code ({stale[1].relative_to(ROOT)} is newer) but a flight may be on: "
                  "kept it", flush=True)
            return
        else:
            print(f"service already up on {port}")
            return
    except OSError:
        pass
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, "-m", "ground_station.service", "--port", str(port)] + (["--lab"] if lab else []), cwd=ROOT,
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
             "Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des",
             # who ended the flight (pilot disarm / stick takeover / firmware trip), so the debrief CSV shows it (10-21)
             "g_wfb_status.safety_trip", "status.arm", "status.rc_authority", "status.motor_idle")


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


def watch(base, term=None, key=None, poll_s=1.0, live_s=LIVE_S, on_pj=None, post_takeoff_gains=None):
    """Flight view until the runner stops running: (last campaign state, live samples). Phase changes are events,
    the live line updates every live_s and each fresh read is kept as a sample {t, <TEL_NAMES>} for the debrief.
    Keys s/n/l post stop/next/land, p calls on_pj; Ctrl+C lands, a second Ctrl+C aborts. While the service is down
    Ctrl+C returns ({"status": "service_down"}, samples) instead."""
    term, key = term or Term(), key or _key
    last, stops, st, samples, t_state, t_down = None, 0, {}, [], -math.inf, None
    t0 = _clock()
    applied_post_takeoff = False
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
                if post_takeoff_gains and not applied_post_takeoff:
                    prim = _state_name(PRIM_NAMES, v, "g_wfb_status.prim_state")
                    z_fb = v.get("Ctrler.Z_posPID.FB", 0.0)
                    if prim == "HOVER" or (prim not in ("IDLE", "CLIMB") and z_fb >= 0.40):
                        applied_post_takeoff = True
                        term.event(f"Transitioning to post-takeoff gains ({post_takeoff_gains})...")
                        if not write_gains(base, post_takeoff_gains, in_flight=True, yes=True):
                            # write_gains stops at the first failed index: the drone now flies a mix of old and new gains
                            term.event(f"POST-TAKEOFF GAINS FAILED ({post_takeoff_gains}): gains are mixed. "
                                       f"Land (l) and fly again.")
                            events.log("post_takeoff_gains_failed", preset=post_takeoff_gains)
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


def gates_off(launch):
    """fly --no-gates (operator 10-24): the launch copy's abort.level1 set false, so the safety monitor runs only the
    battery (soc_min_pct) and firmware-trip checks; the RC kill and stick takeover stay first. Returns `launch`."""
    import yaml
    launch = Path(launch)
    data = yaml.safe_load(launch.read_text(encoding="utf-8")) or {}
    abort = data.get("abort") if isinstance(data.get("abort"), dict) else {}
    abort["level1"] = False
    data["abort"] = abort
    launch.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return launch


def pack_id(said):
    """The packs.yaml id for what the operator typed: '2', 't2', 'type 2' and 'Type2' are all 'type2' (2026-10-15:
    the lab knows two pack types, not single packs). Anything else comes back as typed, for pack_problem."""
    s = "".join((said or "").lower().split()).removeprefix("type").removeprefix("t")
    return f"type{s}" if s.isdigit() else (said or "").strip()


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
    types = " or ".join(k.removeprefix("type") for k in known) or "1 or 2"
    if not pack:
        return f"no pack yet: give --pack {types} (the pack type in the drone) once, it is remembered"
    if known and pack not in known:
        return f"unknown pack type '{pack}': give --pack {types} (packs.yaml: {', '.join(known)})"
    return ""


def _vitals(base):
    try:
        return call(base, "/api/campaign/vitals", timeout=5)[1]
    except OSError:
        return {}


# ── PID GAIN PRESETS & STOP-AND-WAIT GAIN WRITER (Friction A1-A6 & B10) ────────
PID_DEFAULTS = {
    0: ("pitchPID.Kp", 3.0), 1: ("pitchPID.Ki", 0.02), 2: ("pitchPID.Kd", 8.0),
    3: ("rollPID.Kp", 3.0), 4: ("rollPID.Ki", 0.02), 5: ("rollPID.Kd", 8.0),
    6: ("yawPID.Kp", 6.0), 7: ("yawPID.Ki", 0.04), 8: ("yawPID.Kd", 0.20),
    9: ("gyroxPID.Kp", 5.0), 10: ("gyroxPID.Ki", 0.01), 11: ("gyroxPID.Kd", 10.0),
    12: ("gyroyPID.Kp", 5.0), 13: ("gyroyPID.Ki", 0.01), 14: ("gyroyPID.Kd", 10.0),
    15: ("gyrozPID.Kp", 8.0), 16: ("gyrozPID.Ki", 0.005), 17: ("gyrozPID.Kd", 0.02),
    18: ("Z_ratePID.Kp", 400.0), 19: ("Z_ratePID.Ki", 0.435), 20: ("Z_ratePID.Kd", 0.0),
    21: ("Z_posPID.Kp", 0.7), 22: ("Z_posPID.Ki", 0.005), 23: ("Z_posPID.Kd", 0.1),
    # 24-35: the c3 robust gains, the firmware default since 2026-10-22 (API/pid.c change history)
    24: ("locxPID.Kp", 0.3214), 25: ("locxPID.Ki", 0.0001), 26: ("locxPID.Kd", 6.2937),
    27: ("locyPID.Kp", 0.3214), 28: ("locyPID.Ki", 0.0001), 29: ("locyPID.Kd", 6.2937),
    30: ("locxsPID.Kp", 3.2242), 31: ("locxsPID.Ki", 0.0157), 32: ("locxsPID.Kd", 4.7283),
    33: ("locysPID.Kp", 3.2242), 34: ("locysPID.Ki", 0.0157), 35: ("locysPID.Kd", 4.7283),
}

GAIN_PRESETS = {
    "c3": {
        "why": "C3 robust: robust tune over 18 delay-varied plants (sim, PROPOSED): overshoot 3.61 cm, settle 0.84 s vs default 10.0 cm / 1.42 s (docs/lab/run-sheet-2026-10-16.md).",
        "gains": {
            24: 0.3214, 25: 0.0001, 26: 6.2937,
            27: 0.3214, 28: 0.0001, 29: 6.2937,
            30: 3.2242, 31: 0.0157, 32: 4.7283,
            33: 3.2242, 34: 0.0157, 35: 4.7283,
        },
    },
    "c2": {
        "why": "C2 tuned_shift: overshoot reduction on 0.4 m step (docs/lab/run-sheet-2026-10-14.md); higher velocity P (4.70 vs 3.0), zero velocity D (0 vs 6.0), tuned position loop.",
        "gains": {
            24: 0.67, 25: 0.0014, 26: 4.93,
            27: 0.67, 28: 0.0014, 29: 4.93,
            30: 4.70, 31: 0.0031, 32: 0.0,
            33: 4.70, 34: 0.0031, 35: 0.0,
        },
    },
    "rate35": {
        "why": "rate35 (PROPOSED): roll/pitch rate Kp3.5 Kd13, Ki unchanged; autotune on the 10-24 rate-sysid fits "
               "(logs 213634, 214336) predicts phase margin roll 21.4 -> 34.4 deg, pitch 28-30 -> 43-46 deg "
               "(docs/lab/days/2026-10-25.yaml block 5).",
        "gains": {9: 3.5, 10: 0.01, 11: 13.0, 12: 3.5, 13: 0.01, 14: 13.0},
    },
    "yaw104": {
        "why": "yaw104 (PROPOSED): yaw rate Kp10.4 Kd0.014, Ki unchanged; autotune on the 10-24 yaw fit (log 215247). "
               "Current yaw PM is 81.7 deg, so this is optional (docs/lab/days/2026-10-25.yaml block 5).",
        "gains": {15: 10.4, 16: 0.005, 17: 0.014},
    },
    "rate35y104": {
        "why": "rate35y104 (PROPOSED): rate35 roll/pitch Kp3.5 Kd13 plus yaw104 yaw Kp10.4 Kd0.014 in one write, so every "
               "flight re-writes all nine rate gains after a reboot (docs/lab/days/2026-10-27.yaml block 0).",
        "gains": {9: 3.5, 10: 0.01, 11: 13.0, 12: 3.5, 13: 0.01, 14: 13.0, 15: 10.4, 16: 0.005, 17: 0.014},
    },
    "defaults": {
        "why": "defaults: restore API/pid.c firmware default gains without requiring power-cycle (docs/agent/lab-friction-2026-10-15.md A6).",
        # rate loops (9-17) too: F4 livetune writes 9-14, yaw104 writes 15-17, and RAM gains outlive the flight
        "gains": {idx: PID_DEFAULTS[idx][1] for idx in [*range(9, 18), *range(24, 36)]},
    },
    "xy_candidate": {
        "why": "xy_candidate (PROPOSED): API/xy_ctrl_gains_candidate.h pre-sysid ladder base for VAR 75-83 (LQR k0-k3, ADRC wc/b0, model k/al/drag).",
        "gains": {
            75: 6.57910454,
            76: 5.13574747,
            77: 0.477461929,
            78: 0.539490884,
            79: 1.25,
            80: 2.304,
            81: 0.72,
            82: 0.94119394,
            83: 0.38,
        },
    },
}

# Candidate xy controller & model defaults from API/xy_ctrl_gains_candidate.h (PROPOSED ladder base for VAR 75-83)
CANDIDATE_XY_GAINS = {
    "g_xyc_lqr_k0": 6.57910454,
    "g_xyc_lqr_k1": 5.13574747,
    "g_xyc_lqr_k2": 0.477461929,
    "g_xyc_lqr_k3": 0.539490884,
    "g_xyc_adrc_wc": 1.25,
    "g_xyc_adrc_b0": 2.304,
    "g_xyc_model_k": 0.72,
    "g_xyc_model_al": 0.94119394,
    "g_xyc_model_drag": 0.38,
}

# Ground-only (disarmed-only) wfb_var IDs enforced by TASK/AutoflyTask.c:573,581
XY_GROUND_ONLY_VARS = frozenset({44, 47, *range(75, 84)})

_XY_VAR_META = {
    44: ("g_xy_ctrl_kind", 0.0),
    47: ("g_xyc_delay_ticks", 12.0),
    74: ("g_xyc_pred_lead", 0.0),
    75: ("g_xyc_lqr_k0", 6.57910454),
    76: ("g_xyc_lqr_k1", 5.13574747),
    77: ("g_xyc_lqr_k2", 0.477461929),
    78: ("g_xyc_lqr_k3", 0.539490884),
    79: ("g_xyc_adrc_wc", 1.25),
    80: ("g_xyc_adrc_b0", 2.304),
    81: ("g_xyc_model_k", 0.72),
    82: ("g_xyc_model_al", 0.94119394),
    83: ("g_xyc_model_drag", 0.38),
}


LIVETUNE_BEST_PATH = ROOT / "logs" / "campaigns" / "livetune_best.json"


def load_livetune_gains(path=None, force=False):
    """Load best gains from livetune_best.json and build gain dict and why string.

    Returns (gains_dict, why_text, err_msg).
    """
    p = Path(path or LIVETUNE_BEST_PATH)
    if not p.is_file():
        return None, None, f"livetune_best.json not found at {p}: run F2 (lab_d_livetune_rate) and F3 (lab_d_livetune_pos) first"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, None, f"failed to read {p}: {exc}"

    rate_data = data.get("rate")
    pos_data = data.get("pos")

    gains = {}
    why_parts = []

    # Rate gains
    if rate_data:
        rate_rec = rate_data.get("recommended")
        if rate_rec or force:
            best_rate = rate_data.get("best") or (rate_rec if isinstance(rate_rec, dict) else None)
            if isinstance(best_rate, dict):
                kp = float(best_rate.get("Kp", 5.0))
                ki = float(best_rate.get("Ki", 0.01))
                kd = float(best_rate.get("Kd", 10.0))
                # gyroxPID (9, 10, 11) and gyroyPID (12, 13, 14)
                gains[9] = kp
                gains[10] = ki
                gains[11] = kd
                gains[12] = kp
                gains[13] = ki
                gains[14] = kd
                base_j = rate_data.get("base_j")
                best_j = rate_data.get("best_j")
                if base_j is not None and best_j is not None:
                    pct = ((best_j - base_j) / base_j) * 100.0 if base_j > 0 else 0.0
                    why_parts.append(f"rate J {best_j:.3g} vs base {base_j:.3g} ({pct:+.1f}%)")
                else:
                    why_parts.append(f"rate Kp={kp:.4g} Ki={ki:.4g} Kd={kd:.4g}")
        else:
            print("livetune: rate gains were not recommended (did not beat baseline by min_gain). Pass --force-livetune to apply anyway.", flush=True)

    # Pos gains
    if pos_data:
        pos_rec = pos_data.get("recommended")
        if pos_rec or force:
            best_pos = pos_data.get("best") or (pos_rec if isinstance(pos_rec, dict) else None)
            if isinstance(best_pos, dict):
                out_kp = float(best_pos.get("out.Kp", 0.8))
                out_kd = float(best_pos.get("out.Kd", 4.0))
                in_kp = float(best_pos.get("in.Kp", 3.0))
                in_kd = float(best_pos.get("in.Kd", 6.0))
                # locxPID: 24 (Kp), 25 (Ki default), 26 (Kd)
                gains[24] = out_kp
                gains[25] = PID_DEFAULTS[25][1]
                gains[26] = out_kd
                # locyPID: 27 (Kp), 28 (Ki default), 29 (Kd)
                gains[27] = out_kp
                gains[28] = PID_DEFAULTS[28][1]
                gains[29] = out_kd
                # locxsPID: 30 (Kp), 31 (Ki default), 32 (Kd)
                gains[30] = in_kp
                gains[31] = PID_DEFAULTS[31][1]
                gains[32] = in_kd
                # locysPID: 33 (Kp), 34 (Ki default), 35 (Kd)
                gains[33] = in_kp
                gains[34] = PID_DEFAULTS[34][1]
                gains[35] = in_kd
                base_j = pos_data.get("base_j")
                best_j = pos_data.get("best_j")
                if base_j is not None and best_j is not None:
                    pct = ((best_j - base_j) / base_j) * 100.0 if base_j > 0 else 0.0
                    why_parts.append(f"pos J {best_j:.3g} vs base {base_j:.3g} ({pct:+.1f}%)")
                else:
                    why_parts.append(f"pos out.Kp={out_kp:.4g} out.Kd={out_kd:.4g} in.Kp={in_kp:.4g} in.Kd={in_kd:.4g}")
        else:
            print("livetune: pos gains were not recommended (did not beat baseline by min_gain). Pass --force-livetune to apply anyway.", flush=True)

    if not gains:
        return None, None, f"no applicable gains in {p} (neither rate nor pos was recommended; use --force-livetune to force)"

    why_str = "livetune: " + "; ".join(why_parts) + f" from F2/F3 in-flight CMA-ES ({p})"
    return gains, why_str, None


def save_livetune_result(summary, loop=None, path=None):
    """Persist F2/F3 livetune best gains into logs/campaigns/livetune_best.json (merging rate and pos)."""
    p = Path(path or LIVETUNE_BEST_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}

    best = summary.get("best")
    baseline = summary.get("baseline") or {}
    baseline_J = summary.get("baseline_J") or {}
    evals = summary.get("evals") or []
    best_rel = summary.get("best_rel")
    recommended = summary.get("recommended")

    if not loop:
        loop = summary.get("loop")
    if not loop:
        if "out.Kp" in baseline or (isinstance(best, dict) and "out.Kp" in best):
            loop = "pos"
        elif "Kp" in baseline or (isinstance(best, dict) and "Kp" in best):
            loop = "rate"
        else:
            loop = "rate"

    base_j = None
    if baseline_J and isinstance(baseline_J, dict):
        vals = [v for v in baseline_J.values() if isinstance(v, (int, float))]
        if vals:
            base_j = round(sum(vals) / len(vals), 4)
    if base_j is None and summary.get("base_j") is not None:
        try:
            base_j = round(float(summary["base_j"]), 4)
        except Exception:
            pass

    best_j = None
    if best and isinstance(best, dict):
        match_eval = next((e for e in evals if isinstance(e, dict) and e.get("gains") == best and e.get("J") is not None), None)
        if match_eval:
            best_j = round(float(match_eval["J"]), 4)
    if best_j is None and summary.get("best_j") is not None:
        try:
            best_j = round(float(summary["best_j"]), 4)
        except Exception:
            pass
    elif best_j is None and base_j is not None and best_rel is not None:
        try:
            best_j = round(base_j * float(best_rel), 4)
        except Exception:
            pass

    entry = {
        "loop": loop,
        "best": best,
        "best_j": best_j,
        "base_j": base_j,
        "best_rel": round(float(best_rel), 4) if best_rel is not None else None,
        "recommended": recommended,
        "status": summary.get("status"),
        "candidates": summary.get("candidates", 0),
        "generations": summary.get("generations", 0),
        "stamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    existing[loop] = entry
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"LiveTune best gains ({loop}) saved to {p}", flush=True)
    return entry


def check_and_persist_livetune(st, path=None):
    """Scan st (campaign state) and/or outputs_dir/metrics.json for livetune results, persisting to livetune_best.json."""
    if not isinstance(st, dict):
        return []
    found = []
    for flt in (st.get("flights") or []):
        for stp in (flt.get("steps") or []):
            if stp.get("kind") == "livetune" and "livetune" in stp:
                found.append(stp["livetune"])
    out_dir = st.get("outputs_dir")
    if out_dir:
        m_path = Path(out_dir) / "metrics.json"
        if m_path.is_file():
            try:
                m_data = json.loads(m_path.read_text(encoding="utf-8"))
                for flt in m_data.get("flights") or []:
                    for stp in flt.get("steps") or []:
                        if stp.get("kind") == "livetune" and "livetune" in stp:
                            found.append(stp["livetune"])
            except Exception:
                pass
    saved = []
    seen = set()
    for lt_summary in found:
        if isinstance(lt_summary, dict) and lt_summary.get("best"):
            key = json.dumps(lt_summary, sort_keys=True, default=str)
            if key in seen:  # the same step is in both the live state and metrics.json
                continue
            seen.add(key)
            res = save_livetune_result(lt_summary, path=path)
            saved.append(res)
    return saved


class DirectivesTuple(tuple):
    def __new__(cls, gains_preset, why_text, post_takeoff=None):
        inst = super().__new__(cls, (gains_preset, why_text))
        inst.post_takeoff = post_takeoff
        return inst


_GAIN_ALIASES = {
    "pitch_angle_kp": 0, "pitch_angle_ki": 1, "pitch_angle_kd": 2,
    "angle_pitch_kp": 0, "angle_pitch_ki": 1, "angle_pitch_kd": 2,
    "pitch_kp": 0, "pitch_ki": 1, "pitch_kd": 2,
    "roll_angle_kp": 3, "roll_angle_ki": 4, "roll_angle_kd": 5,
    "angle_roll_kp": 3, "angle_roll_ki": 4, "angle_roll_kd": 5,
    "roll_kp": 3, "roll_ki": 4, "roll_kd": 5,
    "yaw_angle_kp": 6, "yaw_angle_ki": 7, "yaw_angle_kd": 8,
    "angle_yaw_kp": 6, "angle_yaw_ki": 7, "angle_yaw_kd": 8,
    "yaw_kp": 6, "yaw_ki": 7, "yaw_kd": 8,
    "roll_rate_kp": 9, "roll_rate_ki": 10, "roll_rate_kd": 11,
    "rate_roll_kp": 9, "rate_roll_ki": 10, "rate_roll_kd": 11,
    "gyrox_kp": 9, "gyrox_ki": 10, "gyrox_kd": 11,
    "pitch_rate_kp": 12, "pitch_rate_ki": 13, "pitch_rate_kd": 14,
    "rate_pitch_kp": 12, "rate_pitch_ki": 13, "rate_pitch_kd": 14,
    "gyroy_kp": 12, "gyroy_ki": 13, "gyroy_kd": 14,
    "yaw_rate_kp": 15, "yaw_rate_ki": 16, "yaw_rate_kd": 17,
    "rate_yaw_kp": 15, "rate_yaw_ki": 16, "rate_yaw_kd": 17,
    "gyroz_kp": 15, "gyroz_ki": 16, "gyroz_kd": 17,
    "z_rate_kp": 18, "z_rate_ki": 19, "z_rate_kd": 20,
    "z_vel_kp": 18, "z_vel_ki": 19, "z_vel_kd": 20,
    "vel_z_kp": 18, "vel_z_ki": 19, "vel_z_kd": 20,
    "highdts_kp": 18, "highdts_ki": 19, "highdts_kd": 20,
    "highdtspid.kp": 18, "highdtspid.ki": 19, "highdtspid.kd": 20,
    "z_pos_kp": 21, "z_pos_ki": 22, "z_pos_kd": 23,
    "pos_z_kp": 21, "pos_z_ki": 22, "pos_z_kd": 23,
    "high_kp": 21, "high_ki": 22, "high_kd": 23,
    "highpid.kp": 21, "highpid.ki": 22, "highpid.kd": 23,
    "x_pos_kp": 24, "x_pos_ki": 25, "x_pos_kd": 26,
    "pos_x_kp": 24, "pos_x_ki": 25, "pos_x_kd": 26,
    "locx_kp": 24, "locx_ki": 25, "locx_kd": 26,
    "y_pos_kp": 27, "y_pos_ki": 28, "y_pos_kd": 29,
    "pos_y_kp": 27, "pos_y_ki": 28, "pos_y_kd": 29,
    "locy_kp": 27, "locy_ki": 28, "locy_kd": 29,
    "x_vel_kp": 30, "x_vel_ki": 31, "x_vel_kd": 32,
    "vel_x_kp": 30, "vel_x_ki": 31, "vel_x_kd": 32,
    "locxs_kp": 30, "locxs_ki": 31, "locxs_kd": 32,
    "y_vel_kp": 33, "y_vel_ki": 34, "y_vel_kd": 35,
    "vel_y_kp": 33, "vel_y_ki": 34, "vel_y_kd": 35,
    "locys_kp": 33, "locys_ki": 34, "locys_kd": 35,
    "g_xy_ctrl_kind": 44, "xy_ctrl_kind": 44,
    "g_xyc_delay_ticks": 47, "xyc_delay_ticks": 47, "delay_ticks": 47,
    "g_xyc_pred_lead": 74, "xyc_pred_lead": 74, "pred_lead": 74,
    "g_xyc_lqr_k0": 75, "xyc_lqr_k0": 75, "lqr_k0": 75,
    "g_xyc_lqr_k1": 76, "xyc_lqr_k1": 76, "lqr_k1": 76,
    "g_xyc_lqr_k2": 77, "xyc_lqr_k2": 77, "lqr_k2": 77,
    "g_xyc_lqr_k3": 78, "xyc_lqr_k3": 78, "lqr_k3": 78,
    "g_xyc_adrc_wc": 79, "xyc_adrc_wc": 79, "adrc_wc": 79,
    "g_xyc_adrc_b0": 80, "xyc_adrc_b0": 80, "adrc_b0": 80,
    "g_xyc_model_k": 81, "xyc_model_k": 81, "model_k": 81,
    "g_xyc_model_al": 82, "xyc_model_al": 82, "model_al": 82,
    "g_xyc_model_drag": 83, "xyc_model_drag": 83, "model_drag": 83,
}
for _idx, (_sym, _) in PID_DEFAULTS.items():
    _GAIN_ALIASES[_sym.lower()] = _idx
    _GAIN_ALIASES[_sym.lower().replace(".", "_")] = _idx


def _resolve_gain_key(k):
    """Resolve an int, stringified int ('9'), PID symbol ('gyroxPID.Kp'), alias ('roll_rate_kp'), or VAR name ('g_xyc_lqr_k0') to int index."""
    if isinstance(k, bool):
        return None
    if isinstance(k, int):
        return k
    if isinstance(k, str):
        s = k.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        low = s.lower()
        if low.startswith("idx_") and low[4:].isdigit():
            return int(low[4:])
        if low.startswith("var") and low[3:].lstrip("_ ").isdigit():
            return int(low[3:].lstrip("_ "))
        if low in _GAIN_ALIASES:
            return _GAIN_ALIASES[low]
        try:
            from ground_station.platform import wfb_var
            row = wfb_var.rows().get(s)
            if row is not None:
                return row.id
        except Exception:
            pass
    return None


def _gain_meta(idx):
    """Return (axis_name, default_val, cmd_id) for a PID gain index (0..35, cmd 1) or wfb_var row id (>=40, cmd 0x2D)."""
    if idx in PID_DEFAULTS:
        name, def_val = PID_DEFAULTS[idx]
        return name, def_val, 1
    if idx in _XY_VAR_META:
        name, def_val = _XY_VAR_META[idx]
        return name, def_val, 0x2D
    if idx >= 40:
        try:
            from ground_station.platform import wfb_var
            row = next((r for r in wfb_var.rows().values() if r.id == idx), None)
            if row is not None:
                return row.name, 0.0, wfb_var.CMD_VAR_WRITE
        except Exception:
            pass
    return f"idx_{idx}", 0.0, 1


def format_gain_table(gains, old_gains=None, col_old="Old", col_new="New"):
    lines = [
        f"| {'Axis':<14} | {'idx':<4} | {col_old:<8} | {col_new:<8} |",
        f"|{'-'*16}|{'-'*6}|{'-'*10}|{'-'*10}|",
    ]
    for idx, new_val in sorted(gains.items()):
        axis, def_val, _ = _gain_meta(idx)
        old_val = old_gains.get(idx, def_val) if old_gains is not None else def_val
        lines.append(f"| {axis:<14} | {idx:<4} | {old_val:<8.4g} | {new_val:<8.4g} |")
    return "\n".join(lines)


def _resolve_gain_mapping(mapping, why=None, force_livetune=False, livetune_path=None, source_label=None):
    """Resolve a day-gains dict {'base': ..., 'values': {...}} or flat {<var>: <value>} dict into (gains_dict, why_text, err_msg)."""
    if "values" in mapping and isinstance(mapping["values"], dict):
        raw_items = mapping["values"].items()
        base_name = mapping.get("base")
        if not mapping["values"] and base_name and "values" not in mapping:
            return resolve_preset_gains(base_name, why=why, force_livetune=force_livetune, livetune_path=livetune_path)
        default_why = mapping.get("why") or (
            f"{source_label} (base {base_name})" if source_label and base_name else
            source_label or (f"day gains (base {base_name})" if base_name else "")
        )
    elif "base" in mapping and "values" not in mapping and len(mapping) <= 4:
        return resolve_preset_gains(mapping["base"], why=why or mapping.get("why"),
                                    force_livetune=force_livetune, livetune_path=livetune_path)
    else:
        raw_items = mapping.items()
        default_why = source_label or ""
    out = {}
    for k, v in raw_items:
        idx = _resolve_gain_key(k)
        if idx is None:
            return None, None, f"unknown gain key {k!r}"
        try:
            out[idx] = float(v)
        except (TypeError, ValueError) as exc:
            return None, None, f"invalid gain value for {k!r}: {exc}"
    return dict(sorted(out.items())), why if why is not None else default_why, None


def resolve_preset_gains(preset_or_gains, why=None, force_livetune=False, livetune_path=None):
    """Resolve preset name, .json file path, dict, or list into (gains_dict, why_text, err_msg)."""
    if isinstance(preset_or_gains, Path):
        p = preset_or_gains
        if not p.is_file():
            return None, None, f"gains file not found: {p}"
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, None, f"failed to read gains file {p}: {exc}"
        if isinstance(loaded, dict):
            return _resolve_gain_mapping(loaded, why=why, force_livetune=force_livetune,
                                         livetune_path=livetune_path, source_label=f"day gains from {p.name}")
        return resolve_preset_gains(loaded, why=why, force_livetune=force_livetune, livetune_path=livetune_path)
    if isinstance(preset_or_gains, str):
        raw_str = preset_or_gains.strip()
        key = raw_str.lower()
        key = {"robust": "c3", "tuned": "c2", "default": "defaults", "pid": "defaults", "pid.c": "defaults"}.get(key, key)
        if key == "livetune":
            gains, why_str, err = load_livetune_gains(path=livetune_path, force=force_livetune)
            if err:
                return None, None, err
            return gains, why or why_str, None
        elif key in GAIN_PRESETS:
            preset = GAIN_PRESETS[key]
            return dict(preset["gains"]), why or preset.get("why", ""), None
        elif key.endswith(".json") or "/" in raw_str or "\\" in raw_str or Path(raw_str).is_file():
            return resolve_preset_gains(Path(raw_str), why=why, force_livetune=force_livetune, livetune_path=livetune_path)
        else:
            return None, None, f"unknown gain preset {preset_or_gains!r}: available presets are {', '.join(GAIN_PRESETS.keys())}, livetune"
    elif isinstance(preset_or_gains, dict):
        return _resolve_gain_mapping(preset_or_gains, why=why, force_livetune=force_livetune, livetune_path=livetune_path)
    elif isinstance(preset_or_gains, (list, tuple)):
        out = {}
        for k, v in preset_or_gains:
            idx = _resolve_gain_key(k)
            if idx is None:
                return None, None, f"unknown gain key {k!r}"
            out[idx] = float(v)
        return dict(sorted(out.items())), why or "", None
    else:
        return None, None, f"invalid gains specification: {type(preset_or_gains)}"


def list_gain_presets(livetune_path=None):
    """Print all available gain presets, their rationale, and full gain table."""
    for key in ("c3", "c2", "defaults"):
        preset = GAIN_PRESETS[key]
        print(f"=== Preset: {key} ===")
        print(f"Why: {preset['why']}")
        print(format_gain_table(preset["gains"], col_old="Default", col_new="Target"))
        print()

    p = Path(livetune_path or LIVETUNE_BEST_PATH)
    print("=== Preset: livetune ===")
    if p.is_file():
        gains, why_str, err = load_livetune_gains(path=p, force=True)
        if gains:
            print(f"Why: {why_str}")
            print(format_gain_table(gains, col_old="Default", col_new="Target"))
        else:
            print(f"Why: in-flight CMA-ES tuned gains from F2/F3 ({p})")
            print(f"Note: {err}")
    else:
        print("Why: in-flight CMA-ES tuned gains from F2/F3 (rate idx 9-14, pos idx 24-35)")
        print(f"Note: {p} not found (run F2 and F3 first to generate livetune gains)")


def list_exp_rows():
    """Print the experiment rows from TASK/StabilizerTask.c."""
    from ground_station.platform import exp_rows
    rows = exp_rows.exp_rows()
    print(f"{'name':<10} {'id':>3} {'xy':>3} {'kp':>3} {'vp':>3} {'lim':>4} {'mdec':>5} {'xyrm':>5}")
    for r in rows:
        print(f"{r.name:<10} {r.id:>3} {r.xy:>3} {r.kp:>3} {r.vp:>3} {r.lim:>4} {r.mdec:>5} {r.xyrm:>5}")


def dry_run(campaign, pack=None, rate=None, gains=None, no_gains=False,
            post_takeoff_gains=None, livetune_path=None):
    """Compile scenario, print resolved gains, trajectory preload summary, and scenario plan without contacting 8081."""
    from ground_station.service.campaign_launch import rel
    from ground_station.service.campaign_schema import load_campaign
    from ground_station.service.scenario_schema import TRAJ_KINDS, PROG_KINDS
    from ground_station.service.trajectory_pipeline import crc32

    pack_chosen = pack_id(pack) or remember_pack() or "type1"
    launch, src = resolve_campaign(campaign, pack_chosen, rate)
    c = load_campaign(launch)

    print(f"=== DRY RUN: {c.campaign} ===")
    print(f"Source:   {rel(src)}")
    print(f"Launch:   {rel(launch)}")
    print(f"Pack:     {pack_chosen}")

    # 1. Resolved gains preset table
    fly_directives = extract_fly_directives(src)
    fly_preset, fly_why = fly_directives[0], fly_directives[1]
    fly_post = getattr(fly_directives, "post_takeoff", None)

    ground_preset = None
    ground_why = None
    if not no_gains:
        if gains:
            ground_preset = gains
        elif fly_preset:
            ground_preset = fly_preset
            ground_why = fly_why

    post_preset = None
    if not no_gains:
        if post_takeoff_gains:
            post_preset = post_takeoff_gains
        elif fly_post:
            post_preset = fly_post

    print("\n--- Gains Plan ---")
    if ground_preset:
        g_dict, g_why, g_err = resolve_preset_gains(ground_preset, why=ground_why, livetune_path=livetune_path)
        if g_err:
            print(f"Ground gains ERROR: {g_err}")
        else:
            print(f"Ground gain preset: {ground_preset}")
            if g_why:
                print(f"Why: {g_why}")
            print(format_gain_table(g_dict, col_old="Default", col_new="Target"))
    else:
        print("Ground gains: none (firmware defaults unchanged)")

    if post_preset:
        p_dict, p_why, p_err = resolve_preset_gains(post_preset, livetune_path=livetune_path)
        if p_err:
            print(f"\nPost-takeoff gains ERROR: {p_err}")
        else:
            print(f"\nPost-takeoff gain preset: {post_preset} (applied after takeoff in HOVER)")
            if p_why:
                print(f"Why: {p_why}")
            print(format_gain_table(p_dict, col_old="Default", col_new="Target"))

    # 2. Trajectory preload summary
    print("\n--- Trajectory Preload ---")
    sc = getattr(c.experiments[0], "scenario", None) if c.experiments else None
    if sc and getattr(sc, "steps", None):
        uploads = [st for st in sc.steps if st.kind in TRAJ_KINDS + PROG_KINDS]
        if uploads:
            for st in uploads:
                if hasattr(st, "points") and st.points:
                    n_pts = len(st.points)
                    crc_val = crc32(list(st.points))
                    cmd_count = n_pts * 5 + 3
                    est_time_s = cmd_count * 0.07
                    cmp_str = "< 2 s" if est_time_s < 2.0 else f"~{est_time_s:.1f} s"
                    print(f"Preload step: {st.kind} ({n_pts} points, CRC=0x{crc_val:08X}, est upload time ~{est_time_s:.1f} s [{cmp_str} for {n_pts}-point goto])")
                elif hasattr(st, "program") and st.program:
                    print(f"Preload step: {st.kind} ({len(st.program)} program operations)")
        else:
            print("Trajectory preload: none")
    else:
        print("Trajectory preload: none")

    # 3. Scenario plan
    print(f"\n--- Scenario Plan ---")
    if sc and getattr(sc, "steps", None):
        print(f"Hover altitude: {sc.hover_z_m:.2f} m ({len(sc.steps)} steps)")
        for i, st in enumerate(sc.steps, 1):
            args = getattr(st, "args", {}) or {}
            dur = getattr(st, "duration_s", 0.0)
            desc = ""
            if st.kind == "takeoff":
                set_info = f", set={args.get('set')}" if args.get("set") else ""
                desc = f"z = {args.get('z', sc.hover_z_m)} m{set_info}"
            elif st.kind == "hold":
                desc = f"duration = {args.get('s', dur):.1f} s"
            elif st.kind == "goto":
                pts_cnt = len(st.points) if hasattr(st, "points") and st.points else "?"
                desc = f"target ({args.get('x')}, {args.get('y')}, {args.get('z')}) m, dwell = {args.get('dwell_s')} s ({pts_cnt} points)"
            elif st.kind == "preset":
                desc = f"name = {args.get('name')}, duration = {dur:.1f} s"
            elif st.kind == "livetune":
                desc = f"loop = {args.get('loop')}, axes = {args.get('axes')}, budget = {args.get('budget_s')} s"
            elif st.kind == "land":
                desc = "controlled landing to pad"
            else:
                desc = f"args = {args}"
            print(f"  {i}. {st.kind:<8} : {desc}")
            # Ground writes before arming, one line each, so a REFUSED one (missing firmware field) shows on the dry run.
            from ground_station.service.ground_plan import write_label
            for w in (args.get("ground") or []) if st.kind == "takeoff" else []:
                print(f"       ground : {write_label(w)}")
    else:
        print("Scenario plan: empty")


TXN_DONE = ("applied", "verified")
TXN_FAILED = ("rejected", "timed_out", "error")
ACK_GRACE_S = 3.0  # ACK -> APPLIED gap; a trajectory BEGIN applied 1.55 s after its ACK (2026-10-15, campaign_live TRAJ_ACK_TIMEOUT_S)


def wait_txn(base, txid, timeout_s=2.0):
    """Final lifecycle of one /commands transaction from the 8081 action journal: 'applied', 'rejected', ... .

    The firmware answers ACK, then APPLIED or REJECTED (TASK/send_data.c). 'applied'/'verified' and the failures
    return at once; an action still 'acknowledged' ACK_GRACE_S after its ACK returns 'acknowledged' (an 8081 started before
    the journal kept ACKed actions open never shows the final outcome); nothing at all returns 'timeout'. The first ACK
    moves the deadline to ACK_GRACE_S after it, so an ACK late in timeout_s still waits the full grace for APPLIED
    (10-19 1017-F1: a 12-gain write aborted on an ACK that came 0.1 s before its 2 s deadline)."""
    deadline = _clock() + timeout_s
    seen = "timeout"
    while True:
        try:
            _, res = call(base, "/api/actions", timeout=timeout_s)
            lc = next((a.get("lifecycle", "") for a in res.get("actions", []) if a.get("transaction_id") == txid), "")
        except Exception:
            lc = ""
        if lc in TXN_DONE or lc in TXN_FAILED:
            return lc
        if lc == "acknowledged" and seen != lc:
            seen = lc
            deadline = _clock() + ACK_GRACE_S
        if _clock() >= deadline:
            return seen
        _sleep(0.02)


def write_gains(base, preset_or_gains, why=None, yes=False, ask_fn=input, timeout_s=2.0, force_livetune=False, livetune_path=None, in_flight=False):
    """Stop-and-wait gain writer enforcing disarmed gate, rationale print, y/N consent, and confirmation table.

    Friction A1-A6 & B10. Returns True if all gains were ACKed/applied; False on refusal or failure.
    """
    # 1. Enforce disarmed gate (A1) unless in-flight transition
    if not in_flight:
        v = _vitals(base)
        arm_state = (v.get("arm") or {}).get("state")
        if arm_state is None and isinstance(v.get("armed"), bool):
            arm_state = "armed" if v["armed"] else "disarmed"
        if arm_state is not None and arm_state != "disarmed":
            print(f"Gain write REFUSED: drone is not disarmed (arm state: {arm_state!r}). Disarm by RC first.", flush=True)
            return False

    # 2. Resolve target gains & why
    gains, why_resolved, err = resolve_preset_gains(preset_or_gains, why=why, force_livetune=force_livetune, livetune_path=livetune_path)
    if err:
        print(f"Gain write REFUSED: {err}", flush=True)
        return False
    why = why_resolved

    if not gains:
        print("no gains to write", flush=True)
        return True

    # 2b. Ground-only VARs (44, 47, 75-83) are disarmed-only in firmware (TASK/AutoflyTask.c:573,581)
    ground_only_hit = sorted(idx for idx in gains if idx in XY_GROUND_ONLY_VARS)
    if ground_only_hit and in_flight:
        names = ", ".join(_gain_meta(i)[0] for i in ground_only_hit)
        print(f"Gain write REFUSED: {names} (VAR {ground_only_hit}) are ground-only (disarmed only); cannot write in_flight.", flush=True)
        return False

    # 3. Retrieve "Old" gains from actions journal or pid.c defaults
    old_gains = {}
    try:
        _, act_res = call(base, "/api/actions", timeout=5)
        for a in act_res.get("actions", []):
            if int(a.get("command_id", 0)) in (1, 45) and int(a.get("index", -1)) in gains:
                idx = int(a["index"])
                if idx not in old_gains:
                    old_gains[idx] = float(a.get("value", 0.0))
    except Exception:
        pass
    for idx in gains:
        if idx not in old_gains:
            old_gains[idx] = _gain_meta(idx)[1]

    # 4. Print Why rationale and proposed table (A4)
    tag = "[In-flight] " if in_flight else ""
    if why:
        print(f"{tag}Why: {why}", flush=True)
    print(f"\n{tag}Proposed gain table:")
    print(format_gain_table(gains, old_gains=old_gains, col_old="Old", col_new="New"), flush=True)

    # 5. Consent prompt (A1)
    if not yes and not in_flight:
        ans = ask_fn(f"\nWrite {len(gains)} gains to RAM? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Gain write cancelled.", flush=True)
            return False

    # 6. Stop-and-wait execution (A2)
    t_start = _clock()
    rows = []
    total_ack = 0
    all_ok = True
    txids = []
    ack_only = []  # ACKed, final APPLIED not seen (8081 started before the journal kept ACKed actions open)

    for idx, new_val in sorted(gains.items()):
        axis, _, cmd_id = _gain_meta(idx)
        old_val = old_gains.get(idx, 0.0)
        acked = False
        ms = 0
        retries = 0
        current_txid = None

        for attempt in range(3):  # initial + up to 2 retries
            retries = attempt
            t0 = _clock()
            try:
                code, res = call(base, "/commands", {"command_id": cmd_id, "index": idx, "value": float(new_val)}, timeout=timeout_s)
            except Exception:
                _sleep(0.05)
                continue
            if code not in (200, 202):
                _sleep(0.05)
                continue
            txid = res.get("transaction_id")
            outcome = "applied" if txid is None else wait_txn(base, txid, timeout_s)
            if outcome in TXN_DONE or outcome == "acknowledged":
                acked = True
                ms = round((_clock() - t0) * 1000)
                if txid is not None:
                    current_txid = txid
                    if outcome == "acknowledged":
                        ack_only.append(idx)
                break
            if outcome == "rejected":
                break  # the firmware refused this value: a retry sends the same value again
        if acked:
            total_ack += 1
            if current_txid is not None:
                txids.append(current_txid)
            rows.append((axis, idx, old_val, new_val, "ACK", ms, retries))
        else:
            rows.append((axis, idx, old_val, new_val, "FAIL", round((_clock() - t0) * 1000), retries))
            all_ok = False
            break

    # 7. Print confirmation table (A3) and parameter shadow / journal read-back check (A5)
    total_s = _clock() - t_start
    print(f"\n| {'Axis':<14} | {'idx':<4} | {'Old':<8} | {'New':<8} | {'ACK':<4} | {'ms':<4} | {'Retries':<7} |")
    print(f"|{'-'*16}|{'-'*6}|{'-'*10}|{'-'*10}|{'-'*6}|{'-'*6}|{'-'*9}|")
    for axis, idx, old_v, new_v, ack_str, ms_val, ret_val in rows:
        print(f"| {axis:<14} | {idx:<4} | {old_v:<8.4g} | {new_v:<8.4g} | {ack_str:<4} | {ms_val:<4} | {ret_val:<7} |")

    journal_ok = not ack_only
    if all_ok and ground_only_hit:
        try:
            _, rb_res = call(base, "/api/actions", timeout=timeout_s)
            rb_actions = rb_res.get("actions", []) if isinstance(rb_res, dict) else []
            for idx in ground_only_hit:
                matched = [
                    a for a in rb_actions
                    if int(a.get("command_id", 0)) == 0x2D and int(a.get("index", -1)) == idx
                ]
                if matched and "value" in matched[0]:
                    rb_val = float(matched[0]["value"])
                    if abs(rb_val - float(gains[idx])) > 1e-4:
                        print(f"Gain write REFUSED: read-back mismatch for {_gain_meta(idx)[0]} (VAR {idx}): "
                              f"wrote {gains[idx]:g}, read back {rb_val:g}", flush=True)
                        all_ok = False
                        break
        except Exception:
            pass
    if all_ok and ack_only:
        print(f"note: idx {ack_only} ACKed but APPLIED not seen in the 8081 journal: restart 8081 on this branch", flush=True)
    ver_suffix = " (applied, 8081 journal)" if (all_ok and journal_ok) else ""
    print(f"\nTotal time: {total_s:.2f} s ({total_ack}/{len(gains)} ACK{ver_suffix})", flush=True)

    events.log("gain_write", total=len(gains), ack=total_ack, total_s=round(total_s, 2), ok=all_ok, applied=all_ok and journal_ok, in_flight=in_flight)
    if not all_ok:
        print(f"Gain write ABORTED: write failed for idx {rows[-1][1]}.", flush=True)
        return False
    return True


# a directive is a whole comment line "# key: value"; prose that merely mentions "gains:" is not one
_DIRECTIVE_RE = re.compile(r"^\s*#\s*(post[_-]takeoff[_-]gains|gains|why)\s*:(.*)$", re.IGNORECASE)


def extract_fly_directives(fly_path):
    """Parse # gains: <preset>, # post_takeoff_gains: <preset>, and # why: <text> directives from a .fly file."""
    p = Path(fly_path)
    if not p.is_file():
        return DirectivesTuple(None, None, None)
    gains_preset = None
    why_text = None
    post_takeoff = None
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            m = _DIRECTIVE_RE.match(line)
            if not m:
                continue
            key, after = m.group(1).lower().replace("-", "_"), m.group(2).strip()
            if key == "post_takeoff_gains":
                post_takeoff = after.split()[0].lower() if after else None
            elif key == "gains":
                gains_preset = after.split()[0].lower() if after else None
            elif key == "why":
                why_text = after
    except Exception:
        pass
    return DirectivesTuple(gains_preset, why_text, post_takeoff)


class HttpWfbClient:
    """Minimal WfbClient sending commands to base /commands endpoint with stop-and-wait."""
    def __init__(self, base, timeout_s=10.0):
        self.base = base
        self.timeout_s = timeout_s

    def _send_cmd(self, cmd: int, idx: int, value: float) -> bool:
        try:
            code, res = call(self.base, "/commands", {"command_id": cmd, "index": idx, "value": float(value)}, timeout=self.timeout_s)
            if code not in (200, 202):
                return False
            txid = res.get("transaction_id")
            # a trajectory APPEND/COMMIT can be REJECTED after its ACK: wait for the final outcome
            return txid is None or wait_txn(self.base, txid, self.timeout_s) in TXN_DONE + ("acknowledged",)
        except Exception:
            return False

    def set_hover_z(self, z: float) -> bool:
        return self._send_cmd(26, 0, float(z))

    def traj_begin(self, n: int) -> bool:
        return self._send_cmd(27, 0, float(n))

    def traj_append(self, v: float) -> bool:
        return self._send_cmd(27, 1, float(v))

    def traj_commit(self, crc: int) -> bool:
        if not self._send_cmd(27, 2, float((crc >> 16) & 0xFFFF)):
            return False
        return self._send_cmd(27, 3, float(crc & 0xFFFF))

    def prog(self, idx: int, value: float) -> bool:
        return self._send_cmd(28, idx, float(value))

    def var_write(self, name: str, value: float) -> bool:
        """VAR_WRITE (CMD 0x2D) of one allowlisted row; VarWriteError when the firmware would refuse it."""
        from ground_station.platform import wfb_var
        return self._send_cmd(wfb_var.CMD_VAR_WRITE, wfb_var.check(name, value).id, float(value))

    def mrac_prior(self, axis: str, idx: int, value: float) -> bool:
        """MRAC_PRIOR (CMD 0x2F..0x34) write; PriorError when the firmware would refuse it (nothing sent)."""
        from ground_station.platform import mrac_prior
        return self._send_cmd(*mrac_prior.check(axis, idx, value))

    # -- CMD 0x1D MRAC_VARIANT (WP-27 knobs, D4 x/y layers) ------------------
    # axis: "pitch" / "roll" / "yaw" / "z" / "x" / "y" or 0..5 (firmware order).
    # field: the knob name ("xy_layers", "basis", "st_eps", ...) or its number in VARIANT_FIELDS.
    # The firmware refuses 0x1D while airborne, so write on the ground.

    def mrac_variant(self, axis: str | int, field: str | int, value: float) -> bool:
        """CMD 0x1D MRAC_VARIANT: write one variant field on one axis.

        Returns True when the firmware APPLIED the write. Raises ValueError before sending anything when the
        axis, the field or the value is outside what the firmware would accept (nothing sent).

            client.mrac_variant("x", "xy_layers", 2)   # outer x/y layer only
            client.mrac_variant(1, "basis", 3)         # roll axis, RBF12 basis
        """
        from ground_station.analysis.mrac_variants import VARIANT_FIELDS, variant_idx, variant_bounds

        a = self._variant_axis(axis)
        f = self._variant_field(field)
        idx = variant_idx(a, f)
        lo, hi = variant_bounds(idx)
        value = float(value)
        if not (lo <= value <= hi):
            raise ValueError(f"MRAC variant {VARIANT_FIELDS[f][0]} on axis {a}: value {value} outside "
                             f"[{lo}, {hi}]")
        return self._send_cmd(0x1D, idx, value)

    def mrac_xy_layers(self, target: int | str) -> bool:
        """The D4 x/y layer target (CMD 0x1D field 31): "inner" (1), "outer" (2), "both" (3), "none" (0).

        It is a target: the ground latch makes it live from the next takeoff (a reboot goes back to inner).
        """
        bits = {"none": 0, "inner": 1, "outer": 2, "both": 3}
        if isinstance(target, str):
            key = target.strip().lower()
            if key not in bits:
                raise ValueError(f"xy layer target {target!r} is not one of {', '.join(bits)}")
            target = bits[key]
        return self.mrac_variant("x", "xy_layers", target)

    @staticmethod
    def _variant_axis(axis: str | int) -> int:
        from ground_station.analysis.mrac_variants import AXES

        if isinstance(axis, str):
            names = ("pitch", "roll", "yaw", "z", "x", "y")
            key = axis.strip().lower()
            if key not in names:
                raise ValueError(f"MRAC axis {axis!r} is not one of {', '.join(names)}")
            return names.index(key)
        if not 0 <= axis < len(AXES):
            raise ValueError(f"MRAC axis {axis} out of range 0..{len(AXES) - 1}")
        return int(axis)

    @staticmethod
    def _variant_field(field: str | int) -> int:
        from ground_station.analysis.mrac_variants import VARIANT_FIELDS

        if isinstance(field, str):
            names = [row[0] for row in VARIANT_FIELDS]
            key = field.strip()
            if key not in names:
                raise ValueError(f"MRAC variant field {field!r} is not one of {', '.join(names)}")
            return names.index(key)
        if not 0 <= field < len(VARIANT_FIELDS):
            raise ValueError(f"MRAC variant field {field} out of range 0..{len(VARIANT_FIELDS) - 1}")
        return int(field)


def ground_exp_id(launch_path, flight=0):
    """The exp_id the takeoff `set` of the campaign's flight `flight` (0-based) writes on the ground, None when it
    writes none. Same queue as run_campaign: each experiment `repeats` times, in order."""
    from ground_station.service.campaign_schema import load_campaign
    queue = [e for e in load_campaign(launch_path).experiments for _ in range(e.repeats)]
    sc = getattr(queue[flight % len(queue)], "scenario", None) if queue else None
    for st in getattr(sc, "steps", None) or ():
        if st.kind == "takeoff":
            return next((float(v) for name, v in st.args.get("set", ()) if name == "exp_id"), None)
    return None


def preapply_exp_id(base, launch_path, flight=0, client=None):
    """Write the flight's exp_id while the drone is still disarmed, before wait_for_arm: Exp_Poll latches an exp row
    only on the ground, and firmware older than Keil_PreTakeoff refuses it once RC-armed on the pad, so the runner's
    takeoff `set exp_id` left exp_active one flight behind (10-19 1017-E1..E4: 255 -> 4 -> 5 -> 6). The runner still
    sends it and checks exp_active. Not reset after the flight: VAR_WRITE refuses 255 (row range 0-7), and the row's
    knobs (kp/vp/lim/xy) stay written anyway. Returns the value written, None when none or on failure."""
    try:
        v = ground_exp_id(launch_path, flight)
        if v is None:
            return None
        ok = (client or HttpWfbClient(base)).var_write("exp_id", v)
    except Exception as e:
        print(f"preapply exp_id: {e}", flush=True)
        return None
    events.log("exp_preapply", exp_id=int(v), ok=ok)
    print(f"exp_id {int(v)} written on the ground before the arm" + ("." if ok else ": NOT applied (the runner retries)."),
          flush=True)
    return v if ok else None


def ground_prior_writes(launch_path, flight=0):
    """The disarmed-only prior writes (`mrac_prior`) of the campaign's flight `flight` (0-based) takeoff `ground`."""
    from ground_station.service.campaign_schema import load_campaign
    queue = [e for e in load_campaign(launch_path).experiments for _ in range(e.repeats)]
    sc = getattr(queue[flight % len(queue)], "scenario", None) if queue else None
    for st in getattr(sc, "steps", None) or ():
        if st.kind == "takeoff":
            return [w for w in st.args.get("ground", ()) if isinstance(w, dict) and w.get("kind") == "prior"]
    return []


def preapply_prior(base, launch_path, flight=0, client=None):
    """Write the flight's mrac_prior (CMD 0x2F..0x34) while the drone is still disarmed, before wait_for_arm:
    Cmd_MracPrior has interlock 6 (DISARMED ONLY) and MRAC_GateStep re-seeds Theta_hat on a disarmed tick."""
    try:
        from ground_station.service import ground_plan
        writes = ground_prior_writes(launch_path, flight)
        if not writes:
            return True
        cli = client or HttpWfbClient(base)
        for w in writes:
            if not ground_plan.send_write(cli, w):
                print(f"preapply {ground_plan.write_label(w)}: NOT applied", flush=True)
                return False
    except Exception as e:
        print(f"preapply mrac_prior: {e}", flush=True)
        return False
    events.log("prior_preapply", count=len(writes), ok=True)
    print(f"mrac_prior ({len(writes)} writes) written on the ground before the arm.", flush=True)
    return True


def upload_preload(base, launch_path=None, runner=None):
    """Run trajectory/program upload on the ground BEFORE wait_for_arm() (Fix 2)."""
    if runner is not None:
        if hasattr(runner, "upload_preload") and callable(runner.upload_preload):
            return runner.upload_preload()
        if hasattr(runner, "preload_send") and callable(runner.preload_send):
            return runner.preload_send()
    if not launch_path:
        return True
    try:
        from ground_station.service.campaign_schema import load_campaign
        from ground_station.service.scenario_schema import TRAJ_KINDS, PROG_KINDS
        from ground_station.platform.trajectory_upload import upload
        from ground_station.platform import wfb_program
        c = load_campaign(launch_path)
        if not c.experiments:
            return True
        sc = getattr(c.experiments[0], "scenario", None)
        if not sc or not getattr(sc, "steps", None):
            return True
        uploads = [st for st in sc.steps if st.kind in TRAJ_KINDS + PROG_KINDS]
        if len(uploads) != 1:
            return True
        st = uploads[0]
        print(f"Preloading {st.kind} upload on the ground before arming...", flush=True)
        events.log("upload_preload_start", kind=st.kind)
        client = HttpWfbClient(base)
        client.set_hover_z(sc.hover_z_m)
        if st.kind in PROG_KINDS:
            res = wfb_program.upload(st.program, client)
            ok, err = res.ok, res.error or ""
        else:
            res = upload(st.points, client)
            ok, err = res.ok, res.error or ""
        events.log("upload_preload_done", kind=st.kind, ok=ok, error=err)
        if ok:
            print(f"Preload {st.kind} upload OK.", flush=True)
        else:
            print(f"Preload {st.kind} upload FAIL: {err}", flush=True)
        return ok
    except Exception as e:
        print(f"upload_preload: {e}", flush=True)
        return False


def quiet_preflight(base, campaign, pack, shown):
    """(ok, failing rows) of one preflight; prints only when the failing set differs from `shown`."""
    from urllib.parse import urlencode
    _, res = call(base, "/api/campaign/preflight?" + urlencode({"campaign": campaign, "pack": pack}), timeout=30)
    fails = tuple(f"{c.get('name')}: {c.get('value')}" + (f"  -> {c['fix']}" if c.get("fix") else "")
                  for c in res.get("checks", []) if c.get("pass") is False)
    if "error" in res:
        fails += (f"preflight error: {res['error']}",)
    # Fix 4 (Preflight race): if very first poll fails ONLY on position not streaming, wait up to 1.5s
    if shown is None and fails and all("position" in f and "not streaming" in f for f in fails):
        for _ in range(3):
            _sleep(0.5)
            _, res = call(base, "/api/campaign/preflight?" + urlencode({"campaign": campaign, "pack": pack}), timeout=30)
            fails = tuple(f"{c.get('name')}: {c.get('value')}" + (f"  -> {c['fix']}" if c.get("fix") else "")
                          for c in res.get("checks", []) if c.get("pass") is False)
            if "error" in res:
                fails += (f"preflight error: {res['error']}",)
            if not fails or not all("position" in f and "not streaming" in f for f in fails):
                break
    if fails != shown:
        events.log("preflight", ok=not fails, fails=list(fails), campaign=campaign, pack=pack)
        print("preflight " + ("FAIL:\n  " + "\n  ".join(fails) if fails else "OK: arm by RC to fly"), flush=True)
    return bool(res.get("ok")) and not fails, fails


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
    t_arm, no_arm = t_link, False
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
            if state in ("armed", "disarmed"):
                t_arm = now
                if no_arm:
                    no_arm = False
                    print(f"arm signal back: {state}", flush=True)
                    events.log("arm_signal_back", state=state)
            elif not lost and not no_arm and now - t_arm > NO_ARM_S:
                no_arm = True
                print(NO_ARM_MSG, flush=True)
                events.log("arm_signal_missing", state=state)
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


PRESET_TRAJ_CMPS = 2.0   # a HOVER x/y target moving faster (cm/s) is a Keil preset trajectory (scorecard PRESET_TRAJ_MPS)
PRESET_TRAJ_MIN_S = 1.0  # target motion shorter than this (a hold setpoint step) is not one (scorecard PRESET_TRAJ_MIN_S)


def _phases(samples):
    """Phase per sample: the primitive name, but TRAJ while a trajectory executes (traj_state EXECUTING) or a Keil
    preset (`preset figure8`) moves the x/y target from AutoflyTask while the primitive stays HOVER (10-19)."""
    def name(s):
        if _state_name(TRAJ_NAMES, s, "g_wfb_status.traj_state") == "EXECUTING":
            return "TRAJ"
        return _state_name(PRIM_NAMES, s, "g_wfb_status.prim_state")
    out = [name(s) for s in samples]
    moving = [False] * len(samples)
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        dt = b.get("t", 0.0) - a.get("t", 0.0)
        d = [(b.get(f"Ctrler.{pid}.Des", math.nan) - a.get(f"Ctrler.{pid}.Des", math.nan)) * scale * 100
             for _, pid, scale in AXES[:2]]
        moving[i] = out[i - 1] == out[i] == "HOVER" and dt > 0 and math.hypot(*d) / dt > PRESET_TRAJ_CMPS
    i = 0
    while i < len(samples):
        j = i
        while j < len(samples) and moving[j]:
            j += 1
        if j > i and samples[j - 1].get("t", 0.0) - samples[i].get("t", 0.0) >= PRESET_TRAJ_MIN_S:
            out[i:j] = ["TRAJ"] * (j - i)
        i = j + 1
    return out


def debrief_lines(samples, st, folder, cam=None):
    """Five lines: result, HOVER and TRAJ error (RMS/max per axis in cm, from the live samples), landing offset from
    the takeoff point, where the files are. `cam` (camera_landing()) adds the camera's landing offset from the pad."""
    phases = _phases(samples)
    air = max((s.get("g_wfb_status.airborne_t", 0.0) for s in samples), default=0.0)
    lines = [f"{st.get('status')} {st.get('reason') or ''}".strip()
             + f"  air {air:.1f} s  {len(st.get('flights') or [])} flight(s)  {len(samples)} live samples"]
    # 10-18 mem1: a takeoff abort read as "it only hovered": say the abort and that the steps after it never ran
    lines += [f"ABORTED L{f.get('abort_level') or '?'} {f.get('experiment') or 'flight'}: {f['abort_reason']} (the safety "
              "monitor landed it; the steps after that point never ran)" for f in st.get("flights") or [] if f.get("abort_reason")]
    for phase in ("HOVER", "TRAJ"):
        part = [s for s, p in zip(samples, phases) if p == phase]
        errs = {ax: _err_cm(part, ax) for ax in "xyz"}
        if not any(errs.values()):
            lines.append(f"{phase:<6} no samples")
            continue
        rms = "/".join(f"{math.sqrt(sum(e * e for e in v) / len(v)):.0f}" if v else "-" for v in errs.values())
        mx = "/".join(f"{max(abs(e) for e in v):.0f}" if v else "-" for v in errs.values())
        lines.append(f"{phase:<6} x/y/z error cm  rms {rms}  max {mx}  ({part[-1]['t'] - part[0]['t']:.0f} s)")
    pad = next((s for s, p in zip(samples, phases) if p in ("IDLE", "CLIMB")), None)
    end = samples[-1] if samples else None
    max_z = max((s.get("Ctrler.Z_posPID.FB", 0.0) for s in samples), default=0.0)
    if air <= 0 or max_z < 0.25:
        lines.append("landing offset: no landing (never airborne)")
    elif pad and end and not any(math.isnan(c) for c in _pos_cm(pad) + _pos_cm(end)):
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


def fly_one(base, launch, src, name, pack, post_takeoff_gains=None):
    """GO for the armed flight, watch it to the end, debrief: (status, live samples, camera landing, folder). The
    status is None when the service refused GO."""
    from ground_station.service.campaign_launch import rel
    folder = flight_folder(name)
    for f in dict.fromkeys((Path(src), Path(launch))):
        shutil.copy2(f, folder / f.name)
    code, res = call(base, "/api/campaign/go", {
        "campaign_path": str(launch), "pack_id": pack, "checklist": {c: True for c in CHECKLIST},
        "source": "operator:cli", "confirmation": CONFIRMATION}, timeout=30)
    events.log("go", code=code, campaign=name, pack=pack, folder=rel(folder), error=res.get("error"))
    if code != 200:
        print(f"GO refused ({code}): {res.get('error', res)}. Disarm by RC.", flush=True)
        return None, [], None, folder
    print("GO. keys: s stop step, n next, l land, p PlotJuggler; Ctrl+C land, twice abort. RC kill stays first.")
    st, samples = watch(base, on_pj=lambda: plotjuggler(base), post_takeoff_gains=post_takeoff_gains)
    t_land = _clock()
    cam = camera_landing()
    lines = debrief_lines(samples, st, rel(folder), cam=cam)
    print("\n".join(lines))
    abort = next((f.get("abort_reason") for f in st.get("flights") or [] if f.get("abort_reason")), None)
    air = max((s.get("g_wfb_status.airborne_t", 0.0) for s in samples), default=0.0)
    events.log("debrief", ms=round((_clock() - t_land) * 1000), folder=rel(folder), status=st.get("status"),
               phase=st.get("phase"), samples=len(samples), aborted=abort is not None, abort_reason=abort,
               air_s=round(air, 1))
    (folder / "debrief.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "outputs.txt").write_text(f"{st.get('outputs_dir') or ''}\n", encoding="utf-8")
    write_samples(folder / "live_5hz.csv", samples)
    try:
        check_and_persist_livetune(st)
    except Exception:
        pass
    return st, samples, cam, folder


def fly_loop(base, arg, pack, rate=None, run=None, once=False, yes=False,
             gains=None, no_gains=False, force_livetune=False, livetune_path=None,
             post_takeoff_gains=None, no_gates=False):
    """Compile -> wait for the RC arm -> go -> watch -> debrief, flight after flight until q."""
    from ground_station.service.campaign_launch import rel
    from ground_station.service.campaign_schema import load_campaign

    print("Arming confirms the checklist:\n" + "\n".join(f"  [ ] {c}" for c in CHECKLIST))
    held = None   # the runner waits for the next flight of a multi-flight campaign: (launch, src, name, its pack)
    next_i = 0    # campaign flight (0-based) the held flight is
    while True:
        if held:
            launch, src, name, go_pack = held
            print(f"{name}: next flight, pack {go_pack}. Arm by RC to fly it (q quits).", flush=True)
        else:
            try:
                launch, src = resolve_campaign(arg, pack, rate)
                if no_gates:
                    launch = gates_off(launch)
                    print("GATES OFF: no position/tilt/oscillation/saturation/stale-link stops; low battery, firmware "
                          "trips and the RC kill/takeover stay on", flush=True)
                c = load_campaign(launch)
            except (ValueError, RuntimeError) as e:
                print(f"{arg}: cannot fly it:\n  " + str(e).replace("\n", "\n  "))
                if input("Fix the file, then Enter to read it again (q quits): ").strip().lower() == "q":
                    return 1
                continue
            name, go_pack = c.campaign, c.packs[0]
            # Check directives in .fly (# gains: c3, # why: ...) or CLI override
            fly_directives = extract_fly_directives(src)
            fly_preset, fly_why = fly_directives[0], fly_directives[1]
            fly_post = getattr(fly_directives, "post_takeoff", None)
            preset_to_write = None
            preset_why = None
            post_to_write = None
            if not no_gains:
                if gains:
                    preset_to_write = gains
                elif fly_preset:
                    preset_to_write = fly_preset
                    preset_why = fly_why
                if post_takeoff_gains:
                    post_to_write = post_takeoff_gains
                elif fly_post:
                    post_to_write = fly_post
            if preset_to_write:
                if not write_gains(base, preset_to_write, why=preset_why, yes=yes,
                                   force_livetune=force_livetune, livetune_path=livetune_path):
                    return 1
            print(f"{name}  {rel(src)}  pack {go_pack}  -> {rel(launch)}\nWaiting for the RC arm (q quits).",
                  flush=True)
        # Fix 2: Trajectory preload on the ground BEFORE wait_for_arm
        upload_preload(base, str(launch))
        preapply_exp_id(base, str(launch), flight=next_i if held else 0)
        preapply_prior(base, str(launch), flight=next_i if held else 0)
        t_wait = _clock()
        r = wait_for_arm(base, str(launch), go_pack, preflight=not held)
        events.log("arm_wait", result=r, campaign=name, pack=go_pack, s=round(_clock() - t_wait, 1))
        if r == "quit":
            return 0
        if r == "link":
            pack, held = _ask_pack(pack), None
            continue
        st, _, _, _ = fly_one(base, launch, src, name, go_pack, post_takeoff_gains=post_to_write)
        if st is None:
            held = None
            continue
        if st.get("status") == "service_down":
            return 1
        if run and st.get("outputs_dir"):
            subprocess.run([sys.executable, "-m", "ground_station.analysis.flight_debrief", st["outputs_dir"],
                            "--run", run], cwd=ROOT)
        waiting = st.get("status") == "waiting_for_go" and st.get("waiting_pack")
        held = (launch, src, name, st["waiting_pack"]) if waiting else None
        next_i = len(st.get("flights") or [])   # flights flown so far: the held one is the next
        if once and not held:
            return 0
        print("Disarm by RC; " + ("the next flight of this campaign" if held else f"then {rel(src)} is read again")
              + " and the next arm flies it.", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign", nargs="?", default="", help=".fly file, campaign yaml, launch copy, or a saved campaign name")
    ap.add_argument("--pack", help="the pack type in the drone, 1 or 2 (default: the last one given, "
                                    "logs/campaigns/fly/last_pack.txt)")
    ap.add_argument("--run", help="workflow-c run folder: flight_debrief after each landing")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--plain-service", action="store_true",
                    help="soak only: keep the service on --port although it runs without --lab")
    ap.add_argument("--once", action="store_true", help="one flight, then exit")
    ap.add_argument("--no-gates", action="store_true", help="safety monitor gates off (position, tilt, oscillation, "
                    "saturation, stale link); keeps the low-battery stop, firmware trips and the RC kill/takeover")
    ap.add_argument("--record-only", metavar="LABEL", help="no flight: record the log plan (drone disarmed, carried "
                                                            "by hand) until Enter; sends no drone command")
    ap.add_argument("--rate", type=float, help="override the log plan rate, Hz")
    ap.add_argument("--gains", help="preset name (c3, c2, defaults, livetune) to write stop-and-wait to RAM before flight")
    ap.add_argument("--no-gains", action="store_true", help="skip any gains write directive in the .fly file")
    ap.add_argument("--force-livetune", action="store_true", help="write livetune gains even if not recommended")
    ap.add_argument("--restore-defaults", action="store_true", help="write API/pid.c defaults to RAM stop-and-wait")
    ap.add_argument("--yes", "-y", action="store_true", help="skip y/N consent prompt for gain writes")
    ap.add_argument("--list-gains", action="store_true", help="list all available gain presets and exit")
    ap.add_argument("--list-exp", action="store_true", help="list all experiment rows and exit")
    ap.add_argument("--dry-run", action="store_true", help="compile .fly scenario and print execution plan without contacting 8081")
    ap.add_argument("--post-takeoff-gains", help="preset name to write once airborne in HOVER (takes off on ground/default gains)")
    a = ap.parse_args(argv)
    if a.list_gains:
        list_gain_presets()
        return 0
    if a.list_exp:
        list_exp_rows()
        return 0
    if a.dry_run:
        if not a.campaign:
            ap.error("--dry-run requires a campaign argument")
        dry_run(a.campaign, a.pack, rate=a.rate, gains=a.gains, no_gains=a.no_gains,
                post_takeoff_gains=a.post_takeoff_gains)
        return 0
    base = f"http://127.0.0.1:{a.port}"
    if a.restore_defaults:
        ensure_service(base, a.port, lab=not a.plain_service)
        ok = write_gains(base, "defaults", yes=a.yes)
        return 0 if ok else 1
    if not a.campaign:
        if a.gains:
            ensure_service(base, a.port, lab=not a.plain_service)
            ok = write_gains(base, a.gains, yes=a.yes, force_livetune=a.force_livetune)
            return 0 if ok else 1
        ap.error("the following arguments are required: campaign")
    pack = pack_id(a.pack) or remember_pack()
    why = pack_problem(pack)
    if why:
        sys.exit(why)
    remember_pack(pack)
    events.log("fly_start", campaign=a.campaign, pack=pack, record_only=a.record_only, once=a.once)
    ensure_service(base, a.port, lab=not a.plain_service)
    print("\n".join(vofa_setup(base)), flush=True)
    if a.record_only:
        try:
            launch, _ = resolve_campaign(a.campaign, pack, a.rate)
        except (ValueError, RuntimeError) as e:
            sys.exit(str(e))
        record_only(base, str(launch), a.rate, a.record_only)
        rc = 0
    else:
        rc = fly_loop(base, a.campaign, pack, a.rate, a.run, a.once, yes=a.yes,
                      gains=a.gains, no_gains=a.no_gains, force_livetune=a.force_livetune,
                      post_takeoff_gains=a.post_takeoff_gains, no_gates=a.no_gates)
    events.log("fly_end", rc=rc)
    print("Stop the video. The service window stays open; close it when you are done.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
