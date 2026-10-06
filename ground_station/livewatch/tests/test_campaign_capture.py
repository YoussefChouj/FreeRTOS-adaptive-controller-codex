"""Tests for the Workflow B capture plan (ground_station/livewatch/campaign_capture.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ground_station.livewatch import campaign_capture as cc
from ground_station.livewatch.campaign_capture import (
    CAMPAIGN_SET,
    CaptureError,
    ManifestError,
    probe_max_rate,
    read_manifest,
    slots_for,
    write_manifest,
)
from ground_station.livewatch.manifest import REQUIRED_SYNC_VARS
from ground_station.livewatch.stream import FRAME_OVERHEAD, MAX_SLOTS, MAX_STREAM_RANGES

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"
CATALOG = Path(cc.__file__).with_name("symbol_catalog.json")
NEEDED, OPTIONAL = CAMPAIGN_SET["needed"], CAMPAIGN_SET["optional"]
ALL_VARS = NEEDED + OPTIONAL
# Resolved by the host from DWARF, not in the firmware's on-board symbol table.
DWARF_ONLY = {"real_voltage", "flight_phase", cc.KF_HEALTH, *cc.WFB_STATUS}


# ---- the campaign set ---------------------------------------------------------------------------------

def test_set_sizes_and_order():
    assert len(NEEDED) == 41 and len(OPTIONAL) == 68
    assert NEEDED[:len(REQUIRED_SYNC_VARS)] == REQUIRED_SYNC_VARS
    scoring = [n for a in cc.POSITION_AXES for n in (a.feedback, a.reference)] + list(cc.MOTORS)
    assert set(scoring) <= set(NEEDED)


def test_needed_and_optional_are_disjoint_without_repeats():
    assert not set(NEEDED) & set(OPTIONAL)
    assert len(set(ALL_VARS)) == len(ALL_VARS)


def test_every_symbol_is_in_the_on_board_catalog():
    roots = {e["name"] for e in json.loads(CATALOG.read_text(encoding="utf-8"))["entries"]}

    def in_catalog(var: str) -> bool:
        return any(var == r or var.startswith((r + ".", r + "[")) for r in roots)

    assert [v for v in ALL_VARS if v not in DWARF_ONLY and not in_catalog(v)] == []
    assert DWARF_ONLY <= set(NEEDED)


@pytest.mark.skipif(not ELF.exists(), reason="needs the built firmware OBJ/JX_FLY.axf")
def test_every_symbol_resolves_in_the_elf_as_one_scalar():
    from ground_station.livewatch.symbols import SymbolResolver

    resolver = SymbolResolver(ELF)
    sizes = {v: resolver.resolve(v).size for v in ALL_VARS + cc.VELOCITY_LOOPS + cc.OPTICAL_FLOW}
    assert {v: s for v, s in sizes.items() if not 1 <= s <= 4} == {}


# ---- slots_for -----------------------------------------------------------------------------------------

def _guard_bps(slots: list[dict]) -> float:
    """Firmware guard price, by hand: (12 + 4 B per var) per frame, at 200 Hz / divider."""
    return sum((FRAME_OVERHEAD + 4 * len(s["vars"])) * 200 * s["hz"] / 100 for s in slots)


@pytest.mark.parametrize("rate", [200, 100, 60, 50, 25, 10, 1])
def test_plan_respects_slot_limits_and_budget(rate):
    slots, dropped = slots_for(rate)
    assert 1 <= len(slots) <= MAX_SLOTS
    assert [s["slot"] for s in slots] == list(range(len(slots)))
    assert all(1 <= len(s["vars"]) <= MAX_STREAM_RANGES for s in slots)
    assert _guard_bps(slots) <= cc.PLANNING_BUDGET_BPS
    recorded = [v for s in slots for v in s["vars"]]
    assert recorded + dropped == list(ALL_VARS)  # needed first; drops come off the end


def test_50hz_records_everything():
    slots, dropped = slots_for(50)
    assert [(s["hz"], len(s["vars"])) for s in slots] == [(50.0, 62), (50.0, 47)]
    assert dropped == [] and _guard_bps(slots) == 46000


def test_100hz_drops_the_last_28_optional():
    slots, dropped = slots_for(100)
    assert [(s["hz"], len(s["vars"])) for s in slots] == [(100.0, 62), (100.0, 19)]
    assert _guard_bps(slots) == 52000 + 17600  # one more var would cost 70400 > 70041.6
    assert dropped == list(OPTIONAL[40:])


def test_divider_truncates_like_the_live_host():
    # int(100 / 60) = 1, so the drone emits 100 Hz (capture_preset.py:329); rounding would give 50 Hz.
    assert {s["hz"] for s in slots_for(60)[0]} == {100.0}


def test_tiny_budget_drops_optional_before_needed():
    needed_only = (FRAME_OVERHEAD + 4 * len(NEEDED)) * 200  # 35200 B/s at 100 Hz
    slots, dropped = slots_for(100, budget_bps=needed_only)
    assert [s["vars"] for s in slots] == [list(NEEDED)] and dropped == list(OPTIONAL)
    slots, dropped = slots_for(100, budget_bps=needed_only + 3 * 4 * 200)
    assert slots[0]["vars"] == list(NEEDED + OPTIONAL[:3]) and dropped == list(OPTIONAL[3:])


def test_needed_that_cannot_fit_raises():
    with pytest.raises(CaptureError, match="needed"):
        slots_for(100, budget_bps=(FRAME_OVERHEAD + 4 * len(NEEDED)) * 200 - 1)


@pytest.mark.parametrize("rate", [0, -50, float("nan"), float("inf")])
def test_bad_rate_raises(rate):
    with pytest.raises(CaptureError):
        slots_for(rate)


# ---- probe_max_rate ------------------------------------------------------------------------------------

def _fake_stream(losses: dict[float, float], calls: list):
    def stream(rate: float, secs: float) -> float:
        calls.append((rate, secs))
        return losses[rate]
    return stream


def test_probe_returns_first_loss_free_rate_trying_high_to_low():
    calls: list = []
    stream = _fake_stream({200: 0.1, 100: 0.02, 50: 0.0, 25: 0.0}, calls)
    assert probe_max_rate(stream, rates=(25, 200, 50, 100), secs=1.5) == 50
    assert calls == [(200, 1.5), (100, 1.5), (50, 1.5)]


def test_probe_raises_when_every_rate_is_lossy():
    stream = _fake_stream({200: 0.3, 100: 0.2, 50: 0.1, 25: 0.01}, [])
    with pytest.raises(CaptureError, match="lost frames"):
        probe_max_rate(stream)


# ---- manifest ------------------------------------------------------------------------------------------

def _good_kwargs() -> dict:
    slots, dropped = slots_for(100)
    return dict(
        pack_id="pid_v1",
        rate_hz=100,
        slots=[{**s, "csv": f"run.slot{s['slot']}.csv"} for s in slots],
        dropped=dropped,
        segments=[
            {"name": "circle", "t0_host_s": 30.0, "t1_host_s": 45.5},
            {"name": "hover", "t0_host_s": 10.0, "t1_host_s": 30.0},  # out of order on purpose
        ],
    )


def test_manifest_round_trip(tmp_path):
    kwargs = _good_kwargs()
    before = json.dumps(kwargs, sort_keys=True)
    path = write_manifest(tmp_path, **kwargs)
    assert path == tmp_path / "manifest.json"
    assert json.dumps(kwargs, sort_keys=True) == before  # inputs untouched
    got = read_manifest(tmp_path)
    assert got["schema"] == "wfb_session_v1" and got["pack_id"] == "pid_v1" and got["rate_hz"] == 100.0
    assert got["slots"] == kwargs["slots"] and got["dropped"] == kwargs["dropped"]
    assert [s["name"] for s in got["segments"]] == ["hover", "circle"]  # sorted by start time
    assert json.loads(path.read_text(encoding="utf-8")) == got


def _with(change):
    def build(m: dict) -> dict:
        change(m)
        return m
    return build


BAD = {
    "schema": _with(lambda m: m.update(schema="wfb_session_v0")),
    "missing key": _with(lambda m: m.pop("dropped")),
    "extra key": _with(lambda m: m.update(note="x")),
    "rate 0": _with(lambda m: m.update(rate_hz=0)),
    "slot id 4": _with(lambda m: m["slots"][1].update(slot=4)),
    "repeated slot id": _with(lambda m: m["slots"][1].update(slot=0)),
    "csv with a directory": _with(lambda m: m["slots"][0].update(csv="../run.slot0.csv")),
    "var in two slots": _with(lambda m: m["slots"][1]["vars"].append(m["slots"][0]["vars"][0])),
    "recorded and dropped": _with(lambda m: m["dropped"].append(m["slots"][0]["vars"][0])),
    "t0 >= t1": _with(lambda m: m["segments"][0].update(t1_host_s=10.0)),
    "overlap": _with(lambda m: m["segments"][1].update(t0_host_s=29.0)),  # circle starts inside hover
    "repeated name": _with(lambda m: m["segments"][0].update(name="circle")),
}


@pytest.mark.parametrize("case", BAD, ids=list(BAD))
def test_read_rejects_bad_manifest(tmp_path, case):
    write_manifest(tmp_path, **_good_kwargs())
    path = tmp_path / "manifest.json"
    manifest = BAD[case](json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError):
        read_manifest(tmp_path)


def test_write_rejects_bad_input_and_writes_nothing(tmp_path):
    kwargs = _good_kwargs()
    kwargs["segments"][0]["t0_host_s"] = 29.0  # overlaps hover
    with pytest.raises(ManifestError, match="overlap"):
        write_manifest(tmp_path, **kwargs)
    assert not (tmp_path / "manifest.json").exists()


def test_read_rejects_non_json(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="JSON"):
        read_manifest(tmp_path)


# ---- per-experiment log plan ---------------------------------------------------------------------------

def test_log_groups_are_disjoint_from_needed_and_each_other():
    group_vars = [v for g in cc.LOG_GROUPS.values() for v in g]
    assert len(set(group_vars)) == len(group_vars)
    assert not set(group_vars) & set(NEEDED)
    assert cc.LOG_GROUPS["mrac_shadow"] == OPTIONAL


@pytest.mark.parametrize("log_plan, problem", [
    ([], "must be a mapping"),
    ({"rate": 50}, "unknown key 'rate'"),
    ({"rate_hz": 0}, "rate_hz"),
    ({"rate_hz": 150}, "rate_hz"),
    ({"rate_hz": True}, "rate_hz"),
    ({"rate_hz": "50"}, "rate_hz"),
    ({"groups": "optical_flow"}, "list of group names"),
    ({"groups": ["gyro"]}, "unknown group 'gyro'"),
    ({"groups": ["optical_flow", "optical_flow"]}, "twice"),
])
def test_check_log_plan_problems(log_plan, problem):
    problems = cc.check_log_plan(log_plan)
    assert any(problem in p for p in problems), problems
    if isinstance(log_plan, dict):
        with pytest.raises(CaptureError, match="log_plan"):
            cc.plan_capture(log_plan)


def test_check_log_plan_accepts_defaults_and_full_plans():
    assert cc.check_log_plan({}) == []
    assert cc.check_log_plan({"rate_hz": 25.5, "groups": list(cc.LOG_GROUPS)}) == []


def test_plan_capture_default_is_core_only_at_50hz():
    plan = cc.plan_capture()
    assert plan["rate_hz"] == 50.0 and plan["requested_hz"] == 50.0 and plan["groups"] == []
    assert [v for s in plan["slots"] for v in s["vars"]] == list(NEEDED) and plan["dropped"] == []


def test_plan_capture_keeps_group_order_and_caps_at_the_probed_rate():
    plan = cc.plan_capture({"rate_hz": 100, "groups": ["optical_flow", "velocity_loops"]}, max_rate_hz=50)
    assert plan["rate_hz"] == 50.0 and plan["requested_hz"] == 100.0
    recorded = [v for s in plan["slots"] for v in s["vars"]]
    assert recorded == list(NEEDED + cc.OPTICAL_FLOW + cc.VELOCITY_LOOPS)


def test_plan_capture_drops_group_tail_at_100hz():
    plan = cc.plan_capture({"rate_hz": 100, "groups": ["optical_flow", "mrac_shadow"]})
    assert plan["dropped"] == list(OPTIONAL[35:])  # 81 vars fit at 100 Hz: 41 core + 5 flow + 35 shadow
    table = cc.log_plan_table(plan)
    assert "| core (always on) | 41/41 | 100 |  |" in table
    assert "| optical_flow | 5/5 | 100 |  |" in table
    assert "| mrac_shadow | 35/68 | 100 | 33 dropped (link budget) |" in table
    assert table.splitlines()[-1].startswith("2 slot(s), 69600 of 70042 B/s")


def test_log_plan_table_notes_a_capped_rate():
    table = cc.log_plan_table(cc.plan_capture({"rate_hz": 100}, max_rate_hz=25))
    assert "| core (always on) | 41/41 | 25 |  |" in table
    assert "requested 100 Hz" in table.splitlines()[-1]


def test_subscribe_steps_match_the_agent_subscribe_action():
    plan = cc.plan_capture({"rate_hz": 50, "groups": ["mrac_shadow"]})
    steps = cc.subscribe_steps(plan)
    assert [s["action"] for s in steps] == ["subscribe", "subscribe"]
    assert [(s["args"]["slot"], s["args"]["divider"]) for s in steps] == [(0, 2), (1, 2)]
    assert [s["args"]["ranges"] for s in steps] == [s["vars"] for s in plan["slots"]]
