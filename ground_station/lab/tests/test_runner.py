"""runner.py: the queue file, the flight order with c/x and skips, resume, scores, picks and one whole flight."""
import pytest

from ground_station.lab import cards, runner
from ground_station.service import campaign_fly as fly


def card(name, stage="list", variants=("a", "b"), metric="overshoot_x_cm", **kw):
    return cards.Card(name, hypothesis=f"{name} helps", path="lab_c_pid", stage=stage,
                      variants={v: cards.Variant(v) for v in variants},
                      baseline="" if stage == "list" else variants[0], metric=metric, **kw)


SCREEN = card("scr", "screen", ("a", "b", "c"))
CONFIRM = card("conf", "confirm", ("a", "b", "c"), source="scr", repeats=2)
LIST = card("lst")


def loader(*cs):
    byname = {c.name: c for c in cs}

    def load(name):
        if name not in byname:
            raise cards.CardError([f"no card {name}"])
        return byname[name]
    return load


def new_state():
    return {"flights": [], "winners": {}, "skipped": {}, "packs": {}}


def fly_all(q, st, **f):
    order = []
    while (nxt := runner.next_flight(q, st))[0]:
        order.append(nxt[1].variant)
        runner.record(st, q, *nxt, **f)
    return order


def test_parse_queue_reads_floor_tracker_and_cards_in_order():
    q = runner.parse_queue("# day\nfloor f1\ntracker --yaw-deg 94\nlst\nscr   # the screen\nconf\n",
                           load=loader(SCREEN, CONFIRM, LIST))
    assert (q.floor, q.tracker, [c.name for c in q.cards]) == ("f1", ["--yaw-deg", "94"], ["lst", "scr", "conf"])


def test_parse_queue_lists_every_problem_with_its_line():
    with pytest.raises(cards.CardError) as e:
        runner.parse_queue("conf\nnope\nlst\nlst\nfloor\nlst a\n", "q.txt", load=loader(SCREEN, CONFIRM, LIST))
    p = e.value.problems
    assert p[0].startswith("q.txt:1: conf confirms scr") and p[1].startswith("q.txt:2: nope: ")
    assert p[2:] == ["q.txt:4: lst given twice",
                     "q.txt:5: floor takes one name, the scan dir under logs/video_truth",
                     "q.txt:6: expected floor <scan dir>, tracker <args> or one card name"]
    with pytest.raises(cards.CardError) as e:
        runner.parse_queue("# nothing\n", load=loader())
    assert e.value.problems == ["<queue>: no cards"]


def test_c_counts_x_flies_again_and_a_second_x_skips_the_card_and_its_confirm():
    q, st = runner.Queue(cards=[SCREEN, CONFIRM, LIST]), new_state()
    c, f = runner.next_flight(q, st)
    assert (c.name, f.variant, f.n) == ("scr", "a", 1)
    runner.record(st, q, c, f, early="x")
    assert runner.next_flight(q, st)[1] == f
    runner.record(st, q, c, f, early="c")
    nxt = runner.next_flight(q, st)
    assert nxt[1].variant == "b"
    runner.record(st, q, *nxt, early="x")
    assert set(st["skipped"]) == {"scr", "conf"}
    assert runner.next_flight(q, st)[0] is LIST


def test_confirm_waits_for_the_pick_then_flies_abba():
    q, st = runner.Queue(cards=[SCREEN, CONFIRM]), new_state()
    assert fly_all(q, st, metrics={"overshoot_x_cm": 1.0}) == ["a", "b", "c", "a"]
    assert runner.next_flight(q, st) == (None, None)
    st["winners"]["scr"] = "c"
    assert fly_all(q, st) == ["a", "c", "c", "a"]


