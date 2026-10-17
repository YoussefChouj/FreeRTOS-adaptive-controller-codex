"""video_truth.video_file: the local cam.mkv, else the Drive copy `lab archive` / `lab end` left (docs/agent/video-archive.md)."""
from ground_station.analysis import video_truth as vt


def test_local_wins_then_drive_then_local_path(monkeypatch, tmp_path):
    drive = tmp_path / "drive"
    monkeypatch.setattr(vt, "DRIVE_ROOT", drive)
    vd = tmp_path / "video_truth" / "f1"
    vd.mkdir(parents=True)
    assert vt.video_file(vd) == str(vd / "cam.mkv")                     # nowhere: the local path, as before
    (drive / "2026-10-12" / "f1").mkdir(parents=True)
    (drive / "2026-10-12" / "f1" / "cam.mkv").write_bytes(b"x")
    assert vt.video_file(vd) == str(drive / "2026-10-12" / "f1" / "cam.mkv")
    (vd / "cam.mkv").write_bytes(b"x")
    assert vt.video_file(vd) == str(vd / "cam.mkv")
