# MicoAir WiFi Link — Telemetry Protocol

**2026-08-10. Replaces com0com-based approach.**

## Physical link

```
Drone FC (USART3 @ 921600) → MicoAir WiFi module → WiFi UDP → PC
```

The MicoAir module bridges UART3 and WiFi transparently:

| Direction | Protocol | Port | Notes |
|---|---|---|---|
| FC → PC (telemetry) | UDP | **14550** | Module pushes; no application-level handshake needed |
| PC → FC (commands) | UART5 VCP | COM6 | `0xCC 0xDD` grammar. CMSIS-DAP dongle shares the probe. |

**PC must join the module's AP** (`MicoAir-XXXX`, `192.168.4.1`). The module does **not**
forward between its AP and the upstream internet — the PC's default route goes out the
phone tether; WiFi stays on the module net.

## The nudge requirement

The module routes its UDP downlink to the **source of the most recent uplink datagram**.
Bind alone receives nothing — the first send from the PC's socket is what aims the stream.

Every script that reads UDP 14550 must send at least one datagram before counting:

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 14550))
sock.sendto(b"\x00", ("192.168.4.1", 14550))  # nudge — one byte is enough
```

The nudge is harmless on the FC: `USART3->HandleRx` dispatches `0xCC 0xDD` command frames
and counts them in `UA3RxFrameCnt`; a single-byte nudge is a no-op command frame and is ignored.

## Validated WiFi subscribe capabilities

The MicoAir telemetry UART and FC USART3 must both use **921600 baud**. The module web panel
at `http://192.168.4.1` persists this setting through **Save & Reboot**. At 115200, USART3
traffic is corrupted and no framed reply can be decoded.

With both sides at 921600, the WiFi lane provides:

- `0xCC 0xDE 0x21` stream-subscribe setup on UDP 14550.
- `0x08` schema replies and `0x7F` firmware errors on the request socket.
- Framed `0x09 + slot` stream data on the same WiFi link.
- The boot-default stream in slot 0 at 20 Hz (`divider=10`) after reset.
- Existing framed telemetry, including `0x01` and `0x02`, alongside subscribed streams.

Live validation 2026-08-20:

- `0x21 -> 0x08` round-trip: PASS.
- Subscribed stream rate: **258.7 Hz** (`xTickCount`, `divider=1`, 10 s capture, 0 losses).
  This exceeds the 200 Hz Send_Task tick because `_wait_for_frame` uses a 2 ms poll — frames
  accumulate slightly faster than the tick cadence. The true firmware tick rate is the
  **lower bound**: ≥200 Hz, consistent with `258 Hz ÷ 2 ms poll ≈ 200 Hz`.
- MicoAir uplink ceiling: ~1100 Hz (commands never saturate).
- Wire utilisation at 258 Hz subscribe stream: **~22 % of 91304 B/s**.

Correction 2026-09-19: the 258.7 Hz figure above was a host polling artefact.
Counting raw `0x09` frames on the UDP socket gives ~80 Hz before the Phase 0
flash and ~100 Hz after it, at `divider=1`.

## Measured capacity

| Metric | Value |
|---|---|
| UART wire | 921600 baud, ~91500 B/s |
| UDP payload | ~90363 B/s |
| Efficiency | **98.8 % of wire** |
| Loss | **0.00 %** (alphabet ladder, `scratchpad/micoair_ladder.py`) |

The module adds ~1 % overhead over the raw wire. This is 55× the UART5 CMSIS-DAP path
(~1600 B/s real ceiling).

## Telemetry frames (FC → PC)

The machine-readable contract is `docs/telemetry_protocol_schema.json`. The test-time gate
`tests/test_protocol_schema.py` checks its envelope, frame lengths, CRC families, and command
sync constants against the Python parser assumptions. It does not alter runtime transport
behavior. Firmware changes still require updating the registry and the corresponding parser
tests together.

### Frame header

```
Byte 0: 0xAA
Byte 1: 0xBB
Byte 2: frame_type  (0x01 = Frame A, 0x02 = Frame B, 0x03 = SysID, ...)
Byte 3: LEN_hi       (payload length, big-endian uint16)
Byte 4: LEN_lo
Byte 5: MAX_NUM_BASIS  (MRAC basis count)
Bytes 6..(6+LEN-1): payload
Last byte: CRC8_XOR  (XOR of bytes 2..(6+LEN-1))
```

