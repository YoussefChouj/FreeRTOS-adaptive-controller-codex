"""campaign_fly --record-only: log plan on the slots, record, stop; never a drone command."""

import json
import time
from pathlib import Path

from ground_station.livewatch.campaign_capture import MAX_SLOTS
from ground_station.service import campaign_fly

YAML = """campaign: t-carry
objective: test
controller: pid
mode: fly
packs: [type2]
max_flights: 1
experiments:
  - name: f01
    scenario: {scenario: hover, steps: [{takeoff: {z: 0.5}}, {hold: {s: 5}}, land]}
    capture: campaign
    repeats: 1
    log_plan: {rate_hz: 50, groups: [estimator_truth, optical_flow]}
abort: {}
"""


def test_record_only_subscribes_records_and_stops(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(YAML, encoding="utf-8")
    calls = []

    def fake_call(base, route, body=None, timeout=10):
        calls.append((route, body))
        if route == "/subscribe":
            return 202, body
        if route == "/api/campaign/vitals":
            return 200, {"streams": {str(k): {"age_s": 0.05} for k in range(MAX_SLOTS)}}
        if route == "/api/recording/start":
            return 202, {"recording": True, "session_dir": "logs/sessions/x_carry"}
        if route == "/api/diagnostics/bundle":   # 1000 frames and 10 dropped on slot 0 while recording
            n = sum(r == route for r, _ in calls) - 1
            return 200, {"transport": {"active_slots": [{"slot": 0, "received": 1000 * n, "dropped": 10 * n}]}}
        return 200, {"recording": False, "session_dir": "logs/sessions/x_carry"}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: None)
    out = campaign_fly.record_only("http://h", str(path), rate_hz=100, label="carry", wait=lambda prompt: "")

    assert out == "logs/sessions/x_carry"
    assert "link slot 0: 1000 frames, 10 dropped (1.0 %)" in capsys.readouterr().out
    routes = [r for r, _ in calls]
    assert routes[-1] == "/api/recording/stop" and "/api/recording/start" in routes
    assert not any(r.startswith("/api/campaign/") and r != "/api/campaign/vitals" for r in routes)
    assert not any(r.startswith("/command") for r in routes)
    subs = [b for r, b in calls if r == "/subscribe"]
    assert sorted(b["slot"] for b in subs) == list(range(MAX_SLOTS))
    used = [b for b in subs if b["divider"]]
    assert used and all(b["divider"] == 2 for b in used)  # 100 Hz override = every 2nd 200 Hz send-task tick
    start = dict(calls)["/api/recording/start"]
    assert start["requested_by"] == "operator" and start["label"] == "carry" and "100 Hz" in start["notes"]


def test_record_only_stops_on_ctrl_c(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(YAML, encoding="utf-8")
    routes = []

    def fake_call(base, route, body=None, timeout=10):
        routes.append(route)
        return (202, {"recording": True, "session_dir": "s"}) if route != "/api/campaign/vitals" else (200, {})

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: None)
    campaign_fly.record_only("http://h", str(path), wait=interrupt)
    assert routes[-1] == "/api/recording/stop"


def test_tel_line_names_states_scales_cm_and_drops_stale(monkeypatch):
    vals = {"g_wfb_status.prim_state": 2, "g_wfb_status.traj_state": 3, "g_wfb_status.airborne_t": 12.34,
            "Ctrler.locxPID.FB": 50.0, "Ctrler.locxPID.Des": 100.0, "Ctrler.Z_posPID.FB": 0.7}
    values = {k: {"value": v, "age_s": 0.1} for k, v in vals.items()}
    values["Ctrler.locyPID.FB"] = {"value": 30.0, "age_s": 5.0}   # stale: left out
    monkeypatch.setattr(campaign_fly, "call", lambda base, route: (200, {"values": values}))
    line = campaign_fly.tel_line("http://h")
    assert line.split() == ["HOVER", "traj", "EXECUTING", "air", "12.3/120s", "x", "+0.50->+1.00", "z", "+0.70"]
    monkeypatch.setattr(campaign_fly, "call", lambda base, route: (200, {"values": {}}))
    assert campaign_fly.tel_line("http://h") == ""


# --- fly: arm = go, live watch, debrief, flight folder ---------------------------------------------------------------

import time
import pytest  # noqa: E402

from ground_station.service.campaign_launch import LaunchError  # noqa: E402
from ground_station.service.tests.test_fly_file import SAMPLE  # noqa: E402


class Rig:
    """Fake clock + 8081: arm(t) -> armed|disarmed|unknown, link(t) -> fresh streams, a preflight verdict."""

    def __init__(self, monkeypatch, arm, link=lambda t: True, pf_ok=True, quit_at=20.0, interrupt_at=()):
        self.t, self.arm, self.link, self.pf_ok, self.quit_at = 0.0, arm, link, pf_ok, quit_at
        self.interrupt_at, self.calls, self.preflights = list(interrupt_at), [], 0
        monkeypatch.setattr(campaign_fly, "_clock", lambda: self.t)
        monkeypatch.setattr(campaign_fly, "_sleep", self.sleep)
        monkeypatch.setattr(campaign_fly, "_key", lambda: "q" if self.t >= self.quit_at else "")
        monkeypatch.setattr(campaign_fly, "call", self.call)

    def sleep(self, s):
        self.t += s
        if self.interrupt_at and self.t >= self.interrupt_at[0]:
            self.interrupt_at.pop(0)
            raise KeyboardInterrupt

    def call(self, base, route, body=None, timeout=10):
        self.calls.append((route, body))
        if route == "/api/campaign/vitals":
            return 200, {"arm": {"state": self.arm(self.t)},
                         "streams": {"0": {"age_s": 0.1 if self.link(self.t) else 99.0}}}
        if route.startswith("/api/campaign/preflight"):
            self.preflights += 1
            return 200, {"ok": self.pf_ok, "checks": [] if self.pf_ok else
                         [{"name": "battery", "value": "3.50 V/cell", "pass": False, "fix": "charge the pack"}]}
        raise AssertionError(route)


def _wait(rig, **kw):
    return campaign_fly.wait_for_arm("http://h", "c.yaml", "type2", **kw)


