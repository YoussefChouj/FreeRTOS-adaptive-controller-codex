"""Streams panel backend: slot swaps, budget, tab coverage, logging, forward."""
from __future__ import annotations

import csv
import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from ground_station.service import streams
from ground_station.service.streams import (
    StreamLogger, StreamsManager, TAB_NEEDS, VofaForward, default_slots,
)
from ground_station.service.telemetry_adapter import StreamMetadata
from ground_station.vofa_studio import core as vcore

ROOT = Path(__file__).resolve().parents[3]
PLUGINS = ROOT / "docs" / "dashboard-platform" / "shell" / "plugins"
needs_elf = pytest.mark.skipif(not vcore.ELF.exists(), reason="no ELF")


# ------------------------------------------------------------------ fakes
class FakeBridge:
    def __init__(self, reply=True):
        self.calls, self.requests = [], []
        self._stream_lock = threading.Lock()
        self._stream_schemas, self._slot_states = {}, {}
        self._resubscribe_fn = None
        self._resubscribe_layout = "dashboard"
        self.reply = reply

    def subscribe_slot(self, slot, divider, ranges=None, transport=1):
        self.calls.append((slot, divider, list(ranges or [])))
        if self.reply:
            with self._stream_lock:
                self._stream_schemas[slot] = SimpleNamespace(
                    divider=divider, ranges=list(ranges or []))
        return len(ranges or [])

    def _request_stream_schema(self, slot, vars_, divider, label):
        self.requests.append((slot, tuple(vars_), divider, label))
        if self.reply:
            with self._stream_lock:
                self._stream_schemas[slot] = SimpleNamespace(
                    divider=divider, ranges=list(vars_))


def _service(arm="disarmed", preset=None, bridge=True):
    return SimpleNamespace(bridge=FakeBridge() if bridge else None,
                           arm_state=lambda: arm, active_preset=preset)


@pytest.fixture(scope="module")
def resolver():
    if not vcore.ELF.exists():
        pytest.skip("no ELF")
    from ground_station.livewatch.symbols import SymbolResolver
    return SymbolResolver(str(vcore.ELF))


def _mgr(resolver, tmp_path, **kw):
    svc = _service(**kw)
    m = StreamsManager(svc, resolver_factory=lambda: resolver,
                       log_dir=tmp_path / "logs", sleep=lambda s: None)
    return svc, m


SMALL = {"slot": 1, "vars": ["imu_data.rol", "imu_data.pit"], "rate": 10,
         "name": "mini"}


# --------------------------------------------- the tab dependency table
def test_tab_needs_verified_against_plugins_and_default_layout():
    from ground_station.service.schema_registry import SchemaRegistry
    aliases = SchemaRegistry.builtin_dashboard()
    slots = default_slots()
    for stem, label, needs in TAB_NEEDS:
        src = (PLUGINS / (stem + ".js")).read_text(encoding="utf-8")
        for slot, vars_ in needs.items():
            for v in vars_:
                names = [n for n in (v, aliases.resolve(v)) if n]
                assert any(re.search(r"[\"'`.\s]" + re.escape(n)
                                     + r"[\"'`\s\[\],;)}:.]", src)
                           for n in names),                     "%s does not mention %s" % (stem, " or ".join(names))
                assert v in slots[slot]["vars"], \
                    "%s needs %s on slot %d but the default layout lacks it" % (
                        stem, v, slot)
                assert v not in slots[0]["vars"], \
                    "%s is on slot 0; it does not belong in TAB_NEEDS" % v


def test_default_slots_match_bridge_layout():
    d = default_slots()
    assert [s["slot"] for s in d] == [0, 1, 2, 3]
    assert [s["divider"] for s in d] == [4, 5, 2, 4]
    assert d[0]["rate"] == 25.0


@needs_elf
def test_default_layout_fits_the_link(resolver):
    from ground_station.vofa_studio.core import plan_budget
    plan = plan_budget(resolver, [{"rate": s["rate"], "vars": s["vars"]}
                                  for s in default_slots()])
    assert plan["ok"], (plan["errors"], [s["errors"] for s in plan["slots"]])


