"""campaign_fly --record-only: log plan on the slots, record, stop; never a drone command."""

import time

from ground_station.livewatch.campaign_capture import MAX_SLOTS
from ground_station.service import campaign_fly

YAML = """campaign: t-carry
objective: test
controller: pid
mode: fly
packs: [P4000-1]
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
    assert used and all(b["divider"] == 1 for b in used)  # 100 Hz override = every 100 Hz send-task tick
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
    return campaign_fly.wait_for_arm("http://h", "c.yaml", "P4000-1", **kw)


def test_arm_edge_counts_down_then_goes(monkeypatch, capsys):
    rig = Rig(monkeypatch, lambda t: "armed" if t >= 1.0 else "disarmed")
    assert _wait(rig) == "go"
    out = capsys.readouterr().out
    assert rig.t >= 1.0 + campaign_fly.COUNTDOWN_S and "preflight OK" in out
    assert [ln.strip() for ln in out.splitlines() if ln.strip() in ("3", "2", "1")] == ["3", "2", "1"]


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
    assert campaign_fly.remember_pack(" P4000-2 ", f) == "P4000-2"
    assert campaign_fly.remember_pack("", f) == "P4000-2"


def test_unknown_pack_is_refused_before_it_is_remembered(tmp_path, monkeypatch):
    f = tmp_path / "pack.txt"
    monkeypatch.setattr(campaign_fly, "PACK_FILE", f)
    monkeypatch.setattr(campaign_fly, "ensure_service", lambda *a: pytest.fail("no service before the pack check"))
    with pytest.raises(SystemExit) as exc:
        campaign_fly.main(["c", "--pack", "P1"])
    assert "unknown pack 'P1'" in str(exc.value) and "P4000-1" in str(exc.value) and not f.exists()
    with pytest.raises(SystemExit) as exc:
        campaign_fly.main(["c"])
    assert "no pack yet" in str(exc.value) and "P4000-1" in str(exc.value)
    assert campaign_fly.pack_problem("P4000-1") == ""


def test_resolve_campaign_fly_file_name_and_launch_copy(tmp_path):
    kw = {"launch_dir": tmp_path / "launch", "fly_dir": tmp_path / "fly"}
    launch, src = campaign_fly.resolve_campaign(str(SAMPLE), "P4000-1", **kw)
    assert src == SAMPLE and launch.parent == tmp_path / "launch"
    by_name, src2 = campaign_fly.resolve_campaign("preset_tour", "P4000-1", **kw)
    assert src2 == SAMPLE and by_name.parent == tmp_path / "launch"
    assert campaign_fly.resolve_campaign(str(launch), "other", **kw) == (launch, launch)
    with pytest.raises(LaunchError, match="preset_tour"):
        campaign_fly.resolve_campaign("no_such_campaign", "P4000-1", **kw)


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
    assert body["pack_id"] == "P4000-1" and body["source"] == "operator:cli"   # the launch copy pack, not P9
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
