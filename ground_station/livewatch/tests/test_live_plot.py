"""Tests for ground_station.livewatch.live_plot and stream_log --plot support."""

import csv
import json
from pathlib import Path
import socket
import tempfile
import time

import numpy as np
import pytest

from ground_station.livewatch.live_plot import (
    SlotBuffer,
    TelemetryStore,
    build_variable_tree,
    load_csv_replay,
    load_layout,
    min_max_decimate,
    parse_datagram,
    save_layout,
)
from ground_station.livewatch.stream_log import (
    format_header_datagram,
    format_row_datagram,
)


def test_header_then_rows_buffer_contents():
    """Header then rows -> buffer contents populated with correct names and values."""
    store = TelemetryStore(capacity_per_slot=100)

    hdr = {"type": "hdr", "slot": 0, "names": ["varA", "varB"]}
    store.feed_datagram(hdr)

    row1 = {"type": "row", "slot": 0, "seq": 1, "t": 1000, "v": [10.5, 20.5]}
    row2 = {"type": "row", "slot": 0, "seq": 2, "t": 1020, "v": [11.5, 21.5]}
    store.feed_datagram(row1)
    store.feed_datagram(row2)

    t_a, y_a = store.get_series("varA")
    t_b, y_b = store.get_series("varB")

    np.testing.assert_allclose(t_a, [1.0, 1.02])
    np.testing.assert_allclose(y_a, [10.5, 11.5])
    np.testing.assert_allclose(t_b, [1.0, 1.02])
    np.testing.assert_allclose(y_b, [20.5, 21.5])


def test_rows_before_header():
    """Rows arriving before header are named slot<k>[i] and renamed when header arrives."""
    store = TelemetryStore(capacity_per_slot=100)

    # Slot 1 row before header
    store.feed_datagram({"type": "row", "slot": 1, "seq": 0, "t": 500, "v": [1.0, 2.0, 3.0]})
    store.feed_datagram({"type": "row", "slot": 1, "seq": 1, "t": 520, "v": [4.0, 5.0, 6.0]})

    # Fallback names
    assert store.all_variables() == ["slot1[0]", "slot1[1]", "slot1[2]"]
    t, y = store.get_series("slot1[1]")
    np.testing.assert_allclose(t, [0.5, 0.52])
    np.testing.assert_allclose(y, [2.0, 5.0])

    # Header arrives late
    store.feed_datagram({"type": "hdr", "slot": 1, "names": ["roll", "pitch", "yaw"]})
    assert store.all_variables() == ["roll", "pitch", "yaw"]

    # Historical values accessible under new names
    t_pitch, y_pitch = store.get_series("pitch")
    np.testing.assert_allclose(t_pitch, [0.5, 0.52])
    np.testing.assert_allclose(y_pitch, [2.0, 5.0])

    # New row after header
    store.feed_datagram({"type": "row", "slot": 1, "seq": 2, "t": 540, "v": [7.0, 8.0, 9.0]})
    t_pitch2, y_pitch2 = store.get_series("pitch")
    np.testing.assert_allclose(t_pitch2, [0.5, 0.52, 0.54])
    np.testing.assert_allclose(y_pitch2, [2.0, 5.0, 8.0])


def test_ring_wrap_around():
    """Ring buffer wrap-around unrolls data chronologically and drops oldest items."""
    buf = SlotBuffer(slot=0, capacity=5)
    buf.set_header(["ch0"])

    # Insert 8 items into capacity-5 buffer
    for i in range(8):
        buf.add_row(seq=i, t_ms=1000 + i * 100, values=[float(i * 10)])

    assert buf.count == 8

    # Extract all data
    t, y = buf.get_series("ch0")
    assert len(t) == 5
    assert len(y) == 5
    # Should contain items 3, 4, 5, 6, 7 in chronological order
    expected_t = [1.3, 1.4, 1.5, 1.6, 1.7]
    expected_y = [30.0, 40.0, 50.0, 60.0, 70.0]
    np.testing.assert_allclose(t, expected_t)
    np.testing.assert_allclose(y, expected_y)

    # Window filter: last 0.25 s (from 1.7 -> cutoff 1.45 s)
    t_win, y_win = buf.get_series("ch0", window_s=0.25)
    np.testing.assert_allclose(t_win, [1.5, 1.6, 1.7])
    np.testing.assert_allclose(y_win, [50.0, 60.0, 70.0])


def test_tree_grouping():
    """Tree grouping creates hierarchical dict from dot-separated names."""
    names = [
        "Ctrler.Z_posPID.FB",
        "Ctrler.Z_posPID.Des",
        "imu_data.rol",
        "real_voltage",
    ]
    tree = build_variable_tree(names)
    assert "Ctrler" in tree
    assert "Z_posPID" in tree["Ctrler"]
    assert tree["Ctrler"]["Z_posPID"]["FB"] == "Ctrler.Z_posPID.FB"
    assert tree["Ctrler"]["Z_posPID"]["Des"] == "Ctrler.Z_posPID.Des"
    assert tree["imu_data"]["rol"] == "imu_data.rol"
    assert tree["real_voltage"] == "real_voltage"