def test_arm_edge_counts_down_then_goes(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed" if t >= 1.0 else "disarmed")
    assert _wait(rig) == "go"
    out = capsys.readouterr().out
    assert rig.t >= 1.0 + campaign_fly.COUNTDOWN_S and "preflight OK" in out
    assert [ln.strip() for ln in out.splitlines() if ln.strip() in ("3", "2", "1")] == ["3", "2", "1"]


def test_no_arm_signal_is_said_once_and_its_return_too(monkeypatch, capsys):
    # 10-18 mem1: slot 0 died after an axf change, arm stayed "unknown" and fly waited silently forever.
    rig = Rig(monkeypatch, lambda t: "unknown" if t < 8.0 else ("disarmed" if t < 9.0 else "armed"))
    assert _wait(rig) == "go"
    out = capsys.readouterr().out
    assert out.count("NO ARM SIGNAL") == 1 and "arm signal back: disarmed" in out
    assert out.index("NO ARM SIGNAL") < out.index("arm signal back") < out.index("ARMED: takeoff")


def test_armed_at_start_needs_a_disarm_first(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed", quit_at=10.0)
    assert _wait(rig) == "quit"
    assert "ARMED already: disarm by RC" in capsys.readouterr().out and rig.preflights == 0


def test_disarm_cancels_the_countdown_and_a_new_arm_restarts_it(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed" if 1.0 <= t < 2.0 or t >= 3.0 else "disarmed")
    assert _wait(rig) == "go"
    assert rig.t >= 3.0 + campaign_fly.COUNTDOWN_S and "countdown cancelled" in capsys.readouterr().out


def test_ctrl_c_in_the_countdown_cancels_and_holding_the_arm_never_goes(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed" if t >= 1.0 else "disarmed", quit_at=12.0, interrupt_at=[2.0])
    assert _wait(rig) == "quit"
    out = capsys.readouterr().out
    assert "countdown cancelled: disarm by RC" in out and "ARMED already" not in out


def test_failing_preflight_blocks_the_go_and_prints_once(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed" if t >= 5.0 else "disarmed", pf_ok=False, quit_at=12.0)
    assert _wait(rig) == "quit"
    out = capsys.readouterr().out
    assert rig.preflights >= 2 and out.count("preflight FAIL") == 1 and "charge the pack" in out
    assert "ARMED with the preflight failing" in out


def test_link_back_after_silence_asks_for_the_pack(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "unknown" if 1.0 <= t < 6.0 else "disarmed", link=lambda t: not 1.0 <= t < 6.0)
    assert _wait(rig) == "link"
    assert "link lost" in capsys.readouterr().out and 6.0 <= rig.t < 6.5


def test_held_campaign_skips_the_preflight(monkeypatch):
    rig = Rig(monkeypatch, lambda t: "armed" if t >= 1.0 else "disarmed")
    assert _wait(rig, preflight=False) == "go" and rig.preflights == 0


def _sample(t, prim, x, y, z, dx=0.0, dz=0.0, air=0.0):
    return {"t": t, "g_wfb_status.prim_state": prim, "g_wfb_status.airborne_t": air,
            "Ctrler.locxPID.FB": x, "Ctrler.locxPID.Des": x - dx, "Ctrler.locyPID.FB": y, "Ctrler.locyPID.Des": y,
            "Ctrler.Z_posPID.FB": z, "Ctrler.Z_posPID.Des": z - dz}


def test_debrief_lines_rms_max_and_landing_offset():
    samples = [_sample(0.0, 0, 10.0, 0.0, 0.0), _sample(1.0, 2, 10.0, 0.0, 0.52, dx=3.0, dz=0.02, air=1.0),
               _sample(2.0, 2, 10.0, 0.0, 0.52, dx=-3.0, dz=0.02, air=2.0), _sample(3.0, 0, 14.0, -3.0, 0.0, air=3.5)]
    lines = campaign_fly.debrief_lines(samples, {"status": "done", "flights": [{}], "outputs_dir": "logs/o"}, "f/01")
    assert lines == ["done  air 3.5 s  1 flight(s)  4 live samples",
                     "HOVER  x/y/z error cm  rms 3/0/2  max 3/0/2  (1 s)",
                     "TRAJ   no samples",
                     "landing offset x +4 y -3 cm from the takeoff point (5 cm)",
                     "outputs logs/o  flight folder f/01"]


def test_flight_folder_numbers_the_days_flights(tmp_path):
    first = campaign_fly.flight_folder("hover", root=tmp_path, now=0)
    second = campaign_fly.flight_folder("hover", root=tmp_path, now=0)
    assert first.name == "01_hover" and second.name == "02_hover" and first.parent == second.parent


def test_remember_pack(tmp_path):
    f = tmp_path / "pack.txt"
    assert campaign_fly.remember_pack(None, f) == ""
    assert campaign_fly.remember_pack(" type1 ", f) == "type1"
    assert campaign_fly.remember_pack("", f) == "type1"


def test_unknown_pack_is_refused_before_it_is_remembered(tmp_path, monkeypatch):
    f = tmp_path / "pack.txt"
    monkeypatch.setattr(campaign_fly, "PACK_FILE", f)
    monkeypatch.setattr(campaign_fly, "ensure_service", lambda *a: pytest.fail("no service before the pack check"))
    with pytest.raises(SystemExit) as exc:
        campaign_fly.main(["c", "--pack", "P1"])
    assert "unknown pack type 'P1'" in str(exc.value) and "type2" in str(exc.value) and not f.exists()
    with pytest.raises(SystemExit) as exc:
        campaign_fly.main(["c"])
    assert "no pack yet" in str(exc.value) and "--pack 1 or 2" in str(exc.value)
    assert campaign_fly.pack_problem("type2") == ""


def test_resolve_campaign_fly_file_name_and_launch_copy(tmp_path):
    kw = {"launch_dir": tmp_path / "launch", "fly_dir": tmp_path / "fly"}
    launch, src = campaign_fly.resolve_campaign(str(SAMPLE), "type2", **kw)
    assert src == SAMPLE and launch.parent == tmp_path / "launch"
    by_name, src2 = campaign_fly.resolve_campaign("preset_tour", "type2", **kw)
    assert src2.resolve() == SAMPLE.resolve() and by_name.parent == tmp_path / "launch"
    assert campaign_fly.resolve_campaign(str(launch), "other", **kw) == (launch, launch)
    with pytest.raises(LaunchError, match="preset_tour"):
        campaign_fly.resolve_campaign("no_such_campaign", "type2", **kw)


class Flight(Rig):
    """Rig + go, campaign state running for `run_s` after the go, then done; live values; keys pressed in order."""

    def __init__(self, monkeypatch, keys=(), run_s=3.0, **kw):
        super().__init__(monkeypatch, lambda t: "armed" if t >= 1.0 else "disarmed", **kw)
        self.go_t, self.run_s, self.keys = None, run_s, list(keys)
        monkeypatch.setattr(campaign_fly, "_key", lambda: self.keys.pop(0) if self.keys else "")

    def call(self, base, route, body=None, timeout=10):
        if route == "/api/campaign/go":
            self.calls.append((route, body))
            self.go_t = self.t
            return 200, {"ok": True}
        if route == "/api/campaign/state":
            running = self.go_t is not None and self.t - self.go_t < self.run_s
            return 200, {"status": "running" if running else "done", "phase": "HOVER", "outputs_dir": "logs/o"}
        if route.startswith("/api/values"):
            return 200, {"values": {k: {"value": v, "age_s": 0.1} for k, v in
                                    _sample(0, 2, 10.0, 0.0, 0.5).items() if k != "t"}}
        if route in ("/api/campaign/stop", "/api/campaign/next", "/api/campaign/land", "/api/campaign/abort"):
            self.calls.append((route, body))
            return 200, {}
        return super().call(base, route, body, timeout)


def test_watch_keys_post_their_routes_and_ctrl_c_lands_then_aborts(monkeypatch):
    rig = Flight(monkeypatch, keys=["s", "n", "l"], interrupt_at=[1.0, 1.5])
    rig.go_t = 0.0
    st, samples = campaign_fly.watch("http://h")
    assert [r for r, _ in rig.calls] == ["/api/campaign/stop", "/api/campaign/next", "/api/campaign/land",
                                         "/api/campaign/land", "/api/campaign/abort"]
    assert st["status"] == "done" and len(samples) >= 10 and samples[0]["g_wfb_status.prim_state"] == 2


def test_fly_loop_once_goes_on_the_arm_and_files_the_flight(tmp_path, monkeypatch):
    launch = tmp_path / "c.yaml"
    launch.write_text(YAML, encoding="utf-8")
    rig = Flight(monkeypatch)
    monkeypatch.setattr(campaign_fly, "resolve_campaign", lambda arg, pack, rate=None: (launch, launch))
    monkeypatch.setattr(campaign_fly, "FLIGHTS_DIR", tmp_path / "flights")
    assert campaign_fly.fly_loop("http://h", "c", "P9", once=True) == 0
    (_, body), = [c for c in rig.calls if c[0] == "/api/campaign/go"]
    assert rig.go_t >= 1.0 + campaign_fly.COUNTDOWN_S
    assert body["pack_id"] == "type2" and body["source"] == "operator:cli"   # the launch copy pack, not P9
    assert body["confirmation"] == campaign_fly.CONFIRMATION and all(body["checklist"].values())
    folder, = (tmp_path / "flights").glob("*/01_t-carry")
    assert sorted(p.name for p in folder.iterdir()) == ["c.yaml", "debrief.txt", "live_5hz.csv", "outputs.txt"]
    assert "HOVER  x/y/z error cm  rms 0/0/0" in (folder / "debrief.txt").read_text(encoding="utf-8")


# --- fly: VOFA+ FLIGHT_VIEW import files, PlotJuggler `p` key ---------------------------------------------------------

def test_vofa_setup_forwards_the_view_and_writes_imports_in_forward_order(tmp_path, monkeypatch):
    from ground_station.vofa_studio import vofa_config
    want = vofa_config.view_channels()["vars"]
    sent = []

    def call(base, route, body=None, timeout=10):
        sent.append((route, body))
        return 200, {"active": True, "addr": "127.0.0.1:1347", "channels": want[:-1], "sent": 0}
    monkeypatch.setattr(campaign_fly, "call", call)
    lines = campaign_fly.vofa_setup("http://h", out_dir=tmp_path)
    assert sent == [("/api/streams/forward", {"enable": True, "channels": want})]
    assert lines[0] == f"VOFA+ FireWater UDP 127.0.0.1:1347, {len(want) - 1} channels (1 not streamed)"
    assert str(tmp_path / "flight_view.tabviews.json") in lines[1] and str(tmp_path / "flight_names.csv") in lines[2]
    assert len(lines) == 3   # same channel order as the forward
    header = (tmp_path / "flight_names.csv").read_text(encoding="utf-8").splitlines()[0]
    assert len(header.split(",")) == len(want) - 1


def test_vofa_setup_refused_is_one_line(monkeypatch):
    monkeypatch.setattr(campaign_fly, "call", lambda *a, **k: (400, {"error": "none of the channels is on a slot"}))
    assert campaign_fly.vofa_setup("http://h") == ["VOFA+: forward refused (400): none of the channels is on a slot"]


def test_p_key_streams_then_opens_plotjuggler_once_on_a_layout(tmp_path, monkeypatch):
    exe = tmp_path / "plotjuggler.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("PJ_BIN", str(exe))
    monkeypatch.setattr(campaign_fly, "call", lambda base, route, body=None, timeout=10: (
        200, {"active": True, "addr": "127.0.0.1:9870", "vars": ["imu_data.rol", "Ctrler.locxPID.FB"]}))
    running, opened = [False], []
    monkeypatch.setattr(campaign_fly, "_pj_running", lambda: running[0])
    line = campaign_fly.plotjuggler("http://h", out_dir=tmp_path, popen=lambda args, **k: opened.append(args))
    assert line == "PlotJuggler: opening on 2 streamed variables, UDP Server 127.0.0.1:9870"
    (args,) = opened
    assert args[:3] == [str(exe), "-l", str(tmp_path / "plotjuggler_live.xml")] and "UDP Server" in args
    assert "/imu_data/rol" in (tmp_path / "plotjuggler_live.xml").read_text(encoding="utf-8")
    running[0] = True
    assert campaign_fly.plotjuggler("http://h", out_dir=tmp_path, popen=opened.append).startswith(
        "PlotJuggler: already open") and len(opened) == 1


def test_p_key_without_plotjuggler_installed_still_streams(monkeypatch):
    monkeypatch.delenv("PJ_BIN", raising=False)
    monkeypatch.setattr(campaign_fly, "PJ_BINS", ("no_such_plotjuggler_bin",))
    monkeypatch.setattr(campaign_fly, "_pj_running", lambda: False)
    monkeypatch.setattr(campaign_fly, "call", lambda *a, **k: (200, {"addr": "127.0.0.1:9870", "vars": []}))
    assert campaign_fly.plotjuggler("http://h") == (
        "PlotJuggler: streaming to 127.0.0.1:9870, but no plotjuggler.exe found (set PJ_BIN)")


def test_camera_landing_reads_the_running_fusion_session(tmp_path):
    hdr = "t_send,t_cap,pad_x_m,pad_y_m,earth_x_cm,earth_y_cm,frame_hex\n"
    live, done = tmp_path / "r8_f1", tmp_path / "r8_f0"
    for d in (live, done):
        d.mkdir()
        (d / "fusion").write_text("2\n")
    (live / "fixes.csv").write_text(hdr + "".join(f"1.0,1.0,{0.5 - i * 0.01:.2f},0.05,0,0,00\n" for i in range(40))
                                    + "2.0,2.0,0.10,-0.04,0,0,00\n" * 15)
    (done / "fixes.csv").write_text(hdr + "1.0,1.0,0.9,0.9,0,0,00\n")
    (done / "stop").write_text("")
    x, y, name, n = campaign_fly.camera_landing(tmp_path)
    assert (round(x), round(y), name, n) == (10, -4, "r8_f1", 15)
    assert campaign_fly.camera_landing(tmp_path, now=time.time() + 60) is None   # stale fixes: no running session
    lines = campaign_fly.debrief_lines([], {"status": "done"}, "f/01", cam=(10.0, -4.0, "r8_f1", 15))
    assert "camera landing x +10 y -4 cm from the pad (11 cm, r8_f1, median of 15 fixes)" in lines


def test_watch_says_once_what_to_do_when_the_service_dies(monkeypatch):
    """2026-10-14: 8081 died mid-session and watch printed a bare 'state unreachable' every second."""
    class Down(Flight):
        def call(self, base, route, body=None, timeout=10):
            if 1.0 <= self.t < 14.0:
                raise ConnectionRefusedError("refused")
            return super().call(base, route, body, timeout)
    class T:
        events = []
        def event(self, s): self.events.append(s)
        def line(self, s): pass
        def close(self): pass
    rig = Down(monkeypatch, run_s=20.0)
    rig.go_t = 0.0
    term = T()
    campaign_fly.watch("http://h", term=term)
    down = [e for e in term.events if "SERVICE DOWN" in e]
    assert len(down) == 1 and "RC" in down[0] and "run fly again" in down[0]
    assert sum("still down" in e for e in term.events) == 1          # one reminder per 10 s, not one per poll
    assert any(e.startswith("service back after 13 s") for e in term.events)


def test_watch_ctrl_c_leaves_while_the_service_is_down(monkeypatch):
    """2026-10-14 review: with 8081 down, Ctrl+C only re-posted land/abort to the dead service, so closing the window
    was the only way out and the debrief and fly_end were lost."""
    class Down(Flight):
        def call(self, base, route, body=None, timeout=10):
            if self.t >= 1.0:
                raise ConnectionRefusedError("refused")
            return super().call(base, route, body, timeout)
    class T:
        events = []
        def event(self, s): self.events.append(s)
        def line(self, s): pass
        def close(self): pass
    rig = Down(monkeypatch, run_s=20.0)
    rig.go_t = 0.0
    def key():
        if rig.t >= 5.0:
            raise KeyboardInterrupt
    term = T()
    st, _ = campaign_fly.watch("http://h", term=term, key=key)
    assert st == {"status": "service_down"} and rig.t < 7.0
    assert any("leaving fly" in e for e in term.events) and not any("FAILED" in e for e in term.events)


def test_write_gains_refuses_when_not_disarmed(monkeypatch, capsys):
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "armed"}})
    ok = campaign_fly.write_gains("http://h", "c2")
    assert ok is False
    out = capsys.readouterr().out
    assert "REFUSED: drone is not disarmed" in out


def test_write_gains_operator_cancellation(monkeypatch, capsys):
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    ok = campaign_fly.write_gains("http://h", "c2", ask_fn=lambda p: "n")
    assert ok is False
    out = capsys.readouterr().out
    assert "Gain write cancelled." in out


def test_write_gains_success_and_table(monkeypatch, capsys):
    vitals_data = {
        "arm": {"state": "disarmed"},
        "g_wfb_status.yaw_kp": 2.0,
        "g_wfb_status.yaw_ki": 0.0,
        "g_wfb_status.yaw_kd": 0.0,
    }
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: vitals_data)
    posted = []

    def fake_call(base, route, body=None, timeout=10):
        posted.append((route, body))
        return 200, {"ok": True, "command_id": 0x01, "status": "applied"}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    test_gains = {0: 2.5, 1: 0.1}
    ok = campaign_fly.write_gains("http://h", test_gains, why="unit test", yes=True)
    assert ok is True
    out = capsys.readouterr().out
    assert "Why: unit test" in out
    assert "| Axis" in out and "| idx" in out and "| Old" in out and "| New" in out and "| ACK" in out
    assert "2/2 ACK" in out
    cmd_posts = [p for p in posted if p[0] == "/commands"]
    assert len(cmd_posts) == 2


def test_write_gains_defaults_preset(monkeypatch, capsys):
    vitals_data = {"arm": {"state": "disarmed"}}
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: vitals_data)
    posted = []

    def fake_call(base, route, body=None, timeout=10):
        posted.append((route, body))
        return 200, {"ok": True, "command_id": 0x01, "status": "applied"}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    ok = campaign_fly.write_gains("http://h", "defaults", yes=True)
    assert ok is True
    out = capsys.readouterr().out
    assert "18/18 ACK" in out
    cmd_posts = [p for p in posted if p[0] == "/commands"]
    assert len(cmd_posts) == 18
    # rate loops 9-14 are reset too (F4 livetune writes them and RAM gains persist)
    assert sorted(b["index"] for _, b in cmd_posts) == [*range(9, 15), *range(24, 36)]