### Frame A (0x01) — 100 Hz, MRAC inner loops + status

Used by VOFA+ JustFloat stream.

```
Offset  Type      Name
0       float32   pitch.e
4       float32   pitch.u_ad
8       float32   roll.e
12      float32   roll.u_ad
16      float32   yaw.e
20      float32   yaw.u_ad
24      float32   z.e
28      float32   z.u_ad
32      uint8     status.arm
33      uint8     status.flymode
34      uint8     status.sbus_lost
35      uint8     status.twc_execute
36      uint8     status.twc_arrived
37      uint8     status.rc_authority  (v2+: PC=1, RC=0)
38      uint8     status.of_hold       (v13+: 1=OF hold, 0=angle)
39      uint8     status.estimator_ready (v13+: 1=converged/armable)
40      uint8     proto_version
```

v10 firmware emits 39-byte payload (no `of_hold`/`estimator_ready`). v13 emits 41.
`socket_bridge.py` accepts both.

### Frame B (0x02) — 20 Hz, MRAC weights + PID + path

Payload size depends on `MAX_NUM_BASIS`. See `TASK/send_data.c`.

### SysID frame (0x03) — 100 Hz, excitation data

Active during MRAC system identification. Replace A/B frames while running.

### OF calibration frame (0x05) — 200 Hz

Active during optical-flow calibration.

## Subscribe protocol (PC → FC)

PC sends a `0xCC 0xDE` **subscribe request** over WiFi (UDP 14550 → USART3) to
start streaming named variables. UART5 subscribe ingress is compiled out
(`SUBSCRIBE_UART5_ENABLED = 0`); a request sent to COM6 is ignored.

```
0xCC 0xDE 0x21 LEN_HI LEN_LO NRANGES  [divider] [transport] [slot]
          N x ( addr u32 LE, size u16 LE, count u16 LE )  CRC8_XOR
```

- `CRC8_XOR` = XOR of every byte from `0x21` up to the last range byte
- `transport`: 0 = UART5, 1 = USART3
- One request per slot; a new request **replaces** that slot's ranges
- Max 62 ranges per slot (a 62-range request is 506 B; RX staging is 512 B)
- `divider = round(send_hz / desired_hz)`; Send_Task measures ~100 Hz after
  the Phase 0 flash (target 200 Hz, see `docs/architecture/send-task-perf.md`)
- `addr` = DWARF address resolved from `OBJ/JX_FLY.axf` (check the build
  identity first, see "Firmware Identity Protocol" below)
- Up to **4 slots**, each at its own independent rate
- Replies: `0x08` schema, `0x09 + slot` data, `0x7F` error (e.g. over link budget)

Firmware `Send_Task` sends existing frames (A/B/SysID/OFCal) regardless of subscriptions.
Subscriptions add **streaming frames** on top.

**`stream_log.py` handles all of this** — it reads `log_frames.md` to get variable names,
resolves addresses from the ELF, and sends subscribe requests automatically:

```bash
# default frame (from log_frames.md)
python -m ground_station.livewatch.stream_log --transport usart3 --seconds 30 --out logs/run.csv

# custom groups at different rates
python -m ground_station.livewatch.stream_log --transport usart3 \
  --group "40:mrac_state.roll.Theta:6" \
  --group "5:imu_data.rol:3" \
  --out logs/custom.csv
```

## Command protocol (PC → FC)

**Commands go over UART5 only** (COM6, CMSIS-DAP VCP). The radio path does not forward them.

```
0xCC 0xDD [CMD] [IDX] [float32 LE] [CRC8_XOR]
```

| CMD | Name | Notes |
|---|---|---|
| 0x01 | PID gains | |
| 0x02 | MRAC gamma | |
| 0x03 | mixer / u_max | |
| 0x04 | flight mode | idx0=DangerousStop+abort, idx1=SDK |
| 0x06 | virtual RC | SBUS lost + SDK authority |
| 0x07 | bench mode | |
| 0x09 | GS safety limits | max_horiz_m/s, max_vert_m/s, max_pitch/roll_deg |
| 0x0A | TWC (point target) | FlyMode_SDK only |
| 0x0B | sinusoid path | FlyMode_SDK only |
| 0x0C | circle path | FlyMode_SDK only |
| 0x0D | abort all paths | |
| 0x0E | arm/disarm | idx0: val≥0.5=arm, <0.5=disarm |
| 0x0F | MRAC flags | adaptation, projection, deadzone, freeze, saturation, ... |
| 0x10 | reset OF origin | |
| 0x14 | abort SysID | |
| 0x17 | capture OF velocity bias | Drone must be level+still |
| 0x18 | force recal | GROUND_IDLE + disarmed only |

