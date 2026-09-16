# Purpose

This skill exists because **MicoAir WiFi has a non-obvious "nudge"
requirement** that has wasted hours of debugging when skipped: the module
routes UDP downlink to the *source of the most recent uplink datagram*.
Binding port 14550 without first sending receives nothing.

## Motivation source

- Lesson `distill-20260821-micoair-fragmentation`: 60-byte 0x08 schema
  reply arrived as 2–5 separate UDP datagrams; `wifi_bridge._parse_one`
  must wait for full frame before queueing.
- Lesson `distill-20260821-wifi-subscribe-stale-note`: stale "UART5 only"
  comments in `API/subscribe.h` masked the fact that USART3 subscribe
  works since 2026-08-20.
- Hardware-safety rule: never reconfigure SPI2 pins PC2–PC5 (PC2=MISO,
  PC3=MOSI, PC4/PC5=CS, SCK=PB13).

## What "done" looks like

- PC joined the MicoAir AP (`MicoAir-XXXX`), IP `192.168.4.2`.
- One UDP datagram sent before the bind; downlink verified.
- Frame parsing waits for full frame; truncated frames return `None`.
- CRC8-XOR validates the assembled frame before `pop_frame()` returns it.
- `docs/telemetry-protocol.md` is the canonical capacity/format reference.

## Failure modes this skill guards against

| Symptom | Root cause | Fix |
|---|---|---|
| No downlink after bind | Skipped nudge | Send one UDP packet before recv |
| Slot times out on 0x08 | Fragment accepted as frame | Reject truncated; wait for full |
| Comments contradict code | Stale SECURITY NOTE | Trust code path, update comment in same commit |