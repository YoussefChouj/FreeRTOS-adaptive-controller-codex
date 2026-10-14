import pytest

from ground_station.lab import cards
from ground_station.service.fly_file import compile_fly

PATH = """# step then hover
campaign c-path
rate 100
set g_xy_ctrl_kind=0 g_land_xy_gate_cm=10
takeoff 0.5
hold 3
land
"""

SCREEN = """# Does LQR or MPC beat PID?
path {path}
stage screen
ground fusion=3
variant pid set g_xy_ctrl_kind=0
variant lqr set g_xy_ctrl_kind=2
variant tuned set g_traj_ff_shift_s=0.31 ground fusion=2 ask "tuned gains written?"
baseline pid
metric overshoot_x_cm lower margin 2
"""


def _screen(tmp_path):
    fly = tmp_path / "c_path.fly"
    fly.write_text(PATH, encoding="utf-8")
    return cards.parse_card(SCREEN.format(path=fly.as_posix()), "xy.card")


def test_screen_flies_the_baseline_first_and_last(tmp_path):
    card = _screen(tmp_path)
    assert card.hypothesis == "Does LQR or MPC beat PID?" and card.ground == {"fusion": "3"}
    tuned = card.variants["tuned"]
    assert tuned.sets == {"g_traj_ff_shift_s": "0.31"} and tuned.ground == {"fusion": "2"}
    assert tuned.ask == "tuned gains written?" and card.margin == 2.0 and card.pass_at is None
    assert [(f.variant, f.n) for f in cards.flights(card)] == [("pid", 1), ("lqr", 2), ("tuned", 3), ("pid", 4)]


def test_confirm_copies_its_screen_and_flies_abba_pairs(tmp_path):
    screen = _screen(tmp_path)
    card = cards.parse_card("stage confirm xy\n", "xy-confirm.card", load=lambda name: screen)
    assert card.path == screen.path and card.baseline == "pid" and card.metric == "overshoot_x_cm"
    assert [f.variant for f in cards.flights(card, "lqr")] == ["pid", "lqr", "lqr", "pid", "pid", "lqr"]
    with pytest.raises(cards.CardError):
        cards.flights(card, "pid")


def test_list_flies_each_variant_repeats_times_in_file_order():
    card = cards.parse_card("path lab_b_land\nvariant a\nvariant b\nrepeats 2\n", "pad.card")
    assert [f.variant for f in cards.flights(card)] == ["a", "a", "b", "b"]


def test_every_problem_is_reported_with_its_line():
    text = "stage sideways\nvariant pid sett k=1 ground fusion=4\nvariant pid\nmetric wobble lower pass x\nbaseline mpc\nstage screen\n"
    with pytest.raises(cards.CardError) as e:
        cards.parse_card(text, "bad.card")
    got = "\n".join(e.value.problems)
    for want in ("bad.card:1: cannot read", "bad.card:2: 'sett'", "bad.card:2: fusion=4","bad.card:3: variant pid given twice",
                 "bad.card:4: unknown metric 'wobble'", "bad.card:4: pass 'x' is not a number", "no path line",
                 "baseline 'mpc' is not one of the variants"):
        assert want in got


def test_fly_text_names_the_flight_and_its_variant_set_wins(tmp_path):
    card = _screen(tmp_path)
    data, _ = compile_fly(cards.fly_text(card, "lqr"), "xy-lqr.fly")
    assert data["campaign"] == "xy-lqr" and data["experiments"][0]["log_plan"]["rate_hz"] == 100
    takeoff = data["experiments"][0]["scenario"]["steps"][0]["takeoff"]
    assert takeoff["set"] == {"g_xy_ctrl_kind": 2, "g_land_xy_gate_cm": 10}
    out = cards.write_fly(card, "pid", tmp_path / "fly")
    assert out.name == "xy-pid.fly" and "campaign xy-pid" in out.read_text(encoding="utf-8")


def test_the_lab_cards_compile(capsys):
    assert cards.main([]) == 0, capsys.readouterr().out