Full table: `ground_station/comm/serial_bridge.py` `_pack_command_frame`.

## VOFA+ integration

VOFA+ connects over UDP directly — no com0com, no virtual COM port.

| Stream | Default port | Protocol | Source |
|---|---|---|---|
| A | **1347** | JustFloat (LE float32 × 13 + tail) | Frame A (100 Hz) |
| B | **1348** | JustFloat (LE float32 × N) | Frame B (20 Hz) |

The dashboard's `VofaManager` auto-generates the channel name list from the Frame A / Frame B
unpacking code in `serial_bridge.py` (`_build_frame_a_channel_names`,
`_build_frame_b_channel_names`). Channel names are applied to VOFA's `vofa+.config.json` on
every `open_plot()` call — no manual renaming needed.

VOFA+ is launched and managed by the dashboard (`Dashboard.open_vofa()`). Standalone launch:

```bash
# Frame A on port 1347 — set VOFA+ protocol to "JustFloat", port 1347
# Frame B on port 1348 — second VOFA+ instance, protocol "JustFloat", port 1348
```

## Tools reference

| Tool | Transport | What it does |
|---|---|---|
| `ground_station.livewatch.stream_log --transport usart3` | UDP 14550 | Subscribe + log named variables to CSV |
| `ground_station.livewatch.watch --transport usart3` | UDP 14550 | Live frame display |
| `ground_station.livewatch.log --transport usart3` | UDP 14550 | Continuous CSV capture |
| `ground_station.gui.dashboard` | UART5 + UDP 1347/1348 | Full GUI + VOFA+ plots |
| `scratchpad/micoair_ladder.py` | UDP 14550 | Throughput/quality benchmark |
| `scratchpad/verify_attitude_frame.py` | UDP 14550 | Frame sanity check |

**No com0com.** All tools speak UDP natively. `scratchpad/micoair_vcom_bridge.py` is retired.

## Measured capacity — updated 2026-08-19

### Wire budget (USART3 @ 921600 actual)

The MicoAir bridges USART3 (921600 baud) to WiFi UDP. USART3 baud is not exactly representable on APB1 @ 42 MHz: BRR=0x2E gives 913043 baud, i.e. 91304 B/s wire capacity (10 bits/byte).

| Metric | Value |
|---|---|
| USART3 actual baud | 913043 (BRR=0x2E) |
| Wire capacity | 91304 B/s |
| Send_Task tick | 200 Hz (5 ms) — drives USART3 TX ring |
| Budget per tick | 456.5 B |

### MAVLink frame sizes (header 8 + payload + CRC 2)

| Message | Payload | Frame | % of tick |
|---|---|---|---|
| MRAC_WEIGHTS (10001) | 132 B | 142 B | 31.1 % |
| EKF_STATES (10002) | 52 B | 62 B | 13.6 % |
| CTRL_DEBUG (10003) | 68 B | 78 B | 17.1 % |
| All three combined | 252 B | 282 B | **61.8 %** |

### Cadence options (theoretical)

| Cadence | Frames | Bytes/s | % Wire | Status |
|---|---|---|---|---|
| 10 Hz all3 (current) | 2820 B/s | 3.1 % | OK |
| 50 Hz all3 | 14100 B/s | 15.4 % | OK |
| 100 Hz all3 | 28200 B/s | 30.9 % | OK |
| 150 Hz all3 | 42300 B/s | 46.3 % | OK |
| 200 Hz all3 (max) | 56400 B/s | 61.8 % | OK |
| 200 Hz MRAC only | 28400 B/s | 31.1 % | OK |

### Real measurements (2026-08-19)

**Downlink — FC telemetry over WiFi:**
- JustFloat stream: **100.2 Hz** confirmed (matches `usart3_send()` polled cadence)
- Throughput: 14716 B/s (16.1 % of wire)
- Frame types received: JustFloat (34.2 Hz), subscribe stream (65.6 Hz), sizes 32–337 B

