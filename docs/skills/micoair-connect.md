---
name: micoair-connect
description: >
  Establish and validate a working MicoAir WiFi UDP connection to the flight controller.
  Use when the user asks to connect over WiFi, reports no telemetry, cannot send commands,
  wants to test the WiFi link, or mentions the MicoAir module. Handles the nudge
  requirement, correct IP/subnet, UDP buffer sizing, and common misconfiguration traps.
  Read docs/telemetry-protocol.md for full capacity numbers and frame format reference.
---

# MicoAir Connect

## Network topology

```
FC (USART3 @ 921600) → MicoAir module → WiFi UDP → PC
```

| Device | IP | Notes |
|---|---|---|
| MicoAir AP | `192.168.4.1` | Gateway, UDP relay. Fixed. |
| PC (joined AP) | `192.168.4.2` | Assigned by MicoAir DHCP. |

The PC **must join the MicoAir AP** (`MicoAir-XXXX`) in Windows WiFi settings before any ground-station tool will work.

## Control plane vs data plane

When debugging WiFi subscribe, a successful handshake proves **only** that the command parser, CRC, reply routing, and transport all work. It does **not** prove the data plane works.

Test both planes separately:

1. **Control plane test** — send a `0x21` request, expect a `0x08` schema reply in <2 s. Proves parser, CRC, reply routing, transport.
2. **Data plane test** — after handshake, sniff the wire for `0x0A` frames at the expected rate (`slot.divider × base rate`). Proves `Subscribe_StreamTick` is being called and active slots are being built.

A green handshake with no data frames = bug in `Subscribe_StreamTick`, not in the parser.

### Failure modes

| Symptom | Plane | Likely cause |
|---|---|---|
| No `0x08` reply | Control | Parser rejects request, CRC mismatch, transport blocked |
| `0x08` received but slot times out | Data | Frame fragmentation, `_parse_one` accepted truncated frame |
| Legacy `0x01` frames after subscribe | Data | `Subscribe_StreamTick` not called; active/due state wrong |

Two prior incidents this rule would have caught: `distill-20260821-micoair-fragmentation` (60-byte `0x08` arrived as 2–5 UDP datagrams; first 14-byte packet accepted as the full frame) and `distill-20260615-193708` (dashboard `CMD 0x0E` set `GS_KeySDKflag=1` permanently; the arm control plane succeeded but the stick-read data plane was blocked).

## The nudge requirement (critical)

The MicoAir routes UDP downlink to the **source of the most recent uplink datagram**.
Binding port 14550 without first sending receives **nothing**.

Every reader must send a nudge first:

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)  # always set this
sock.bind(("0.0.0.0", 14550))
sock.sendto(b"\x00", ("192.168.4.1", 14550))  # nudge — one byte is enough
```

The nudge is harmless: FC ignores a one-byte no-op command frame.

## Validation checklist

Run this to confirm a working connection (no FC needed):

```python
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
s.bind(("0.0.0.0", 14550))
s.sendto(b"\x00", ("192.168.4.1", 14550))  # nudge
data, addr = s.recvfrom(2048)
print(f"Got {len(data)} B from {addr}")
print(f"First byte: 0x{data[0]:02X}")  # 0xAA=JustFloat, 0xFE=MAVLink
```

Expected: `Got N B from ('192.168.4.1', 14550)`. If timeout: wrong subnet, AP not joined, or module not powered.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `TimeoutError` on recv | PC not on `192.168.4.0/24` subnet | Join MicoAir AP in Windows WiFi settings |
| `TimeoutError` after joining AP | Module not powered or AP not active | Check MicoAir power/LED |
| 0 B received after nudge | `bind()` called after first send — order matters | Send nudge first, then recv |
| `OSError 10040` on recv | Windows UDP buffer too small | Set `SO_RCVBUF = 65535` |
| Stale ELF values | `OBJ/JX_FLY.axf` does not match flashed image | Run `python -m ground_station.livewatch verify` |

## Ground-station tools

```bash
# Test connection (simulation mode — no FC needed)
python -m ground_station.comm.tests.test_mavlink_limit --sim

# Test with real FC (WiFi connected)
python -m ground_station.comm.tests.test_mavlink_limit

# Stream log (subscribe variables over WiFi)
python -m ground_station.livewatch.stream_log --transport usart3 --seconds 30

# Live watch (named variables)
python -m ground_station.livewatch.watch group:ekf --transport usart3

# wifi_bridge (commands + telemetry + VoFA+ forwarding)
python -m ground_station.comm.wifi_bridge
```

## Subscribe stream benchmarks (2026-08-20)

Measured on a powered, streaming FC over WiFi UDP 14550.

**Single-slot solo rates** (no other active slots):

| Variable | Payload | Divider | Measured rate |
|---|---|---|---|
| `xTickCount` | 4 B | 1 (full) | ~258 Hz |
| `xTickCount` | 4 B | 4 | ~70 Hz |
| `xTickCount` | 4 B | 10 | ~28 Hz |
| `mrac_state.roll.e` | 4 B | 1 | ~182 Hz |
| `mrac_state.roll.Whatf[0..5]` | 24 B | 1 | ~181 Hz |

**Multi-variable in one slot**: frame rate halves as payload doubles (wire bandwidth limit).
Three variables at 12 B each: ~102 Hz. One variable at 4 B: ~182 Hz.

**Multi-slot sharing**: each slot gets its own frame cadence; all share the USART3 wire budget.
Three simultaneous slots (28 Hz / 70 Hz / 181 Hz) all active: link stays healthy.

**Wire utilisation**: ~22% of 91304 B/s at ~258 Hz single-var stream.

**Uplink commands**: ~1100 Hz ceiling (ESP32 bridge limit). Commands never saturate the link.

## Where things live

| What | Where |
|---|---|
| Protocol doc | `docs/telemetry-protocol.md` |
| Wifi bridge | `ground_station/comm/wifi_bridge.py` |
| Limit test | `ground_station/comm/tests/test_mavlink_limit.py` |
| Stream log | `ground_station/livewatch/stream_log.py` |
| Live watch | `ground_station/livewatch/cli.py` |
