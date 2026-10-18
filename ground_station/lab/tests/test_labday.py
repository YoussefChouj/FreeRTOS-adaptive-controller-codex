"""labday player: the day file, the run sheet, resume order, analysis stops and auto stops (no drone, no camera)."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from ground_station.lab import labday


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


class _Fake:
    """Stands in for subprocess.run / Popen and input: records the commands, scripted answers and exits."""

    def __init__(self, answers, fly_rc=(), dry_rc=0):
        self.answers, self.fly_rc, self.dry_rc, self.cmds = list(answers), list(fly_rc), dry_rc, []

    def ask(self, prompt):
        return self.answers.pop(0)

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        return SimpleNamespace(returncode=self.dry_rc if "--dry-run" in cmd else 0, stdout="boom", stderr="")

    def popen(self, cmd, **kw):
        self.cmds.append(cmd)
        rc = self.fly_rc.pop(0) if self.fly_rc else 0
        return SimpleNamespace(pid=4242, wait=lambda: rc)


def _player(tmp_path, monkeypatch, fake, flights, status="complete", camera=None, pack="2"):
    d = _day(tmp_path, flights, branches=[{"when": "w", "do": "d"}], camera=camera)
    monkeypatch.setattr(labday, "debrief_event", lambda pid, since: status and {"status": status, "phase": "land",
                                                                              "folder": "f"})
    monkeypatch.setattr(labday, "git_bash", lambda: "bash")
    return labday.Day(d, tmp_path / "state.json", pack=pack, ask=fake.ask, run=fake.run, popen=fake.popen)


def _flown(fake):
    return [c[c.index("ground_station.service.campaign_fly") + 1] for c in fake.cmds if "--once" in c]


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
    p.cooldown_s, p.wait = 60, waits.append
    assert p.go() == 0 and _flown(fake) == ["lab_c_robust", "lab_c_lqr", "lab_c_mpc"]
    assert len(waits) == 2 and all(55 < w <= 60 for w in waits)
    off = _Fake(["", ""])
    (tmp_path / "off").mkdir()
    p = _player(tmp_path / "off", monkeypatch, off, [{"id": "A", "fly": "lab_c_robust"}, {"id": "B", "fly": "lab_c_lqr"}])
    waits.clear()
    p.wait = waits.append
    assert p.go() == 0 and not waits      # cooldown_s 0 = off


def test_cooldown_counts_down_and_enter_ends_it(capsys):
    now = [0.0]
    keys = iter([False, False, True])
    waited = labday.cooldown(10, key=lambda: next(keys), clock=lambda: now[0],
                             sleep=lambda s: now.__setitem__(0, now[0] + 1), flush=lambda: None)
    assert waited == 2 and "cooldown 0:10" in capsys.readouterr().out
    now[0] = 0.0
    assert labday.cooldown(3, key=lambda: False, clock=lambda: now[0],
                           sleep=lambda s: now.__setitem__(0, now[0] + 1), flush=lambda: None) == 3


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


def test_camera_around_each_flight_and_floor_check(tmp_path, monkeypatch):
    monkeypatch.setattr(labday, "VIDEO_DIR", tmp_path / "vt")
    cam = {"floor": "fl", "mode": 3, "args": ["--yaw-deg", 94]}
    fake = _Fake([])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=cam)
    assert p.go() == 1 and not fake.cmds            # no floor scan yet: nothing runs
    (tmp_path / "vt" / "fl").mkdir(parents=True)
    fake = _Fake([""])
    p = _player(tmp_path, monkeypatch, fake, [{"id": "A", "fly": "lab_c_robust"}], camera=cam)
    assert p.go() == 0
    sh = [c[2:] for c in fake.cmds if c[:2] == ["bash", labday.CAMERA_SH]]
    assert sh == [["fusion", "a-lab-c-robust", "3", "--yaw-deg", "94"], ["stop", "a-lab-c-robust"]]
    assert (tmp_path / "vt" / "a-lab-c-robust" / "stop").is_file()
    assert fake.cmds.index(next(c for c in fake.cmds if "--once" in c)) == 2   # dry run, fusion, then the flight


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
