"""labday player: the day file, the run sheet, resume order, analysis stops and auto stops (no drone, no camera)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ground_station.lab import labday
from ground_station.service import campaign_fly

PNG = "coral_scenery_0-90-120-12-255-255.png"   # live_tracker.floor_scenery's name for the coral thresholds


@pytest.fixture(autouse=True)
def _events(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_EVENTS_DIR", str(tmp_path / "events"))


def _day(tmp_path, flights, branches=None, camera=None):
    p = tmp_path / "day.yaml"
    p.write_text(json.dumps({"day": "2099-01-01", "title": "t", "camera": camera, "flights": flights,
                             "branches": branches or []}), encoding="utf-8")
    return labday.load_day(p)


def test_today_loads_and_every_fly_file_exists():
    d = labday.load_day(labday.day_path("2026-10-17"))
    ids = [f["id"] for f in d["flights"]]
    assert ids == ["F1", "E1", "E2", "E3", "E4", "H0", "H1", "H2", "F2", "F3", "F4", "F5", "F6"]
    assert [f["id"] for f in d["flights"] if f.get("stop")] == ["F1", "E4", "H2", "F3"]
    assert d["camera"]["mode"] == 3 and d["camera"]["args"] == ["--yaw-deg", "94"]
    assert "15.8" not in labday.day_path("2026-10-17").read_text(encoding="utf-8")


def test_load_rejects_bad_files(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        _day(tmp_path, [{"id": "A", "fly": "lab_c_robust"}, {"id": "A", "fly": "lab_c_lqr"}])
    with pytest.raises(ValueError, match="no ground_station"):
        _day(tmp_path, [{"id": "A", "fly": "no_such_flight"}])
    with pytest.raises(ValueError, match="needs an id"):
        _day(tmp_path, [{"fly": "lab_c_robust"}])


def test_sheet_has_every_flight_and_branch():
    d = labday.load_day(labday.day_path("2026-10-17"))
    md = labday.sheet(d)
    for f in d["flights"]:
        assert f"| {f['id']} | `.\\fly {f['fly']} --once" in md
    assert md.count("| yes |") == 4
    assert "## Branches & fallbacks" in md and "0xEE" in md
    assert "## Before the first flight" in md and "rebuild_and_flash.py --yes" in md


def test_todo_resumes_after_good_and_skipped(tmp_path):
    d = _day(tmp_path, [{"id": i, "fly": "lab_c_robust"} for i in "ABCD"])
    st = {"flown": {"A": {"ok": True}, "B": {"ok": False}}, "skipped": ["C"]}
    assert [f["id"] for f in labday.todo(d, st)] == ["B", "D"]
    assert [f["id"] for f in labday.todo(d, st, "C")] == ["C", "D"]
    with pytest.raises(ValueError):
        labday.todo(d, st, "Z")


def test_bad_end():
    assert labday.bad_end(1, {"status": "complete"}) == "fly exited 1"
    assert "no debrief" in labday.bad_end(0, None)
    assert labday.bad_end(0, {"status": "aborted", "phase": "fence"}) == "ended aborted in fence"
    assert labday.bad_end(0, {"status": "complete"}) is None


def test_bad_end_abort_or_no_air_is_bad_although_complete(tmp_path, monkeypatch):
    """2026-10-19: every aborted flight and the four refused takeoffs ended `complete` and were marked OK."""
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    assert labday.bad_end(0, {"status": "complete", "aborted": True}) == "aborted: safety monitor"
    assert labday.bad_end(0, {"status": "complete", "abort_reason": "position_error"}) == "aborted: position_error"
    assert labday.bad_end(0, {"status": "complete", "air_s": 0.0}) == "never airborne (0 s air)"
    assert labday.bad_end(0, {"status": "complete", "air_s": 12.5}) is None
    for name, text in (("ab", "complete  air 53.8 s  1 flight(s)  270 live samples\n"
                              "ABORTED L1 f01_lab_m_memory_u0: position_error (the safety monitor landed it; the "
                              "steps after that point never ran)\n"),
                       ("na", "complete  air 0.0 s  1 flight(s)  120 live samples\n"
                              "landing offset: no landing (never airborne)\n"),
                       ("ok", "complete  air 20.0 s  1 flight(s)  10 live samples\n"
                              "landing offset x +3 y +4 cm from the takeoff point (5 cm)\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "debrief.txt").write_text(text, encoding="utf-8")
    assert labday.bad_end(0, {"status": "complete", "folder": "ab"}) == \
        "ABORTED L1 f01_lab_m_memory_u0: position_error"
    assert labday.bad_end(0, {"status": "complete", "folder": "na"}) == "never airborne (0 s air)"
    assert labday.bad_end(0, {"status": "complete", "folder": "ok"}) is None
    assert labday.bad_end(0, {"status": "complete", "folder": "missing"}) is None


def test_debrief_event_reads_only_the_child_after_offset(tmp_path):
    p = tmp_path / "e.jsonl"
    old = json.dumps({"event": "debrief", "pid": 7, "status": "old"}) + "\n"
    p.write_text(old + json.dumps({"event": "debrief", "pid": 8, "status": "other"}) + "\n"
                 + json.dumps({"event": "debrief", "pid": 7, "status": "complete"}) + "\n", encoding="utf-8")
    assert labday.debrief_event(7, {p: len(old)})["status"] == "complete"
    assert labday.debrief_event(9, {p: 0}) is None


def _scan(folder, seen, n=10):
    """What camera_truth.sh floor leaves: aruco.npz with the pad (id0) in `seen` of `n` frames, and the scenery mask
    PNG only when the pad was in half of them (it is made after "floor OK"). Its exit code."""
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True)
    c0 = np.full((n, 4, 2), np.nan)
    c0[:seen] = [[100, 200], [140, 200], [140, 240], [100, 240]]
    np.savez(folder / "aruco.npz", t=np.arange(n) / 30, c0=c0)
    if 2 * seen >= n and seen:
        (folder / PNG).write_bytes(b"png")
        return 0
    return 1


class _Fake:
    """Stands in for subprocess.run / Popen, input and the drone's vitals: records the commands, prompts, scripted
    answers, exits, floor scans (pad frames out of 10) and battery volts (None = link down; the last one repeats)."""

    def __init__(self, answers, fly_rc=(), dry_rc=0, scans=(), volts=(16.0,)):
        self.answers, self.fly_rc, self.dry_rc, self.cmds = list(answers), list(fly_rc), dry_rc, []
        self.scans, self.volts, self.prompts, self.floors, self.now = list(scans), list(volts), [], [], 0.0
        self.service_fail = 0

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[1:3] == [labday.CAMERA_SH, "fusion"]:
            self.floors.append(kw["env"]["CT_FLOOR"])
        if cmd[1:3] == [labday.CAMERA_SH, "floor"]:
            return SimpleNamespace(returncode=_scan(labday.VIDEO_DIR / cmd[3], self.scans.pop(0) if self.scans else 10))
        return SimpleNamespace(returncode=self.dry_rc if "--dry-run" in cmd else 0, stdout="boom", stderr="")

    def vitals(self):
        v = self.volts.pop(0) if len(self.volts) > 1 else self.volts[0]
        return {} if v is None else {"battery": {"value": v}, "streams": {"0": {"age_s": 0.1}}}

    def sleep(self, s):
        self.now += s

    def service(self):
        self.cmds.append(["8081"])
        if self.service_fail:
            self.service_fail -= 1
            raise SystemExit("service did not answer on 8081 in 60 s: check its window")

    def popen(self, cmd, **kw):
        self.cmds.append(cmd)
        if "--plot-folder" in cmd:
            rc = self.plot_rc.pop(0) if getattr(self, "plot_rc", None) else 0
            return SimpleNamespace(pid=4243, wait=lambda timeout=None: rc, stderr="", stdout="")
        if "--pack" in cmd:   # fly remembers the pack it was given
            campaign_fly.remember_pack(campaign_fly.pack_id(cmd[cmd.index("--pack") + 1]))
        rc = self.fly_rc.pop(0) if self.fly_rc else 0
        return SimpleNamespace(pid=4242, wait=lambda timeout=None: rc)


def _player(tmp_path, monkeypatch, fake, flights, status="complete", camera=None, pack="2"):
    d = _day(tmp_path, flights, branches=[{"when": "w", "do": "d"}], camera=camera)
    monkeypatch.setattr(labday, "debrief_event", lambda pid, since: status and {"status": status, "phase": "land",
                                                                              "folder": "f"})
    monkeypatch.setattr(labday, "git_bash", lambda: "bash")
    monkeypatch.setattr(labday, "code_stamp", lambda: "test")
    monkeypatch.setattr(labday, "VIDEO_DIR", tmp_path / "vt")
    monkeypatch.setattr(campaign_fly, "PACK_FILE", tmp_path / "last_pack.txt")
    return labday.Day(d, tmp_path / "state.json", pack=pack, ask=fake.ask, run=fake.run, popen=fake.popen,
                      service=fake.service, vitals=fake.vitals, clock=lambda: fake.now, sleep=fake.sleep)


def _flown(fake):
    return [c[c.index("ground_station.service.campaign_fly") + 1] for c in fake.cmds if "--once" in c]


def _sh(fake):
    return [c[2:] for c in fake.cmds if c[:2] == ["bash", labday.CAMERA_SH]]


def test_flies_in_order_with_pack_once_and_analysis_stop(tmp_path, monkeypatch):
    fake = _Fake(["", "", "", "q"])     # fly A, fly B, B's analysis stop: go on, quit at C
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"},
                                              {"id": "B", "fly": "lab_c_lqr", "stop": True},
                                              {"id": "C", "fly": "lab_c_mpc"}])
    assert p.go() == 0
    assert _flown(fake) == ["lab_c_robust", "lab_c_lqr"]
    once = [c for c in fake.cmds if "--once" in c]
    assert "--pack" in once[0] and "--pack" not in once[1]
    assert sum("--dry-run" in c for c in fake.cmds) == 3 and not fake.answers
    st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert st["flown"]["A"]["ok"] and st["flown"]["B"]["folder"] == "f"
    again = _Fake(["q"])
    p2 = _player(tmp_path, monkeypatch, again, [{"id": "A", "fly": "lab_c_robust"},
                                                {"id": "B", "fly": "lab_c_lqr", "stop": True},
                                                {"id": "C", "fly": "lab_c_mpc"}])
    assert [f["id"] for f in labday.todo(p2.d, p2.state)] == ["C"]


def test_cooldown_between_flights_not_after_the_last(tmp_path, monkeypatch):
    fake = _Fake(["", "", ""])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"},
                                              {"id": "C", "fly": "lab_c_mpc"}])
    waits = []
    p.cooldown_s, p.wait = 60, lambda s, battery: waits.append((s, battery()))
    assert p.go() == 0 and _flown(fake) == ["lab_c_robust", "lab_c_lqr", "lab_c_mpc"]
    assert len(waits) == 2 and all(55 < w <= 60 and v == 16.0 for w, v in waits)   # live volts on the line
    off = _Fake(["", ""])
    (tmp_path / "off").mkdir()
    p = _player(tmp_path / "off", monkeypatch, off, [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}])
    waits.clear()
    p.wait = lambda s, battery: waits.append(s)
    assert p.go() == 0 and not waits      # cooldown_s 0 = off


def test_cooldown_line_shows_the_battery(capsys):
    now = [0.0]
    labday.cooldown(3, key=lambda: False, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + 1),
                    flush=lambda: None, battery=lambda: 15.87)
    assert "cooldown 0:03 before the next flight, battery 15.87 V" in capsys.readouterr().out


def test_cooldown_counts_down_and_enter_ends_it(capsys):
    now = [0.0]
    keys = iter([False, False, True])
    waited = labday.cooldown(10, key=lambda: next(keys), clock=lambda: now[0],
                             sleep=lambda s: now.__setitem__(0, now[0] + 1), flush=lambda: None)
    assert waited == 2 and "cooldown 0:10" in capsys.readouterr().out
    now[0] = 0.0
    assert labday.cooldown(3, key=lambda: False, clock=lambda: now[0],
                           sleep=lambda s: now.__setitem__(0, now[0] + 1), flush=lambda: None) == 3


def test_cooldown_line_fits_a_narrow_terminal(capsys, monkeypatch):
    monkeypatch.setattr(labday.shutil, "get_terminal_size", lambda fallback: SimpleNamespace(columns=40, lines=20))
    now = [0.0]
    labday.cooldown(3, key=lambda: False, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + 1),
                    flush=lambda: None)
    rows = capsys.readouterr().out.split("\r")[1:]
    assert len(rows) == 4 and all(len(r.rstrip("\n")) == 39 for r in rows)   # 3 ticks + "over", never wraps
    assert rows[0].startswith("  cooldown 0:03") and rows[-1].startswith("  cooldown over.")


def test_dry_run_reruns_only_what_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(labday, "CAMPAIGNS", tmp_path)
    (tmp_path / "a.fly").write_text("x", encoding="utf-8")
    (tmp_path / "b.fly").write_text("y", encoding="utf-8")
    fake = _Fake([])
    flights = [{"id": "A", "fly": "a"}, {"id": "B", "fly": "b"}]
    p = _player(tmp_path, monkeypatch, fake, flights)
    stamp = ["c1"]
    monkeypatch.setattr(labday, "code_stamp", lambda: stamp[0])
    left = labday.todo(p.d, p.state)

    def dry():
        return sum("--dry-run" in c for c in fake.cmds)

    assert p.dry_run(left) == [] and dry() == 2
    assert p.dry_run(left) == [] and dry() == 2                       # unchanged: nothing re-run
    again = _player(tmp_path, monkeypatch, fake, flights)            # a new labday start reads the saved passes
    monkeypatch.setattr(labday, "code_stamp", lambda: stamp[0])
    assert again.dry_run(labday.todo(again.d, again.state)) == [] and dry() == 2
    (tmp_path / "b.fly").write_text("y2", encoding="utf-8")
    assert p.dry_run(left) == [] and dry() == 3                       # only B's file changed
    stamp[0] = "c2"
    assert p.dry_run(left) == [] and dry() == 5                       # new host code: every flight again
    fake.dry_rc, stamp[0] = 2, "c3"
    assert p.dry_run(left) == ["A", "B"] and p.state["dry_ok"] == {}
    fake.dry_rc = 0
    assert p.dry_run(left) == [] and dry() == 9                       # a failure is never remembered as a pass
    stamp[0] = None
    p.dry_run(left)
    p.dry_run(left)
    assert dry() == 13                                                # host code unknown: never skipped


def test_code_stamp_ignores_commits_outside_ground_station(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True,
                       capture_output=True)

    (tmp_path / "ground_station").mkdir()
    (tmp_path / "ground_station" / "m.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("1\n", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "c1")
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    s1 = labday.code_stamp()
    (tmp_path / "notes.md").write_text("2\n", encoding="utf-8")
    git("commit", "-qam", "docs only")
    assert labday.code_stamp() == s1 is not None                     # docs/state/firmware commit: passes kept
    (tmp_path / "ground_station" / "m.py").write_text("b\n", encoding="utf-8")
    s2 = labday.code_stamp()
    assert s2 != s1                                                  # uncommitted host change: re-run
    git("commit", "-qam", "host")
    assert labday.code_stamp() not in (s1, s2)                       # committed host change: re-run


def test_auto_stop_on_bad_end_then_refly(tmp_path, monkeypatch):
    fake = _Fake(["", "r"], fly_rc=[1, 0])   # fly A: exits 1 -> r flies again -> ok
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}])
    assert p.go() == 0 and not fake.answers
    assert _flown(fake) == ["lab_c_robust", "lab_c_robust"]
    assert p.state["flown"]["A"]["ok"]


def test_auto_stop_quit_keeps_flight_to_do(tmp_path, monkeypatch):
    fake = _Fake(["", "q"])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], status="aborted")
    assert p.go() == 0
    assert p.state["flown"]["A"]["why"] == "ended aborted in land"
    assert [f["id"] for f in labday.todo(p.d, p.state)] == ["A"]


def test_dry_run_failure_flies_nothing(tmp_path, monkeypatch):
    fake = _Fake([], dry_rc=2)
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}])
    assert p.go() == 1 and not _flown(fake)


CAM = {"floor": "fl", "mode": 3, "args": ["--yaw-deg", 94]}


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_camera_override_records_and_restores_day_mode(tmp_path, monkeypatch, mode):
    fake = _Fake([])
    p = _player(
        tmp_path, monkeypatch, fake, [{"id": "sysid", "fly": "lab_d_sysid_rate_pitch", "camera_mode": mode},
                                    {"id": "verify", "fly": "lab_a_hover_verify"}], camera=CAM)
    assert p.camera_start("sysid", p.d["flights"][0]) is True
    assert p.camera_start("verify", p.d["flights"][1]) is True
    assert _sh(fake) == [["fusion", "sysid", str(mode), "--yaw-deg", "94"],
                         ["fusion", "verify", "3", "--yaw-deg", "94"]]
    assert p.cam["mode"] == 3


@pytest.mark.parametrize("mode", [-1, 4, "0", False])
def test_camera_override_rejects_invalid_mode(tmp_path, mode):
    with pytest.raises(ValueError, match="camera_mode"):
        _day(tmp_path, [{"id": "sysid", "fly": "lab_d_sysid_rate_pitch", "camera_mode": mode}])


def test_oct27_sysid_camera_off_other_flights_keep_mode3():
    d = labday.load_day(labday.day_path("2026-10-27"))
    assert d["camera"]["mode"] == 3
    sysid = [f for f in d["flights"] if "-sysid-" in f["id"]]
    assert len(sysid) == 8
    assert all(f["camera_mode"] == 0 for f in sysid)
    assert all(f.get("camera_mode", 3) == 3 for f in d["flights"] if f not in sysid)


def test_oct27_restart_roll_repeats_sequence_even_when_previously_flown():
    d = labday.load_day(labday.day_path("2026-10-27"))
    state = {"flown": {f["id"]: {"ok": True} for f in d["flights"]}, "skipped": []}
    queue = labday.todo(d, state, "rate-sysid-roll-wide")
    assert [f["id"] for f in queue[:4]] == [
        "rate-sysid-roll-wide", "rate-sysid-pitch-wide", "rate-sysid-yaw-wide", "rate-gain-verify-hover"]
    assert [f["camera_mode"] for f in queue[:3]] == [0, 0, 0]
    assert queue[2]["tune"]["from"] == [f["id"] for f in queue[:3]]


def test_camera_around_each_flight_after_the_launch_floor_scan(tmp_path, monkeypatch):
    fake = _Fake(["q"])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=CAM)
    yaml_text = (tmp_path / "day.yaml").read_text(encoding="utf-8")
    assert p.go() == 0 and not _sh(fake) and not _flown(fake)       # q at the scan prompt: nothing flies
    assert "k keeps" not in fake.prompts[0]                         # day file's "fl" was never scanned
    assert fake.prompts[0].startswith("Take the drone out of the camera view, Enter scans the floor")
    fake = _Fake(["", ""])                                          # scan, fly A
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=CAM)
    assert p.go() == 0
    assert _sh(fake) == [["floor", "floor-2099-01-01"], ["fusion", "a-lab-c-robust", "3", "--yaw-deg", "94"],
                         ["stop", "a-lab-c-robust"]]
    assert (tmp_path / "vt" / "a-lab-c-robust" / "stop").is_file()
    assert fake.floors == ["logs/video_truth/floor-2099-01-01"]
    assert p.state["floor"] == "floor-2099-01-01" and p.state["floors"] == ["floor-2099-01-01"]
    assert (tmp_path / "day.yaml").read_text(encoding="utf-8") == yaml_text   # the day yaml stays as written
    # (dry run passed in the first launch) 8081 (before the scan: the battery lines read its vitals), scan,
    # 8081 (before the camera: lab 10-18), fusion, then the flight
    assert [c[0] if c == ["8081"] else c[2] if c[:2] == ["bash", labday.CAMERA_SH] else "--once" in c and "fly"
            for c in fake.cmds[:5]] == ["8081", "floor", "8081", "fusion", "fly"]


def test_fly_ctrl_c_keeps_waiting_for_child(tmp_path, monkeypatch):
    fake = _Fake([])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "smoke-hover", "fly": "lab_c_robust"}])
    calls = []
    logged = []

    def wait(timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr(p, "popen", lambda *a, **kw: SimpleNamespace(pid=4242, wait=wait))
    monkeypatch.setattr(labday, "log_offsets", lambda: {})
    monkeypatch.setattr(labday.events, "log", lambda event, **kw: logged.append((event, kw)))
    rc, ev = p.fly(p.d["flights"][0], None)
    assert rc == 0 and ev["status"] == "complete"
    assert len(calls) == 2 and all(timeout is not None for timeout in calls)
    assert [event for event, _ in logged] == ["labday_ctrl_c"]
    assert logged[0][1]["id"] == "smoke-hover"


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError])
def test_flight_exception_stops_camera_exactly_once(tmp_path, monkeypatch, error):
    fake = _Fake([])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "smoke-hover", "fly": "lab_c_robust"}])
    stopped = []
    monkeypatch.setattr(p, "service_up", lambda: True)
    monkeypatch.setattr(p, "camera_start", lambda name, flight=None: True)
    monkeypatch.setattr(p, "camera_stop", stopped.append)

    def fly(*args):
        raise error

    monkeypatch.setattr(p, "fly", fly)
    with pytest.raises(error):
        p.one(p.d["flights"][0], None)
    assert stopped == ["smoke-hover-lab-c-robust"]


def test_floor_check_reads_what_the_scan_left(tmp_path):
    assert _scan(tmp_path / "ok", 8) == 0
    ok, text = labday.floor_check(tmp_path / "ok")
    assert ok and text == "pad (id0) at pixel (120, 220), found in 8 of 10 frames"
    assert _scan(tmp_path / "few", 3) == 1
    assert labday.floor_check(tmp_path / "few") == (False, f"no scenery mask {labday.rel(tmp_path / 'few' / PNG)}: "
                                                           "the scan did not finish (see its output above)")
    (tmp_path / "few" / PNG).write_bytes(b"png")       # mask there but the pad too rarely seen
    assert labday.floor_check(tmp_path / "few") == (False, "landing pad (marker id0) found in 3 of 10 frames, "
                                                           "needs half")
    (tmp_path / "none").mkdir()
    (tmp_path / "none" / PNG).write_bytes(b"png")
    assert not labday.floor_check(tmp_path / "none")[0]      # no aruco.npz


def test_failed_floor_scan_says_why_and_scans_again_then_k_keeps(tmp_path, monkeypatch, capsys):
    fake = _Fake(["", "", "q"], scans=[2, 10])       # scan: pad 2 of 10 -> FAILED, scan again -> OK, quit at A
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=CAM)
    assert p.go() == 0 and not _flown(fake)
    out = capsys.readouterr().out
    assert ("floor scan floor-2099-01-01 FAILED (exit code 1): no scenery mask" in out
            and "floor scan floor-2099-01-01-2 OK: pad (id0) at pixel (120, 220), found in 10 of 10 frames" in out)
    assert p.state["floor"] == "floor-2099-01-01-2"
    again = _Fake(["k", "q"])                         # next launch: k keeps the last good scan, no camera run
    p = _player(tmp_path, monkeypatch, again, [{"id": "A", "fly": "lab_c_robust"}], camera=CAM)
    assert p.go() == 0 and not _sh(again)
    assert "k keeps floor-2099-01-01-2" in again.prompts[0]


def test_f_rescans_at_the_flight_prompt_and_at_auto_stop(tmp_path, monkeypatch, capsys):
    # launch scan, f at A's prompt -> scan -2, fly A (exits 1), f at AUTO STOP -> scan -3, flies A again: ok
    fake = _Fake(["", "f", "", "", "f", ""], fly_rc=[1, 0])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=CAM)
    assert p.go() == 0 and not fake.answers
    assert [c[1] for c in _sh(fake) if c[0] == "floor"] == ["floor-2099-01-01", "floor-2099-01-01-2",
                                                            "floor-2099-01-01-3"]
    assert _flown(fake) == ["lab_c_robust", "lab_c_robust"] and p.state["flown"]["A"]["ok"]
    assert fake.floors == ["logs/video_truth/floor-2099-01-01-2", "logs/video_truth/floor-2099-01-01-3"]
    assert ", f rescans the floor, q quits" in fake.prompts[1]
    assert "f rescans the floor and flies it again" in fake.prompts[4]
    assert "AUTO STOP: A" in capsys.readouterr().out


def test_battery_lines_before_and_after_each_flight(tmp_path, monkeypatch, capsys):
    fake = _Fake(["", "q"], volts=[16.0, 15.6, 16.4])   # rest A, after A, rest B
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}])
    assert p.go() == 0 and _flown(fake) == ["lab_c_robust"]
    out = capsys.readouterr().out
    assert "  battery 16.00 V resting, pack type2: 78% now, 67.5% after the flight (gate 30%): ok" in out
    assert "  battery after A: 15.60 V just after landing, sag 0.40 V from 16.00 V resting" in out
    assert "  battery 16.40 V resting, pack type2: 88% now, 78.3% after the flight (gate 30%): ok" in out   # fly's pack


def test_battery_without_link_or_pack(tmp_path, monkeypatch, capsys):
    fake = _Fake(["q"], volts=[None])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], pack=None)
    assert p.go() == 0
    assert "  battery: no voltage from the drone (the preflight checks the link)" in capsys.readouterr().out
    assert labday.battery_gate("", 16.4)[2] == ("  battery 16.40 V resting; no pack type yet, SoC not checked "
                                                "(fly asks)")
    assert labday.battery_gate("zz", 16.4)[:2] == (True, "")


def test_low_pack_swaps_mid_day_and_goes_on(tmp_path, monkeypatch, capsys):
    # A rests 16.0 and lands 15.6; B rests 15.0: SWAP; the link drops (None) and comes back 16.4; pack 1; B flies
    fake = _Fake(["", "1", ""], volts=[16.0, 15.6, 15.0, 15.0, None, None, 16.4, 16.4, 16.0])
    flights = [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}]
    p = _player(tmp_path, monkeypatch, fake, flights)
    assert p.go() == 0 and _flown(fake) == ["lab_c_robust", "lab_c_lqr"] and not fake.answers
    out = capsys.readouterr().out
    assert ("SWAP THE PACK: SOC: predicted post-flight SoC 15.0% < required 30.0% (measured resting SoC 25.0%, "
            "expected drop 10.0%)") in out
    assert "  link dropped: waiting for the drone to come back" in out
    assert "  battery 16.40 V resting, pack type1" in out
    once = [c for c in fake.cmds if "--once" in c]
    assert once[1][once[1].index("--pack") + 1] == "type1" and campaign_fly.remember_pack() == "type1"
    assert "Enter = type2" in fake.prompts[1]
    # same run: 8081 never restarted by hand, the dry-run cache untouched (its key is the launch pack)
    assert p.pack == "2" and sum("--dry-run" in c for c in fake.cmds) == 2
    assert p.state["dry_ok"]["B"] == labday.dry_key(p.d["flights"][1], "2", "test")


def test_swap_waits_for_the_link_or_quits(tmp_path, monkeypatch):
    fake = _Fake(["q"], volts=[15.0])                  # never powered off: the drop times out, q quits
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}])
    assert p.go() == 0 and not _flown(fake)
    assert fake.prompts == ["No link drop in 90 s: Enter waits again, q quits: "] and fake.now >= 90
    fake = _Fake(["q"], volts=[15.0, 15.0, None, 16.4])   # q at the pack type
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}])
    assert p.go() == 0 and not _flown(fake) and fake.prompts[0].startswith("Pack type now in the drone")


def test_8081_failure_asks_again_or_quits(tmp_path, monkeypatch):
    fake = _Fake(["", "q"])                         # 8081 before the first prompt: retry once, then quit
    fake.service_fail = 2
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}])
    assert p.go() == 0
    assert fake.cmds.count(["8081"]) == 2 and not [c for c in fake.cmds if "--once" in c]


def test_fly_cmd_is_campaign_fly():
    f = {"fly": "lab_c_robust", "args": ["--gains", "c2"]}
    assert labday.fly_cmd(f, "1", dry=True)[1:] == ["-m", "ground_station.service.campaign_fly", "lab_c_robust",
                                                    "--dry-run", "--pack", "1", "--gains", "c2"]
    assert subprocess.list2cmdline(labday.fly_cmd(f)).endswith("lab_c_robust --once --gains c2")


def _days(tmp_path, monkeypatch, days, states=None):
    """Day files <date>.yaml in tmp_path (date -> flights) and their labday states; returns the last day loaded."""
    monkeypatch.setattr(labday, "LAB_DIR", tmp_path / "lab")
    for day, flights in days.items():
        (tmp_path / f"{day}.yaml").write_text(json.dumps({"day": day, "flights": flights, "branches": [
            {"when": f"w{day[-1]}", "do": "d"}]}), encoding="utf-8")
    for day, st in (states or {}).items():
        labday.save_state(labday.state_file(day), {"flown": {}, "skipped": [], **st})
    return labday.load_day(tmp_path / f"{max(days)}.yaml")


def test_carry_over_appends_unflown_flights_of_earlier_days(tmp_path, monkeypatch):
    d = _days(tmp_path, monkeypatch, {
        "2099-01-01": [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}],
        "2099-01-02": [{"id": "C", "fly": "lab_c_mpc"}, {"id": "B2", "fly": "lab_c_lqr"}],
        "2099-01-03": [{"id": "D", "fly": "lab_c_autotune"}]},
        {"2099-01-01": {"flown": {"A": {"ok": True}}}})
    assert [(f["id"], f.get("from")) for f in d["flights"]] == [
        ("D", None), ("0102-C", "2099-01-02"), ("0102-B2", "2099-01-02")]   # 01 A flown, 01 B planned again on 02
    assert d["carry_note"] == ["2099-01-01: 0 carried, 1 planned again on a later day", "2099-01-02: 2 carried"]
    assert [b["when"] for b in d["branches"]] == ["w3", "(2099-01-02) w2"]
    assert "Carried over from earlier days" in labday.sheet(d)


def test_carry_over_done_on_a_later_day_or_today(tmp_path, monkeypatch):
    days = {"2099-01-01": [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}],
            "2099-01-02": [{"id": "C", "fly": "lab_c_mpc"}],
            "2099-01-03": [{"id": "D", "fly": "lab_c_autotune"}]}
    d = _days(tmp_path, monkeypatch, days, {"2099-01-02": {"flown": {"0101-A": {"ok": True}}, "skipped": ["0101-B"]},
                                            "2099-01-03": {"flown": {"0102-C": {"ok": True, "t": "t"}}}})
    assert [f["id"] for f in d["flights"]] == ["D", "0102-C"]           # today's own state: listed, not to do
    assert [f["id"] for f in labday.todo(d, labday.load_state(labday.state_file("2099-01-03")))] == ["D"]
    flown_bad = _days(tmp_path, monkeypatch, days, {"2099-01-02": {"flown": {"0101-A": {"ok": False}}}})
    assert [f["id"] for f in flown_bad["flights"]][:2] == ["D", "0101-A"]


def test_carry_off_and_bad_earlier_day(tmp_path, monkeypatch):
    monkeypatch.setattr(labday, "LAB_DIR", tmp_path / "lab")
    (tmp_path / "2099-01-01.yaml").write_text(json.dumps({"day": "2099-01-01", "flights": [
        {"id": "A", "fly": "no_such_flight"}]}), encoding="utf-8")
    (tmp_path / "2099-01-02.yaml").write_text(json.dumps({"day": "2099-01-02", "carry": False, "flights": [
        {"id": "B", "fly": "lab_c_lqr"}]}), encoding="utf-8")
    assert [f["id"] for f in labday.load_day(tmp_path / "2099-01-02.yaml")["flights"]] == ["B"]
    flights, _, notes = labday.carry_over(tmp_path, "2099-01-02", [])
    assert flights == [] and notes[0].startswith("2099-01-01: not carried (")


def test_note_is_the_fly_files_first_comment_and_its_steps():
    about, steps = labday.note({"fly": "lab_m_memory_u100"})
    assert about.startswith("M: gate-memory premise")
    assert steps == "takeoff 0.8 mrac_injection=1 -> hold 28 -> preset figure8 f8_type=1 f8_a_cm=60 f8_laps=2 -> hold 28 -> land"
    assert labday.note({"fly": "no_such_flight"}) == ("", "")


def test_score_line_after_each_flight_and_day_report(tmp_path, monkeypatch, capsys):
    """The real scorecard on the flight folder the debrief event names; a resumed day reports every flown flight;
    a scorecard crash prints a line and the day goes on."""
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "debrief.txt").write_text("complete  air 20.0 s  1 flight(s)  10 live samples\n"
                                                "landing offset x +3 y +4 cm from the takeoff point (5 cm)\n"
                                                "outputs -  flight folder f\n", encoding="utf-8")
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    monkeypatch.setattr(labday, "REPORT_DIR", tmp_path / "days")
    flights = [{"id": "A", "fly": "lab_c_robust", "pass": "lands on the pad"},
               {"id": "B", "fly": "lab_c_lqr", "pass": "lands on the pad"}]
    assert _player(tmp_path, monkeypatch, _Fake(["", "q"]), flights).go() == 0
    out = capsys.readouterr().out
    assert "score A: pass+manual (PROPOSED checks; your call from the flight decides); land offset 5 cm; aborts 0" in out
    assert out.index("--- A done") < out.index("score A:")
    md = (tmp_path / "days" / "2099-01-01-report.md").read_text(encoding="utf-8")
    assert "| A |" in md and "| B |" not in md
    assert _player(tmp_path, monkeypatch, _Fake(["", "q"]), flights).go() == 0     # resume: B only, report has both
    md = (tmp_path / "days" / "2099-01-01-report.md").read_text(encoding="utf-8")
    assert "| A |" in md and "| B |" in md
    from ground_station.analysis import scorecard

    def boom(*a, **k):
        raise RuntimeError("bad log")
    monkeypatch.setattr(scorecard, "score_flight", boom)
    (tmp_path / "state.json").unlink()
    capsys.readouterr()
    assert _player(tmp_path, monkeypatch, _Fake(["", "q"]), flights).go() == 0
    assert "score A: scorecard failed (RuntimeError: bad log)" in capsys.readouterr().out


def test_port_seam_reaches_the_flight_and_the_service_check(tmp_path, monkeypatch):
    """The soak runs its own service on a spare port: every `fly --once` and the service check must use it, a dry
    run (no service) stays unchanged, and 8081 stays the default with no extra argument."""
    from ground_station.service import campaign_fly
    f = {"id": "A", "fly": "lab_c_robust", "args": []}
    assert "--port" not in labday.fly_cmd(f, "2")
    assert labday.fly_cmd(f, "2", port=18081)[-2:] == ["--port", "18081"]
    assert "--port" not in labday.fly_cmd(f, "2", dry=True, port=18081)
    # Soak 10-25: fly restarted the soak's plain service; the flight must carry --plain-service, a dry run never
    assert labday.fly_cmd(f, "2", port=18081, plain=True)[-3:] == ["--port", "18081", "--plain-service"]
    assert "--plain-service" not in labday.fly_cmd(f, "2", dry=True, port=18081, plain=True)
    assert "--plain-service" not in labday.fly_cmd(f, "2")
    seen = []
    monkeypatch.setattr(campaign_fly, "ensure_service", lambda base, port, **kw: seen.append((base, port)))
    fake = _Fake([""])
    d = _day(tmp_path, [{"id": "A", "fly": "lab_c_robust"}])
    monkeypatch.setattr(labday, "debrief_event", lambda pid, since: {"status": "complete", "phase": "land",
                                                                     "folder": "f"})
    monkeypatch.setattr(labday, "code_stamp", lambda: "test")
    p = labday.Day(d, tmp_path / "state.json", pack="2", ask=fake.ask, run=fake.run, popen=fake.popen, port=18081)
    assert p.go() == 0
    assert set(seen) == {("http://127.0.0.1:18081", 18081)}
    once = [c for c in fake.cmds if "--once" in c]
    assert len(once) == 1 and once[0][-2:] == ["--port", "18081"]
    made = []
    monkeypatch.setattr(labday, "Day", lambda *a, **kw: made.append(kw) or SimpleNamespace(listing=lambda: []))
    assert labday.main([str(tmp_path / "day.yaml"), "--list", "--port", "18081"]) == 0
    assert labday.main([str(tmp_path / "day.yaml"), "--list"]) == 0
    assert [kw["port"] for kw in made] == [18081, 8081]


def test_service_check_wants_lab_mode_unless_the_soak_keeps_its_plain_service(monkeypatch, tmp_path):
    """Lab 2026-10-24: labday flew on a hand-started 8081 without --lab, so the sysid card ran at stream rates the
    link could not carry. The service check now asks for --lab. The soak's own plain service on spare link ports is
    kept (--plain-service): a lab restart there would start a second service on the lab's link ports."""
    seen = []
    monkeypatch.setattr(campaign_fly, "ensure_service", lambda base, port, **kw: seen.append((port, kw.get("lab"))))
    labday.ensure_8081()
    labday.ensure_8081(18081, lab=False)
    assert seen == [(8081, True), (18081, False)]
    made = []
    monkeypatch.setattr(labday, "Day", lambda *a, **kw: made.append(kw) or SimpleNamespace(listing=lambda: []))
    _day(tmp_path, [{"id": "A", "fly": "lab_c_robust"}])
    assert labday.main([str(tmp_path / "day.yaml"), "--list"]) == 0
    assert labday.main([str(tmp_path / "day.yaml"), "--list", "--port", "18081", "--plain-service"]) == 0
    assert [m["lab_service"] for m in made] == [True, False]