**Uplink — command forwarding ceiling:**
- MicoAir ESP32 WiFi bridge forwarding ceiling: **~1050 Hz** (9 B frames)
- Python GIL limits host-side loop to ~1090 Hz with non-blocking sockets
- Commands are never the bottleneck: 200 Hz × 9 B = 1800 B/s = 2 % of wire

**Simulation (FakeFC loopback, MAVLink frames):**
- Timer granularity + GIL causes 5–20 % loss at higher send rates — test harness artifact, not FC limit
- Real FC ceiling: 100 Hz (current firmware), wire has room for 200 Hz all3

**Key conclusion:** Wire is at 3.1 % utilisation at 10 Hz all3. There is **32× headroom** — the WiFi link will never saturate during normal telemetry or PID sweeps.

### No MAVLink from real FC yet

The new MAVLink telemetry (`BSP/mavlink_custom.h`, `TASK/send_data.c`) has not been flashed to the FC. A `0xFE` frame was captured with `msg_id=56526` — the MicoAir is translating the byte stream. Flash the firmware to get 10001/10002/10003 MAVLink frames.

## Network topology and IP addresses

| Device | IP | Role |
|---|---|---|
| MicoAir module (AP mode) | `192.168.4.1` | Gateway, UDP relay |
| PC (joined MicoAir AP) | `192.168.4.2` | Ground station |
| FC USART3 | — | Telemetry source |

**The PC must join the MicoAir AP (`MicoAir-XXXX`, password configured via AT commands over the CH340 USB serial port).** The PC's WiFi adapter gets `192.168.4.2` from the module's DHCP server. The MicoAir does not forward between its AP and upstream internet.

The wifi_bridge and all ground-station scripts hardcode `192.168.4.1` as the MicoAir IP. This is correct and verified 2026-08-19.

## Common pitfalls — and how to avoid them

### Pitfall 1: MicoAir not reachable — wrong subnet or AP not joined

The PC must be on the `192.168.4.0/24` subnet. Check `ipconfig`:
```
Wireless LAN adapter Wi-Fi:
   IPv4 Address. . . . . . . . . . . : 192.168.4.2   ← correct
   Default Gateway . . . . . . . . . . : 192.168.4.1   ← MicoAir
```
If the PC has no `192.168.4.x` address, join the MicoAir AP in Windows WiFi settings.

### Pitfall 2: `bind()` receives nothing — forgot to nudge

The MicoAir routes its UDP downlink to the **source of the most recent uplink datagram**. Binding to port 14550 without first sending receives **nothing**.

Every reader script must send at least one byte first:

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)  # avoid buffer overruns
sock.bind(("0.0.0.0", 14550))
sock.sendto(b"\x00", ("192.168.4.1", 14550))   # nudge — one byte is enough
```

The nudge is harmless: the FC's `USART3->HandleRx` dispatches `0xCC 0xDD` command frames and ignores a single-byte no-op.

### Pitfall 3: Python blocking socket + `time.sleep()` limits uplink rate

`time.sleep()` has ~1 ms resolution on Windows. A 50 Hz loop using `sleep(1/50)` actually runs at ~47 Hz. Use non-blocking sockets or a high-resolution timer:

```python
sock.setblocking(False)
interval = 1.0 / target_hz
while running:
    tick = time.monotonic()
    sock.sendto(frame, (host, port))
    elapsed = time.monotonic() - tick
    sleep = interval - elapsed
    if sleep > 0:
        time.sleep(sleep)   # Windows resolution ≈ 1 ms; loop is GIL-limited above ~1000 Hz
```

### Pitfall 4: Windows UDP recv buffer too small

The default Windows UDP receive buffer is too small for sustained high-rate telemetry. Always set it:

```python
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
```

### Pitfall 5: Confusing COM ports

| Port | What it is |
|---|---|
| COM6 (CMSIS-DAP VCP) | UART5 wired to the ST-Link VCP. Subscribe control plane (`0xCC 0xDE 0x21`). |
| COM7 (MicoAir CH340) | MicoAir USB-serial config port. AT commands only. |
| UDP 14550 | MicoAir WiFi UDP. FC telemetry (TX) + command forwarding (RX). |

The ground station tools use UDP 14550 natively. **No virtual COM ports needed.**

### Pitfall 6: Stale ELF causes wrong address resolution

`OBJ/JX_FLY.axf` must match the flashed image. Every address in the ELF can shift between builds. Run `python -m ground_station.livewatch verify` before any address-based read. If it says `STALE ELF`, report nothing — the values are garbage.

### Pitfall 7: Two processes fighting over the CMSIS-DAP dongle

SWD reads and the UART5 VCP share the same ST-Link probe. Close `livewatch` before streaming over UART5, and vice versa.

## Quick reference

```python
# Minimal telemetry reader
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
sock.bind(("0.0.0.0", 14550))
sock.sendto(b"\x00", ("192.168.4.1", 14550))   # nudge
while True:
    data, _ = sock.recvfrom(2048)
    print(f"{len(data)} B from FC")
