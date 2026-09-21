"""DoD verification for the three revived panels (synthetic, offline)."""
import struct
import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")

from ground_station.comm.wifi_bridge import WifiBridge, _rpm_scalar_keys
from ground_station.livewatch.stream import StreamRange
from ground_station.service.telemetry_adapter import TelemetryAdapter
from ground_station.service.schema_registry import SchemaRegistry


def data_frame(slot, seq, t_ms, chunks):
    from ground_station.livewatch.transport import crc16_ccitt
    payload = struct.pack("<I", t_ms) + b"".join(chunks)
    ft = 0x09 + slot
    crc = crc16_ccitt(bytes([ft, len(payload) >> 8, len(payload) & 0xFF, seq]) + payload)
    return bytes([0xAA, 0xBB, ft, len(payload) >> 8, len(payload) & 0xFF, seq]) + payload + struct.pack(">H", crc)


def frame_c(rpms):
    p = struct.pack("<3f3f2ff4HH", 1, 2, 3, 0.01, 0.02, 0.03, 0.5, 0.6, 1.25, *rpms, 42)
    return bytes([0xAA, 0xBB, 0x06, 0, len(p), 0]) + p + b"\x00\x00"


b = WifiBridge(vofa_enabled=False)
for a in ("_wifi", "_cmd_udp", "_telem_udp", "_udp_send"):
    setattr(b, a, MagicMock())

print("=== F1: xTickCount u32 (1234567) ===")
tick = 1234567
for label, fmt in (("BEFORE fmt unset", None), ("AFTER fmt=I   ", "I")):
    b._stream_schemas.pop(1, None)
    b.apply_manifest_schema(1, type("S", (), {"ranges": (StreamRange(0x20001000, 4, 1, "xTickCount", fmt=fmt),)})())
    d = b._decode_stream_frame(1, data_frame(1, 1, 999, [struct.pack("<I", tick)]))
    print(label, "values[0]=", repr(d["values"][0]), "json=", d["json"]["slot1.xTickCount"])

print()
print("=== I2: Frame C rpm [5100,5200,5300,5400] ===")
tag, payload = b._parse_one(bytearray(frame_c([5100, 5200, 5300, 5400])))
print("tag:", tag)
panel_keys = ["motor.rpm_0", "motor.rpm_1", "motor.rpm_2", "motor.rpm_3"]
print("panel-facing keys/values:", {k: payload[k] for k in panel_keys})
print("also c.rpm list:", payload["c.rpm"])

print()
print("=== E1: s_ekf.x[0..8] -> streams['0'].values ===")
names = [f"s_ekf.x[{i}]" for i in range(9)]
vals = [0.1 * (i + 1) for i in range(9)]
sidebar = WifiBridge._slot0_to_sidebar(names, vals)
adapter = TelemetryAdapter(SchemaRegistry.builtin_dashboard())
sample = adapter.adapt_from_bridge(0, dict(sidebar))
streams = {}
adapter.apply(sample, streams)
sv = streams[0]["values"]
ekf_keys = ["ekf.vel_x", "ekf.vel_y", "ekf.vel_z", "ekf.bias_accel_x", "ekf.bias_accel_y",
            "ekf.bias_accel_z", "ekf.bias_gyro_x", "ekf.bias_gyro_y", "ekf.bias_gyro_z"]
print("published ekf keys:", {k: sv[k] for k in ekf_keys})
print("raw s_ekf names also carried by bridge tag-a:", dict(zip(names, vals)))
absent = ["ekf.pos_x", "ekf.pos_y", "ekf.pos_z", "estimator.filter_status",
          "estimator.cov_pxx", "estimator.cov_vzvz"]
print("absent-key lookups (must be None):", {k: sv.get(k) for k in absent})
print("panel honest state: NOT_PUBLISHED='n/p', hint='Not published by this build',",
      "position note='Not published by this build — this 9-state EKF has no position states.'")