def test_state_survives_a_restart_and_a_torn_file_stops_the_day(tmp_path):
    path, q = tmp_path / "day" / "queue_state.json", runner.Queue(cards=[LIST])
    st = runner.load_state(path)
    runner.record(st, q, *runner.next_flight(q, st))
    runner.save_state(path, st)
    assert runner.next_flight(q, runner.load_state(path))[1].variant == "b"
    path.write_text("{torn", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read"):
        runner.load_state(path)


def sample(t, prim, ex_cm=0.0, ey_cm=0.0):          # the loc PIDs run in cm
    return {"t": t, "g_wfb_status.prim_state": fly.PRIM_NAMES.index(prim), "Ctrler.locxPID.FB": ex_cm,
            "Ctrler.locxPID.Des": 0.0, "Ctrler.locyPID.FB": ey_cm, "Ctrler.locyPID.Des": 0.0}


def test_measure_traj_rms_over_traj_only_and_the_camera_landing():
    s = [sample(0, "IDLE", 9), sample(1, "TRAJ", 3, 4), sample(2, "TRAJ", -3, -4), sample(9, "IDLE", 9)]
    m = runner.measure(card("t", metric="traj_xy_rms_cm"), {}, s, (3.0, 4.0, "sess", 15))
    assert m == {"traj_xy_rms_cm": 5.0, "cam_landing_cm": 5.0}
    assert runner.flight_s(s[1:3]) == 1.0 and runner.flight_s([]) is None
    assert runner.volts({"battery": {"key": "real_voltage", "value": 16.2, "age_s": 0.1}}) == 16.2
    assert runner.volts({"battery": None}) is None and runner.volts({}) is None


def test_rank_puts_a_controller_failure_last_and_verdict_needs_every_flight():
    st = new_state()
    for v, x, early in (("a", 5.0, None), ("b", 2.0, None), ("c", None, "c"), ("a", 7.0, None)):
        st["flights"].append({"card": "scr", "variant": v, "metrics": {"overshoot_x_cm": x} if x else {},
                              "early": early})
    assert [r[0] for r in runner.rank(SCREEN, st)] == ["b", "a", "c"]
    assert runner.rank(SCREEN, st)[1][1] == 6.0
    st["winners"]["scr"] = "b"
    for v, x in (("a", 5.0), ("b", 3.0), ("b", 4.0), ("a", 4.5)):
        st["flights"].append({"card": "conf", "variant": v, "metrics": {"overshoot_x_cm": x}})
    assert runner.verdict(CONFIRM, st)[0] is True
    st["flights"][-1]["metrics"]["overshoot_x_cm"] = 3.5
    assert runner.verdict(CONFIRM, st)[0] is False
    st["flights"][-1]["metrics"] = {}
    assert runner.verdict(CONFIRM, st)[0] is None
    assert runner.passes(card("p", pass_at=10.0), 9.5) is True and runner.passes(LIST, 9.5) is None


class FakeDrone:
    """The runner's view of 8081: the volts it reads and the flights it flies."""

    def __init__(self, monkeypatch, tmp_path, answers, v=16.4, status="complete"):
        self.v, self.status, self.flown, self.answers, self.now = v, status, [], list(answers), 1000.0
        monkeypatch.setattr(fly, "_vitals", lambda base: {"battery": {"value": self.v}} if self.v else {})
        monkeypatch.setattr(fly, "resolve_campaign", lambda src, pack: (tmp_path / "launch.yaml", src))
        monkeypatch.setattr(fly, "wait_for_arm", lambda *a, **k: "go")
        monkeypatch.setattr(fly, "fly_one", self.fly_one)
        monkeypatch.setattr(fly, "pack_problem", lambda p: "" if p in ("P1", "P2") else f"{p}: unknown pack")
        monkeypatch.setattr(fly, "remember_pack", lambda p=None: p)
        monkeypatch.setattr(runner, "_pack_ids", lambda: ["P1", "P2"])
        monkeypatch.setattr(runner.events, "log", lambda *a, **k: None)
        self.lab = runner.Lab("http://x", runner.Queue(cards=[card("lst", variants=("a", "b"))]),
                              tmp_path / "day" / "queue_state.json", "P1", camera=False,
                              ask=lambda prompt: self.answers.pop(0), wall=lambda: self.now, sleep=self.sleep)

    def sleep(self, s):
        self.now += s

    def fly_one(self, base, launch, src, name, pack):
        self.flown.append((name, pack))
        self.now += 100.0
        samples = [sample(0, "CLIMB"), sample(60, "DESCEND"), sample(100, "IDLE")]
        return {"status": self.status, "phase": "TRAJ"}, samples, None, launch.parent


def test_lab_flies_rests_and_swaps_after_two_flights(monkeypatch, tmp_path):
    d = FakeDrone(monkeypatch, tmp_path, answers=["", ""])
    lab = d.lab
    assert lab.one(*runner.next_flight(lab.queue, lab.state)) is None
    t_land = lab.state["flights"][0]["t_land"]
    assert lab.state["flights"][0]["flight_s"] == 60.0 and lab.state["packs"]["P1"]["rest_v"] == 16.4
    assert lab.one(*runner.next_flight(lab.queue, lab.state)) is None
    assert d.flown == [("lst-a", "P1"), ("lst-b", "P1")]
    assert lab.state["flights"][1]["t_land"] - t_land >= 100 + runner.REST_X * 60   # the rest held the takeoff
    lab.queue.cards[0].repeats = 2                                    # two more flights: P1 has flown its two
    assert lab.one(*runner.next_flight(lab.queue, lab.state)) is None
    assert d.flown[-1][1] == "P2" and lab.state["packs"]["P1"]["state"] == "charging"
    assert runner.load_state(lab.state_file)["packs"]["P2"]["flights"] == 1


def test_lab_early_end_asks_why_and_x_flies_it_again(monkeypatch, tmp_path):
    d = FakeDrone(monkeypatch, tmp_path, answers=["?", "x"], status="aborted")
    lab = d.lab
    assert lab.one(*runner.next_flight(lab.queue, lab.state)) is None
    assert lab.state["flights"][0]["early"] == "x"
    assert runner.next_flight(lab.queue, lab.state)[1].variant == "a"