```

```bash
# Limit test — simulation (no FC needed)
python -m ground_station.comm.tests.test_mavlink_limit --sim

# Limit test — real FC (WiFi connected)
python -m ground_station.comm.tests.test_mavlink_limit

# Uplink only
python -m ground_station.comm.tests.test_mavlink_limit --uplink
```

## Firmware Identity Protocol (Build ID Handshake)

The firmware identity query allows ground stations and tooling (e.g. `livewatch`, `wifi_bridge`)
to inspect the running build identity, load image geometry, and CRC32 directly over WiFi (USART3)
or the debugger VCP (UART5) using the existing subscribe request envelope family (`0xCC 0xDE`).

### Query Request (PC → FC)

Sent over either USART3 (WiFi UDP 14550) or UART5 (VCP):

```
Offset  Width  Field         Value  Description
0       1      SYNC_HI       0xCC   Subscribe family sync byte 0
1       1      SYNC_LO       0xDE   Subscribe family sync byte 1
2       1      CMD           0x24   FW_IDENTITY_CMD
3       1      LEN_HI        0x00   Payload length high byte (0)
4       1      LEN_LO        0x00   Payload length low byte (0)
5       1      FLAGS         0x00   Unused / reserved (must be 0)
6       1      CRC8_XOR      0x24   XOR over bytes 2..5 (0x24 ^ 0x00 ^ 0x00 ^ 0x00)
```
Total request frame length: **7 bytes**.

### Identity Reply Frame (FC → PC, Type 0x24)

Routed via `Subscribe_TxReplyToTransport` to return over whichever link (USART3 or UART5) the query arrived on.

```
Offset  Width  Field         Value       Description
0       1      SYNC_HI       0xAA        Frame start sync byte 0
1       1      SYNC_LO       0xBB        Frame start sync byte 1
2       1      FRAME_TYPE    0x24        FW_IDENTITY_FRAME (0x24)
3       1      LEN_HI        0x00        Payload length high byte (24 bytes)
4       1      LEN_LO        0x18        Payload length low byte (0x18 = 24)
5       1      STATUS        0x00        Frame flags / status byte
6..9    4      magic         0x44495746  ASCII "FWID" in little-endian (0x46, 0x57, 0x49, 0x44)
10..13  4      version       1           Contract version (uint32 LE: 0x01, 0x00, 0x00, 0x00)
14..17  4      image_base    0x08000000  Flash load base address (uint32 LE: 0x00, 0x00, 0x00, 0x08)
18..21  4      image_len     uint32 LE   Image length in bytes (rounded UP to multiple of 4)
22..25  4      image_crc32   uint32 LE   STM32F4 hardware CRC-32/MPEG-2 over flash image
26..29  4      status        1           1 = valid, 0 = uncomputed (uint32 LE: 0x01, 0x00, 0x00, 0x00)
30      1      CRC8_XOR      uint8       XOR checksum over bytes 2..29 (FRAME_TYPE .. status)
```
Total reply frame length: **31 bytes**.

### CRC32 Definition & Computation

- **Polynomial**: `0x04C11DB7` (CRC-32/MPEG-2, standard STM32 hardware CRC peripheral)
- **Initial Value**: `0xFFFFFFFF`
- **Reflection / Reversal**: None (input unreversed, output unreversed)
- **Final XOR**: None (`0x00000000`)
- **Firmware computation**: Hardware CRC unit fed 32-bit words sequentially from flash address
  `image_base` (`0x08000000`) to `image_base + image_len` (reading words little-endian).
- **Host ELF computation**: Bytes of all `PT_LOAD` segments positioned at physical address (`p_paddr`)
  relative to `0x08000000`, gaps and tail padding filled with `0xFF`, length rounded up to multiple of 4,
  then identical word-wise CRC32.