def test_fly_cmd_passes_no_gates_to_flights_not_dry_runs():
    f = {"fly": "card", "args": []}
    assert "--no-gates" in labday.fly_cmd(f, no_gates=True)
    assert "--no-gates" not in labday.fly_cmd(f, dry=True, no_gates=True)
    assert "--no-gates" not in labday.fly_cmd(f)


def test_2026_10_27_has_single_header_gains_and_no_per_flight_copies():
    d = labday.load_day(labday.day_path("2026-10-27"))
    assert d.get("gains") == "rate35y104"
    assert len(d["flights"]) == 39
    assert sum(1 for f in d["flights"] if not f.get("optional")) == 29
    assert sum(1 for f in d["flights"] if f.get("optional")) == 10
    for f in d["flights"]:
        assert "--gains" not in f["args"], f"{f['id']} still has per-flight --gains"


def test_2026_10_28_flies_the_rate_step_first_and_the_thesis_pairs_before_baselines():
    d = labday.load_day(labday.day_path("2026-10-28"))
    assert d.get("gains") == "rate32d169y104" and d.get("carry") is False
    gains, _, err = labday.campaign_fly.resolve_preset_gains("rate32d169y104")
    assert not err and gains[11] == gains[14] == 16.9 and gains[15] == 10.4   # roll/pitch step, yaw kept
    ids = [f["id"] for f in d["flights"]]
    assert ids[:2] == ["rate-gains-apply", "smoke-hover"]
    assert ids.index("adapt-pos-live-verify") < ids.index("pid-track") < ids.index("lqr-track")
    assert "flash" in d["before"][0].lower()
    for f in d["flights"]:
        assert "--gains" not in f["args"], f"{f['id']} still has per-flight --gains"


