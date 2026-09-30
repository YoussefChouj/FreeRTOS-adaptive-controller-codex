"""Saved REC logs for the Path panel Review mode (rec_logs.py + /api/rec-logs)."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request

import pytest

from ground_station.service import rec_logs
from ground_station.service.storage import CsvRecorder

T0 = 1_700_000_000.0


def _ns(t):
    return int(t * 1e9)


def _record(root, label="run1", seconds=10.0, err=0.05, with_des=True, exec_at=None,
            adapt_at=None, z_cm_only=False):
    """Write a session through the real recorder: a circle with a fixed error,
    slot-0 style keys (x/y cm, Z_posPID in m, Des cm)."""
    rec = CsvRecorder(root, enabled=True, flush_interval_s=0.05)
    assert rec.start(label=label)
    n = int(seconds * 50)
    for i in range(n):
        t = T0 + i / 50.0
        a = 0.5 * (i / 50.0)
        dx, dy = 30 * math.cos(a), 30 * math.sin(a)
        vals = {"c.earth_x": dx + 100 * err, "c.earth_y": dy,
                "status.flymode": 1, "status.vbat": 15.5}
        if z_cm_only:
            vals["c.altitude_cm"] = 50.0
        else:
            vals["Ctrler.Z_posPID.FB"] = 0.5
        if with_des:
            vals.update({"Ctrler.locxPID.Des": dx, "Ctrler.locyPID.Des": dy,
                         "Ctrler.Z_posPID.Des": 0.5})
        if adapt_at is not None:
            vals["mrac_flags.adaptation_on"] = 1 if (i / 50.0) >= adapt_at else 0
        rec.note(0, vals, _ns(t))
    # events written with a controlled clock (the recorder stamps wall time)
    if exec_at is not None:
        for cmd, idx, val in ((0x0C, 6, 1),):
            rec._write_event_line("command", {"id": cmd, "idx": idx, "value": val,
                                             "lifecycle": "applied"}, ts=T0 + exec_at)
            rec._write_event_line("command", {"id": cmd, "idx": idx, "value": val,
                                             "lifecycle": "applied"}, ts=T0 + exec_at + 0.5)  # duplicate
    rec._write_event_line("goal", {"text": "gust from door", "kind": "goal", "source": "operator"}, ts=T0 + 4.0)
    rec._write_event_line("note", {"text": "gust from door", "kind": "goal", "source": "operator"}, ts=T0 + 4.05)  # POST logs it twice
    rec._write_event_line("agent", {"text": "roll ringing", "kind": "agent", "source": "agent:copilot"}, ts=T0 + 6.0)
    rec.stop()
    # the samples carry a synthetic clock: make the manifest agree with it
    man = rec.session_dir / "manifest.json"
    m = json.loads(man.read_text())
    m["started_at_epoch"], m["stopped_at_epoch"] = T0, T0 + seconds
    man.write_text(json.dumps(m))
    return rec.session_dir.name


def test_list_and_load_basic(tmp_path):
    name = _record(tmp_path, exec_at=2.0)
    lst = rec_logs.list_rec_logs(tmp_path)
    assert lst["count"] == 1 and lst["logs"][0]["name"] == name
    assert lst["logs"][0]["label"] == "run1" and lst["logs"][0]["recording"] is False

    d = rec_logs.load_rec_log(tmp_path, name)
    assert d["has_setpoint"] and d["has_z"]
    assert d["t_ref"] == "path_execute", "t = 0 at path execute"
    s = d["samples"]
    assert 400 <= len(s) <= 500
    t, x, y, z, dx, dy, dz, e3, exy = s[100]
    assert math.isclose(z, 0.5) and math.isclose(dz, 0.5)
    assert math.isclose(exy, 0.05, abs_tol=2e-3) and math.isclose(e3, 0.05, abs_tol=2e-3), (e3, exy)
    # first sample is 2 s before the path executes
    assert s[0][0] == pytest.approx(-2.0, abs=0.05)
    kinds = [e["kind"] for e in d["events"]]
    assert kinds.count("path") == 1, "command + duplicate within 3 s is one event"
    assert kinds.count("note") == 1, "the note POST's double log is deduped"
    assert kinds.count("finding") == 1
    note = next(e for e in d["events"] if e["kind"] == "note")
    assert note["text"].startswith("goal: gust") and note["pos"] is not None
    assert note["t"] == pytest.approx(4.0 - 2.0, abs=0.01)


def test_rec_start_fallback_and_adaptation_event(tmp_path):
    name = _record(tmp_path, adapt_at=5.0)
    d = rec_logs.load_rec_log(tmp_path, name)
    assert d["t_ref"] == "rec_start"
    assert d["samples"][0][0] == pytest.approx(0.0, abs=0.06)
    ad = [e for e in d["events"] if e["kind"] == "adapt"]
    assert len(ad) == 1 and ad[0]["text"] == "adaptation ON" and ad[0]["t"] == pytest.approx(5.0, abs=0.06)


def test_no_setpoint_means_no_error_not_zero(tmp_path):
    name = _record(tmp_path, with_des=False)
    d = rec_logs.load_rec_log(tmp_path, name)
    assert d["has_setpoint"] is False
    assert all(row[7] is None and row[8] is None for row in d["samples"])


def test_altitude_falls_back_to_cm_key(tmp_path):
    name = _record(tmp_path, z_cm_only=True, with_des=False)
    d = rec_logs.load_rec_log(tmp_path, name)
    assert d["samples"][0][3] == pytest.approx(0.5)


def test_downsample_keeps_ends(tmp_path):
    name = _record(tmp_path, seconds=20.0)
    d = rec_logs.load_rec_log(tmp_path, name, max_points=100)
    assert len(d["samples"]) == 100 and d["truncated"] is True
    assert d["n_samples"] > 900
    full = rec_logs.load_rec_log(tmp_path, name, max_points=20000)
    assert d["samples"][0] == full["samples"][0] and d["samples"][-1] == full["samples"][-1]


@pytest.mark.parametrize("bad", ["../etc", "a/b", "..", "", "x" * 200, "a b", "a\\b"])
def test_bad_names_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        rec_logs.load_rec_log(tmp_path, bad)


def test_missing_log(tmp_path):
    with pytest.raises(FileNotFoundError):
        rec_logs.load_rec_log(tmp_path, "20260101-000000-nothing")


def test_slot_prefixed_keys_and_junk_rows(tmp_path):
    d = tmp_path / "20260101-000000-x"
    d.mkdir()
    rows = ["received_ns,slot,key,value"]
    for i in range(50):
        t = _ns(T0 + i * 0.05)
        rows += ["%d,1,slot1.ano_of.earth_x,%d" % (t, i), "%d,1,slot1.ano_of.earth_y,0" % t,
                 "%d,1,junk,not-a-number" % t, "garbage-line", "%d,1,c.altitude_cm,nan" % t]
    (d / "telemetry.csv").write_text("\n".join(rows), encoding="utf-8")
    out = rec_logs.load_rec_log(tmp_path, d.name)
    assert out["n_samples"] >= 40
    assert out["samples"][-1][1] == pytest.approx(0.49, abs=0.02)   # cm -> m
    assert out["has_z"] is False, "NaN altitude is not a height"


def test_list_skips_dirs_without_telemetry(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "bad name").mkdir()
    assert rec_logs.list_rec_logs(tmp_path)["count"] == 0
    assert rec_logs.list_rec_logs(tmp_path / "nope")["logs"] == []


# -- HTTP routes -------------------------------------------------------------
def _serve(root):
    from ground_station.service.api import ApiServer
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore
    svc = GroundStationService(store=SessionStore(), schemas=[], source="sim")
    svc.recorder = CsvRecorder(root, enabled=True)
    svc.start()
    api = ApiServer(svc, host="127.0.0.1", port=0)
    api.start()
    return svc, api


def _get(api, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d%s" % (api.address[1], path), timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_routes_list_load_and_errors(tmp_path):
    name = _record(tmp_path)
    svc, api = _serve(tmp_path)
    try:
        st, lst = _get(api, "/api/rec-logs")
        assert st == 200 and [l["name"] for l in lst["logs"]] == [name]
        st, d = _get(api, "/api/rec-logs/%s?max_points=120" % name)
        assert st == 200 and len(d["samples"]) == 120
        st, _ = _get(api, "/api/rec-logs/nope-nope")
        assert st == 404
        st, _ = _get(api, "/api/rec-logs/..%2F..%2Fetc")
        assert st in (400, 404)
        st, routes = _get(api, "/api/routes")
        assert st == 200 and any("rec-logs" in k for k in json.dumps(routes).split('"'))
    finally:
        api.stop()
        svc.stop()


def _manual(root, name, frames, events=()):
    """A session dir written by hand: frames = [(t, {key: value})]."""
    d = root / name
    d.mkdir(parents=True)
    rows = ["received_ns,slot,key,value"]
    for t, vals in frames:
        rows += ["%d,0,%s,%s" % (_ns(t), k, v) for k, v in vals.items()]
    (d / "telemetry.csv").write_text("\n".join(rows) + "\n")
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "manifest.json").write_text(json.dumps({"started_at_epoch": T0}))
    return d


def test_last_frame_kept_inside_throttle_window(tmp_path):
    rec_logs._CACHE.clear()
    _manual(tmp_path, "20260101-000000", [(T0, {"c.earth_x": 0, "c.earth_y": 0}),
                                           (T0 + 0.005, {"c.earth_x": 42, "c.earth_y": 0})])
    s = rec_logs.load_rec_log(tmp_path, "20260101-000000")["samples"]
    assert len(s) == 2 and s[-1][1] == pytest.approx(0.42)


def test_event_pos_without_altitude_is_null_not_zero(tmp_path):
    rec_logs._CACHE.clear()
    _manual(tmp_path, "20260101-000000",
            [(T0 + i / 50.0, {"c.earth_x": 10, "c.earth_y": 20}) for i in range(10)],
            [{"t": T0 + 0.05, "kind": "note", "source": "operator", "data": {"text": "hi"}}])
    ev = rec_logs.load_rec_log(tmp_path, "20260101-000000")["events"][0]
    assert ev["pos"][:2] == [0.1, 0.2] and ev["pos"][2] is None


def test_cache_sees_notes_appended_after_stop(tmp_path):
    rec_logs._CACHE.clear()
    name = _record(tmp_path)
    before = len(rec_logs.load_rec_log(tmp_path, name)["events"])
    ev = tmp_path / name / "events.jsonl"
    with ev.open("a") as fh:
        fh.write(json.dumps({"t": T0 + 8.0, "kind": "note", "source": "operator",
                             "data": {"text": "late note"}}) + "\n")
    after = rec_logs.load_rec_log(tmp_path, name)["events"]
    assert len(after) == before + 1 and any(e["text"] == "late note" for e in after)


def test_recording_flag_only_for_active_session(tmp_path):
    for name in ("20260101-000000", "20260101-000100"):
        _manual(tmp_path, name, [(T0, {"c.earth_x": 0, "c.earth_y": 0})])   # no stopped_at
    logs = {r["name"]: r["recording"] for r in rec_logs.list_rec_logs(tmp_path)["logs"]}
    assert logs == {"20260101-000000": False, "20260101-000100": False}   # crashed, not live
    logs = {r["name"]: r["recording"]
            for r in rec_logs.list_rec_logs(tmp_path, active="20260101-000100")["logs"]}
    assert logs == {"20260101-000000": False, "20260101-000100": True}


def test_concurrent_loads_single_flight_and_capped(tmp_path, monkeypatch):
    import threading
    rec_logs._CACHE.clear()
    names = [_record(tmp_path, label="c%d" % i, seconds=1.0) for i in range(4)]
    real = rec_logs._scan_telemetry
    calls, live, peak, lock = [], [0], [0], threading.Lock()

    def slow(path):
        with lock:
            calls.append(path.parent.name)
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        try:
            return real(path)
        finally:
            with lock:
                live[0] -= 1

    monkeypatch.setattr(rec_logs, "_scan_telemetry", slow)
    out = []
    threads = [threading.Thread(target=lambda n=n: out.append(rec_logs.load_rec_log(tmp_path, n)))
               for n in names + names]           # every log requested twice at once
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert len(out) == 8
    assert sorted(calls) == sorted(names)        # one parse per log
    assert peak[0] <= 2                          # at most two parses at a time
    assert not rec_logs._INFLIGHT


def test_dz_is_metres_raw(tmp_path):
    rec_logs._CACHE.clear()
    _manual(tmp_path, "20260101-000000",
            [(T0, {"c.earth_x": 20.0, "c.earth_y": 0.0, "c.altitude": 1.5,
                   "pid.locx.Des": 10.0, "pid.locy.Des": 0.0, "pid.z_pos.Des": 1.2})])
    d = rec_logs.load_rec_log(tmp_path, "20260101-000000")
    s = d["samples"][0]
    t, x, y, z, dx, dy, dz, e3, exy = s
    assert math.isclose(x, 0.2) and math.isclose(dx, 0.1)
    assert math.isclose(z, 1.5) and math.isclose(dz, 1.2)
    assert e3 == pytest.approx(math.hypot(0.1, 0.3), abs=1e-4)