def test_debrief_lines_names_the_abort():
    # 10-18 mem1: a takeoff abort printed as a plain "done" and read as "it only hovered"
    st = {"status": "done", "flights": [{"experiment": "lab_m_memory_u", "abort_level": 1,
                                          "abort_reason": "position_error"}]}
    lines = campaign_fly.debrief_lines([], st, "f/01")
    assert lines[1] == ("ABORTED L1 lab_m_memory_u: position_error (the safety monitor landed it; "
                        "the steps after that point never ran)")
    assert not any("ABORTED" in ln for ln in campaign_fly.debrief_lines([], {"status": "done", "flights": [{}]}, "f/01"))


def test_debrief_lines_never_airborne():
    # Friction B14: air <= 0 or max_z < 0.25 -> "landing offset: no landing (never airborne)"
    samples_air_zero = [_sample(0.0, 0, 10.0, 0.0, 0.0, air=0.0)]
    lines = campaign_fly.debrief_lines(samples_air_zero, {"status": "done"}, "f/01")
    assert "landing offset: no landing (never airborne)" in lines

    # Even if air > 0, if max_z < 0.25, report never airborne
    samples_low_z = [_sample(0.0, 0, 10.0, 0.0, 0.1, air=1.0)]
    lines2 = campaign_fly.debrief_lines(samples_low_z, {"status": "done"}, "f/01")
    assert "landing offset: no landing (never airborne)" in lines2


