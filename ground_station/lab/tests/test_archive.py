"""archive.py: old video folders to Drive (copied, checked, removed), floor scans and live trackers kept on D:."""
import os
import time

from ground_station.lab import archive, end, runner
from ground_station.lab import __main__ as lab


def folder(root, name, age_days, extra=()):
    d = root / "video" / name
    d.mkdir(parents=True)
    (d / "cam.mkv").write_bytes(b"x" * 1000)
    (d / "fixes.csv").write_text("t,x\n", encoding="utf-8")
    for e in extra:
        (d / e).write_bytes(b"")
    t = time.time() - age_days * 86400
    for p in d.iterdir():
        os.utime(p, (t, t))
    return d


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "VIDEO_DIR", tmp_path / "video")
    monkeypatch.setattr(end, "DRIVE_DAY", tmp_path / "drive" / "UAV_lab")
    monkeypatch.setattr(archive.events, "log", lambda *a, **k: None)
    monkeypatch.setattr(archive, "INDEX", tmp_path / "video-archive.csv")
    (tmp_path / "drive").mkdir()


def test_lists_only_without_yes(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    old = folder(tmp_path, "f1-old", 10)
    assert lab.main(["archive"]) == 0
    assert (old / "cam.mkv").exists() and "would move  f1-old" in capsys.readouterr().out


def test_moves_old_keeps_floor_live_and_new(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    old = folder(tmp_path, "f1-old", 10)
    floor = folder(tmp_path, "r5_marks_1", 10, ["aruco.npz"])
    live = folder(tmp_path, "f2-live", 10, ["tracker.pid"])
    new = folder(tmp_path, "f3-new", 1)
    assert lab.main(["archive", "--yes"]) == 0
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 10 * 86400))
    assert (tmp_path / "drive" / "UAV_lab" / day / "f1-old" / "cam.mkv").stat().st_size == 1000
    assert not (old / "cam.mkv").exists() and (old / "fixes.csv").exists()          # the CSVs stay on D:
    assert "cam.mkv" in (old / "ARCHIVED.txt").read_text(encoding="utf-8")
    assert all((d / "cam.mkv").exists() for d in (floor, live, new))
    rows = (tmp_path / "video-archive.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "day,folder,file,bytes,drive_path" and rows[1].startswith(f"{day},f1-old,cam.mkv,1000,")
    assert len(rows) == 2


def test_no_drive_moves_nothing(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(end, "DRIVE_DAY", tmp_path / "nodrive" / "UAV_lab")
    old = folder(tmp_path, "f1-old", 10)
    assert lab.main(["archive", "--yes"]) == 1 and (old / "cam.mkv").exists()