def test_a1_day_gains_carry_forward_and_restart_persistence(tmp_path, monkeypatch):
    """A1: Carry-forward: after a y gate, the next flight's fly_cmd carries --gains <day-gains.json>, and the FakeDrone
    ground write sends the approved values. A restarted labday (same state) still carries them."""
    from ground_station.platform.transactions import Outcome
    from ground_station.platform.wfb_commands import encode
    from ground_station.service.fake_drone import FakeDrone

    drone = FakeDrone()
    tx_seq = [0]
    actions = []

    def fake_call(base, route, body=None, timeout=10):
        if route == "/api/actions":
            return 200, {"actions": actions}
        if route == "/commands":
            tx_seq[0] += 1
            txid = tx_seq[0]
            out = Outcome(drone.send(encode(int(body["command_id"]), int(body["index"]), float(body["value"]), txid)))
            lc = "applied" if out == Outcome.APPLIED else "rejected"
            actions.append({
                "transaction_id": txid,
                "command_id": int(body["command_id"]),
                "index": int(body["index"]),
                "value": float(body["value"]),
                "lifecycle": lc,
            })
            return 200, {"ok": out == Outcome.APPLIED, "transaction_id": txid}
        return 200, {}

    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    monkeypatch.setattr(campaign_fly, "call", fake_call)

    # 1. Start day with base preset rate35y104; fly flight A, then stop after flight A for gate
    day_yaml = tmp_path / "2026-10-27.yaml"
    flights = [
        {"id": "rate-gains-apply", "fly": "lab_a_hover_verify", "stop": True},
        {"id": "angle-gain-verify-hover", "fly": "lab_a_hover_verify", "stop": True},
        {"id": "pos-gain-verify-hover", "fly": "lab_a_hover_verify"},
    ]
    day_yaml.write_text(json.dumps({
        "day": "2026-10-27",
        "carry": False,
        "gains": "rate35y104",
        "flights": flights,
        "branches": [],
    }), encoding="utf-8")
    d = labday.load_day(day_yaml)

    def make_player(fake):
        monkeypatch.setattr(labday, "debrief_event", lambda pid, since: {"status": "complete", "phase": "land", "folder": "f"})
        monkeypatch.setattr(labday, "code_stamp", lambda: "test")
        monkeypatch.setattr(campaign_fly, "PACK_FILE", tmp_path / "last_pack.txt")
        return labday.Day(d, tmp_path / "labday_state.json", pack="2", camera=False,
                          ask=fake.ask, run=fake.run, popen=fake.popen,
                          service=fake.service, vitals=fake.vitals, clock=lambda: fake.now, sleep=fake.sleep)

    # Flight 1 flies and stops at analysis stop; we quit ('q') at the stop to simulate gate + restart
    fake1 = _Fake(["", "q"])
    p1 = make_player(fake1)
    expected_gains_path = tmp_path / "2026-10-27-gains.json"
    assert p1.gains_path == expected_gains_path
    assert expected_gains_path.is_file()

    raw_init = json.loads(expected_gains_path.read_text(encoding="utf-8"))
    assert raw_init["base"] == "rate35y104"
    assert raw_init["staged"] == {}
    assert raw_init["history"] == []
    assert float(raw_init["values"]["9"]) == pytest.approx(3.5)
    assert float(raw_init["values"]["15"]) == pytest.approx(10.4)

    assert p1.go() == 0
    once1 = [c for c in fake1.cmds if "--once" in c]
    assert len(once1) == 1
    assert "--gains" in once1[0]
    assert once1[0][once1[0].index("--gains") + 1] == str(expected_gains_path)

    # Simulate y gate approving new roll/pitch rate & angle gains + LQR k0 (VAR 75)
    p1.stage_gains({"gyroxPID.Kp": 3.8, "roll_angle_kp": 3.4, "g_xyc_lqr_k0": 12.5})
    assert p1.gains["staged"] == {"9": 3.8, "3": 3.4, "75": 12.5}
    p1.apply_gains(gate="angle-gain-verify-hover", decision="y", why="autotune fit approved", t="14:00:00")
    assert p1.gains["staged"] == {}
    assert len(p1.gains["history"]) == 3
    assert p1.gains["history"][0] == {
        "t": "14:00:00",
        "gate": "angle-gain-verify-hover",
        "var": "9",
        "old": 3.5,
        "new": 3.8,
        "decision": "y",
        "why": "autotune fit approved",
    }

    # 2. Restart labday on the same state directory; verify approved gains persist and are passed to flight 2
    fake2 = _Fake(["", "q"])
    p2 = make_player(fake2)
    assert [f["id"] for f in labday.todo(p2.d, p2.state)] == ["angle-gain-verify-hover", "pos-gain-verify-hover"]
    assert p2.gains["values"]["9"] == pytest.approx(3.8)
    assert p2.gains["values"]["3"] == pytest.approx(3.4)
    assert p2.gains["values"]["75"] == pytest.approx(12.5)
    assert len(p2.gains["history"]) == 3

    assert p2.go() == 0
    once2 = [c for c in fake2.cmds if "--once" in c]
    assert len(once2) == 1
    gains_arg = once2[0][once2[0].index("--gains") + 1]
    assert gains_arg == str(expected_gains_path)

    # 3. Execute fly ground write with `--gains <day-gains.json>` against FakeDrone and verify FakeDrone receives all approved values
    drone.param_writes.clear()
    drone.var_writes.clear()
    assert campaign_fly.write_gains("http://h", gains_arg, yes=True) is True

    pid_written = {idx: val for cmd_id, idx, val in drone.param_writes if cmd_id == 0x01}
    assert pid_written[9] == pytest.approx(3.8)
    assert pid_written[3] == pytest.approx(3.4)
    assert pid_written[11] == pytest.approx(13.0)
    assert pid_written[15] == pytest.approx(10.4)
    assert ("g_xyc_lqr_k0", pytest.approx(12.5)) in drone.var_writes

    # 4. Revert last gate and verify values restore to old
    p2.revert_gains(gate="angle-gain-verify-hover", why="revert test", t="14:05:00")
    assert p2.gains["values"]["9"] == pytest.approx(3.5)
    assert p2.gains["values"]["3"] == pytest.approx(3.0)
    assert p2.gains["values"]["75"] == pytest.approx(6.57910454)
    assert p2.gains["history"][-1]["decision"] == "revert"


