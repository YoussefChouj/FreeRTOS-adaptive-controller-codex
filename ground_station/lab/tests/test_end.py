"""end.py: the day report from the queue state, and the videos' move to Drive (copied, checked, then removed)."""
import json

from ground_station.lab import catalog, end, runner
from ground_station.lab import __main__ as lab

QUEUE = str(end.ROOT / "docs" / "lab" / "queue-2026-10-15.txt")
CARDS = ("fusion-hover", "pad-landing", "xy-ctrl-screen", "xy-ctrl-confirm")        # the queue's cards


def day(monkeypatch, tmp_path, flights=2):
    """A day with `flights` pad-landing flights, each with a 1000-byte cam.mkv and a fixes.csv."""
    monkeypatch.setattr(runner, "VIDEO_DIR", tmp_path / "video")
    monkeypatch.setattr(end, "DRIVE_DAY", tmp_path / "drive" / "UAV_lab")
    monkeypatch.setattr(end.events, "log", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "CATALOG", tmp_path / "catalog.yaml")       # never the real docs/lab/catalog.yaml
    (tmp_path / "catalog.yaml").write_text("# head\ncards:\n" + "".join(f"  {c}: {{status: idea}}\n" for c in CARDS),
                                           encoding="utf-8")
    (tmp_path / "drive").mkdir()
    fs = []
    for n in range(1, flights + 1):
        vid = tmp_path / "video" / f"f0{n}_pad_landing"
        vid.mkdir(parents=True)
        (vid / "cam.mkv").write_bytes(b"x" * 1000)
        (vid / "fixes.csv").write_text("t,x\n", encoding="utf-8")
        fs.append({"card": "pad-landing", "variant": "v1", "n": n, "pack": "type2", "status": "complete", "early": None,
                   "metrics": {"cam_landing_cm": 7.5 + n}, "camera": vid.name, "t_land": 1e9 + 300 * n,
                   "flight_s": 130.0})
    state = tmp_path / "lab" / "queue_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"flights": fs, "winners": {}, "skipped": {},
                                 "packs": {"type2": {"packs": 1, "flights": flights, "rest_v": 15.62}}}))
    return state


def run(state, *args, answer=""):
    asked = []
    rc = end.main([QUEUE, "--day", "2026-10-15", "--state", str(state), *args],
                  ask=lambda prompt: asked.append(prompt) or answer)
    return rc, asked, (state.parent / "day_report.md").read_text(encoding="utf-8")


def test_enter_moves_the_videos_and_the_report_says_so(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path)
    rc, asked, rep = run(state)
    assert rc == 0 and len(asked) == 1 and "2 videos, 0.00 GB" in asked[0]
    for n in (1, 2):
        vid = tmp_path / "video" / f"f0{n}_pad_landing"
        assert not (vid / "cam.mkv").exists() and (vid / "fixes.csv").exists()       # the CSVs stay on D:
        assert (tmp_path / "drive" / "UAV_lab" / "2026-10-15" / vid.name / "cam.mkv").stat().st_size == 1000
    assert "| 1 |" in rep and "| 8.5 |" in rep and "on Drive" in rep and "rested 15.62 V" in rep
    assert (state.parent / "videos_moved.txt").read_text(encoding="utf-8").count(" moved ") == 2


def test_n_keeps_the_videos_on_d(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path)
    rc, _, rep = run(state, answer="n")
    assert rc == 0 and (tmp_path / "video" / "f01_pad_landing" / "cam.mkv").exists() and "on D:" in rep
    assert not (tmp_path / "drive" / "UAV_lab").exists()


def test_a_short_copy_keeps_the_video(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path, flights=1)
    monkeypatch.setattr(end.shutil, "copy2", lambda src, dst: dst.write_bytes(b"x" * 10))
    rc, _, rep = run(state)
    assert rc == 0 and (tmp_path / "video" / "f01_pad_landing" / "cam.mkv").exists()
    assert "KEPT" in rep and "the copy has 10 of 1000 bytes" in rep


def test_no_drive_asks_nothing_and_keeps_them(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path, flights=1)
    monkeypatch.setattr(end, "DRIVE_DAY", tmp_path / "no-drive" / "UAV_lab")
    rc, asked, rep = run(state)
    assert rc == 0 and asked == [] and "not found" in rep and "on D:" in rep


def test_no_flights_no_report(tmp_path, capsys):
    rc = end.main(["--day", "2026-10-15", "--state", str(tmp_path / "none.json")], ask=lambda p: 1 / 0)
    assert rc == 0 and "nothing to report" in capsys.readouterr().out


def test_the_day_moves_its_cards_forward_in_the_catalog(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path)
    _, _, rep = run(state, "--no-videos")
    cat = catalog.load()
    assert cat["cards"]["pad-landing"] == {"status": "flown", "flown": {"2026-10-15": 2}}
    assert "- pad-landing: 2 flights count, idea -> flown" in rep
    assert (tmp_path / "catalog.yaml").read_text(encoding="utf-8").startswith("# head\n")
    run(state, "--no-videos")                                               # a second lab end changes nothing
    assert catalog.load()["cards"]["pad-landing"]["flown"] == {"2026-10-15": 2}


def test_a_passed_confirm_is_confirmed(monkeypatch, tmp_path):
    state = day(monkeypatch, tmp_path)
    s = json.loads(state.read_text())
    for f in s["flights"]:
        f["card"] = "xy-ctrl-confirm"
    state.write_text(json.dumps(s))
    cat = catalog.load()
    cat["cards"]["xy-ctrl-confirm"]["status"] = "flown"
    catalog.save(cat)
    monkeypatch.setattr(runner, "complete", lambda c, st: True)
    monkeypatch.setattr(runner, "verdict", lambda c, st: (True, "lqr beat pid on every flight."))
    _, _, rep = run(state, "--no-videos")
    assert catalog.load()["cards"]["xy-ctrl-confirm"]["status"] == "confirmed" and "flown -> confirmed" in rep


def test_lab_end_word(monkeypatch):
    seen = []
    monkeypatch.setattr(end, "main", lambda argv: seen.append(argv) or 0)
    assert lab.main(["end", "--day", "2026-10-15"]) == 0 and seen == [["--day", "2026-10-15"]]
