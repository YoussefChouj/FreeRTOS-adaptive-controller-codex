# S14 — Command panel expansion and plugin improvements

Address the S13 gap analysis findings: expand the Command Panel plugin to full command coverage, fix command-ID mismatches, add safety classification, and ship missing sub-panels (Virtual RC, Bench Mode, Navigation Paths, EKF Reset). Also add time-series, FFT, bandwidth, replay, and path planning panels.

## Objective

Deliver an operational-quality dashboard with complete command coverage and a full set of analysis panels.

## S13 gap analysis findings to address

| Gap | Fix |
|-----|-----|
| Command panel covers only ~12 of 30 commands | Expand `COMMAND_REGISTRY` to all 30 commands from `COMMAND_SPEC.md` |
| Abort All ID wrong (`0x13` instead of `0x0D`) | Correct all known ID mismatches |
| No safety classification | Add `critical`/`boundary`/`operational`/`diagnostic` classes with visual badges |
| No ARM/SDK live status | Add top-bar badge driven by `ch13`/`ch14` |
| No Virtual RC panel | Add 5-channel sliders, enable/disable, center-all, SDK-mode gating |
| No Bench Mode panel | Add confirm-guarded toggle with live status pill |
| No Navigation Paths panel | Add Start/Stop for TWC, Sinusoid, Circle, Figure-8 |
| No EKF Reset confirmation | Add disarm precondition + `confirm()` guard |
| Command history too short (20) | Extend to 50 entries, add session grouping, filter chips |
| Result polling: timeout → "assumed applied" | Change to "Submitted (no result feedback)" |

## S14 plugin deliverables

| Plugin | File | Notes |
|--------|------|-------|
| Command Panel (expanded) | `command-panel.js` | Full 30-command registry, 1042 lines |
| Time Series | `time-series-panel.js` | SVG line chart, up to 4 vars |
| FFT Spectrum | `fft-panel.js` | Radix-2 Cooley-Tukey, peak detection |
| Bandwidth Manager | `bandwidth-panel.js` | Per-slot rate/loss, budget bar |
| Session Replay | `replay-panel.js` | List/browse/export/playback |
| Path Planning | `path-panel.js` | 2D canvas, waypoints, EKF position |

## Session brief deliverables

1. **REPORT_S14-commands.md** — Command panel expansion detail
2. **sessions/S14-implementation.md** — This file
3. Updated `STATE.md` with S14 gate result

## Verification checklist

- [ ] All 30 commands present in `COMMAND_REGISTRY` with correct IDs
- [ ] Abort All sends `0x0D` (not `0x13`)
- [ ] Safety class badges visible on dropdown and quick-command buttons
- [ ] ARM/SDK badge updates on state change
- [ ] Virtual RC panel visible only when SDK mode
- [ ] Bench Mode ON shows confirmation dialog and blocks when armed
- [ ] EKF Reset blocked when armed, shows `confirm()`
- [ ] Command history shows session grouping and filter chips
- [ ] Result polling shows "submitted (no result feedback)" on timeout
- [ ] All 6 new plugins load without errors in browser
- [ ] 14 plugin panels listed in `STATE.md` plugin registry