def test_a9_plots_after_flight_path_ready_and_index_md(tmp_path, monkeypatch, capsys):
    """A9: After each flight, labday prints `plots: <absolute path>` right after the battery line, writes
    flight_report/flight_debrief, autotune (sysid), and adapt_gate (adaptation) figures to `<flight folder>/plots/`
    plus `plots/index.md` (one line per PNG with a one-line caption), and prints `plots ready: <n> files`."""
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    monkeypatch.setattr(labday, "REPORT_DIR", tmp_path / "days")
    flight_dir = tmp_path / "f"
    flight_dir.mkdir()
    (flight_dir / "debrief.txt").write_text(
        "complete  air 20.0 s  1 flight(s)  10 live samples\n"
        "landing offset x +3 y +4 cm from the takeoff point (5 cm)\n"
        "outputs -  flight folder f\n",
        encoding="utf-8",
    )

    def real_plot_popen(cmd, **kw):
        rc = labday.main(cmd[cmd.index("ground_station.lab.labday") + 1 :])
        return SimpleNamespace(pid=4243, wait=lambda timeout=None: rc, stderr="", stdout="")

    fake = _Fake(["", "q"])
    p = _player(
        tmp_path,
        monkeypatch,
        fake,
        [{"id": "rate-sysid-roll-wide", "fly": "lab_d_sysid_rate_roll"}, {"id": "B", "fly": "lab_c_lqr"}],
    )
    p.plot_popen = real_plot_popen
    assert p.go() == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    bat_idx = next(i for i, ln in enumerate(lines) if ln.startswith("  battery after rate-sysid-roll-wide:"))
    assert lines[bat_idx + 1].startswith("  plots: ")
    plots_path_str = lines[bat_idx + 1].split("  plots: ", 1)[1].strip()
    assert Path(plots_path_str).is_absolute()
    assert Path(plots_path_str) == (flight_dir / "plots").resolve()
    assert "  plots ready: 3 files" in out

    plots_dir = flight_dir / "plots"
    assert (plots_dir / "tracking.png").is_file()
    assert (plots_dir / "analysis.png").is_file()
    assert (plots_dir / "autotune_rate_roll.png").is_file()
    idx_lines = (plots_dir / "index.md").read_text(encoding="utf-8").strip().splitlines()
    pngs = sorted(plots_dir.glob("*.png"))
    assert len(idx_lines) == len(pngs) == 3
    for ln, png in zip(idx_lines, pngs):
        assert f"`{png.name}`:" in ln and len(ln.split(":", 1)[1].strip()) > 5

    # Adaptation flight also writes adapt_gate.png and updates index.md
    adapt_dir = tmp_path / "f_adapt"
    adapt_dir.mkdir()
    n_adapt = labday.generate_flight_plots(adapt_dir, flight_id="memory-mrac-on-2", fly_name="lab_m_memory_u100", root=tmp_path)
    assert n_adapt >= 3
    assert (adapt_dir / "plots" / "adapt_gate.png").is_file()
    adapt_idx = (adapt_dir / "plots" / "index.md").read_text(encoding="utf-8").strip().splitlines()
    assert len(adapt_idx) == len(list((adapt_dir / "plots").glob("*.png")))
    assert any("`adapt_gate.png`:" in ln for ln in adapt_idx)


