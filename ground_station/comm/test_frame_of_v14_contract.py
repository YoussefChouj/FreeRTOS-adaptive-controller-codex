"""v14 contract: Frame 0x05 must accept 39, 55, and 75 byte payloads.

ADR-0011 appended fields to the OF calibration frame (v14, EKF_TELEM_ENABLED=1):
- Always-on (+16 B): acc_bias[3] mg, gyro_bias[3] 1e-4 rad/s, cal_health u16,
  bias_mode u8, bias_ema_freeze u8  -> always-on = 39+16 = 55 B
- EKF telem (+20 B): v_body[3] mm/s, P_diag[3] 1e-3, NIS 1e-3, K_last[3] 1e-3
  -> with EKF = 55+20 = 75 B

Pre-v14 firmware (39 B) must still parse so dashboards don't break.
"""
import struct

import pytest

from ground_station.comm.serial_bridge import (
    GS_PROTO_VERSION,
    SerialBridge,
)


def _bridge() -> SerialBridge:
    """Construct a SerialBridge without starting any threads."""
    return SerialBridge.__new__(SerialBridge)


def _build_v14_of_payload(ekf: bool = False) -> bytes:
    """Build a 53-byte (or 73-byte with EKF) Frame 0x05 payload."""
    counter = 12345
    of2_dx_fix, of2_dy_fix = 10, -20
    of2_dx, of2_dy = 11, -21
    acc_x, acc_y = 100, -50
    lin_acc_x, lin_acc_y = 30, -10
    yaw_c, pit_c, rol_c = 1234, 56, -78
    bias_x_c, bias_y_c = 50, -25
    alt_cm = 150
    earth_x, earth_y = 1.5, -2.5
    of_quality = 200

    head = struct.pack(
        "<H13hH",
        counter,
        of2_dx_fix, of2_dy_fix,
        of2_dx, of2_dy,
        acc_x, acc_y,
        lin_acc_x, lin_acc_y,
        yaw_c, pit_c, rol_c,
        bias_x_c, bias_y_c,
        alt_cm,
    ) + struct.pack("<2f", earth_x, earth_y) + bytes([of_quality])
    assert len(head) == 39

    # v14 tail: 6h + H + BB = 16 bytes (acc_bias[3], gyro_bias[3], cal_health, bias_mode, bias_ema_freeze)
    acc_bias = (12, -34, 56)
    gyro_bias = (789, -101, 112)
    cal_health = 0x0123
    bias_mode = 1
    bias_ema_freeze = 0
    tail = struct.pack("<6hHBB", *acc_bias, *gyro_bias, cal_health, bias_mode, bias_ema_freeze)
    assert len(tail) == 16

    payload = head + tail
    if ekf:
        ekf_tail = struct.pack(
            "<10h",
            1000, -2000, 3000,    # vx,vy,vz mm/s
            100, 200, 300,         # P_diag * 1e3
            42,                    # NIS * 1e3
            7, -8, 9,              # K_last * 1e3
        )
        assert len(ekf_tail) == 20
        payload = payload + ekf_tail

    return payload


def test_proto_version_is_14():
    assert GS_PROTO_VERSION >= 14  # OF frame layout is v14; later bumps (v15 Frame A) keep it


def test_v14_payload_size_55():
    payload = _build_v14_of_payload(ekf=False)
    assert len(payload) == 55


def test_v14_payload_size_75_with_ekf():
    payload = _build_v14_of_payload(ekf=True)
    assert len(payload) == 75


def test_v14_unpack_extracts_acc_bias_and_cal_health():
    bridge = _bridge()
    payload = _build_v14_of_payload(ekf=False)
    lines = bridge._unpack_frame_of(payload)
    mp = dict(lines)
    assert mp["of.acc_bias_x_mg"] == 12
    assert mp["of.acc_bias_y_mg"] == -34
    assert mp["of.acc_bias_z_mg"] == 56
    assert mp["of.gyro_bias_x_1e4radps"] == 789
    assert mp["of.cal_health"] == 0x0123
    assert mp["of.bias_mode"] == 1
    assert mp["of.bias_ema_freeze"] == 0
    # existing fields still present
    assert mp["of.of2_dx_fix"] == 10
    assert mp["of.of2_dy_fix"] == -20
    assert mp["of.quality"] == 200


def test_v14_ekf_unpack_extracts_kalman_outputs():
    bridge = _bridge()
    payload = _build_v14_of_payload(ekf=True)
    lines = bridge._unpack_frame_of(payload)
    mp = dict(lines)
    assert mp["of.ekf_vx_mmps"] == 1000
    assert mp["of.ekf_nis_1e3"] == 42
    assert mp["of.ekf_k0_1e3"] == 7


def test_pre_v14_39_byte_payload_still_parses():
    """Bridge must accept legacy 39-byte payloads so old firmware stays visible."""
    bridge = _bridge()
    payload = _build_v14_of_payload(ekf=False)[:39]
    assert len(payload) == 39
    lines = bridge._unpack_frame_of(payload)
    mp = dict(lines)
    assert "of.quality" in mp
    assert "of.acc_bias_x_mg" not in mp  # no v14 fields on legacy frame


def test_unknown_size_rejected():
    bridge = _bridge()
    bogus = b"\x00" * 50  # between v10 (39) and v14 base (55) sizes
    assert bridge._unpack_frame_of(bogus) == []
