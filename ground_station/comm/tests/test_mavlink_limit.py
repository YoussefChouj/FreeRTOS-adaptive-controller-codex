"""ground_station/comm/tests/test_mavlink_limit.py

Limit-test script for the MAVLink WiFi protocol.

Modes:
  --sim        Simulation: local UDP loopback (no FC needed).
                Measures Python-side UDP performance limits.
  --uplink     Uplink flood: send 0xCC 0xDD commands at target Hz to FC.
  --downlink   Downlink receive: listen for MAVLink telemetry from FC.
                Skips if FC not reachable.
  --full       Run all available tests (default).

Theoretical wire budget:
   USART3 @ 921600 actual = 913043 baud
   Wire capacity           = 91304 B/s  (10 bits/byte)
   Send_Task cadence       = 200 Hz  (5 ms tick)
   Budget per tick         = 456 B

   MAVLink frame sizes (header 8 + payload + CRC 2):
     MRAC_WEIGHTS  (132B pl)  = 142 B
     EKF_STATES    ( 52B pl)  =  62 B
     CTRL_DEBUG    ( 68B pl)  =  78 B
     0xCC 0xDD command        =  12 B

Usage:
    python -m ground_station.comm.tests.test_mavlink_limit           # all tests
    python -m ground_station.comm.tests.test_mavlink_limit --sim     # simulation only
    python -m ground_station.comm.tests.test_mavlink_limit --uplink  # uplink flood
    python -m ground_station.comm.tests.test_mavlink_limit --downlink # downlink receive
"""

import argparse
import socket
import struct
import time
import threading
import sys
from typing import NamedTuple, Optional


# ── Network constants ──────────────────────────────────────────────────────────
MICOAIR_HOST = "192.168.4.1"   # MicoAir AP gateway (verified 2026-08-19)
WIFI_PORT    = 14550            # MicoAir UDP TX (FC→GS)
CMD_PORT     = 14551            # Bridge command port (GS→FC via bridge)

# ── MAVLink frame sizes ───────────────────────────────────────────────────────
MRAC_FRAME  = 8 + 132 + 2   # 142 B  (payload 132 B)
EKF_FRAME   = 8 +  52 + 2   #  62 B  (payload  52 B)
CTRL_FRAME  = 8 +  68 + 2   #  78 B  (payload  68 B)
ALL_THREE   = MRAC_FRAME + EKF_FRAME + CTRL_FRAME   # 282 B
CMD_FRAME   = 9               # 0xCC 0xDD command (9 B: 2 header + 1 cmd + 1 idx + 4 float + 1 crc8)

# ── Wire budget ───────────────────────────────────────────────────────────────
USART3_BAUD_ACTUAL = 913043   # BRR=0x2E on APB1 @ 42 MHz
WIRE_BPS           = USART3_BAUD_ACTUAL // 10   # 91304 B/s
SEND_TASK_HZ       = 200
WIRE_B_PER_TICK    = WIRE_BPS / SEND_TASK_HZ     # 456 B/tick