def test_a9_plots_crash_or_timeout_never_blocks_next_prompt(tmp_path, monkeypatch, capsys):
    """A9: Plotting runs in the background with PLOT_TIMEOUT_S = 120 (PROPOSED). A plotting crash or timeout prints
    `plots failed: <reason>` and the next-flight prompt still appears with no delay and never stops the day."""
    import threading
    import time

    assert labday.PLOT_TIMEOUT_S == 120
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    flights = [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}]

    # 1. Crash case: non-zero exit with stderr reason
    fake_crash = _Fake(["", ""])
    p_crash = _player(tmp_path, monkeypatch, fake_crash, flights)
    p_crash.plot_popen = lambda cmd, **kw: SimpleNamespace(
        pid=4243, wait=lambda timeout=None: 1, stderr="RuntimeError: corrupt csv\n", stdout=""
    )
    assert p_crash.go() == 0
    out_crash = capsys.readouterr().out
    assert "  plots: " in out_crash
    assert "  plots failed: RuntimeError: corrupt csv" in out_crash
    assert _flown(fake_crash) == ["lab_c_robust", "lab_c_lqr"]

    # 2. Timeout + non-blocking prompt case: slow background worker times out while next-flight prompt is already shown
    (tmp_path / "state.json").unlink()
    prompt_seen_before_timeout = []
    release = threading.Event()

    def slow_timeout_wait(timeout=None):
        release.wait(timeout=0.5)
        raise subprocess.TimeoutExpired(cmd=["plot"], timeout=timeout or 120)

    fake_to = _Fake(["", "q"])
    orig_ask = fake_to.ask

    def timed_ask(prompt):
        if len(fake_to.prompts) == 1:   # B's flight prompt right after A finishes
            prompt_seen_before_timeout.append(not release.is_set())
            release.set()
            time.sleep(0.05)
        return orig_ask(prompt)

    fake_to.ask = timed_ask
    p_to = _player(tmp_path, monkeypatch, fake_to, flights)
    p_to.plot_popen = lambda cmd, **kw: SimpleNamespace(
        pid=4244, wait=slow_timeout_wait, kill=lambda: None, stderr="", stdout=""
    )
    t0 = time.monotonic()
    assert p_to.go() == 0
    elapsed = time.monotonic() - t0
    for t in p_to._plot_threads:
        t.join(timeout=1.0)
    out_to = capsys.readouterr().out
    assert prompt_seen_before_timeout == [True]
    assert elapsed < 0.4
    assert "  plots failed: timeout after 120 s" in out_to


