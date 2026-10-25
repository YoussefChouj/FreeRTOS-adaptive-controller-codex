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


def test_restarted_part_when_cam_mkv_is_empty(monkeypatch, tmp_path):
    """scrcpy crashed, cam_loop.sh restarted it: cam.mkv is 0 B and the flight is in cam_2.mkv with its own start stamp."""
    monkeypatch.setattr(vt, "DRIVE_ROOT", tmp_path / "drive")
    vd = tmp_path / "f1"
    vd.mkdir()
    (vd / "cam.mkv").write_bytes(b"")
    (vd / "scrcpy_start_host_time.txt").write_text("100.0 2026-10-24T23:00:51")
    (vd / "cam_2.mkv").write_bytes(b"xx")
    (vd / "scrcpy_start_host_time_2.txt").write_text("104.0 2026-10-24T23:00:55")
    (vd / "cam_3.mkv").write_bytes(b"xxxx")                               # no start stamp of its own: never picked
    assert vt.video_file(vd) == str(vd / "cam_2.mkv")
    assert vt.start_file(vd) == vd / "scrcpy_start_host_time_2.txt"
    (vd / "cam.mkv").write_bytes(b"x")                                    # cam.mkv has bytes: it wins, as before
    assert vt.video_file(vd) == str(vd / "cam.mkv")
    assert vt.start_file(vd) == vd / "scrcpy_start_host_time.txt"
