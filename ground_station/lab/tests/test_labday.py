"""labday player: the day file, the run sheet, resume order, analysis stops and auto stops (no drone, no camera)."""
from __future__ import annotations

import json
import subprocess
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
        if "--pack" in cmd:   # fly remembers the pack it was given
            campaign_fly.remember_pack(campaign_fly.pack_id(cmd[cmd.index("--pack") + 1]))
        rc = self.fly_rc.pop(0) if self.fly_rc else 0
        return SimpleNamespace(pid=4242, wait=lambda: rc)


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
    assert "score A: pass pass (PROPOSED); land offset 5 cm; aborts 0" in out
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