def test_quiet_preflight_waits_for_position_streaming(monkeypatch, capsys):
    # Fix 4: first poll fails ONLY on position not streaming -> retries up to 1.5s
    attempts = [0]

    def fake_call(base, route, body=None, timeout=10):
        attempts[0] += 1
        if attempts[0] == 1:
            return 200, {
                "ok": False,
                "checks": [{"name": "position", "value": "position not streaming", "pass": False}],
            }
        return 200, {"ok": True, "checks": [{"name": "position", "value": "streaming at 50 Hz", "pass": True}]}

    slept = []
    monkeypatch.setattr(campaign_fly, "call", fake_call)
    monkeypatch.setattr(campaign_fly, "_sleep", lambda s: slept.append(s))
    events_logged = []
    monkeypatch.setattr(campaign_fly.events, "log", lambda ev, **kw: events_logged.append((ev, kw)))

    ok, fails = campaign_fly.quiet_preflight("http://h", "test_camp", "type2", shown=None)
    assert ok is True
    assert fails == ()
    assert slept == [0.5]
    assert attempts[0] == 2
    assert "preflight OK: arm by RC to fly" in capsys.readouterr().out


def test_upload_preload(tmp_path, monkeypatch):
    class DummyRunner:
        called = False
        def upload_preload(self):
            self.called = True
            return True

    r = DummyRunner()
    assert campaign_fly.upload_preload("http://h", runner=r) is True
    assert r.called is True

    # Empty launch path returns True
    assert campaign_fly.upload_preload("http://h", launch_path=None) is True

    # Campaign yaml without upload steps returns True
    c_yaml = tmp_path / "c_no_upload.yaml"
    c_yaml.write_text(YAML, encoding="utf-8")
    assert campaign_fly.upload_preload("http://h", launch_path=str(c_yaml)) is True

    # Campaign with goto upload step executes upload
    from unittest.mock import Mock
    c_traj_yaml = tmp_path / "c_traj.yaml"
    traj_text = """campaign: t-goto
objective: test
controller: pid
mode: fly
packs: [type2]
max_flights: 1
experiments:
  - name: f01
    scenario:
      scenario: circle
      steps:
        - takeoff: {z: 0.5}
        - goto: {x: 1.0, y: 0.5, z: 0.5}
        - land
    capture: campaign
    repeats: 1
abort: {}
"""
    c_traj_yaml.write_text(traj_text, encoding="utf-8")
    res_obj = Mock(ok=True, error=None)
    monkeypatch.setattr("ground_station.platform.trajectory_upload.upload", lambda pts, client: res_obj)
    monkeypatch.setattr(campaign_fly, "HttpWfbClient", lambda base: Mock())
    assert campaign_fly.upload_preload("http://h", launch_path=str(c_traj_yaml)) is True