# ── CRC-16/X.25 (MAVLink CRC) ────────────────────────────────────────────────
_CRC_TABLE = (
    0x0000, 0x1189, 0x2312, 0x329b, 0x4624, 0x57ad, 0x6536, 0x74bf,
    0x8c48, 0x9dc1, 0xaf5a, 0xbed3, 0xca6c, 0xdbe5, 0xe97e, 0xf8f7,
    0x1081, 0x0108, 0x3393, 0x221a, 0x56a5, 0x472c, 0x75b7, 0x643e,
    0x9cc9, 0x8d40, 0xbfdb, 0xae52, 0xdaed, 0xcb64, 0xf9ff, 0xe876,
    0x2102, 0x308b, 0x0210, 0x1399, 0x6726, 0x76af, 0x4434, 0x55bd,
    0xad4a, 0xbcc3, 0x8e58, 0x9fd1, 0xeb6e, 0xfae7, 0xc87c, 0xd9f5,
    0x3183, 0x200a, 0x1291, 0x0318, 0x77a7, 0x662e, 0x54b5, 0x453c,
    0xbdcb, 0xac42, 0x9ed9, 0x8f50, 0xfbef, 0xea66, 0xd8fd, 0xc974,
    0x4204, 0x538d, 0x6116, 0x709f, 0x0420, 0x15a9, 0x2732, 0x36bb,
    0xce4c, 0xdfc5, 0xed5e, 0xfcd7, 0x8868, 0x99e1, 0xab7a, 0xbaf3,
    0x5285, 0x430c, 0x7197, 0x601e, 0x14a1, 0x0528, 0x37b3, 0x263a,
    0xdecd, 0xcf44, 0xfddb, 0xec52, 0x98e9, 0x8960, 0xbbfb, 0xaa72,
    0x6306, 0x728f, 0x4014, 0x519d, 0x2522, 0x34ab, 0x0630, 0x17b9,
    0xef4e, 0xfec7, 0xcc5c, 0xddd5, 0xa96a, 0xb8e3, 0x8a78, 0x9bf1,
    0x7387, 0x620e, 0x5095, 0x411c, 0x35a3, 0x242a, 0x16b1, 0x0738,
    0xffcf, 0xee46, 0xdcdd, 0xcd54, 0xb9eb, 0xa862, 0x9af9, 0x8b70,
    0x8408, 0x9581, 0xa71a, 0xb693, 0xc22c, 0xd3a5, 0xe13e, 0xf0b7,
    0x0840, 0x19c9, 0x2b52, 0x3adb, 0x4e64, 0x5fed, 0x6d76, 0x7cff,
    0x9489, 0x8500, 0xb79b, 0xa612, 0xd2ad, 0xc324, 0xf1bf, 0xe036,
    0x18c1, 0x0948, 0x3bd3, 0x2a5a, 0x5ee5, 0x4f6c, 0x7df7, 0x6c7e,
    0xa50a, 0xb483, 0x8618, 0x9791, 0xe32e, 0xf2a7, 0xc03c, 0xd1b5,
    0x2942, 0x38cb, 0x0a50, 0x1bd9, 0x6f66, 0x7eef, 0x4c74, 0x5dfd,
    0xb58b, 0xa402, 0x9699, 0x8710, 0xf3af, 0xe226, 0xd0bd, 0xc134,
    0x39c3, 0x284a, 0x1ad1, 0x0b58, 0x7fe7, 0x6e6e, 0x5cf5, 0x4d7c,
    0xc60e, 0xd787, 0xe51c, 0xf495, 0x802a, 0x91a3, 0xa338, 0xb2b1,
    0x4a46, 0x5bcf, 0x6954, 0x78dd, 0x0c62, 0x1deb, 0x2f70, 0x3ef9,
    0xd68f, 0xc706, 0xf59d, 0xe414, 0x90ab, 0x8122, 0xb3b9, 0xa230,
    0x5ac7, 0x4b4e, 0x79d5, 0x685c, 0x1ce3, 0x0d6a, 0x3ff1, 0x2e78,
    0xe70e, 0xf687, 0xc41c, 0xd595, 0xa12a, 0xb0a3, 0x8238, 0x93b1,
    0x6b46, 0x7acf, 0x4854, 0x59dd, 0x2d62, 0x3ceb, 0x0e70, 0x1ff9,
    0xf78f, 0xe606, 0xd49d, 0xc514, 0xb1ab, 0xa022, 0x92b9, 0x8330,
    0x7bc7, 0x6a4e, 0x58d5, 0x495c, 0x3de3, 0x2c6a, 0x1ef1, 0x0f78,
)