def test_decimation_keeps_min_max_of_each_bucket():
    """Decimation preserves min and max of each bucket to prevent losing peaks."""
    # Create 1000 points split into 10 buckets of 100 points
    n = 1000
    t = np.linspace(0.0, 10.0, n)
    y = np.zeros(n)

    # Inject specific min and max in each of the 10 buckets
    expected_mins = []
    expected_maxs = []
    for b in range(10):
        start = b * 100
        min_idx = start + 10
        max_idx = start + 80
        val_min = -10.0 - b
        val_max = 50.0 + b
        y[min_idx] = val_min
        y[max_idx] = val_max
        expected_mins.append(val_min)
        expected_maxs.append(val_max)

    # Decimate to target 20 points (10 buckets -> 20 points)
    t_dec, y_dec = min_max_decimate(t, y, target_points=20)

    # Every bucket's min and max must be in y_dec
    for val_min in expected_mins:
        assert val_min in y_dec, f"Missing min {val_min} in decimated output"
    for val_max in expected_maxs:
        assert val_max in y_dec, f"Missing max {val_max} in decimated output"

    # Time values must be strictly non-decreasing
    assert np.all(np.diff(t_dec) >= 0)

    # Smaller array returns unchanged
    t_small = np.array([1.0, 2.0, 3.0])
    y_small = np.array([4.0, 5.0, 6.0])
    t_out, y_out = min_max_decimate(t_small, y_small, target_points=20)
    np.testing.assert_allclose(t_out, t_small)
    np.testing.assert_allclose(y_out, y_small)


def test_layout_round_trip(tmp_path):
    """Layout round trip saves and reloads panel structure accurately."""
    layout_data = {
        "name": "custom_test",
        "time_window": 45.0,
        "autoscale_y": False,
        "panels": [
            {"title": "Panel 1", "variables": ["var1", "var2"]},
            {"title": "Panel 2", "variables": ["var3"]},
        ],
    }
    saved_path = save_layout("test_layout", layout_data, directory=tmp_path)
    assert saved_path.exists()

    loaded = load_layout("test_layout", directory=tmp_path)
    assert loaded == layout_data


def test_shipped_fusion_layout():
    """Verify shipped fusion.json layout exists and matches r5_extpos_frames.md symbols."""
    loaded = load_layout("fusion")
    assert "panels" in loaded
    assert len(loaded["panels"]) >= 3

    # Check that variables in fusion.json exist in r5_extpos_frames.md
    frames_md = Path(__file__).parents[2] / "livewatch" / "r5_extpos_frames.md"
    assert frames_md.exists()
    content = frames_md.read_text(encoding="utf-8")

    all_vars = [v for panel in loaded["panels"] for v in panel.get("variables", [])]
    for v in all_vars:
        assert v in content, f"Variable {v} in fusion.json not found in r5_extpos_frames.md"


def test_stream_log_helpers_and_loopback():
    """Unit-test format helpers and test loopback UDP datagram transmission."""
    # 1. Helper unit tests
    hdr_bytes = format_header_datagram(2, ["chA", "chB"])
    parsed_hdr = parse_datagram(hdr_bytes)
    assert parsed_hdr == {"type": "hdr", "slot": 2, "names": ["chA", "chB"]}

    row_bytes = format_row_datagram(2, 99, 12345, [3.14, 2.71])
    parsed_row = parse_datagram(row_bytes)
    assert parsed_row == {"type": "row", "slot": 2, "seq": 99, "t": 12345, "v": [3.14, 2.71]}

    # 2. UDP loopback test
    rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_sock.bind(("127.0.0.1", 0))
    rx_port = rx_sock.getsockname()[1]
    rx_sock.settimeout(1.0)

    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx_sock.sendto(hdr_bytes, ("127.0.0.1", rx_port))
    tx_sock.sendto(row_bytes, ("127.0.0.1", rx_port))

    data1, _ = rx_sock.recvfrom(4096)
    data2, _ = rx_sock.recvfrom(4096)
    rx_sock.close()
    tx_sock.close()

    p1 = parse_datagram(data1)
    p2 = parse_datagram(data2)
    assert p1["type"] == "hdr" and p1["slot"] == 2
    assert p2["type"] == "row" and p2["seq"] == 99

    store = TelemetryStore(capacity_per_slot=10)
    store.feed_datagram(p1)
    store.feed_datagram(p2)
    t, y = store.get_series("chA")
    np.testing.assert_allclose(t, [12.345])
    np.testing.assert_allclose(y, [3.14])


def test_csv_replay(tmp_path):
    """Test loading CSV files for replay."""
    # Create fake slot0 and slot1 files
    slot0_path = tmp_path / "exp1.slot0.csv"
    with slot0_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t_src_ms", "t_host_s", "seq", "roll", "pitch"])
        writer.writerow([1000, "0.1000", 0, 1.5, 2.5])
        writer.writerow([1010, "0.1010", 1, 1.6, 2.6])

    slot1_path = tmp_path / "exp1.slot1.csv"
    with slot1_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t_src_ms", "t_host_s", "seq", "alt", "vel"])
        writer.writerow([1000, "0.1000", 0, 50.0, 1.2])

    store = TelemetryStore()
    loaded = load_csv_replay(str(tmp_path / "exp1"), store)
    assert len(loaded) == 2

    assert "roll" in store.all_variables()
    assert "alt" in store.all_variables()

    t_roll, y_roll = store.get_series("roll")
    np.testing.assert_allclose(t_roll, [1.0, 1.01])
    np.testing.assert_allclose(y_roll, [1.5, 1.6])

    t_alt, y_alt = store.get_series("alt")
    np.testing.assert_allclose(t_alt, [1.0])
    np.testing.assert_allclose(y_alt, [50.0])