def test_extract_fly_directives(tmp_path):
    fly = tmp_path / "test.fly"
    fly.write_text("# gains: c3\n# why: test robust gains\ncampaign test\ntakeoff 0.5\nland\n", encoding="utf-8")
    preset, why = campaign_fly.extract_fly_directives(fly)
    assert preset == "c3"
    assert why == "test robust gains"

    fly2 = tmp_path / "test2.fly"
    fly2.write_text("# gains: DEFAULTS\ncampaign test2\n", encoding="utf-8")
    preset2, why2 = campaign_fly.extract_fly_directives(fly2)
    assert preset2 == "defaults"
    assert why2 is None

    fly3 = tmp_path / "test3.fly"
    fly3.write_text("campaign test3\n", encoding="utf-8")
    preset3, why3 = campaign_fly.extract_fly_directives(fly3)
    assert preset3 is None
    assert why3 is None


def test_fly_loop_directive_and_no_gains(tmp_path, monkeypatch):
    launch = tmp_path / "c.yaml"
    launch.write_text(YAML, encoding="utf-8")
    fly_src = tmp_path / "c.fly"
    fly_src.write_text("# gains: c3\n# why: robust\ncampaign t-carry\n", encoding="utf-8")

    monkeypatch.setattr(campaign_fly, "resolve_campaign", lambda arg, pack, rate=None: (launch, fly_src))
    monkeypatch.setattr(campaign_fly, "upload_preload", lambda base, path: True)
    monkeypatch.setattr(campaign_fly, "wait_for_arm", lambda *args, **kwargs: "quit")

    gains_written = []

    def fake_write_gains(base, preset, why=None, yes=False, force_livetune=False, livetune_path=None):
        gains_written.append((preset, why))
        return True

    monkeypatch.setattr(campaign_fly, "write_gains", fake_write_gains)

    # 1. Automatic directive gains
    assert campaign_fly.fly_loop("http://h", "c", "P9", once=True, yes=True) == 0
    assert len(gains_written) == 1
    assert gains_written[0] == ("c3", "robust")

    # 2. --no-gains skips directive
    gains_written.clear()
    assert campaign_fly.fly_loop("http://h", "c", "P9", once=True, yes=True, no_gains=True) == 0
    assert len(gains_written) == 0

    # 3. CLI --gains overrides directive
    gains_written.clear()
    assert campaign_fly.fly_loop("http://h", "c", "P9", once=True, yes=True, gains="c2") == 0
    assert len(gains_written) == 1
    assert gains_written[0][0] == "c2"


def test_livetune_best_save_merge_and_load(tmp_path):
    best_file = tmp_path / "livetune_best.json"

    # F2: rate summary
    rate_summary = {
        "status": "budget",
        "loop": "rate",
        "baseline": {"Kp": 5.0, "Ki": 0.01, "Kd": 10.0},
        "baseline_J": {"roll": 1.25, "pitch": 1.15},
        "best": {"Kp": 4.8, "Ki": 0.009, "Kd": 9.2},
        "best_rel": 0.82,
        "recommended": {"Kp": 4.8, "Ki": 0.009, "Kd": 9.2},
        "candidates": 8,
        "generations": 2,
        "evals": [{"gains": {"Kp": 4.8, "Ki": 0.009, "Kd": 9.2}, "J": 0.984}],
    }
    campaign_fly.save_livetune_result(rate_summary, path=best_file)

    # Verify rate is saved
    data = json.loads(best_file.read_text(encoding="utf-8"))
    assert "rate" in data
    assert data["rate"]["best"]["Kp"] == 4.8
    assert data["rate"]["best_j"] == 0.984
    assert data["rate"]["base_j"] == 1.2

    # F3: pos summary
    pos_summary = {
        "status": "budget",
        "loop": "pos",
        "baseline": {"out.Kp": 0.8, "out.Kd": 4.0, "in.Kp": 3.0, "in.Kd": 6.0},
        "baseline_J": {"x": 0.50, "y": 0.50},
        "best": {"out.Kp": 0.72, "out.Kd": 4.4, "in.Kp": 3.1, "in.Kd": 5.7},
        "best_rel": 0.78,
        "recommended": {"out.Kp": 0.72, "out.Kd": 4.4, "in.Kp": 3.1, "in.Kd": 5.7},
        "candidates": 6,
        "generations": 1,
        "evals": [{"gains": {"out.Kp": 0.72, "out.Kd": 4.4, "in.Kp": 3.1, "in.Kd": 5.7}, "J": 0.39}],
    }
    campaign_fly.save_livetune_result(pos_summary, path=best_file)

    # Verify merged JSON contains both rate and pos
    merged = json.loads(best_file.read_text(encoding="utf-8"))
    assert "rate" in merged and "pos" in merged

    # Test load_livetune_gains
    gains, why, err = campaign_fly.load_livetune_gains(path=best_file)
    assert err is None
    assert "rate J 0.984 vs base 1.2" in why
    assert "pos J 0.39 vs base 0.5" in why

    # Check gain indices:
    # Rate: 9-11 (gyrox), 12-14 (gyroy)
    assert gains[9] == 4.8 and gains[10] == 0.009 and gains[11] == 9.2
    assert gains[12] == 4.8 and gains[13] == 0.009 and gains[14] == 9.2
    # Pos: outer Kp/Kd (24, 26, 27, 29), inner Kp/Kd (30, 32, 33, 35), Ki defaults (25, 28, 31, 34)
    assert gains[24] == 0.72 and gains[26] == 4.4
    assert gains[27] == 0.72 and gains[29] == 4.4
    assert gains[30] == 3.1 and gains[32] == 5.7
    assert gains[33] == 3.1 and gains[35] == 5.7
    assert gains[25] == campaign_fly.PID_DEFAULTS[25][1]
    assert gains[31] == campaign_fly.PID_DEFAULTS[31][1]
    assert len(gains) == 18


def test_write_gains_livetune_preset_and_force(tmp_path, monkeypatch, capsys):
    best_file = tmp_path / "livetune_best.json"
    unrec_data = {
        "rate": {
            "loop": "rate",
            "best": {"Kp": 5.1, "Ki": 0.01, "Kd": 10.2},
            "best_j": 1.15,
            "base_j": 1.20,
            "best_rel": 0.96,
            "recommended": None,  # Did not beat by 5%
        }
    }
    best_file.write_text(json.dumps(unrec_data), encoding="utf-8")

    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    posted = []

    def fake_call(base, route, body=None, timeout=10):
        posted.append((route, body))
        return 200, {"ok": True, "command_id": 0x01, "status": "applied"}

    monkeypatch.setattr(campaign_fly, "call", fake_call)

    # Without force: unrecommended gains are refused
    ok = campaign_fly.write_gains("http://h", "livetune", yes=True, livetune_path=best_file)
    assert ok is False
    out = capsys.readouterr().out
    assert "neither rate nor pos was recommended" in out

    # With force: unrecommended gains are accepted
    ok_force = campaign_fly.write_gains("http://h", "livetune", yes=True, force_livetune=True, livetune_path=best_file)
    assert ok_force is True
    out_force = capsys.readouterr().out
    assert "6/6 ACK" in out_force