def test_a9_no_plots_flag_and_day_header_print_nothing_and_start_no_process(tmp_path, monkeypatch, capsys):
    """A9: `--no-plots` and day-file header `plots: false` turn plotting off: print nothing and start no process."""
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    flights = [{"id": "A", "fly": "lab_c_robust"}]

    # 1. Day(..., plots=False) / --no-plots
    fake = _Fake([""])
    p = _player(tmp_path, monkeypatch, fake, flights)
    p.plots = False
    assert p.go() == 0
    out = capsys.readouterr().out
    assert "plots:" not in out and "plots ready" not in out and "plots failed" not in out
    assert not [c for c in fake.cmds if "--plot-folder" in c]

    # 2. Day file header `plots: false` + CLI `--no-plots`
    day_yaml = tmp_path / "day_no_plots.yaml"
    day_yaml.write_text(
        json.dumps({"day": "2099-01-02", "plots": False, "flights": flights, "branches": []}),
        encoding="utf-8",
    )
    d = labday.load_day(day_yaml)
    fake2 = _Fake([""])
    p2 = labday.Day(
        d, tmp_path / "state2.json", pack="2", ask=fake2.ask, run=fake2.run, popen=fake2.popen,
        service=fake2.service, vitals=fake2.vitals, clock=lambda: fake2.now, sleep=fake2.sleep,
    )
    assert p2.plots is False
    assert p2.go() == 0
    out2 = capsys.readouterr().out
    assert "plots:" not in out2
    assert not [c for c in fake2.cmds if "--plot-folder" in c]

    made = []
    monkeypatch.setattr(labday, "Day", lambda *a, **kw: made.append(kw) or SimpleNamespace(listing=lambda: []))
    assert labday.main([str(tmp_path / "day.yaml"), "--list", "--no-plots"]) == 0
    assert labday.main([str(day_yaml), "--list"]) == 0
    assert [kw["plots"] for kw in made] == [False, False]


def _setup_fake_drone(monkeypatch, armed_fn=lambda: False):
    from ground_station.platform.transactions import Outcome
    from ground_station.platform.wfb_commands import encode
    from ground_station.service.fake_drone import FakeDrone

    drone = FakeDrone()
    tx_seq = [0]
    actions = []

    def fake_call(base, route, body=None, timeout=10):
        if route == "/api/actions":
            return 200, {"actions": actions}
        if route == "/commands":
            tx_seq[0] += 1
            txid = tx_seq[0]
            drone._armed = bool(armed_fn())
            out = Outcome(drone.send(encode(int(body["command_id"]), int(body["index"]), float(body["value"]), txid)))
            lc = "applied" if out == Outcome.APPLIED else "rejected"
            actions.append({
                "transaction_id": txid,
                "command_id": int(body["command_id"]),
                "index": int(body["index"]),
                "value": float(body["value"]),
                "lifecycle": lc,
            })
            return 200, {"ok": out == Outcome.APPLIED, "transaction_id": txid}
        return 200, {}

    monkeypatch.setattr(
        campaign_fly,
        "_vitals",
        lambda base: {"arm": {"state": "armed" if armed_fn() else "disarmed"}, "battery": {"value": 16.0}},
    )
    monkeypatch.setattr(campaign_fly, "call", fake_call)
    return drone, actions


def test_a2_gate_y_writes_once_disarmed_and_refuses_when_armed(tmp_path, monkeypatch, capsys):
    """A2: `y` writes once, disarmed. With the drone armed, the gate refuses, writes nothing and prints why."""
    is_armed = [False]
    drone, _ = _setup_fake_drone(monkeypatch, armed_fn=lambda: is_armed[0])
    monkeypatch.setattr(labday, "ROOT", tmp_path)

    tune_spec = {
        "kind": "pid",
        "loop": "rate",
        "axes": ["roll", "pitch"],
        "from": ["rate-sysid-roll-wide", "rate-sysid-pitch-wide"],
        "result": {
            "proposals": {9: 4.0, 12: 3.9},
            "margins": {"pm_deg": 52.0, "gm_db": 9.5, "crossover_rad_s": 24.0},
            "coherence": 0.88,
            "residual": 0.14,
            "bw_sep": 4.2,
        },
    }
    flights = [{"id": "rate-sysid-pitch-wide", "fly": "lab_d_sysid_rate_pitch", "tune": tune_spec}]

    # 1. Disarmed: y writes once
    fake = _Fake(["", "y"])
    p = _player(tmp_path, monkeypatch, fake, flights)
    p.plots = False
    assert p.go() == 0
    out = capsys.readouterr().out
    assert "=== TUNE GATE" in out and "next flights fly:" in out
    pid_writes = [(idx, val) for cmd_id, idx, val in drone.param_writes if cmd_id == 0x01]
    assert pid_writes == [(9, pytest.approx(4.0)), (12, pytest.approx(3.9))]
    assert p.gains["values"]["9"] == pytest.approx(4.0)
    assert p.gains["values"]["12"] == pytest.approx(3.9)

    # 2. Armed: gate refuses, writes nothing, prints why
    drone.param_writes.clear()
    is_armed[0] = True
    (tmp_path / "state.json").unlink()
    fake_armed = _Fake(["", "y"])
    p_armed = _player(tmp_path, monkeypatch, fake_armed, flights)
    p_armed.plots = False
    p_armed.vitals = lambda: {"arm": {"state": "armed"}, "battery": {"value": 16.0}, "streams": {"0": {"age_s": 0.1}}}
    assert p_armed.go() == 0
    out_armed = capsys.readouterr().out
    assert "refused: drone is armed — land and disarm before writing gains" in out_armed
    assert drone.param_writes == []
    assert p_armed.gains["values"]["9"] == pytest.approx(4.0)  # unchanged from prior saved state


def test_a3_gate_n_keeps_old_r_reflies_from_and_q_stops_day(tmp_path, monkeypatch):
    """A3: `n` keeps the old values. `r` re-flies exactly the `from` flights. `q` stops the day with state saved."""
    drone, _ = _setup_fake_drone(monkeypatch)
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    tune_spec = {
        "kind": "pid",
        "loop": "rate",
        "axes": ["roll", "pitch"],
        "from": ["rate-sysid-roll-wide", "rate-sysid-pitch-wide"],
        "result": {
            "proposals": {9: 4.1, 12: 3.9},
            "margins": {"pm_deg": 50.0, "gm_db": 9.0, "crossover_rad_s": 22.0},
            "coherence": 0.85,
            "residual": 0.12,
            "bw_sep": 4.0,
        },
    }
    flights = [
        {"id": "rate-sysid-roll-wide", "fly": "lab_d_sysid_rate_roll"},
        {"id": "rate-sysid-pitch-wide", "fly": "lab_d_sysid_rate_pitch", "tune": tune_spec},
        {"id": "rate-gain-verify-hover", "fly": "lab_a_hover_verify"},
    ]

    # 1. `n` keeps old values and proceeds to rate-gain-verify-hover
    fake_n = _Fake(["", "", "n", ""])
    p_n = _player(tmp_path, monkeypatch, fake_n, flights)
    p_n.plots = False
    assert p_n.go() == 0
    assert drone.param_writes == []
    assert p_n.gains["values"]["9"] == pytest.approx(3.5)
    assert p_n.gains["history"][-1]["decision"] == "n"
    assert _flown(fake_n) == ["lab_d_sysid_rate_roll", "lab_d_sysid_rate_pitch", "lab_a_hover_verify"]

    # 2. `r` re-flies exact `from` flights (roll and pitch), then `q` stops at the gate on the second pass
    (tmp_path / "state.json").unlink()
    (tmp_path / "2099-01-01-gains.json").unlink()
    fake_rq = _Fake(["", "", "r", "", "", "q"])
    p_rq = _player(tmp_path, monkeypatch, fake_rq, flights)
    p_rq.plots = False
    assert p_rq.go() == 0
    assert _flown(fake_rq) == [
        "lab_d_sysid_rate_roll",
        "lab_d_sysid_rate_pitch",
        "lab_d_sysid_rate_roll",
        "lab_d_sysid_rate_pitch",
    ]
    saved_st = labday.load_state(tmp_path / "state.json")
    assert saved_st["flown"]["rate-sysid-roll-wide"]["ok"]
    assert saved_st["flown"]["rate-sysid-pitch-wide"]["ok"]
    assert "rate-gain-verify-hover" not in saved_st["flown"]
    assert p_rq.gains["history"][-1]["decision"] == "q"


@pytest.mark.parametrize(
    "bad_result,expected_substr",
    [
        (
            {"proposals": {9: 4.0}, "coherence": 0.45, "residual": 0.15, "bw_sep": 4.0},
                "refused: fit-bin coherence 0.45 < 0.6 (PROPOSED)",
        ),
        (
            {"proposals": {9: 4.0}, "coherence": 0.85, "residual": 0.42, "bw_sep": 4.0},
            "refused: fit residual 0.42 > 0.35 (PROPOSED)",
        ),
        (
            {"proposals": {3: 3.5}, "coherence": 0.85, "residual": 0.15, "bw_sep": 2.4},
            "refused: inner/outer bandwidth separation 2.40x < 3x (PROPOSED)",
        ),
        (
            {"proposals": {9: 8.0}, "coherence": 0.85, "residual": 0.15, "bw_sep": 4.0},
            "refused: knob gyroxPID.Kp (VAR 9) ratio 2.29x outside 0.5x-2x of current value 3.5 (PROPOSED)",
        ),
    ],
)
def test_a4_refused_proposals_each_threshold_prints_reason_and_offers_only_nrq(
    tmp_path, monkeypatch, capsys, bad_result, expected_substr
):
    """A4: A refused proposal (each PROPOSED threshold, one test each) prints its reason and offers only n/r/q."""
    drone, _ = _setup_fake_drone(monkeypatch)
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    flights = [
        {
            "id": "rate-sysid-pitch-wide",
            "fly": "lab_d_sysid_rate_pitch",
            "tune": {"kind": "pid", "loop": "rate", "from": ["rate-sysid-pitch-wide"], "result": bad_result},
        }
    ]
    # Attempt 'y' (must be rejected when refused), then 'n'
    fake = _Fake(["", "y", "n"])
    p = _player(tmp_path, monkeypatch, fake, flights)
    p.plots = False
    assert p.go() == 0
    out = capsys.readouterr().out
    assert expected_substr in out
    assert fake.prompts[-1] == "[n = keep old gains / r = re-fly / q = stop]: "
    assert "refused: 'y' is unavailable when gate checks failed (PROPOSED)" in out
    assert drone.param_writes == []


