"""Unit tests for trajectory upload CRC caching and skip_if_loaded (Workflow B Friction B1a)."""

from __future__ import annotations

from unittest.mock import MagicMock
from ground_station.platform.trajectory_upload import upload, UploadResult
from ground_station.service.trajectory_pipeline import TrajPoint, crc32


def _make_points(n: int = 4) -> list[TrajPoint]:
    return [
        TrajPoint(0.0, 0.0, 0.8, 0.0, 0.0),
        TrajPoint(0.5, 0.3, 1.0, 0.0, 2.0),
        TrajPoint(0.5, 0.3, 1.0, 0.0, 4.0),
        TrajPoint(0.0, 0.0, 0.8, 0.0, 6.0),
    ][:n]


def test_upload_records_last_committed_crc_and_skips_when_requested():
    points = _make_points(4)
    crc = crc32(points)

    client = MagicMock()
    client.traj_begin.return_value = True
    client.traj_append.return_value = True
    client.traj_commit.return_value = True

    # 1. First upload: uploads normally and commits CRC
    res1 = upload(points, client, attempts=3, skip_if_loaded=True)
    assert res1.ok is True
    assert res1.attempts == 1
    assert getattr(client, "_last_committed_traj_crc", None) == (4, crc)
    assert client.traj_begin.call_count == 1
    assert client.traj_commit.call_count == 1

    # 2. Second upload with skip_if_loaded=True: skips completely
    client.traj_begin.reset_mock()
    client.traj_append.reset_mock()
    client.traj_commit.reset_mock()

    res2 = upload(points, client, attempts=3, skip_if_loaded=True)
    assert res2.ok is True
    assert res2.attempts == 0
    assert client.traj_begin.call_count == 0
    assert client.traj_append.call_count == 0
    assert client.traj_commit.call_count == 0

    # 3. Third upload with skip_if_loaded=False (default): re-uploads despite matching CRC
    res3 = upload(points, client, attempts=3, skip_if_loaded=False)
    assert res3.ok is True
    assert res3.attempts == 1
    assert client.traj_begin.call_count == 1
    assert client.traj_commit.call_count == 1


def test_upload_re_uploads_when_points_differ():
    points1 = _make_points(4)
    points2 = [
        TrajPoint(0.0, 0.0, 0.8, 0.0, 0.0),
        TrajPoint(0.4, 0.2, 0.9, 0.0, 2.0),
        TrajPoint(0.0, 0.0, 0.8, 0.0, 4.0),
    ]

    client = MagicMock()
    client.traj_begin.return_value = True
    client.traj_append.return_value = True
    client.traj_commit.return_value = True

    res1 = upload(points1, client, skip_if_loaded=True)
    assert res1.ok is True
    assert client.traj_begin.call_count == 1

    client.traj_begin.reset_mock()
    res2 = upload(points2, client, skip_if_loaded=True)
    assert res2.ok is True
    assert client.traj_begin.call_count == 1