def test_write_gains_waits_for_the_final_outcome(monkeypatch, capsys):
    """ACK then APPLIED passes; REJECTED fails at once with no retry; ACK-only passes with a restart-8081 note.
    No /api/diagnostics/bundle: it keeps only the last 10 results, fewer than one 12-gain write produces."""
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    t = [0.0]
    monkeypatch.setattr(campaign_fly, "_clock", lambda: t[0])
    monkeypatch.setattr(campaign_fly, "_sleep", lambda s: t.__setitem__(0, t[0] + s))
    lifecycles, sent, routes = [], [], []

    def fake_call(base, route, body=None, timeout=10):
        routes.append(route)
        if route == "/commands":
            sent.append(body["index"])
            return 202, {"transaction_id": 42}
        if route == "/api/actions":
            lc = lifecycles.pop(0) if len(lifecycles) > 1 else lifecycles[0]
            return 200, {"actions": [{"transaction_id": 42, "lifecycle": lc}]}
        return 200, {}

    monkeypatch.setattr(campaign_fly, "call", fake_call)

    lifecycles[:] = ["submitted", "acknowledged", "applied"]
    assert campaign_fly.write_gains("http://h", {0: 3.5}, yes=True) is True
    assert "1/1 ACK (applied, 8081 journal)" in capsys.readouterr().out
    assert "/api/diagnostics/bundle" not in routes

    sent.clear()
    lifecycles[:] = ["acknowledged", "rejected"]
    assert campaign_fly.write_gains("http://h", {0: 3.5}, yes=True) is False
    assert sent == [0]  # a rejected value is not re-sent
    assert "Gain write ABORTED" in capsys.readouterr().out

    lifecycles[:] = ["acknowledged"]
    assert campaign_fly.write_gains("http://h", {0: 3.5}, yes=True) is True
    out = capsys.readouterr().out
    assert "1/1 ACK" in out and "restart 8081" in out


def test_check_and_persist_livetune(tmp_path):
    target_path = tmp_path / "livetune_best.json"
    st = {
        "status": "done",
        "flights": [
            {
                "steps": [
                    {
                        "kind": "livetune",
                        "livetune": {
                            "loop": "rate",
                            "baseline": {"Kp": 5.0, "Ki": 0.01, "Kd": 10.0},
                            "baseline_J": {"roll": 1.1},
                            "best": {"Kp": 4.9, "Ki": 0.009, "Kd": 9.8},
                            "best_rel": 0.85,
                            "recommended": {"Kp": 4.9, "Ki": 0.009, "Kd": 9.8},
                        },
                    }
                ]
            }
        ],
    }
    saved = campaign_fly.check_and_persist_livetune(st, path=target_path)
    assert len(saved) == 1
    assert saved[0]["loop"] == "rate"
    assert target_path.is_file()
    data = json.loads(target_path.read_text(encoding="utf-8"))
    assert "rate" in data
    assert data["rate"]["best"]["Kp"] == 4.9



def test_check_and_persist_livetune_saves_a_step_once_when_also_in_metrics(tmp_path):
    lt = {"loop": "pos", "baseline": {"out.Kp": 0.8}, "best": {"out.Kp": 0.7}, "best_rel": 0.9}
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    flights = [{"steps": [{"kind": "livetune", "livetune": lt}]}]
    (out_dir / "metrics.json").write_text(json.dumps({"flights": flights}), encoding="utf-8")
    st = {"status": "done", "flights": flights, "outputs_dir": str(out_dir)}
    saved = campaign_fly.check_and_persist_livetune(st, path=tmp_path / "livetune_best.json")
    assert [e["loop"] for e in saved] == ["pos"]

