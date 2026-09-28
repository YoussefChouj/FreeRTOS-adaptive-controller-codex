import json
import socket
import time
from types import SimpleNamespace

import pytest

from ground_station.vofa_studio import core, vofa_config


def test_divider_and_real_rate():
    assert core.divider_for(100) == 1
    assert core.divider_for(30) == 3          # 33.3 Hz real
    assert core.divider_for(0.1) == 255
    assert core.BUDGET_BPS == 87552


@pytest.mark.skipif(not core.ELF.exists(), reason="no ELF")
def test_plan_budget_default_preset():
    from ground_station.livewatch.symbols import SymbolResolver
    plan = core.plan_budget(SymbolResolver(core.ELF), core.load_preset("flight_default")["slots"])
    assert plan["ok"], plan["errors"]
    s0 = plan["slots"][0]
    assert s0["bps"] == (12 + s0["payload"]) * 100 // s0["divider"]
    assert plan["remaining_bps"] == plan["budget_bps"] - plan["total_bps"]
    bad = core.plan_budget(SymbolResolver(core.ELF), [{"rate": 50, "vars": ["no_such_var"]}])
    assert not bad["ok"] and bad["slots"][0]["vars"][0]["ok"] is False


def test_preset_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PRESETS_DIR", tmp_path)
    saved = core.save_preset({"name": "t1", "slots": [{"rate": "25", "vars": [" a ", ""]}],
                              "vofa": ["a"], "junk": 1})
    assert saved == {"name": "t1", "notes": "", "slots": [{"rate": 25.0, "vars": ["a"]}],
                     "vofa": ["a"]}
    assert core.list_presets() == ["t1"] and core.load_preset("t1") == saved
    with pytest.raises(ValueError):
        core.save_preset({"name": "../x", "slots": []})
    with pytest.raises(ValueError):
        core.save_preset({"name": "x", "slots": [{}] * 5})
    core.delete_preset("t1")
    assert core.list_presets() == []


def test_rolling_writer_prunes_and_merges(tmp_path):
    w = core.RollingWriter(tmp_path / "run", 0, ["t_host_s", "v"], window_s=25, segment_s=10)
    for i in range(600):                      # 60 s at 10 Hz
        t = i / 10
        w.write(t, ["%.1f" % t, i])
    assert len(w._segs) <= 4                  # window 25 s / 10 s segments (+ current)
    w.close()
    out = (tmp_path / "run.slot0.csv").read_text().splitlines()
    assert out[0] == "t_host_s,v"
    first = float(out[1].split(",")[0])
    assert 34.8 <= first <= 35.0 and out[-1].startswith("59.9")
    assert not (tmp_path / "run.segments").exists()


def test_vofa_patch_fixture(tmp_path):
    entry = {"is_draw": False, "color": "#ffffff", "scale": 1, "yoffset": 0,
             "xoffset": 0, "decimal": -7, "value": 0, "name": ""}
    cfg = {"type": 1, "ctx": {"wave_view": {"ctx": {"settingsPanel": {"ctx": {".": {
        "settings_ctx": [dict(entry, name="I%d" % i) for i in range(3)]}}}}}}}
    p = tmp_path / "vofa+.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    bak = vofa_config.patch_names(["roll", "pitch", "yaw", "thr", "v"], path=p)
    assert bak.exists()
    assert vofa_config.read_names(p) == ["roll", "pitch", "yaw", "thr", "v"]
    vofa_config.patch_names(["a"], path=p, backup=False)
    assert vofa_config.read_names(p) == ["a", "I1", "I2", "I3", "I4"]
    ctx = json.loads(p.read_text())["ctx"]["wave_view"]["ctx"]["settingsPanel"]["ctx"]["."]["settings_ctx"]
    assert ctx[0]["is_draw"] is True and ctx[0]["decimal"] == -7
    assert vofa_config.CONFIG != p


