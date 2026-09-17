# Adaptive Controller Ground Station Platform

This directory is the durable implementation handoff for the modular firmware,
ground station, experiment, and agent-observability program. Agents must read
`IMPLEMENTATION_PLAN.md` and `STATE.md` before changing code, then update
`STATE.md` and the relevant session report when they finish.

## Required session order

1. **S1 — Baseline and contract audit** (`sessions/S1-baseline.md`)
2. **S2 — Firmware identity, capability, and registry foundation**
3. **S3 — Transactional command and event protocol**
4. **S4 — Typed telemetry/schema and transport hardening**
5. **S5 — Safety authority and controller/estimator plugin runtime**
6. **S6 — Firmware experiment runtime and active/shadow sweeps**
7. **S7 — Generated resource map and RTOS observability**
8. **S8 — Ground-station core service, session log, and replay**
9. **S9 — Dashboard shell and plugin API**
10. **S10 — Operational plugins: status, estimator, adaptive control, bench, paths**
11. **S11 — Analysis, automation, and agent API**
12. **S12 — Integrated hardware validation and release hardening**

Sessions are sequential unless a brief explicitly permits parallel work. Every
session must leave the repository buildable, update `STATE.md`, and write a
report under `reports/`.