# ------------------------------------------------------------ tab status
@needs_elf
def test_default_layout_feeds_every_tab(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    assert all(t["ok"] for t in m.tab_status())


@needs_elf
def test_swap_flags_tabs_and_names_the_holder(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    m._overrides[1] = dict(SMALL, divider=10, source="custom", default=False)
    tabs = {t["tab"]: t for t in m.tab_status()}
    est = tabs["estimator-panel"]
    assert not est["ok"] and est["reason"] == "not streamed: slot 1 holds mini"
    assert est["restore_slots"] == [1]
    assert {"var": "ano_of.of_alt_cm", "slot": 1} in tabs["status-panel"]["missing"]
    # slot 2 group is still the dashboard one, so nothing to restore there
    assert tabs["command-panel"]["restore_slots"] == [1]
    # a tab needing only slot 3 stays green
    assert tabs["safety-panel"]["ok"] is False and tabs["path-panel"]["ok"] is False


@needs_elf
def test_custom_slot_carrying_the_variable_satisfies_the_tab(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    m._overrides[1] = dict(SMALL, vars=SMALL["vars"] + [
        "ano_of.of_alt_cm", "Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real"],
                           divider=10, source="custom", default=False)
    tabs = {t["tab"]: t for t in m.tab_status()}
    assert tabs["status-panel"]["ok"] and tabs["time-series-panel"]["ok"]
    assert not tabs["overview-panel"]["ok"]      # still needs s_state etc.


# --------------------------------------------------------------- refusals
@pytest.mark.parametrize("kw,frag", [
    ({"arm": "armed"}, "disarmed"),
    ({"arm": "unknown"}, "disarmed"),
    ({"preset": "flight_comprehensive"}, "dashboard layout first"),
    ({"bridge": False}, "no WiFi bridge"),
])
def test_apply_refusals(resolver, tmp_path, kw, frag):
    svc, m = _mgr(resolver, tmp_path, **kw)
    code, body = m.apply([SMALL], background=False)
    assert code == 409 and frag in body["error"]
    if svc.bridge:
        assert svc.bridge.calls == []


@needs_elf
@pytest.mark.parametrize("item,frag", [
    ({"slot": 0, "vars": ["imu_data.rol"], "rate": 10}, "cannot be swapped"),
    ({"slot": 4, "vars": ["imu_data.rol"], "rate": 10}, "cannot be swapped"),
    ({"slot": 1, "vars": [], "rate": 10}, "no variables"),
    ({"slot": 1, "vars": ["imu_data.rol"], "rate": 0}, "rate"),
    ({"slot": 1, "vars": ["no_such_variable_x"], "rate": 10}, "slot 1"),
])
def test_apply_rejects_bad_items(resolver, tmp_path, item, frag):
    svc, m = _mgr(resolver, tmp_path)
    code, body = m.apply([item], background=False)
    assert code in (400, 409) and frag in body["error"], body
    assert svc.bridge.calls == []


@needs_elf
def test_apply_rejects_over_budget(resolver, tmp_path):
    svc, m = _mgr(resolver, tmp_path)
    big = [v for v in default_slots()[0]["vars"]] + \
        default_slots()[2]["vars"] + default_slots()[3]["vars"]
    code, body = m.apply([{"slot": 1, "vars": big, "rate": 100}], background=False)
    assert code == 409
    assert "budget" in body["error"] or "range" in body["error"], body
    assert svc.bridge.calls == []


def test_apply_rejects_duplicate_slot_and_empty(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    assert m.apply([], background=False)[0] == 400
    assert m.apply([SMALL, SMALL], background=False)[0] == 400


# ------------------------------------------------------------ swap + restore
@needs_elf
def test_swap_then_restore_round_trip(resolver, tmp_path):
    svc, m = _mgr(resolver, tmp_path)
    code, body = m.apply([SMALL], background=False)
    assert code == 200 and body["ok"], body
    assert svc.bridge.calls == [(1, 10, ["imu_data.rol", "imu_data.pit"])]
    snap = m.snapshot()
    assert snap["slots"][1]["name"] == "mini" and not snap["slots"][1]["default"]
    assert snap["slots"][0]["fixed"] and snap["can_swap"]
    assert svc.bridge._resubscribe_fn is not None
    assert svc.bridge._resubscribe_layout == "streams"
    # restore puts the dashboard group back and re-arms the plain watchdog
    code, body = m.restore([1], background=False)
    assert code == 200, body
    slot, div, names = svc.bridge.calls[-1]
    assert slot == 1 and div == 5 and names == default_slots()[1]["vars"]
    assert m.snapshot()["slots"][1]["default"]
    assert svc.bridge._resubscribe_fn is None
    assert svc.bridge._resubscribe_layout == "dashboard"
    assert m.restore("all")[1]["started"] is False


@needs_elf
def test_missing_schema_retries_then_reports(resolver, tmp_path):
    svc, m = _mgr(resolver, tmp_path)
    svc.bridge.reply = False
    code, body = m.apply([SMALL], background=False)
    assert code == 500 and "no schema reply" in body["error"]
    assert len(svc.bridge.calls) == 1 + m.MAX_RETRIES
    assert m.status["busy"] is False


@needs_elf
def test_replay_keeps_swaps_and_defaults(resolver, tmp_path):
    svc, m = _mgr(resolver, tmp_path)
    m.apply([SMALL], background=False)
    svc.bridge.calls.clear()
    svc.bridge._resubscribe_fn()
    assert [r[0] for r in svc.bridge.requests] == [0, 2, 3]   # defaults, not 1
    assert svc.bridge.calls == [(1, 10, ["imu_data.rol", "imu_data.pit"])]


@needs_elf
def test_reset_after_preset_apply(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    m.apply([SMALL], background=False)
    m.reset()
    assert all(s["default"] for s in m.snapshot()["slots"])


@needs_elf
def test_second_apply_while_busy_is_refused(resolver, tmp_path):
    _, m = _mgr(resolver, tmp_path)
    assert m._apply_lock.acquire(blocking=False)
    try:
        code, body = m.apply([SMALL], background=False)
        assert code == 409 and "already running" in body["error"]
    finally:
        m._apply_lock.release()


# ---------------------------------------------------------------- logging
def _sample(vals, seq=1, t_ms=100):
    # The live service passes a StreamMetadata dataclass; a dict here hid a crash in note().
    return SimpleNamespace(values=vals, received_ns=0,
                           metadata=StreamMetadata(sequence=seq, source_time_ms=t_ms))


def _rows(path):
    with Path(path).open(newline="") as fh:
        return list(csv.reader(fh))


def test_unlimited_log_columns_and_lookup(tmp_path):
    lg = StreamLogger(tmp_path)
    lg.start("run", "unlimited", {1: {"vars": ["a", "b.c", "d"], "rate": 10}})
    lg.note(1, _sample({"slot1.a": 1.5, "b.c": [2.5]}), now=time.monotonic())
    lg.note(1, _sample({"a": 3, "b.c": [1, 2], "d": 9}, seq=2))
    lg.note(2, _sample({"a": 99}))            # unlogged slot is ignored
    st = lg.stop()
    assert st["rows"] == {1: 2} and not st["active"]
    rows = _rows(tmp_path / "run.slot1.csv")
    assert rows[0] == ["t_src_ms", "t_host_s", "seq", "a", "b.c", "d"]
    assert rows[1][0] == "100" and rows[1][2] == "1"   # t_src_ms, seq from the metadata
    assert rows[1][3:] == ["1.5", "2.5", ""]
    assert rows[2][3:] == ["3", "", "9"]      # 2-element list is not a scalar
    meta = json.loads((tmp_path / "run.meta.json").read_text())
    assert meta["finished"] and meta["mode"] == "unlimited"


def test_timed_log_stops_itself(tmp_path):
    lg = StreamLogger(tmp_path)
    lg.start("t", "timed", {1: {"vars": ["a"], "rate": 10}}, seconds=5)
    lg.note(1, _sample({"a": 1}), now=lg.t0 + 1)
    assert lg.active
    lg.note(1, _sample({"a": 2}), now=lg.t0 + 6)
    assert not lg.active
    assert len(_rows(tmp_path / "t.slot1.csv")) == 3


def test_rolling_log_keeps_only_the_window(tmp_path):
    lg = StreamLogger(tmp_path)
    lg.start("r", "rolling", {1: {"vars": ["a"], "rate": 10}}, window_s=20)
    for i in range(60):
        lg.note(1, _sample({"a": i}, seq=i), now=lg.t0 + i)
    lg.stop()
    rows = _rows(tmp_path / "r.slot1.csv")[1:]
    ts = [float(r[1]) for r in rows]
    assert ts[-1] == pytest.approx(59, abs=0.01) and ts[0] >= 39 - 0.01
    assert not (tmp_path / "r.segments").exists()


def test_swap_mid_log_starts_a_new_part(tmp_path):
    lg = StreamLogger(tmp_path)
    lg.start("s", "unlimited", {1: {"vars": ["a"], "rate": 10}})
    lg.note(1, _sample({"a": 1}))
    lg.swap_slot(1, {"vars": ["z"], "rate": 5})
    lg.note(1, _sample({"z": 7}))
    lg.stop()
    assert _rows(tmp_path / "s.slot1.csv")[0][3:] == ["a"]
    assert _rows(tmp_path / "s.part2.slot1.csv")[0][3:] == ["z"]
    assert _rows(tmp_path / "s.part2.slot1.csv")[1][3] == "7"


def test_logger_validation(tmp_path):
    lg = StreamLogger(tmp_path)
    for bad in (dict(name="../x", mode="timed"), dict(name="ok", mode="weird")):
        with pytest.raises(ValueError):
            lg.start(slots={1: {"vars": ["a"], "rate": 1}}, **bad)
    with pytest.raises(ValueError):
        lg.start("ok", "timed", {})
    lg.start("ok", "timed", {1: {"vars": ["a"], "rate": 1}})
    with pytest.raises(RuntimeError):
        lg.start("ok2", "timed", {1: {"vars": ["a"], "rate": 1}})
    lg.stop()


# ---------------------------------------------------------------- forward
class FakeSock:
    def __init__(self):
        self.sent, self.closed = [], False

    def sendto(self, data, target):
        self.sent.append((data.decode(), target))

    def close(self):
        self.closed = True


def test_forward_emits_on_fastest_slot_with_hold():
    sock = FakeSock()
    fw = VofaForward(sock_factory=lambda: sock)
    slots = {1: {"vars": ["a", "b"], "rate": 20}, 2: {"vars": ["c"], "rate": 50}}
    fw.start(["a", "c", "missing"], slots)
    assert fw.channels == ["a", "c"]
    fw.note(1, _sample({"a": 1.5}))            # slow slot: hold only
    assert sock.sent == []
    fw.note(2, _sample({"c": 7}))              # fast slot: emit
    fw.note(2, _sample({"c": 8}))
    assert sock.sent == [("1.5,7\n", ("127.0.0.1", 1347)),
                         ("1.5,8\n", ("127.0.0.1", 1347))]
    assert fw.stop()["active"] is False and sock.closed
    fw.note(2, _sample({"c": 9}))
    assert len(sock.sent) == 2


def test_forward_refuses_without_channels():
    fw = VofaForward(sock_factory=FakeSock)
    slots = {1: {"vars": ["a"], "rate": 20}}
    with pytest.raises(ValueError):
        fw.start([], slots)
    with pytest.raises(ValueError):
        fw.start(["nope"], slots)
    with pytest.raises(ValueError):
        fw.start(["a"], slots, addr="nonsense")


# ------------------------------------------------------------ HTTP handlers
@needs_elf
def test_handlers_plan_presets_log_forward(resolver, tmp_path, monkeypatch):
    svc, m = _mgr(resolver, tmp_path)
    svc.streams = m
    monkeypatch.setattr(vcore, "PRESETS_DIR", tmp_path / "presets")
    code, body = streams.handle_get(svc, "/api/streams")
    assert code == 200 and len(body["slots"]) == 4 and body["plan"]["ok"]
    code, body = streams.handle_post(svc, "/api/streams/plan",
                                     {"slots": [{"rate": 10, "vars": ["imu_data.rol"]}]})
    assert code == 200 and body["ok"]
    assert streams.handle_post(svc, "/api/streams/plan", {})[0] == 400
    code, _ = streams.handle_post(svc, "/api/streams/presets",
                                  {"name": "mine", "slots": [{"rate": 10, "vars": ["imu_data.rol"]}],
                                   "vofa": ["imu_data.rol"]})
    assert code == 200
    assert [p["name"] for p in streams.handle_get(svc, "/api/streams/presets")[1]["presets"]] == ["mine"]
    assert streams.handle_get(svc, "/api/streams/presets/mine")[1]["vofa"] == ["imu_data.rol"]
    assert streams.handle_get(svc, "/api/streams/presets/nope")[0] == 404
    assert streams.handle_post(svc, "/api/streams/presets", {"name": "bad name"})[0] == 400
    assert streams.handle_post(svc, "/api/streams/presets/delete", {"name": "mine"})[0] == 200
    assert streams.handle_post(svc, "/api/streams/presets/delete", {"name": "mine"})[0] == 404
    # log
    code, st = streams.handle_post(svc, "/api/streams/log/start",
                                   {"name": "h", "mode": "unlimited", "slots": [1]})
    assert code == 200 and st["active"]
    assert streams.handle_post(svc, "/api/streams/log/start", {"name": "h2"})[0] == 409
    m.note(1, _sample({"imu_data.rol": 1}))
    code, st = streams.handle_post(svc, "/api/streams/log/stop", {})
    assert code == 200 and not st["active"]
    # forward: refuses with no channels, accepts a real variable
    assert streams.handle_post(svc, "/api/streams/forward", {"enable": True})[0] == 400
    assert streams.handle_post(svc, "/api/streams/forward",
                               {"enable": True, "channels": [default_slots()[0]["vars"][0]]})[0] == 200
    assert streams.handle_post(svc, "/api/streams/forward", {"enable": False})[1]["active"] is False
    assert streams.handle_post(svc, "/api/streams/nope", {})[0] == 404


@needs_elf
def test_service_ingest_hook_reaches_the_manager(resolver, tmp_path):
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore
    svc = GroundStationService(store=SessionStore(), schemas=[], source="sim")
    got = []
    svc.streams = SimpleNamespace(note=lambda slot, s: got.append(slot))
    svc._note_recorder(2, _sample({"a": 1}))
    assert got == [2]
    svc.streams = SimpleNamespace(note=lambda *a: 1 / 0)      # must not raise
    svc._note_recorder(2, _sample({"a": 1}))


@needs_elf
def test_http_routes_end_to_end(tmp_path):
    from ground_station.service.api import ApiServer
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore
    svc = GroundStationService(store=SessionStore(), schemas=[], source="sim")
    svc.start()
    api = ApiServer(svc, host="127.0.0.1", port=0)
    api.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % api.address[1]

    def call(path, body=None):
        req = urllib.request.Request(
            base + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    try:
        st, snap = call("/api/streams")
        assert st == 200 and snap["slots"][0]["fixed"] and snap["can_swap"] is False
        st, body = call("/api/streams/apply", {"slots": [SMALL]})
        assert st == 409                       # no bridge / not disarmed
        st, routes = call("/api/routes")
        assert any("/api/streams/apply" in k for k in json.dumps(routes).split('"'))
    finally:
        api.stop()
        svc.stop()


# ------------------------------------------------- VOFA Studio startup guard
def test_vofa_studio_refuses_while_dashboard_answers():
    from ground_station.vofa_studio import __main__ as vmain

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert vmain.dashboard_owns_link(port) is True
        import sys
        old = sys.argv
        sys.argv = ["vofa_studio", "--no-browser", "--dashboard-port", str(port)]
        try:
            with pytest.raises(SystemExit) as ex:
                vmain.main()
            assert ex.value.code == 2
        finally:
            sys.argv = old
    finally:
        srv.shutdown()
    assert vmain.dashboard_owns_link(port) is False


# ---------------------------------------------------- test 2: _replay timing
@needs_elf
def test_replay_timing_and_retries(resolver, tmp_path):
    """_replay: one slot at a time, >= 0.15s gap, retries on missing reply."""
    clock = [0.0]
    sleep_log = []

    def fake_sleep(s):
        sleep_log.append(s)
        clock[0] += s

    svc = _service()
    m = StreamsManager(svc, resolver_factory=lambda: resolver,
                       log_dir=tmp_path / "logs", sleep=fake_sleep)

    # Set up: override slot 1, defaults for 0, 2, 3
    m.apply([SMALL], background=False)
    svc.bridge.calls.clear()
    svc.bridge.requests.clear()

    # Make slot 2 never reply by selectively suppressing
    orig_request = svc.bridge._request_stream_schema
    slot2_call_count = [0]

    def patched_request(slot, vars_, divider, label):
        orig_request(slot, vars_, divider, label)
        if slot == 2:
            slot2_call_count[0] += 1
            # Remove the schema so it looks like no reply
            with svc.bridge._stream_lock:
                svc.bridge._stream_schemas.pop(slot, None)

    svc.bridge._request_stream_schema = patched_request

    failed = svc.bridge._resubscribe_fn()

    # Slot 2 should be in the failed list
    assert 2 in failed

    # Defaults: slots 0, 2, 3 were requested (not 1 - it's overridden)
    default_request_slots = [r[0] for r in svc.bridge.requests]
    assert 0 in default_request_slots
    assert 2 in default_request_slots
    assert 3 in default_request_slots
    assert 1 not in default_request_slots

    # Slot 1 was re-sent via subscribe_slot
    override_slots = [c[0] for c in svc.bridge.calls]
    assert 1 in override_slots

    # Slot 2 was sent 1 + 3 retries = 4 times (once initial + 3 retries in _await_schemas)
    assert slot2_call_count[0] == 1 + m.REPLAY_RETRIES

    # Sleep gaps >= 0.15s between slots
    gap_sleeps = [s for s in sleep_log if s >= 0.14]
    assert len(gap_sleeps) >= 3  # at least 3 inter-slot gaps (for 4 slots)


# ---------------------------------------------------- test 3: logger faults
def test_logger_empty_rows_dropped_and_faults(tmp_path):
    """All-empty rows dropped; 0 rows 2 s after start -> slot_faults in meta."""
    lg = StreamLogger(tmp_path)
    lg.start("faults", "unlimited", {
        1: {"vars": ["a", "b"], "rate": 10},
        2: {"vars": ["c"], "rate": 10},
    })
    # Note: slot 1 gets real data; slot 2 gets nothing
    lg.note(1, _sample({"a": 1.0, "b": 2.0}), now=lg.t0 + 0.1)
    # All-empty row: both vars are None
    lg.note(1, _sample({}), now=lg.t0 + 0.2)
    # Another good row
    lg.note(1, _sample({"a": 3.0}), now=lg.t0 + 0.3)

    # Trigger fault check at 2+ seconds
    lg.note(1, _sample({"a": 4.0}), now=lg.t0 + 2.1)

    st = lg.stop()
    # Slot 2 should be in faults (0 rows, rate > 0, 2s after start)
    assert 2 in st["slot_faults"]
    # Slot 1 should NOT be in faults
    assert 1 not in st["slot_faults"]

    # Verify rows: all-empty row was dropped
    assert st["rows"][1] == 3  # 3 good rows (the empty one was dropped)

    # Verify meta.json
    meta = json.loads((tmp_path / "faults.meta.json").read_text())
    assert meta["slot_status"]["2"] == "fault"
    assert meta["slot_status"]["1"] == "ok"
    assert 2 in meta["slot_faults"]
    assert meta["rows"]["1"] == 3
    assert meta["rows"]["2"] == 0


# ------------------------------------------------ test 4: preflight check
def test_preflight_all_growing(tmp_path):
    """All slots growing -> (True, '')."""

    class FakePrefBridge:
        _stream_stats = {0: {"received": 0}, 1: {"received": 0},
                         2: {"received": 0}, 3: {"received": 0}}
        _stream_lock = threading.Lock()

        def resend_slot(self, slot):
            return True

    bridge = FakePrefBridge()
    svc = SimpleNamespace(bridge=bridge)
    m = StreamsManager(svc, resolver_factory=lambda: None,
                       log_dir=tmp_path / "logs",
                       sleep=lambda s: _advance_bridge_stats(bridge, s))
    ok, reason = m.preflight_check()
    assert ok is True
    assert reason == ""


def _advance_bridge_stats(bridge, s):
    """Simulate stream stats advancing during sleep."""
    with bridge._stream_lock:
        for slot in bridge._stream_stats:
            bridge._stream_stats[slot]["received"] += 100


def test_preflight_slot_frozen(tmp_path):
    """Slot 3 frozen -> resend_slot(3) once and (False, 'slot 3 silent')."""
    resend_calls = []

    class FakePrefBridge:
        _stream_stats = {0: {"received": 0}, 1: {"received": 0},
                         2: {"received": 0}, 3: {"received": 0}}
        _stream_lock = threading.Lock()

        def resend_slot(self, slot):
            resend_calls.append(slot)
            return True

    bridge = FakePrefBridge()
    svc = SimpleNamespace(bridge=bridge)

    def sleep_fn(s):
        # Only advance slots 0-2, slot 3 stays frozen
        with bridge._stream_lock:
            for slot in (0, 1, 2):
                bridge._stream_stats[slot]["received"] += 100

    m = StreamsManager(svc, resolver_factory=lambda: None,
                       log_dir=tmp_path / "logs", sleep=sleep_fn)
    ok, reason = m.preflight_check()
    assert ok is False
    assert "slot 3 silent" in reason
    assert 3 in resend_calls


def test_preflight_no_bridge_or_no_stats_allowed(tmp_path):
    """No bridge or no _stream_stats -> (True, '')."""
    # No bridge
    svc = SimpleNamespace(bridge=None)
    m = StreamsManager(svc, resolver_factory=lambda: None,
                       log_dir=tmp_path / "logs", sleep=lambda s: None)
    ok, reason = m.preflight_check()
    assert ok is True

    # Bridge without _stream_stats
    svc2 = SimpleNamespace(bridge=SimpleNamespace(_stream_lock=threading.Lock()))
    m2 = StreamsManager(svc2, resolver_factory=lambda: None,
                        log_dir=tmp_path / "logs2", sleep=lambda s: None)
    ok2, reason2 = m2.preflight_check()
    assert ok2 is True


@needs_elf
def test_log_start_preflight_failure(resolver, tmp_path):
    """log/start -> 409 'slot 3 silent'; skip_preflight bypasses."""
    class FakePrefBridge:
        _stream_stats = {0: {"received": 0}, 1: {"received": 0},
                         2: {"received": 0}, 3: {"received": 0}}
        _stream_lock = threading.Lock()
        _stream_schemas = {}
        _slot_states = {}
        _resubscribe_fn = None
        _resubscribe_layout = "dashboard"

        def subscribe_slot(self, **kw):
            return 0

        def _request_stream_schema(self, *a, **kw):
            pass

        def resend_slot(self, slot):
            return True

    bridge = FakePrefBridge()

    def sleep_fn(s):
        # Advance all slots except 3
        with bridge._stream_lock:
            for slot in (0, 1, 2):
                bridge._stream_stats[slot]["received"] += 100

    svc = SimpleNamespace(bridge=bridge, arm_state=lambda: "disarmed",
                          active_preset=None)
    m = StreamsManager(svc, resolver_factory=lambda: resolver,
                       log_dir=tmp_path / "logs", sleep=sleep_fn)
    m._overrides[3] = {"vars": ["y"], "rate": 10, "divider": 10,
                        "source": "custom", "default": False, "name": "b"}
    svc.streams = m

    # preflight fails -> 409
    code, body = streams.handle_post(svc, "/api/streams/log/start",
                                     {"name": "pf", "mode": "unlimited", "slots": [3]})
    assert code == 409
    assert "slot 3 silent" in body["error"]

    # skip_preflight bypasses
    code, body = streams.handle_post(svc, "/api/streams/log/start",
                                     {"name": "pf2", "mode": "unlimited", "slots": [3],
                                      "skip_preflight": True})
    assert code == 200
    assert body["active"]
    m.logger.stop()