def _fake_runner(rows_per_slot):
    def runner(_dp, _port, groups, _tr, seconds, _out, stop_event=None,
               on_start=None, on_row=None, **_kw):
        schemas = []
        for slot, (_rate, specs) in enumerate(groups):
            rngs = [SimpleNamespace(_names=None, name=s, count=1, address=0) for s in specs]
            schemas.append(SimpleNamespace(slot=slot, ranges=rngs))
        dec = SimpleNamespace(decoders={s.slot: SimpleNamespace(dropped=0, loss_pct=0.0,
                                                                crc_errors=0) for s in schemas})
        on_start(schemas, dec)
        for i in range(rows_per_slot):
            if stop_event.is_set():
                break
            for s in schemas:
                on_row(s.slot, i & 0xFF, i * 10, i * 0.01,
                       [float(i + k) for k in range(len(s.ranges))])
        return [{"slot": s.slot} for s in schemas]
    return runner


def test_session_fake_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOG_DIR", tmp_path)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    preset = {"name": "p", "slots": [{"rate": 50, "vars": ["a", "b"]},
                                     {"rate": 10, "vars": []},
                                     {"rate": 10, "vars": ["real_voltage"]}],
              "vofa": ["b", "real_voltage", "missing"]}
    s = core.Session(preset, "run1", mode="timed", seconds=5,
                     vofa_addr="127.0.0.1:%d" % rx.getsockname()[1],
                     runner=_fake_runner(20))
    s.start()
    s.join(5)
    assert s.state == "done", s.error
    st = s.status()
    assert [x["rows"] for x in st["slots"]] == [20, 20]     # empty slot dropped
    assert st["vofa"] == ["b", "real_voltage"]
    assert st["health"]["real_voltage"] == 19.0
    assert str((tmp_path / "run1.slot0.csv").resolve()) in st["saved"]
    first = rx.recv(256).decode()
    assert first.endswith("\n") and len(first.strip().split(",")) == 2
    lines = (tmp_path / "run1.slot0.csv").read_text().splitlines()
    assert lines[0] == "t_src_ms,t_host_s,seq,a,b" and len(lines) == 21
    meta = json.loads((tmp_path / "run1.meta.json").read_text())
    assert meta["state"] == "done" and meta["ended_at"]
    s.mark("hover")
    assert "hover" in (tmp_path / "run1.events.csv").read_text()
    rx.close()


def test_session_stop_event(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOG_DIR", tmp_path)

    def slow(*a, stop_event=None, on_start=None, on_row=None, **kw):
        on_start([SimpleNamespace(slot=0, ranges=[SimpleNamespace(
            _names=None, name="a", count=1, address=0)])],
            SimpleNamespace(decoders={0: SimpleNamespace(dropped=0, loss_pct=0, crc_errors=0)}))
        while not stop_event.wait(0.01):
            on_row(0, 0, 0, 0.0, [1.0])
        return []
    s = core.Session({"slots": [{"rate": 10, "vars": ["a"]}]}, "run2",
                     mode="unlimited", vofa_addr="", runner=slow)
    s.start()
    time.sleep(0.1)
    s.stop()
    s.join(2)
    assert s.state == "done"


def test_next_session_name(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOG_DIR", tmp_path)
    (tmp_path / "flight8.slot0.csv").write_text("")
    assert core.next_session_name() == "flight9"


def test_read_tabs_fixture(tmp_path):
    def chart(lines):
        return {"path": "WaveChart", "ctx": {"rbw": {"ctx": {".": {"lines": lines}}}}}
    tv = {"type": "tabviews", "ctx": [{"tabs": [
        {"name": "gyro", "widgets": [chart([2, 1]), chart([1, 5])]},
        {"name": "empty", "widgets": [chart([])]},
        {"name": "bare", "widgets": [{"path": "WaveChart", "ctx": {}}]}]}]}
    p = tmp_path / "vofa+.tabviews.json"
    p.write_text(json.dumps(tv), encoding="utf-8")
    assert vofa_config.read_tabs(p) == [{"name": "gyro", "lines": [1, 2, 5]},
                                        {"name": "empty", "lines": []},
                                        {"name": "bare", "lines": []}]
