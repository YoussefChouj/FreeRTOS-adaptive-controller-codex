"""soak.py without processes: the Driver answers a labday transcript the way the operator does (one answer per
prompt, the RC arm at preflight, the mid-day pack swap), the fixture, the store cap, and the checks and report
failing on the outcomes they read."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys

import pytest

from ground_station.comm import lag_histogram as lh
from ground_station.lab import soak

PLAN = [{"id": "mem1", "fly": "lab_m_memory_u100"}, {"id": "mem2", "fly": "lab_m_memory_u0"}]


def _clock():
    t = [1000.0]

    def now():
        t[0] += 10
        return t[0]
    return now


def _flight_text(fid, i, n, status="complete", swap=False):
    head = (f"=== {fid} ({i} of {n}): memory\nEnter flies it (then arm by RC), s skips it, q quits: ")
    body = [f"--- FLYING {fid}: lab_m\n", "preflight OK: arm by RC to fly\n"]
    if swap:
        body += ["link lost: waiting for the drone (a new pack asks for its id)\n", "link back\n",
                 "new pack? [Enter = 1] ", "preflight OK: arm by RC to fly\n"]
    body += ["ARMED: takeoff in 3 s\n", f"{status}  air 41.2 s\n",
             f"outputs /x/out/{fid}  flight folder logs/flights/{fid}\n",
             f"--- {fid} done 10:42: OK; 1 min 05 s in fly; status {status}; log logs/x.log\n"]
    return head, "".join(body)


def test_driver_answers_each_prompt_once_and_arms_at_preflight():
    d = soak.Driver(1, PLAN, swap_id=None, clock=_clock())
    head, body = _flight_text("mem1", 1, 2)
    acts = d.feed(head)
    assert acts == [("write", "\n")]
    assert d.feed("") == []                         # the cursor moved past it: no second Enter
    text = body + "\n" + "".join(_flight_text("mem2", 2, 2))
    acts = []
    for i in range(0, len(text), 7):                # arrives in small chunks, split mid-line
        acts += d.feed(text[i:i + 7])
    acts += d.feed("soak-d1: day done.\n")
    kinds = [(k, getattr(a, "id", a)) for k, a in acts]
    assert kinds == [("flight_start", "mem1"), ("arm", "mem1"), ("flight_done", "mem1"), ("disarm", "mem1"),
                     ("write", "\n"), ("flight_start", "mem2"), ("arm", "mem2"), ("flight_done", "mem2"),
                     ("disarm", "mem2")]
    f1, f2 = d.flights
    assert f1.fly == "lab_m_memory_u100" and f1.armed and f1.done_ok == "OK" and f1.done_status == "complete"
    assert f1.outputs == "/x/out/mem1" and f1.folder == "logs/flights/mem1" and f1.done_why == ""
    assert f1.start_s < f1.end_s < f2.start_s
    assert d.day_done and not d.failures and d.stale == 0


def test_driver_swaps_the_pack_instead_of_arming_then_arms_the_new_pack():
    d = soak.Driver(1, PLAN, swap_id="mem2", clock=_clock())
    for part in _flight_text("mem1", 1, 2) + _flight_text("mem2", 2, 2, swap=True):
        d.feed(part)
    f2 = d.flights[1]
    assert f2.swap and f2.link_lost and f2.link_back and f2.new_pack_asked and f2.preflights == 2
    assert not d.flights[0].swap


def test_driver_never_arms_the_swap_flight_before_the_new_pack_prompt():
    """A reboot too short for the fly to call the link lost: the preflight fails then passes on the old pack."""
    d = soak.Driver(1, PLAN, swap_id="mem1", clock=_clock())
    head, _ = _flight_text("mem1", 1, 2)
    d.feed(head)
    acts = d.feed("--- FLYING mem1: x\npreflight OK: arm by RC to fly\n")
    assert [k for k, _ in acts] == ["flight_start", "swap"]
    assert d.feed("preflight FAIL:\n  link: slot 0 silent\npreflight OK: arm by RC to fly\n") == []
    acts = d.feed("link lost: waiting\nlink back\nnew pack? [Enter = 1] preflight OK: arm by RC to fly\n")
    assert [k for k, _ in acts] == ["write", "arm"]


def test_driver_counts_stale_aborts_and_fails_on_unknown_stops():
    d = soak.Driver(1, PLAN, swap_id=None, clock=_clock())
    head, _ = _flight_text("mem1", 1, 2)
    d.feed(head + "--- FLYING mem1: x\npreflight OK\nARMED: takeoff in 3 s\n"
           "ABORTED L1 at 12.0 s: stale_telemetry\n"
           "--- mem1 done 10:42: BAD, aborted; 0 min 20 s in fly; status aborted; log y\n")
    acts = d.feed("\nAUTO STOP: mem1 aborted.\n"
                  "r flies it again, Enter goes on to the next flight, q quits (the same command resumes): ")
    assert acts == [("write", "\n")]
    assert d.stale == 1 and d.flights[0].done_status == "aborted" and d.flights[0].done_ok == "BAD"
    assert d.failures == ["day 1: AUTO STOP mem1 aborted."]
    assert d.feed("Enter tries again, q quits: ") == [("write", "q\n")] and len(d.failures) == 2


def test_driver_ignores_fly_text_outside_a_flight():
    """The ground dry run before a flight prints fly text too: a "preflight OK" there must not arm the drone."""
    d = soak.Driver(1, PLAN, swap_id=None, clock=_clock())
    assert d.feed("  dry run mem1 lab_m_memory_u100 OK\npreflight OK\n") == []
    assert d.feed("  dry run mem2 lab_m_memory_u0 FAILED\n") == [] and len(d.failures) == 1


def test_fixture_drops_camera_and_carry(tmp_path):
    d = soak.fixture(soak.DAY_SRC, 2)
    assert "camera" not in d and d["day"] == "soak-d2" and d["carry"] is False
    assert [f["id"] for f in d["flights"]][:2] == ["mem1", "mem2"] and len(d["flights"]) == 8
    assert len(soak.fixture(soak.DAY_SRC, 1, flights=3)["flights"]) == 3
    assert soak.swap_index(8) == 5 and soak.swap_index(2) == 1 and soak.swap_index(1) == 1


def test_store_cap_allows_one_prune_batch_only():
    st = {"max_rows": 10_000, "prune_every": 1000, "rows": {"telemetry": 10_999, "raw_frames": 5}}
    assert soak.store_ok(st)
    st["rows"]["raw_frames"] = 11_000
    assert not soak.store_ok(st)


def test_debriefs_by_folder_and_stale_mentions(tmp_path):
    ev = tmp_path / "events"
    ev.mkdir()
    (ev / "2026-10-19.jsonl").write_text(
        json.dumps({"event": "debrief", "folder": "logs/flights/a", "status": "complete"}) + "\nnot json\n"
        + json.dumps({"event": "fake_drone_ctl", "word": "arm"}) + "\n", encoding="utf-8")
    assert soak.debriefs(ev) == {"logs/flights/a": {"event": "debrief", "folder": "logs/flights/a",
                                                    "status": "complete"}}
    log = tmp_path / "service.log"
    log.write_text("abort stale_telemetry\n", encoding="utf-8")
    assert soak.stale_mentions(ev, log) == 1


def _done_flight(day, fid, idx, swap=False, max_ms=4.0, over=0, rows=10_500):
    f = soak.Flight(day, fid, idx, 2, fly="lab_m", start_s=1.0, end_s=2.0, swap=swap, link_lost=swap,
                    link_back=swap, new_pack_asked=swap, preflights=2 if swap else 1, armed=True, done_ok="OK",
                    done_status="complete", debrief_status="complete",
                    metrics={"status": "complete", "abort_level": 0, "abort_reason": None, "duration_s": 41.0})
    counts = [0] * (len(lh.EDGES_MS) + 1)
    counts[3] = 100
    counts[-1] = over
    f.lag = {"frames": 100 + over, "counts": counts, "p50_ms": 1.0, "p99_ms": 1.0, "max_ms": max_ms,
             "over_200ms": over}
    f.health = {"rss_mb": 80.0, "ingest_queue": 0, "ingest_dropped": 0,
                "store": {"max_rows": 10_000, "prune_every": 1000, "rows": {"telemetry": rows, "raw_frames": rows}}}
    return f


def _days(**kw):
    return [{"n": 2, "flights": [_done_flight(d, "mem1", 1, swap=True), _done_flight(d, "mem2", 2, **kw)],
             "driver_failures": [], "day_done": True, "stale": 0, "end_health": {"rss_mb": 80.0 + d}}
            for d in (1, 2, 3)]


def _run(days, over=0):
    return {"lag_total": {"frames": 600, "p50_ms": 1.0, "p99_ms": 1.0, "max_ms": 4.0, "over_200ms": over},
            "stale_mentions": 0, "instance_changed": False, "rss_end": [d["end_health"]["rss_mb"] for d in days]}


def test_checks_pass_a_clean_run_and_the_report_says_so():
    days = _days()
    result = soak.checks(days, _run(days))
    assert all(ok for _, ok, _ in result), [r for r in result if not r[1]]
    assert len(result) == 9
    text = soak.report(days, _run(days), result)
    assert text.count("\n| ") == 7                  # header + 6 flights
    assert "No block over 200 ms reproduced in 6 flights (600 frames)." in text


def test_checks_fail_on_each_bad_outcome():
    def failed(days, run=None):
        return {name for name, ok, _ in soak.checks(days, run or _run(days)) if not ok}

    assert failed(_days(over=1)) == {"ingest lag <= 200 ms in every flight"}
    assert failed(_days(max_ms=250.0)) == {"ingest lag <= 200 ms in every flight"}
    assert failed(_days(rows=11_000)) == {"SessionStore holds its 10k-row cap"}
    days = _days()
    days[1]["flights"][1].debrief_status = "aborted"
    assert failed(days) == {"every flight complete", "mid-day pack swap, then the day goes on"}
    days = _days()
    days[0]["flights"][1].metrics["abort_reason"] = "stale_telemetry"
    assert "zero stale_telemetry aborts" in failed(days)
    days = _days()
    days[2]["end_health"]["rss_mb"] = 200.0
    assert failed(days) == {"no memory leak across days"}
    days = _days()
    days[0]["flights"][0].link_back = False
    assert failed(days) == {"mid-day pack swap, then the day goes on"}
    days = _days()
    days[0]["flights"][1].health["ingest_queue"] = 50
    assert failed(days) == {"no queue backlog or drops after any flight"}
    days = _days()
    assert failed(days, dict(_run(days), instance_changed=True)) == {"3 days back to back on one service"}
    assert failed(days, _run(days, over=2)) == {"ingest lag <= 200 ms over the whole run (ground time too)"}
    days = _days()
    days[0]["flights"].pop()                        # a planned flight never flown
    assert "every flight complete" in failed(days)


def test_a_flight_that_never_flew_fails_the_checks_without_crashing():
    """Soak 10-19 quick run: mem2 hung on NO ARM SIGNAL, so it has no lag window, metrics or health."""
    days = _days()
    f = days[0]["flights"][1]
    f.lag, f.health, f.metrics, f.armed = None, {}, {}, False
    f.done_ok = f.done_status = f.debrief_status = None
    days[0]["day_done"] = False
    result = soak.checks(days, _run(days))
    failed = {name for name, ok, _ in result if not ok}
    assert {"every flight complete", "ingest lag <= 200 ms in every flight"} <= failed
    assert "d1 mem2 no lag samples" in dict((n, d) for n, _, d in result)["ingest lag <= 200 ms in every flight"]
    text = soak.report(days, _run(days), result)
    assert "No block over 200 ms reproduced in 5 flights (600 frames). 1 flights have no lag samples." in text


def test_driver_ends_the_day_on_a_refused_go():
    """The fly asks for a disarm and a new arm after a refused GO, and reads no keys on a pipe: the soak stops."""
    d = soak.Driver(1, PLAN, swap_id=None, clock=_clock())
    head, _ = _flight_text("mem1", 1, 2)
    d.feed(head + "--- FLYING mem1: x\npreflight OK: arm by RC to fly\nARMED: takeoff in 3 s\n")
    assert d.ended is None
    d.feed("GO refused (409): campaign deps not ready: No module named 'bench'. Disarm by RC.\n")
    assert d.ended == "day 1: GO refused (409): campaign deps not ready: No module named 'bench'. Disarm by RC."
    assert d.failures == [d.ended]


def test_kill_tree_stops_a_child_started_in_its_own_session():
    # 10-22: the stuck-day cleanup called os.killpg, which Windows lacks, and the soak died with a traceback
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        soak.kill_tree(p)
        assert p.wait(timeout=15) is not None
    finally:
        if p.poll() is None:
            p.kill()


def test_setup_refuses_when_another_service_holds_the_bridge_port(tmp_path, monkeypatch):
    # 10-22: the lab's 8081 service still held 0.0.0.0:14550, took the fake drone's replies, and every soak flight
    # died on "link lost". The soak must stop before it copies the tree or starts anything.
    held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as the bridge binds it
    try:
        held.bind(("0.0.0.0", soak.DRONE_PORT))
    except OSError:
        pass                                                      # a real service holds it: the same condition
    monkeypatch.setattr(soak, "copy_tree", lambda dst: pytest.fail("setup went past the port check"))
    s = soak.Soak(argparse.Namespace(out=str(tmp_path / "run")))
    try:
        with pytest.raises(SystemExit, match="taken"):
            s.setup()
    finally:
        held.close()
    assert s.procs == []