def test_list_gains(capsys, tmp_path):
    rc = campaign_fly.main(["--list-gains"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== Preset: c3 ===" in out
    assert "=== Preset: c2 ===" in out
    assert "=== Preset: defaults ===" in out
    assert "=== Preset: livetune ===" in out
    assert "| Axis           | idx  | Default  | Target   |" in out
    assert "0.3214" in out
    assert "0.67" in out

    # Test with livetune_best.json present
    lt_json = tmp_path / "livetune_best.json"
    lt_json.write_text(json.dumps({
        "rate": {
            "recommended": {"Kp": 4.5, "Ki": 0.015, "Kd": 9.0},
            "best": {"Kp": 4.5, "Ki": 0.015, "Kd": 9.0},
            "best_j": 0.08,
            "base_j": 0.12,
        }
    }), encoding="utf-8")
    campaign_fly.list_gain_presets(livetune_path=lt_json)
    out2 = capsys.readouterr().out
    assert "4.5" in out2
    assert "rate J 0.08 vs base 0.12 (-33.3%)" in out2


def test_dry_run_scenario(capsys):
    rc = campaign_fly.main(["lab_c_robust", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== DRY RUN: lab-c-robust ===" in out
    assert "Ground gain preset: c3" in out
    assert "| Axis           | idx  | Default  | Target   |" in out
    assert "Preload step: goto" in out
    assert "CRC=0x848956AB" in out
    assert "< 2 s for 4-point goto" in out
    assert "Scenario Plan" in out
    assert "takeoff" in out
    assert "land" in out


def test_dry_run_post_takeoff_gains(capsys):
    rc = campaign_fly.main(["lab_c_robust", "--dry-run", "--gains", "defaults", "--post-takeoff-gains", "c3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Ground gain preset: defaults" in out
    assert "Post-takeoff gain preset: c3 (applied after takeoff in HOVER)" in out
    assert "0.3214" in out


def test_extract_fly_directives_ignores_prose_mentions(tmp_path):
    fly = tmp_path / "prose.fly"
    fly.write_text("# C: same track; ground gains: c3 were too soft\n# note why: drift\n"
                   "  #GAINS : C2  extra\n# post-takeoff-gains: c3\ncampaign t\n", encoding="utf-8")
    res = campaign_fly.extract_fly_directives(fly)
    assert (res[0], res[1], res.post_takeoff) == ("c2", None, "c3")


def test_lab_c_lqr_and_mpc_reset_pid_gains_first():
    # F5/F6 fly after F4 wrote livetune gains into RAM: both must restore defaults on the ground
    for name in ("lab_c_lqr", "lab_c_mpc"):
        fly = Path(campaign_fly.__file__).parent / "campaigns" / f"{name}.fly"
        assert campaign_fly.extract_fly_directives(fly)[0] == "defaults"


def test_livetune_best_path_is_repo_relative():
    assert campaign_fly.LIVETUNE_BEST_PATH == campaign_fly.ROOT / "logs" / "campaigns" / "livetune_best.json"


def test_extract_fly_directives_post_takeoff(tmp_path):
    fly = tmp_path / "post_test.fly"
    fly.write_text("# gains: defaults\n# post_takeoff_gains: c3\n# why: climb on defaults then switch\ncampaign test\ntakeoff 0.5\nland\n", encoding="utf-8")
    res = campaign_fly.extract_fly_directives(fly)
    # 2-tuple unpacking backward compatibility
    preset, why = res
    assert preset == "defaults"
    assert why == "climb on defaults then switch"
    # post_takeoff attribute
    assert res.post_takeoff == "c3"


def test_write_gains_in_flight(capsys, monkeypatch):
    # Drone is armed: normal write_gains should REFUSE, in_flight should PASS without prompt
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "armed"}})
    txn_status = "applied"

    def fake_call(base, route, body=None, timeout=10):
        if route == "/commands":
            return 200, {"ok": True, "transaction_id": 99}
        if route == "/api/actions":
            return 200, {"actions": [{"transaction_id": 99, "lifecycle": "applied"}]}
        if route == "/api/diagnostics/bundle":
            return 200, {"commands": {"results": [{"transaction_id": 99, "status": txn_status}]}}
        return 200, {}

    monkeypatch.setattr(campaign_fly, "call", fake_call)

    # Normal write while armed is refused
    assert campaign_fly.write_gains("http://h", {24: 0.3214}) is False
    assert "Gain write REFUSED: drone is not disarmed" in capsys.readouterr().out

    # In-flight write while armed succeeds
    assert campaign_fly.write_gains("http://h", {24: 0.3214}, in_flight=True) is True
    out = capsys.readouterr().out
    assert "[In-flight]" in out
    assert "1/1 ACK (applied, 8081 journal)" in out


def _fake_service(monkeypatch, stale, safe):
    # ensure_service with a fake 8081: down after the kill, up once a new one is started
    log = {"kill": [], "popen": []}
    monkeypatch.setattr(campaign_fly, "_service_stale", lambda base: stale)
    monkeypatch.setattr(campaign_fly, "_safe_to_restart", lambda base: safe)
    monkeypatch.setattr(campaign_fly.os, "kill", lambda pid, sig: log["kill"].append(pid))
    monkeypatch.setattr(campaign_fly.subprocess, "Popen", lambda args, **k: log["popen"].append(args))
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: None)
    monkeypatch.setattr(campaign_fly.events, "log", lambda *a, **k: None)
    monkeypatch.setattr(campaign_fly, "_stream_logs", lambda: [])

    def call(base, path, timeout=None):
        if log["kill"] and not log["popen"]:
            raise OSError("down")
        return 200, {}
    monkeypatch.setattr(campaign_fly, "call", call)
    return log


def test_ensure_service_restarts_stale_code_when_safe(monkeypatch, capsys):
    # 10-18: the takeoff-limit change sat unused in a running 8081 until a hand restart
    newest = campaign_fly.ROOT / "ground_station" / "service" / "abort_monitor.py"
    log = _fake_service(monkeypatch, (4242, newest), True)
    campaign_fly.ensure_service("http://x", 8081, lab=True)
    assert log["kill"] == [4242]
    assert log["popen"] and log["popen"][0][-3:] == ["--port", "8081", "--lab"]
    assert "restarting it" in capsys.readouterr().out


def test_ensure_service_never_restarts_while_flying(monkeypatch, capsys):
    newest = campaign_fly.ROOT / "ground_station" / "service" / "abort_monitor.py"
    log = _fake_service(monkeypatch, (4242, newest), False)
    campaign_fly.ensure_service("http://x", 8081)
    assert log == {"kill": [], "popen": []}
    assert "kept it" in capsys.readouterr().out
    log = _fake_service(monkeypatch, None, True)
    campaign_fly.ensure_service("http://x", 8081)
    assert log == {"kill": [], "popen": []}


@pytest.mark.parametrize("runner,arm,link,ok", [
    ({"pass": True, "value": "idle"}, True, True, True),
    ({"pass": True, "value": "done: 3 flights"}, True, True, True),
    ({"pass": False, "value": "running: f/01"}, True, True, False),
    ({"pass": True, "value": "paused: hovering"}, True, True, False),
    ({"pass": True, "value": "idle"}, False, True, False),   # armed, or arm state unknown with the link up
    ({"pass": True, "value": "idle"}, False, False, True),   # no link: nothing to cut
])
def test_safe_to_restart(monkeypatch, runner, arm, link, ok):
    pf = {"checks": [dict(runner, name="runner"), {"name": "arm_state", "pass": arm}, {"name": "link", "pass": link}]}
    monkeypatch.setattr(campaign_fly, "call", lambda base, path, timeout=None: (200, pf))
    assert campaign_fly._safe_to_restart("http://x") is ok


def test_service_stale_reads_the_instance_header(monkeypatch):
    class Resp:
        def __init__(self, h):
            self.headers = h

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    for header, stale in [("77-0", True), (f"77-{10**15}", False), (None, False)]:
        monkeypatch.setattr(campaign_fly.urllib.request, "urlopen",
                            lambda url, timeout=None, h=header: Resp({"X-GS-Instance": h} if h else {}))
        got = campaign_fly._service_stale("http://x")
        assert (got is not None) is stale
        if stale:
            assert got[0] == 77 and "tests" not in got[1].parts


def test_ensure_service_ends_leftover_stream_log_first(monkeypatch, tmp_path, capsys):
    # 10-18: a camera session's stream_log kept udp 14550/47800, the next 8081 died on WinError 10013
    log = _fake_service(monkeypatch, None, True)
    alive = [777]
    monkeypatch.setattr(campaign_fly, "ROOT", tmp_path)
    monkeypatch.setattr(campaign_fly, "_stream_logs", lambda: list(alive))
    monkeypatch.setattr(campaign_fly.os, "kill", lambda pid, sig: (log["kill"].append(pid), alive.clear()))
    reaped = []
    monkeypatch.setattr(campaign_fly.events, "log", lambda ev, **k: reaped.append((ev, k)))
    campaign_fly.ensure_service("http://x", 8081)
    assert log["kill"] == [777] and ("stream_log_reaped", {"pids": [777], "killed": [777]}) in reaped
    assert not (tmp_path / "logs" / "plot_live.end").exists()
    assert "leftover live logger" in capsys.readouterr().out
    alive[:] = [778]                                  # one that obeys the stop file is never killed
    log["kill"].clear()
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: alive.clear())
    campaign_fly.ensure_service("http://x", 8081)
    assert log["kill"] == []
def test_watch_warns_when_post_takeoff_gains_fail(monkeypatch):
    rig = Flight(monkeypatch)
    rig.go_t = 0.0
    monkeypatch.setattr(campaign_fly, "write_gains", lambda *a, **k: False)
    said = []

    class T:
        event = staticmethod(said.append)
        line = staticmethod(lambda s: None)
        close = staticmethod(lambda: None)
    campaign_fly.watch("http://h", term=T(), post_takeoff_gains="c3")
    assert any(s.startswith("POST-TAKEOFF GAINS FAILED (c3)") for s in said)


# --- 10-19 exp-gains: late ACK grace, exp_id on the ground, preset figure-8 as TRAJ, abort in the debrief event ------

def _journal(monkeypatch, lifecycle):
    """Fake clock + /api/actions answering lifecycle(t) for transaction 7; returns the clock cell."""
    t = [0.0]
    monkeypatch.setattr(campaign_fly, "_clock", lambda: t[0])
    monkeypatch.setattr(campaign_fly, "_sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(campaign_fly, "call", lambda base, route, body=None, timeout=10:
                        (200, {"actions": [{"transaction_id": 7, "lifecycle": lifecycle(t[0])}]}))
    return t


def test_wait_txn_late_ack_waits_the_full_grace_for_applied(monkeypatch):
    """10-19 1017-F1: ACK 0.1 s before the 2 s deadline, APPLIED 1.6 s later: 'applied', not 'acknowledged'."""
    t = _journal(monkeypatch, lambda t: "submitted" if t < 1.9 else "acknowledged" if t < 3.5 else "applied")
    assert campaign_fly.wait_txn("http://h", 7, timeout_s=2.0) == "applied" and t[0] >= 3.5
    t = _journal(monkeypatch, lambda t: "submitted" if t < 0.5 else "acknowledged")
    assert campaign_fly.wait_txn("http://h", 7, timeout_s=2.0) == "acknowledged"
    assert 0.5 + campaign_fly.ACK_GRACE_S <= t[0] < 0.6 + campaign_fly.ACK_GRACE_S   # grace counts from the ACK
    t = _journal(monkeypatch, lambda t: "submitted")
    assert campaign_fly.wait_txn("http://h", 7, timeout_s=2.0) == "timeout" and t[0] < 2.1


EXP_YAML = YAML.replace("max_flights: 1", "max_flights: 3").replace(
    "{takeoff: {z: 0.5}}", "{takeoff: {z: 0.5, set: {exp_id: 4}}}").replace("repeats: 1", "repeats: 2").replace(
    "abort: {}\n", """\
  - name: f02
    scenario: {scenario: hover, steps: [{takeoff: {z: 0.5, set: {exp_id: 5}}}, {hold: {s: 5}}, land]}
    capture: campaign
    repeats: 1
abort: {}
""")


def test_ground_exp_id_follows_the_runner_queue(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(EXP_YAML, encoding="utf-8")
    assert [campaign_fly.ground_exp_id(path, i) for i in range(4)] == [4.0, 4.0, 5.0, 4.0]   # f01 x2, f02, wrap
    plain = tmp_path / "p.yaml"
    plain.write_text(YAML, encoding="utf-8")
    assert campaign_fly.ground_exp_id(plain) is None


def test_preapply_exp_id_writes_var_write_on_the_ground_and_never_255(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(EXP_YAML, encoding="utf-8")
    sent, logged = [], []
    monkeypatch.setattr(campaign_fly.events, "log", lambda ev, **kw: logged.append((ev, kw)))

    def fake_call(base, route, body=None, timeout=10):
        if route == "/commands":
            sent.append(body)
            return 202, {"transaction_id": 9}
        assert route == "/api/actions", route
        return 200, {"actions": [{"transaction_id": 9, "lifecycle": "applied"}]}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    assert campaign_fly.preapply_exp_id("http://h", path, flight=2) == 5.0
    assert sent == [{"command_id": 45, "index": 50, "value": 5.0}]   # VAR_WRITE row exp_id
    assert logged == [("exp_preapply", {"exp_id": 5, "ok": True})]
    assert "exp_id 5 written on the ground before the arm." in capsys.readouterr().out
    plain = tmp_path / "p.yaml"
    plain.write_text(YAML, encoding="utf-8")
    assert campaign_fly.preapply_exp_id("http://h", plain) is None and len(sent) == 1
    with pytest.raises(Exception):   # the firmware refuses 255 (row 0-7): no post-flight reset can exist
        campaign_fly.HttpWfbClient("http://h").var_write("exp_id", 255)
    assert all(b["value"] != 255 for b in sent)


def test_fly_loop_preapplies_exp_id_before_the_arm_wait(tmp_path, monkeypatch):
    launch = tmp_path / "c.yaml"
    launch.write_text(EXP_YAML, encoding="utf-8")
    rig = Flight(monkeypatch)
    seen = []
    monkeypatch.setattr(campaign_fly, "resolve_campaign", lambda arg, pack, rate=None: (launch, launch))
    monkeypatch.setattr(campaign_fly, "FLIGHTS_DIR", tmp_path / "flights")
    monkeypatch.setattr(campaign_fly, "preapply_exp_id",
                        lambda base, path, flight=0, client=None: seen.append((path, flight, len(rig.calls))))
    assert campaign_fly.fly_loop("http://h", "c", "type2", once=True) == 0
    assert seen == [(str(launch), 0, 0)]   # before the first /api/campaign/vitals of wait_for_arm


def _figure8(lag=3.0):
    """IDLE, 1 s HOVER at the start point, 2 s with the x/y target moving 5 cm/s (a Keil preset: prim stays HOVER),
    1 s HOVER at the end, IDLE: 5 Hz like the live samples."""
    out = [_sample(0.0, 0, 0.0, 0.0, 0.0)]
    for k in range(1, 21):
        t = k * 0.2
        des = 0.0 if t <= 1.0 else min(t - 1.0, 2.0) * 5.0
        moving = 1.0 < t <= 3.0
        s = _sample(t, 2, des + (lag if moving else 0.0), 0.0, 0.5, dx=lag if moving else 0.0, air=t)
        out.append(s)
    out.append(_sample(4.2, 0, 10.0, 0.0, 0.0, air=4.2))
    return out


def test_debrief_lines_counts_a_preset_figure8_as_traj():
    lines = campaign_fly.debrief_lines(_figure8(), {"status": "done", "flights": [{}]}, "f/01")
    hover, traj = lines[1], lines[2]
    assert hover.startswith("HOVER  x/y/z error cm  rms 0/0/0") and traj.startswith("TRAJ   x/y/z error cm  rms 3/0/0")
    assert campaign_fly._phases(_figure8())[:6] == ["IDLE"] + ["HOVER"] * 5   # the CLIMB->HOVER target step is not TRAJ
    step = [_sample(k * 0.2, 2, 0.0, 0.0, 0.5, dx=3.0 if k >= 8 else 0.0) for k in range(20)]
    assert set(campaign_fly._phases(step)) == {"HOVER"}   # a 3 cm hold step (15 cm/s for one sample) is not one


def test_fly_one_debrief_event_says_the_abort_and_air_time(tmp_path, monkeypatch):
    class Aborted(Flight):
        def call(self, base, route, body=None, timeout=10):
            code, res = super().call(base, route, body, timeout)
            if route == "/api/campaign/state":
                res["flights"] = [{"abort_reason": "pos_err 0.62 m > 0.50 m", "abort_level": 2}]
            return code, res

    rig = Aborted(monkeypatch)
    rig.t = 2.0
    logged = []
    monkeypatch.setattr(campaign_fly.events, "log", lambda ev, **kw: logged.append((ev, kw)))
    monkeypatch.setattr(campaign_fly, "FLIGHTS_DIR", tmp_path / "flights")
    monkeypatch.setattr(campaign_fly, "camera_landing", lambda: None)
    launch = tmp_path / "c.yaml"
    launch.write_text(YAML, encoding="utf-8")
    campaign_fly.fly_one("http://h", launch, launch, "t-carry", "type2")
    (kw,) = [kw for ev, kw in logged if ev == "debrief"]
    assert kw["aborted"] is True and kw["abort_reason"] == "pos_err 0.62 m > 0.50 m" and kw["air_s"] == 0.0


def test_http_client_mrac_prior_posts_the_axis_id_and_refuses_locally(monkeypatch):
    from ground_station.platform import mrac_prior
    sent = []

    def fake_call(base, route, body=None, timeout=10):
        if route == "/commands":
            sent.append(body)
            return 202, {"transaction_id": 4}
        return 200, {"actions": [{"transaction_id": 4, "lifecycle": "applied"}]}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    client = campaign_fly.HttpWfbClient("http://h")
    assert client.mrac_prior("z", 3, -0.5) is True
    assert sent == [{"command_id": 0x32, "index": 3, "value": -0.5}]   # 0x2F + MRAC_AXIS_Z
    with pytest.raises(mrac_prior.PriorError):
        client.mrac_prior("x", mrac_prior.IDX_SIGMA, -1.0)
    assert len(sent) == 1