def test_a5_lossy_link_unknown_twice_then_applied_and_always_unknown_after_3_tries(tmp_path, monkeypatch, capsys):
    """A5: Lossy link: a fake write that returns unknown twice and then applied ends applied, with no duplicate effect.
    Always unknown: reported after 3 tries, and the day-gains file is NOT updated."""
    from ground_station.platform.transactions import Outcome
    from ground_station.platform.wfb_commands import encode
    from ground_station.service.fake_drone import FakeDrone

    drone = FakeDrone()
    tx_seq = [0]
    actions = []
    mode = ["twice_unknown"]
    cmd_attempts = [0]

    def lossy_call(base, route, body=None, timeout=10):
        if route == "/api/actions":
            return 200, {"actions": actions}
        if route == "/commands":
            tx_seq[0] += 1
            txid = tx_seq[0]
            cmd_attempts[0] += 1
            if mode[0] == "always_unknown" or (mode[0] == "twice_unknown" and cmd_attempts[0] <= 2):
                actions.append({
                    "transaction_id": txid,
                    "command_id": int(body["command_id"]),
                    "index": int(body["index"]),
                    "value": float(body["value"]),
                    "lifecycle": "unknown",
                })
                return 200, {"ok": True, "transaction_id": txid}
            out = Outcome(drone.send(encode(int(body["command_id"]), int(body["index"]), float(body["value"]), txid)))
            actions.append({
                "transaction_id": txid,
                "command_id": int(body["command_id"]),
                "index": int(body["index"]),
                "value": float(body["value"]),
                "lifecycle": "applied" if out == Outcome.APPLIED else "rejected",
            })
            return 200, {"ok": out == Outcome.APPLIED, "transaction_id": txid}
        return 200, {}

    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    monkeypatch.setattr(campaign_fly, "call", lossy_call)
    monkeypatch.setattr(labday, "ROOT", tmp_path)

    tune_spec = {
        "kind": "pid",
        "loop": "rate",
        "from": ["rate-sysid-pitch-wide"],
        "result": {"proposals": {9: 4.2}, "coherence": 0.9, "residual": 0.1, "bw_sep": 4.0},
    }
    flights = [{"id": "rate-sysid-pitch-wide", "fly": "lab_d_sysid_rate_pitch", "tune": tune_spec}]

    # Case 1: unknown twice, applied on 3rd try -> succeeds, idempotent value on FakeDrone
    fake1 = _Fake(["", "y"])
    p1 = _player(tmp_path, monkeypatch, fake1, flights)
    p1.plots = False
    assert p1.go() == 0
    assert cmd_attempts[0] == 3
    assert [(idx, val) for cmd_id, idx, val in drone.param_writes if cmd_id == 0x01] == [(9, pytest.approx(4.2))]
    assert p1.gains["values"]["9"] == pytest.approx(4.2)

    # Case 2: always unknown across 3 tries -> reported after 3 tries, day-gains file NOT updated
    mode[0] = "always_unknown"
    cmd_attempts[0] = 0
    drone.param_writes.clear()
    (tmp_path / "state.json").unlink()
    tune_spec["result"]["proposals"] = {9: 4.8}
    fake2 = _Fake(["", "y"])
    p2 = _player(tmp_path, monkeypatch, fake2, flights)
    p2.plots = False
    capsys.readouterr()
    assert p2.go() == 0
    out2 = capsys.readouterr().out
    assert cmd_attempts[0] == 3
    assert "unknown after 3 tries" in out2
    assert "gate write NOT applied; day-gains file unchanged" in out2
    assert p2.gains["values"]["9"] == pytest.approx(4.2)
    on_disk = json.loads((tmp_path / "2099-01-01-gains.json").read_text(encoding="utf-8"))
    assert on_disk["values"]["9"] == pytest.approx(4.2)


def test_a7_sysid_xy_gains_at_pos_gate_and_reverse_ab_candidate_rung(tmp_path, monkeypatch):
    """A7: Sysid xy gains at the pos gate: in a fake 10-27 run, `y` at the xy_model gate writes VAR 75-83 once, and
    every later diag, fix and model flight re-writes those values; fix-candidate-gains flies the pre-sysid candidate.h
    set and the next flight is back on the sysid set. After `n`, every flight flies the candidate.h set."""
    drone, _ = _setup_fake_drone(monkeypatch)
    monkeypatch.setattr(labday, "ROOT", tmp_path)

    sysid_xy = {
        75: 7.25,
        76: 4.80,
        77: 0.55,
        78: 0.60,
        79: 1.50,
        80: 2.60,
        81: 0.85,
        82: 0.95,
        83: 0.32,
    }
    xy_gate = {
        "id": "xy_model",
        "kind": "xy_model",
        "loop": "pos",
        "from": ["pos-sysid-x", "pos-sysid-y"],
        "result": {
            "proposals": sysid_xy,
            "margins": {"pm_deg": 44.0, "gm_db": 8.5, "crossover_rad_s": 2.8},
            "coherence": 0.86,
            "residual": 0.16,
            "bw_sep": 3.6,
        },
    }
    flights = [
        {"id": "pos-sysid-xy", "fly": "lab_d_sysid_pos_xy", "tune": xy_gate},
        {"id": "diag-xy-steps-lqr", "fly": "lab_e_diag_lqr_steps"},
        {"id": "fix-slow-clock-lqr-half-speed", "fly": "lab_e_fix_slow_clock_lqr_half"},
        {"id": "fix-candidate-gains-lqr-half-speed", "fly": "lab_e_fix_cand_gains_lqr_half"},
        {"id": "model-based-adrc", "fly": "lab_e_model_adrc"},
    ]

    # Branch 1: `y` at xy_model gate
    flight_xy_snapshots = {}

    class _FlyFake(_Fake):
        def popen(self, cmd, **kw):
            if "--once" in cmd and "--gains" in cmd:
                gpath = cmd[cmd.index("--gains") + 1]
                campaign_fly.write_gains("http://127.0.0.1:8081", gpath, yes=True)
                gjson = json.loads(Path(gpath).read_text(encoding="utf-8"))
                flight_xy_snapshots.setdefault("runs", []).append(dict(gjson["values"]))
            return super().popen(cmd, **kw)

    fake_y = _FlyFake(["", "y", "", "", "", ""])
    p_y = _player(tmp_path, monkeypatch, fake_y, flights)
    p_y.d["gains"] = "rate35y104"
    p_y.gains = labday.init_day_gains(p_y.gains_path, base="rate35y104")
    p_y.plots = False

    # Verify gate write happens once at `pos-sysid-xy`
    assert p_y.go() == 0
    runs = flight_xy_snapshots["runs"]
    assert len(runs) == 5
    # Flight 2 (diag), Flight 3 (fix), Flight 5 (model) all carry the sysid VAR 75..83 set
    for run_idx in (1, 2, 4):
        for var_id, val in sysid_xy.items():
            assert float(runs[run_idx][str(var_id)]) == pytest.approx(val)
    # Flight 4 (fix-candidate-gains-lqr-half-speed) carries the pre-sysid candidate.h set
    cand_k0 = campaign_fly.CANDIDATE_XY_GAINS["g_xyc_lqr_k0"]
    assert float(runs[3]["75"]) == pytest.approx(cand_k0)
    # And after Flight 5, active day-gains are on the sysid set
    assert float(p_y.gains["values"]["75"]) == pytest.approx(7.25)

    # Branch 2: `n` at xy_model gate -> every flight flies the candidate.h set
    tmp_n = tmp_path / "run_n"
    tmp_n.mkdir()
    flight_xy_snapshots["runs"] = []
    fake_n = _FlyFake(["", "n", "", "", "", ""])
    p_n = _player(tmp_n, monkeypatch, fake_n, flights)
    p_n.d["gains"] = "rate35y104"
    p_n.gains = labday.init_day_gains(p_n.gains_path, base="rate35y104")
    p_n.plots = False
    assert p_n.go() == 0
    for run_vals in flight_xy_snapshots["runs"][1:]:
        assert float(run_vals["75"]) == pytest.approx(cand_k0)


