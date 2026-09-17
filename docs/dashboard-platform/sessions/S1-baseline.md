# S1 — Baseline and contract audit

## Objective

Produce an evidence-backed baseline before modifying firmware or dashboard code.

## Inspect

- `TASK/send_data.c`, `API/subscribe.*`, USART3/USART5 drivers, command handlers
- Existing manifests, presets, decoders, Wi-Fi bridge, livewatch, flashtool
- FreeRTOS tasks, queues, hooks, stack/heap instrumentation
- Keil project, current ELF, build and flash verification

## Validate on hardware

- Wi-Fi telemetry receive and command round trip
- Multi-slot subscription setup, rates, loss, and stale-slot behavior
- Safe command acknowledgement/rejection behavior
- SWD build/flash/ELF verification path
- Existing dashboard/bridge simulation path

## Deliverables

- Baseline protocol matrix and command matrix
- Firmware ownership/resource inventory
- Known inconsistencies and migration risks
- Captured reference session and replay fixture
- Report and updated `STATE.md`

## Gate

No implementation session starts until all current commands and telemetry paths
are classified as verified, unverified, deprecated, or unsafe.

