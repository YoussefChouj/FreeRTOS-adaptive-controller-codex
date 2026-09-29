# Review brief: 3D panel flight UX + Streams panel (2026-09-29)

For the agent that wrote `3d-panel-flight-ux-spec.md`. Purpose: adjudicate the implementation against the spec.
Branch `claude/inspiring-bell-e0jl6u`, range `bd74668..HEAD` (6 commits, 23 files, +4739/-48). No PR is open.

## How to review

```
git log --format='%h %s' bd74668..HEAD
git diff bd74668..HEAD -- <path>
python -m pytest ground_station/service/tests/test_streams.py ground_station/service/tests/test_streams_panel.py \
  ground_station/service/tests/test_path_fly.py ground_station/service/tests/test_rec_logs.py \
  ground_station/service/tests/test_service.py ground_station/comm/tests/test_slot0_layout.py -q
```
Do NOT POST to a live service. Demo-server screenshots were taken against a mock, not hardware.

## Spec item -> commit -> where to look

| Spec item | Commit | Code | Tests |
|---|---|---|---|
| 1 Fly mode (Plan/Fly/Review, drone dot, fixed camera, green/red trail, threshold slider, 3D/xy, whole/fading, event markers, metrics strip, keys R N F T 1-4) | 2203d9c | `shell/plugins/path-panel.js` | `path_fly_harness.js`, `test_path_fly.py` |
| 1 Slot 0 gains the Fly variables | 2203d9c | `ground_station/comm/boot_default_layout.py` | `test_slot0_layout.py` |
| 2 Multi-log overlay (N logs, colour per log, colour-by-error) | 18196b9 | `path-panel.js` Review mode, `service/rec_logs.py` | `test_rec_logs.py` |
| 3 Streams backend (plan/apply/restore/presets/log/forward) | a87ad8f, bd474b7 | `service/streams.py`, `api.py`, `core.py` | `test_streams.py` (33) |
| 3 VOFA Studio refuses to start while 8081 owns the link | a87ad8f | `vofa_studio/__main__.py` (exit 2, `--dashboard-port`) | `test_streams.py` |
| 3 Streams panel UI (slot table, editor, ELF autocomplete, budget bars, restore chips, timed/rolling/unlimited logging, VOFA+ forward) | 16b7a91 | `shell/plugins/streams-panel.js`, `shell/index.html` | `streams_panel_harness.js` (16), `test_streams_panel.py` |
| Docs | 567acd7 | spec "Phase 3 as built", `vofa-studio-spec.md`, `AGENT_GUIDE.md` | - |

## Verification status

- Done: unit tests, Node fake-DOM harnesses, Chromium screenshots against a demo server and mocked GET snapshots.
- NOT done: any run against the live firmware / 8081 service. Everything hardware-facing is unverified.
- Full-suite run (`service/tests` + `vofa_studio`): 458 passed, 12 failed. After that run I fixed the 2 route-map failures.
  The 3 `vofa_studio` failures reproduce on base `bd74668`, so they are pre-existing.
  The other 7 (`test_all_panels_offline_audit`, `test_t5` x5, `test_terminal_ws_echo_t12b_ok`) I believe are pre-existing but did NOT check against base.

## Spec deviations and judgement calls (adjudicate these)

1. **Slot-0 headroom is 1.** Fly variables took slot 0 from 54 to 61 of 62 ranges. The spec said "check the budget before adding"; it fits but leaves little room.
2. **Swap gate is `can_swap && !active_preset && !apply.busy`.** Disarmed-only per spec; additionally refused while a full-layout preset is active or an apply is in flight.
3. **Restore of a lost tab** is one click in a chip on the Streams panel; it is not shown inside the affected panel itself. Spec says the tab "shows" the message.
4. **Tab-to-slot map** is the `TAB_NEEDS` table in `streams.py`, built by reading the plugins. `s_ekf_of.x[*]` was dropped as never read. Please spot-check it, because the spec said "do not guess".
5. **Single preset store** is `vofa_studio/presets/*.json`; the YAML layouts are read-only, as specified.
6. **Forward channels** are seeded from slots 1-3 names; target 127.0.0.1:1347 (FireWater UDP).
7. **`/subscribe` has no arm gate of its own.** The gate sits in the streams service only, so anything calling `/subscribe` directly bypasses it.

## Known gaps

- Motor-bench panel reads `motor.rpm_*`, but the default slot-3 group does not carry them (pre-existing mismatch, not fixed).
- Status panel's Fly-mode label does not match the new Fly mode.
- Swaps while armed: blocked until the bench measurement the spec asks for.
- Agent MCP tools (camera, markers, overlays): deferred, per "Later".
- Untracked `logs/activity/2026-09-29.jsonl` is the service's runtime journal; deliberately not committed.

## Suggested adjudication checklist

- [ ] Fly trail error uses position minus firmware `.Des` in hold and path modes (not path-only).
- [ ] Nothing in Fly/Streams sends a flight command; keys are UI-only.
- [ ] Apply is refused while armed at the API, not only in the UI (`streams.py` apply/restore).
- [ ] Budget: per-slot <=62 ranges / <=499 B, <=4 slots, total <= 87552 B/s enforced in `plan`.
- [ ] Bench-measure re-subscribe timing, then decide on armed swaps.