def test_a12_fake_end_to_end_transcript_rate_sysid_gate_verify_hover(tmp_path, monkeypatch, capsys):
    """A12: A fake end-to-end transcript: rate-sysid -> rate gate table -> `y` -> rate-gain-verify-hover."""
    _setup_fake_drone(monkeypatch)
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    monkeypatch.setattr(labday, "REPORT_DIR", tmp_path / "days")
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "debrief.txt").write_text(
        "complete  air 18.5 s  1 flight(s)  92 live samples\n"
        "landing offset x +2 y -3 cm from the takeoff point (4 cm)\n"
        "outputs -  flight folder f\n",
        encoding="utf-8",
    )

    rate_gate = {
        "id": "rate_pid",
        "kind": "pid",
        "loop": "rate",
        "axes": ["roll", "pitch"],
        "from": ["rate-sysid-roll-wide", "rate-sysid-pitch-wide"],
        "result": {
            "proposals": {9: 3.85, 10: 0.012, 11: 14.2, 12: 3.80, 13: 0.012, 14: 14.0},
            "margins": {"pm_deg": 51.4, "gm_db": 9.8, "crossover_rad_s": 24.6},
            "coherence": 0.89,
            "residual": 0.13,
            "bw_sep": 4.5,
        },
    }
    flights = [
        {"id": "rate-sysid-roll-wide", "fly": "lab_d_sysid_rate_roll"},
        {"id": "rate-sysid-pitch-wide", "fly": "lab_d_sysid_rate_pitch", "tune": rate_gate},
        {"id": "rate-gain-verify-hover", "fly": "lab_a_hover_verify"},
    ]
    fake = _Fake(["", "", "y", ""])
    p = _player(tmp_path, monkeypatch, fake, flights)
    p.plots = False
    assert p.go() == 0
    transcript = capsys.readouterr().out
    assert "=== FLYING rate-sysid-roll-wide" in transcript or "--- FLYING rate-sysid-roll-wide" in transcript
    assert "=== TUNE GATE rate_pid (from rate-sysid-roll-wide, rate-sysid-pitch-wide) ===" in transcript
    assert "gyroxPID.Kp" in transcript and "gyroyPID.Kp" in transcript
    assert "margins: PM=51.4 deg, GM=9.8 dB, crossover=24.60 rad/s" in transcript
    assert "fit quality: coherence=0.89, residual=0.13" in transcript
    assert "bandwidth separation: 4.50x" in transcript
    assert "next flights fly:" in transcript
    assert "--- FLYING rate-gain-verify-hover" in transcript
    out_dir = Path(r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\out\n28")
    if out_dir.is_dir():
        (out_dir / "a12-transcript.txt").write_text(transcript, encoding="utf-8")


def test_a13_revert_end_to_end_a_through_h(tmp_path, monkeypatch, capsys):
    """A13: Revert in a fake 10-27 run: (a) xy revert after 2 flights, (b) pid gate revert, (c) refused while armed,
    (d) read-back mismatch leaves values unchanged, (e) auto-offer after abort names the gate, (f) --revert xy CLI,
    (g) per-flight gains.json matches what was written, (h) reverting the revert brings sysid set back."""
    is_armed = [False]
    mismatch = [False]
    drone, actions = _setup_fake_drone(monkeypatch, armed_fn=lambda: is_armed[0])
    orig_call = campaign_fly.call

    def hooked_call(base, route, body=None, timeout=10):
        code, resp = orig_call(base, route, body=body, timeout=timeout)
        if route == "/api/actions" and mismatch[0] and actions:
            bad = [dict(a, value=float(a["value"]) + 99.0) for a in actions]
            return 200, {"actions": bad}
        return code, resp

    monkeypatch.setattr(campaign_fly, "call", hooked_call)
    monkeypatch.setattr(labday, "ROOT", tmp_path)

    sysid_xy = {75: 7.40, 76: 4.80, 77: 0.55, 78: 0.60, 79: 1.50, 80: 2.60, 81: 0.85, 82: 0.95, 83: 0.32}
    cand_k0 = campaign_fly.CANDIDATE_XY_GAINS["g_xyc_lqr_k0"]

    rate_gate = {
        "id": "rate_pid",
        "kind": "pid",
        "loop": "rate",
        "from": ["f_rate"],
        "result": {"proposals": {9: 4.1, 12: 4.0}, "coherence": 0.9, "residual": 0.1, "bw_sep": 4.2},
    }
    xy_gate = {
        "id": "xy_model",
        "kind": "xy_model",
        "loop": "pos",
        "from": ["f_pos"],
        "result": {"proposals": sysid_xy, "coherence": 0.88, "residual": 0.14, "bw_sep": 3.8},
    }
    flights = [
        {"id": "f_rate", "fly": "lab_d_sysid_rate_pitch", "tune": rate_gate},
        {"id": "f_pos", "fly": "lab_d_sysid_pos_xy", "tune": xy_gate},
        {"id": "f_after1", "fly": "lab_e_diag_lqr_steps"},
        {"id": "f_after2", "fly": "lab_e_fix_slow_clock_lqr_half"},
        {"id": "f_after3", "fly": "lab_e_model_adrc"},
    ]

    flight_written = {}

    class _RecFake(_Fake):
        def popen(self, cmd, **kw):
            if "--once" in cmd and "--gains" in cmd:
                gpath = cmd[cmd.index("--gains") + 1]
                campaign_fly.write_gains("http://127.0.0.1:8081", gpath, yes=True)
                fid = next(f["id"] for f in flights if f["fly"] == cmd[cmd.index("ground_station.service.campaign_fly") + 1])
                flight_written[fid] = dict(json.loads(Path(gpath).read_text(encoding="utf-8"))["values"])
            return super().popen(cmd, **kw)

    # Give each flight its own folder so (g) verifies `<flight folder>/gains.json`
    folder_seq = ["dir_rate", "dir_pos", "dir_after1", "dir_after2", "dir_after3"]
    for dname in folder_seq:
        (tmp_path / dname).mkdir(exist_ok=True)
    f_iter = iter(folder_seq)

    # Scripted answers:
    # f_rate: "" (fly), "y" (rate_pid gate)
    # f_pos: "" (fly), "y" (xy_model gate)
    # f_after1: "" (fly)
    # f_after2: "" (fly)
    # Before f_after3: "v" -> "xy" -> "y" (part a: revert xy to candidate.h), then "" (fly f_after3)
    fake = _RecFake(["", "y", "", "y", "", "", "v", "xy", "y", ""])
    p = _player(tmp_path, monkeypatch, fake, flights)
    p.d["gains"] = "rate35y104"
    p.gains = labday.init_day_gains(p.gains_path, base="rate35y104")
    monkeypatch.setattr(labday, "debrief_event", lambda pid, since: {"status": "complete", "phase": "land", "folder": next(f_iter)})
    p.plots = False
    assert p.go() == 0

    # (a) After `v -> xy -> y` after f_after2, VAR 75..83 were reverted to candidate.h and f_after3 wrote candidate.h
    assert float(flight_written["f_after1"]["75"]) == pytest.approx(7.40)
    assert float(flight_written["f_after2"]["75"]) == pytest.approx(7.40)
    assert float(flight_written["f_after3"]["75"]) == pytest.approx(cand_k0)
    assert float(p.gains["values"]["75"]) == pytest.approx(cand_k0)

    # (g) Every flight folder has gains.json matching what was written
    for fid, dname in zip([f["id"] for f in flights], folder_seq):
        gfile = tmp_path / dname / "gains.json"
        assert gfile.is_file(), f"missing {gfile}"
        gpayload = json.loads(gfile.read_text(encoding="utf-8"))
        assert gpayload["flight"] == fid
        assert gpayload["values"] == flight_written[fid]

    # (h) Reverting the revert brings the sysid set back
    fake.answers.extend(["y"])
    assert p.execute_revert(target="revert", confirm=True) is True
    assert float(p.gains["values"]["75"]) == pytest.approx(7.40)

    # (c) Armed: refused, values unchanged
    is_armed[0] = True
    fake.answers.extend(["y"])
    capsys.readouterr()
    assert p.execute_revert(target="xy", confirm=True) is False
    assert "refused: drone is armed — land and disarm first" in capsys.readouterr().out
    assert float(p.gains["values"]["75"]) == pytest.approx(7.40)
    is_armed[0] = False

    # (d) Read-back mismatch: "revert NOT applied", values unchanged
    mismatch[0] = True
    fake.answers.extend(["y"])
    capsys.readouterr()
    assert p.execute_revert(target="xy", confirm=True) is False
    out_d = capsys.readouterr().out
    assert "revert NOT applied" in out_d
    assert float(p.gains["values"]["75"]) == pytest.approx(7.40)
    mismatch[0] = False

    # (b) Revert PID gate (`rate_pid`): pre-gate values (3.5, 3.5) come back
    assert float(p.gains["values"]["9"]) == pytest.approx(4.1)
    fake.answers.extend(["y"])
    assert p.execute_revert(target="rate_pid", confirm=True) is True
    assert float(p.gains["values"]["9"]) == pytest.approx(3.5)
    assert float(p.gains["values"]["12"]) == pytest.approx(3.5)

    # (f) `--revert xy` CLI between runs gives the same result as (a)
    monkeypatch.setattr(labday, "state_file", lambda day: tmp_path / "state.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert labday.main([str(tmp_path / "day.yaml"), "--revert", "xy"]) == 0
    reloaded = labday.load_day_gains(tmp_path / "2099-01-01-gains.json")
    assert float(reloaded["values"]["75"]) == pytest.approx(cand_k0)

    # (e) A flight aborts after a gate write: auto-offer names that gate (`revert <gate> gains? y/n: `)
    tmp_e = tmp_path / "abort_case"
    tmp_e.mkdir()
    abort_flights = [
        {"id": "f_sysid", "fly": "lab_d_sysid_rate_pitch", "tune": rate_gate},
        {"id": "f_abort", "fly": "lab_a_hover_verify"},
    ]
    # f_sysid flies OK (rc=0) -> "y" at rate_pid gate; f_abort fails (rc=1) -> auto-offer "y" -> "q" at AUTO STOP
    fake_e = _Fake(["", "y", "", "y", "q"], fly_rc=[0, 1])
    p_e = _player(tmp_e, monkeypatch, fake_e, abort_flights)
    p_e.plots = False
    assert p_e.go() == 0
    assert any("revert rate_pid gains? y/n:" in pr for pr in fake_e.prompts)
    assert float(p_e.gains["values"]["9"]) == pytest.approx(3.5)




def test_score_runs_in_the_background_and_a_resume_rescores_only_new_flights(tmp_path, monkeypatch):
    """10-28: the deck scored synchronously and a resumed day re-scored every flown flight before the next prompt.
    The score is queued (the prompt does not wait), each card is cached in <folder>/scorecard.json, and a resume
    scores only the flight just flown."""
    import threading as th
    from ground_station.analysis import scorecard
    for n in ("a", "b"):
        (tmp_path / n).mkdir()
    gate, calls = th.Event(), []

    def slow(folder, plan_f, **kw):
        calls.append(Path(folder).name)
        gate.wait(5)
        return {"id": plan_f["id"], "fly": "x", "verdict": "pass"}
    monkeypatch.setattr(scorecard, "score_flight", slow)
    monkeypatch.setattr(scorecard, "one_line", lambda c: f"score {c['id']}")
    monkeypatch.setattr(scorecard, "write_day_report", lambda d, cards, rd: tmp_path / "r.md")
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    d = _day(tmp_path, [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}])
    p = labday.Day(d, tmp_path / "state.json", pack="2", ask=lambda q: "", run=None, popen=None)
    p.state["flown"] = {"A": {"folder": "a"}}
    t0 = time.monotonic()
    p.score({"id": "A"})
    assert time.monotonic() - t0 < 1.0          # queued: returns while the scorer is still blocked
    gate.set()
    p.drain(timeout_s=5)
    assert calls == ["a"] and (tmp_path / "a" / "scorecard.json").is_file()
    q = labday.Day(d, tmp_path / "state.json", pack="2", ask=lambda q: "", run=None, popen=None)   # resumed deck
    q.state["flown"] = {"A": {"folder": "a"}, "B": {"folder": "b"}}
    q.score({"id": "B"}, wait=True)
    assert calls == ["a", "b"] and q.cards["A"]["verdict"] == "pass"   # A from its cache, only B scored


def test_camera_stop_is_bounded(tmp_path, monkeypatch):
    """10-28: `camera.sh stop` hung forever and the operator had to Ctrl+C the deck after every flight."""
    said = []
    monkeypatch.setattr(labday, "VIDEO_DIR", tmp_path)

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    d = _day(tmp_path, [{"id": "A", "fly": "lab_c_robust"}])
    p = labday.Day(d, tmp_path / "state.json", pack="2", ask=lambda q: "", run=hang, popen=None)
    p.say = lambda *a: said.extend(a)
    p.camera_stop("cam1")
    assert any("camera stop: no exit in" in s for s in said) and (tmp_path / "cam1" / "stop").is_file()