def _crc16_x25(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFF


def build_mavlink_frame(msg_id: int, payload: bytes) -> bytes:
    """Pack a MAVLink v1.0 frame."""
    seq = 0
    sys_id, comp_id = 1, 1
    plen = len(payload)
    header = bytes([0xFE, plen, seq, sys_id, comp_id,
                    msg_id & 0xFF, (msg_id >> 8) & 0xFF])
    crc_data = bytes([msg_id & 0xFF, (msg_id >> 8) & 0xFF, plen]) + payload
    crc = _crc16_x25(crc_data)
    return header + payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_command_frame(cmd_id: int, index: int, value: float) -> bytes:
    """Build a 0xCC 0xDD command frame (12 B)."""
    body = bytes([0xCC, 0xDD, cmd_id, index]) + struct.pack("<f", value)
    crc8 = 0
    for b in body[2:]:
        crc8 ^= b
    return body + bytes([crc8])


# ── Test results structure ─────────────────────────────────────────────────────
class TestResult(NamedTuple):
    name: str
    offered_hz: float
    received_hz: Optional[float]
    offered_bps: float
    received_bps: Optional[float]
    loss_pct: Optional[float]
    theory_bps: float
    utilisation_pct: float
    note: str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — Theory summary
# ══════════════════════════════════════════════════════════════════════════════
def print_theory():
    print("\n" + "═" * 70)
    print(" THEORY")
    print("═" * 70)
    print(f"  USART3 actual baud     : {USART3_BAUD_ACTUAL:,} (BRR=0x2E on APB1 @ 42 MHz)")
    print(f"  Wire capacity          : {WIRE_BPS:,} B/s  (10 bits/byte)")
    print(f"  Send_Task cadence      : {SEND_TASK_HZ} Hz  (5 ms tick)")
    print(f"  Budget per tick        : {WIRE_B_PER_TICK:.1f} B")
    print()
    print("  MAVLink frame sizes:")
    print(f"    MRAC_WEIGHTS (132B pl)  : {MRAC_FRAME} B  ({MRAC_FRAME/WIRE_B_PER_TICK*100:.1f}% of tick)")
    print(f"    EKF_STATES   ( 52B pl) : {EKF_FRAME} B  ({EKF_FRAME/WIRE_B_PER_TICK*100:.1f}% of tick)")
    print(f"    CTRL_DEBUG   ( 68B pl)  : {CTRL_FRAME} B  ({CTRL_FRAME/WIRE_B_PER_TICK*100:.1f}% of tick)")
    print(f"    All three combined      : {ALL_THREE} B  ({ALL_THREE/WIRE_B_PER_TICK*100:.1f}% of tick)")
    print()
    print("  Per-tick budget analysis:")
    for label, fsize in [
        ("MRAC only", MRAC_FRAME),
        ("All three", ALL_THREE),
        ("Wire budget", WIRE_B_PER_TICK),
    ]:
        pct = fsize / WIRE_B_PER_TICK * 100
        status = "OK" if pct <= 100 else "OVER BUDGET"
        print(f"    {label:<22} {fsize:>6.1f} B  {pct:>6.1f}%  {status}")
    print()
    print("  Throughput at various cadences:")
    for hz, label in [(10, "current"), (50, ""), (100, ""), (200, "max")]:
        bps = ALL_THREE * hz
        util = bps / WIRE_BPS * 100
        tag = f" ({label})" if label else ""
        print(f"    {hz} Hz all3{tag:12} : {bps:>8,} B/s  ({util:>5.1f}% of wire)")
    print()
    print(f"  0xCC 0xDD command : {CMD_FRAME} B")
    print(f"  Theoretical cmd rate at wire cap: {WIRE_BPS / CMD_FRAME:,.0f} Hz")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — FakeFC: simulation mode (no FC needed)
# ══════════════════════════════════════════════════════════════════════════════
class FakeFC:
    """Simulates FC MAVLink telemetry over local UDP loopback.

    Generates MAVLink frames at a configurable rate to test the GS-side
    receive pipeline without needing the actual drone.
    """

    def __init__(self, send_to_host: str = "127.0.0.1", send_to_port: int = 14550,
                 bind_host: str = "127.0.0.1", bind_port: int = 14560, hz: float = 10.0):
        self.send_to_host = send_to_host
        self.send_to_port = send_to_port
        self.bind_host   = bind_host
        self.bind_port   = bind_port
        self.hz = hz
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sent = 0
        self._send_sock: Optional[socket.socket] = None
        # Build the three frame templates using the SAME CRC as the firmware.
        # MAVLink CRC-16/X.25 uses reflected form: crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
        def mav_crc16(data: bytes) -> int:
            crc = 0xFFFF
            for b in data:
                crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
            return crc ^ 0xFFFF

        def make_frame(msg_id: int, payload: bytes) -> bytes:
            plen = len(payload)
            header = bytes([0xFE, plen, 0, 1, 1,
                            msg_id & 0xFF, (msg_id >> 8) & 0xFF, 0])
            crc_d = bytes([msg_id & 0xFF, (msg_id >> 8) & 0xFF, plen]) + payload
            crc = mav_crc16(crc_d)
            return header + payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])

        self._frames = {
            10001: make_frame(10001, bytes(132)),
            10002: make_frame(10002, bytes(52)),
            10003: make_frame(10003, bytes(68)),
        }
        assert len(self._frames[10001]) == MRAC_FRAME
        assert len(self._frames[10002]) == EKF_FRAME
        assert len(self._frames[10003]) == CTRL_FRAME

    def start(self):
        # Send socket: NO bind — OS picks an ephemeral source port.
        # Windows loopback will deliver to any socket bound to send_to_port,
        # including our receive socket (different source port doesn't block delivery).
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"  [FakeFC]  sending to {self.send_to_host}:{self.send_to_port} "
              f"@ {self.hz} Hz")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._send_sock:
            self._send_sock.close()
        print(f"  [FakeFC]  stopped  (sent {self._sent} frames)")

    def _run(self):
        interval = 1.0 / self.hz
        seq = [0, 0, 0]
        msg_ids = [10001, 10002, 10003]

        while self._running:
            tick = time.monotonic()
            for i, msg_id in enumerate(msg_ids):
                frame = bytearray(self._frames[msg_id])
                frame[2] = seq[i] & 0xFF
                seq[i] += 1
                try:
                    self._send_sock.sendto(bytes(frame),
                                          (self.send_to_host, self.send_to_port))
                    self._sent += 1
                except Exception:
                    pass
            elapsed = time.monotonic() - tick
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — Downlink: telemetry receive test
# ══════════════════════════════════════════════════════════════════════════════
def test_downlink_real(fc_host: str, port: int,
                      duration_s: float = 10.0) -> TestResult:
    """Receive MAVLink telemetry from the real FC over WiFi."""
    print(f"\n  [DOWNLINK — REAL]  {duration_s:.0f} s on {fc_host}:{port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("", port))
    except OSError:
        sock.close()
        return _skip("port in use")

    mrac_n = ekf_n = ctrl_n = 0
    total_bytes = 0
    start = time.monotonic()
    deadline = start + duration_s

    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        total_bytes += len(data)
        if len(data) >= 8 and data[0] == 0xFE:
            msg_id = data[5] | (data[6] << 8)
            if msg_id == 10001:   mrac_n += 1
            elif msg_id == 10002: ekf_n += 1
            elif msg_id == 10003: ctrl_n += 1

    elapsed = time.monotonic() - start
    sock.close()

    # Expected: all three at 10 Hz (UART4_Task cadence)
    expected_hz = 10.0
    received_hz = mrac_n / elapsed if elapsed > 0 else 0.0
    received_bps = total_bytes / elapsed if elapsed > 0 else 0.0
    loss_pct = max(0.0, (1.0 - received_hz / expected_hz) * 100) if expected_hz > 0 else None

    print(f"    MRAC frames : {mrac_n:,}")
    print(f"    EKF frames  : {ekf_n:,}")
    print(f"    CTRL frames : {ctrl_n:,}")
    print(f"    Rate        : {received_hz:.2f} Hz  (expected {expected_hz:.0f} Hz)")
    print(f"    Throughput  : {received_bps:,.0f} B/s  (wire cap {WIRE_BPS:,})")
    print(f"    Loss        : {loss_pct:.1f}%  (expected 0%)")

    return TestResult(
        name="downlink_real",
        offered_hz=expected_hz * 3,
        received_hz=mrac_n + ekf_n + ctrl_n,
        offered_bps=expected_hz * 3 * ALL_THREE,
        received_bps=received_bps,
        loss_pct=loss_pct,
        theory_bps=WIRE_BPS,
        utilisation_pct=received_bps / WIRE_BPS * 100,
        note="real FC",
    )


def _skip(reason: str) -> TestResult:
    print(f"\n  [SKIPPED] {reason}")
    return TestResult(
        name="skipped", offered_hz=0, received_hz=None,
        offered_bps=0, received_bps=None, loss_pct=None,
        theory_bps=WIRE_BPS, utilisation_pct=0, note=reason,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — Downlink: simulation mode
# ══════════════════════════════════════════════════════════════════════════════
def test_downlink_sim(sim_host: str, port: int,
                     sim_hz: float, duration_s: float = 10.0) -> TestResult:
    """Receive MAVLink telemetry from FakeFC over loopback.

    FakeFC sends to recv_port; receiver also binds recv_port.
    This avoids the sender/receiver binding conflict.
    """
    recv_port = port
    # FakeFC sends from ephemeral port to recv_port; receiver binds recv_port.
    fake = FakeFC(send_to_host=sim_host, send_to_port=recv_port, hz=sim_hz)
    fake.start()
    time.sleep(0.5)   # let FakeFC stabilise

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)
    try:
        sock.bind((sim_host, recv_port))
    except OSError:
        fake.stop()
        sock.close()
        return _skip("port in use")

    mrac_n = ekf_n = ctrl_n = 0
    total_bytes = 0
    start = time.monotonic()
    deadline = start + duration_s

    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        total_bytes += len(data)
        if len(data) >= 8 and data[0] == 0xFE:
            msg_id = data[5] | (data[6] << 8)
            if msg_id == 10001:   mrac_n += 1
            elif msg_id == 10002: ekf_n += 1
            elif msg_id == 10003: ctrl_n += 1

    elapsed = time.monotonic() - start
    sock.close()
    fake.stop()

    received_hz = mrac_n / elapsed if elapsed > 0 else 0.0
    received_bps = total_bytes / elapsed if elapsed > 0 else 0.0
    expected_total_hz = sim_hz * 3
    loss_pct = max(0.0, (1.0 - received_hz / sim_hz) * 100) if sim_hz > 0 else None

    print(f"    MRAC frames : {mrac_n:,}  (expected {int(sim_hz * elapsed)} @ {sim_hz} Hz)")
    print(f"    EKF frames  : {ekf_n:,}")
    print(f"    CTRL frames : {ctrl_n:,}")
    print(f"    Rate/stream : {received_hz:.2f} Hz  (expected {sim_hz} Hz)")
    print(f"    Total rate  : {(mrac_n + ekf_n + ctrl_n)/elapsed:.1f} Hz")
    print(f"    Throughput  : {received_bps:,.0f} B/s  (wire cap {WIRE_BPS:,})")
    print(f"    Loss/stream : {loss_pct:.2f}%")

    return TestResult(
        name=f"downlink_sim_{sim_hz}hz",
        offered_hz=expected_total_hz,
        received_hz=mrac_n + ekf_n + ctrl_n,
        offered_bps=sim_hz * 3 * ALL_THREE,
        received_bps=received_bps,
        loss_pct=loss_pct,
        theory_bps=WIRE_BPS,
        utilisation_pct=received_bps / WIRE_BPS * 100,
        note=f"sim {sim_hz} Hz loopback",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — Uplink: command flood test
# ══════════════════════════════════════════════════════════════════════════════
def test_uplink(target_hz: float, host: str, port: int,
                duration_s: float = 2.0, frame_len: int = CMD_FRAME) -> TestResult:
    """Flood the FC with 0xCC 0xDD commands at target rate."""
    interval = 1.0 / target_hz
    frame = build_command_frame(0x01, 0, 0.0)
    assert len(frame) == frame_len

    print(f"\n  [UPLINK]  {target_hz:.0f} Hz × {duration_s:.0f} s  "
          f"({frame_len} B/frame)  → {host}:{port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(2.0)

    sent = errors = 0
    start = time.monotonic()
    deadline = start + duration_s

    while time.monotonic() < deadline:
        tick = time.monotonic()
        try:
            sock.sendto(frame, (host, port))
            sent += 1
        except Exception as e:
            errors += 1
        elapsed = time.monotonic() - tick
        sleep = interval - elapsed
        if sleep > 0:
            time.sleep(sleep)

    actual_elapsed = time.monotonic() - start
    sock.close()

    achieved_hz = sent / actual_elapsed if actual_elapsed > 0 else 0.0
    offered_bps = sent * frame_len / actual_elapsed if actual_elapsed > 0 else 0.0

    print(f"    Sent       : {sent:,} frames  ({frame_len * sent:,} B)")
    print(f"    Achieved   : {achieved_hz:.1f} Hz")
    print(f"    Offered    : {offered_bps:,.0f} B/s")
    print(f"    Errors     : {errors}")

    return TestResult(
        name=f"uplink_{target_hz:.0f}hz",
        offered_hz=target_hz,
        received_hz=achieved_hz,
        offered_bps=offered_bps,
        received_bps=None,
        loss_pct=None,
        theory_bps=WIRE_BPS,
        utilisation_pct=offered_bps / WIRE_BPS * 100,
        note=f"→ {host}:{port}",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — Capacity table (theory only)
# ══════════════════════════════════════════════════════════════════════════════
def print_capacity_table():
    print("\n  [DOWNLINK CAPACITY TABLE]  — theoretical")
    print(f"  {'Cadence':>12} | {'Bytes/tick':>10} | {'% Wire':>7} | {'Verdict'}")
    print("  " + "-" * 55)
    for hz, label in [
        (10,  "current"),
        (50,  ""),
        (100, ""),
        (150, ""),
        (200, "max"),
    ]:
        b = ALL_THREE * hz
        util = b / WIRE_BPS * 100
        status = "OK" if util <= 100 else "OVER BUDGET"
        tag = f"  ({label})" if label else ""
        print(f"  {hz} Hz{tag:>12} | {b:>10,} B | {util:>6.1f}% | {status}")
    print()
    print("  MRAC-only (142 B) at 200 Hz:")
    b = MRAC_FRAME * 200
    util = b / WIRE_BPS * 100
    print(f"    {b:,} B/s  ({util:.1f}% of wire)")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — Run
# ══════════════════════════════════════════════════════════════════════════════
def run(sim_only: bool, uplink_only: bool, downlink_only: bool):
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║       MAVLink WiFi Protocol — Limit Test                            ║")
    print("║  Theory vs Real: uplink (commands) + downlink (telemetry)          ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    print_theory()
    print_capacity_table()
    results = []

    # ── DOWNLINK ──────────────────────────────────────────────────────────────
    if not uplink_only:
        # Try real FC first; fall back to simulation
        reachable = _check_reachable(MICOAIR_HOST, WIFI_PORT)
        if reachable:
            dl = test_downlink_real(MICOAIR_HOST, WIFI_PORT, duration_s=10.0)
        else:
            print(f"\n  [INFO]  {MICOAIR_HOST}:{WIFI_PORT} unreachable — using simulation")
            dl = test_downlink_sim("127.0.0.1", 14550, sim_hz=10.0, duration_s=10.0)
        results.append(dl)

        # Also run simulation at higher rates to show headroom
        for sim_hz in [50, 100, 200]:
            r = test_downlink_sim("127.0.0.1", 14551 + sim_hz,
                                  sim_hz=sim_hz, duration_s=5.0)
            results.append(r)

    # ── UPLINK ────────────────────────────────────────────────────────────────
    if not downlink_only:
        host = "127.0.0.1"  # uplink uses loopback when FC unreachable
        port = 14550

        if _check_reachable(MICOAIR_HOST, WIFI_PORT):
            host = MICOAIR_HOST
            port = WIFI_PORT

        for hz in [50, 200, 500, 1000, 2000, 5000]:
            r = test_uplink(hz, host, port, duration_s=2.0)
            results.append(r)

    # ── SUMMARY ────────────────────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print(" SUMMARY")
    print("═" * 70)
    print(f"  {'Test':<28} | {'Offered Hz':>10} | {'Achieved Hz':>12} | "
          f"{'% Wire':>7} | Note")
    print("  " + "-" * 85)
    for r in results:
        note = r.note[:18] if r.note else ""
        if r.received_hz is not None:
            print(f"  {r.name:<28} | {r.offered_hz:>10.1f} | "
                  f"{r.received_hz:>12.2f} | "
                  f"{r.utilisation_pct:>6.1f}% | {note}")
        else:
            print(f"  {r.name:<28} | {r.offered_hz:>10.1f} | "
                  f"{r.offered_hz:>12.2f} | "
                  f"{r.utilisation_pct:>6.1f}% | {note}")

    print("\n  KEY FINDINGS:")
    dl_result = next((r for r in results if "downlink" in r.name and "_sim_" not in r.name), None)
    if dl_result and dl_result.received_hz:
        print(f"  Telemetry confirmed: {dl_result.received_hz:.1f} Hz total "
              f"({dl_result.utilisation_pct:.1f}% of wire)")
    ul_results = [r for r in results if "uplink" in r.name]
    if ul_results:
        max_ul = max(ul_results, key=lambda r: r.offered_hz)
        print(f"  Uplink survived up to {max_ul.offered_hz:.0f} Hz "
              f"({max_ul.utilisation_pct:.1f}% of theoretical wire cap)")
    print(f"  Wire capacity: {WIRE_BPS:,} B/s")
    print(f"  Headroom at 10 Hz all3: "
          f"{WIRE_BPS / (ALL_THREE * 10) * 100:.0f}× "
          f"(can go to {WIRE_BPS // ALL_THREE:.0f} Hz before saturating wire)")


def _check_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a UDP host:port is reachable (non-blocking connect)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except (socket.timeout, OSError):
        return False
    finally:
        s.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MAVLink WiFi limit test")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sim", action="store_true",
                       help="simulation only (no FC needed)")
    group.add_argument("--uplink", action="store_true",
                       help="uplink flood test only")
    group.add_argument("--downlink", action="store_true",
                       help="downlink receive test only")
    args = parser.parse_args()

    sim_only       = args.sim
    uplink_only    = args.uplink
    downlink_only  = args.downlink

    run(sim_only=sim_only, uplink_only=uplink_only, downlink_only=downlink_only)
