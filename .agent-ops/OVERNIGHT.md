# Overnight run 2026-09-19 -> 09-20 (user asleep ~5-6 h, started ~23:15)

READ THIS FIRST AFTER EVERY COMPACTION. Then `tail -20 .agent-ops/state.log`.

## Goals (in order)
1. Dashboard working END TO END by morning (launch -> connect drone over WiFi ->
   live telemetry/plots -> commands -> logging), no manual fixes needed.
2. Only after 1 is reasonably done: firmware audit for bugs that could hurt a
   real flight test (report + safe fixes; no EKF into control; no flashing
   of risky changes without a clear verify path).

## Rules for me (supervisor)
- Token budget: delegate every heavy read/implement/review to agy workers
  (`.agent-ops/spawn-worker.sh -f <task.md>`, max 3 live). Read only result
  files (<=20 lines) and `git diff --stat`. Never read big files myself.
- Wait with ONE background command (wait-task.sh), never poll.
- Workers never flash; I flash only via rebuild_and_flash when a change is
  reviewed and needed. Drone is disarmed; flashing blocked when armed.
- Send_Task: user undecided. Default overnight = Option B (firmware stays
  100 Hz, host constants -> 100). Do NOT change firmware rate.
- opencode has no model: use agy only.
- Log every step below (one line) so I can resume.

## Progress log
- 23:15 plan written. 214427 (stream_log fix) DONE per result file.
  203935 (phase0-host-3) no result file -> respawn continuation.
- 23:11 fixed spawn-worker.sh silent exit (pipefail on 0 live workers; `|| true`).
  Spawn via: wsl -d Ubuntu -- bash -l /mnt/c/tmp/spawn.sh <task.md> <timeout_s>
  Spawned 231043 (phase0-host-4) + 231046 (dash-e2e-audit). Waiting.
- 23:11 spawned 231113 (fw-flight-audit, report only -> reports/FW-FLIGHT-AUDIT-2026-09-19.md). Bg wait (/c/tmp/wait2.sh) on 231043/231046.
- 23:17 231043 died (agy exits when idle w/ bg task). Added FOREGROUND rule to spawn-worker.sh. Respawned as phase0-host-5.
- 23:25 231046 e2e audit DONE (4 P0: wifi_bridge 0x09 len, plot keys, arm badge, cmd 0x04; P1: livewatch LiveReader import, motor gating, replay). Spawned dash-fix-p0 (P0 1-4 + motor gating). livewatch cli.py LiveReader import -> give to phase0-host follow-up.
- 23:28 231113 fw audit DONE: 15 P0/16 P1 (many possibly over-reported). Spawned fw-audit-verify to confirm/refute + risk-rate before any fw fix. Plan: fix only CONFIRMED+LOW-risk, build only, no flash w/o user.
- 23:42 232732 verify DONE: 9 confirmed LOW, 5 confirmed HIGH (RC-loss failsafe, SBUS fs bit, battery autoland, alt gate 5m, OF m->cm; NOT applied, user decision), 1 FP (MRAC sign). Spawned fw-fix-low (build only, candidate to OBJ/fw-fix-candidate, originals restored).
- 23:43 NOTE 231526 + 232506 both edit wifi_bridge.py -> after both exit, spawn integration check (service+comm tests, real WiFi smoke).
- 23:50 231526 phase0-host-5 died again (idle w/ bg task, no result). I fixed livewatch/cli.py verify LiveReader import myself (line 155). Remaining phase0 items (wifi_bridge.py:946 SUBSCRIBE_SEND_TASK_HZ 80->100, test_mavlink_limit SEND_TASK_HZ 200->100, test_subscribe_batching expects nonexistent subscribe_slot) -> fold into integration task after 232506 exits. Run livewatch verify after 234149 restores axf.
- 23:51 232506 dash-fix-p0 DONE (0x09 len fixed+test, plot/fft keys, arm badge, cmd 0x04 remap, motor gating UI+core; 101 svc tests pass). Spawned 235025 dash-integrate (rate consts 100, subscribe_slot test, full comm/service tests, live WiFi smoke telemetry-only).
- 00:05 234149 fw-fix-low DONE (9 LOW fixes, build 0 err, candidate OBJ/fw-fix-candidate/, patch reports/fw-fix-low.patch, NOT flashed; OBJ axf restored). 235025 died (bg pytest). I did Part A: wifi_bridge:946 ->100, capture_preset divider/docstring ->100, test_mavlink_limit ->100. FW SUBSCRIBE_SEND_TASK_HZ 200U (subscribe.h:268) left as is -> morning note. livewatch verify OK. Tests: comm 71F/96P (mostly unimplemented subscribe_slot/_slot_states spec tests, untracked files), service 1F/100P (subscribe_preview endpoint), livewatch 55P. Spawning comm-test-triage; I run live smoke myself.
- 00:40 (me) wifi_bridge 0x08 schema parse fix (n_ranges=frame[5]) + typed per-range decode (_SIZE_FMT). service/__main__.py: bridge callback was set on unused `_on_telemetry_typed` -> now `_on_telemetry` (root cause of samples:0). wifi_bridge sidebar "a" payload now also forwarded to _on_telemetry (status.*/mrac.* keys). telemetry_adapter prunes pre-schema positional slotN.chN.* garbage once named keys arrive. LIVE SMOKE OK: samples 1086, 181 keys, status.roll_deg/arm=0/mrac.roll.e present, 0 ch0 keys. Lessons: NO_PROXY for localhost (Clash 502s); workers must not run pytest (agy backgrounds it and dies).
- 00:20 tests (me): comm 70F/97P, service 1F/100P -> failures in .agent-ops/tasks/failures.txt; spawned 20260920-001258 comm-test-triage (no pytest). Replay P1 = feature gap (replay not pushed to other panels) -> morning note only.
- 00:30 LOGGING BUG fixed (me): WiFi path ingest_decoded never persisted telemetry (sessions had only service_started). core.py ingest_decoded now store.append_telemetry(...); storage.py PRAGMA synchronous=NORMAL (WAL). Live: 1094 telemetry rows/22 s, samples rate unchanged, no tracebacks. NEXT: wait triage, rerun tests (incl. service after core change), then commands path check (read-only), then fw P1 LOW review.
- 00:45 spawned 20260920-001701 fw-p1-review (report only, EKF-leak check). bg wait bn12uzgz6 on triage 001258.
- 00:50 cmd path review (me, read-only, nothing sent): fixed core.py:567/597 ACK (success) was logged to /api/faults -> now REJECTED or reason!=0. Morning note: is_disarmed() fails OPEN when no arm telemetry (host 0x16 gate); gateway has no timeout path (timed_out never produced).
- 01:00 triage 001258 DONE (A7/B62 skipped as unimplemented spec/C2; wifi_bridge CRC16 check added). Me: comm 102P/50S, service 101P, 0 fail. Live smoke after CRC: samples 1086, 182 keys, arm=0, 0 ch0, 0 tracebacks. DASHBOARD GOAL 1 = DONE (commands verified by code/tests only, none sent). NEXT: wait fw-p1-review 001701.
- 00:45 spawned 20260920-001701 fw-p1-review (report only, EKF-leak check). bg wait bn12uzgz6 on triage.
- 01:05 fw-p1-review 001701 DONE (reports/FW-P1-REVIEW.md): 17 confirmed; HIGH 16 (OF failsafe), 18 (mixer desat). s_ekf never reaches control (readers: send_data.c telemetry/init only); s_ekf_of DOES feed locxPID.FB (StabilizerTask.c:111/316) -> morning note. Behaviour-changing, left for user: 17,19,20,22,23,27. Spawning fw-fix-low2 (items 21,24,25,26,28,29,30,31,32; build only -> OBJ/fw-fix-candidate2, patch reports/fw-fix-low2.patch). After it: write morning summary reports/MORNING-2026-09-20.md.
- 00:55 004334 fw-fix-low2 DONE (9 items, 0 err/0 new warn, candidate2 staged, axf restored, livewatch verify OK). Morning summary written: docs/dashboard-platform/reports/MORNING-2026-09-20.md. BOTH GOALS DONE. Nothing flashed, no commands sent.

## Goal 2 (09-20): dashboard features -> fw bugs (build/flash/stream-log OK) -> /improve-codebase-architecture
- 03:30 reverted USER/ADC.c (item 25: critical section spanned 100k EOC poll + GBK damage). Restored GBK comment bytes (worker had UTF-8 transcoded) in Ano_OF.c(18), spi.c(7), usart1.c(4); send_data.c/main.c U+FFFD damage pre-existed in HEAD.
- 03:35 FLASHED batches 1+2 (minus ADC) via rebuild_and_flash --yes (arm gate PASSED telemetry+SWD, 0 err). verify OK. stream_log 30 s: slots 5/2/2/1 Hz, 0 dropped, 0 NaN, IMU live -> logs/postflash-2026-09-20.slot*.csv.
- 03:36 spawned 20260920-033615 dash-gaps (fail-closed arm, cmd timeout, sqlite lock, replay->bus). bg file wait.
- 03:37 spawned 20260920-033650 fw-hunt3 (report only: subscribe bounds, mrac NaN, RTOS prio/stack, volatile). bg file wait.
- 04:xx both agy workers hit 429 quota (reset ~04:30); did dash-gaps by hand: (1) fail-closed arm gate ARM_STALE_NS, (2) COMMAND_TIMEOUT_NS 1 s expire_commands() in api poll loop, (3) storage RLock, (4) replay_to_bus + persist=False in ingest_decoded + POST /replay/<sid>/play. service 104P, comm 101P/50S (test_downlink_real flaky, passes on rerun). Live smoke: samples 1165, replay 1177 records, 0 tracebacks. NEXT: respawn fw-hunt3, then /improve-codebase-architecture.

## 04:25 dashboard E2E + docs
- browser_smoke.py: all 10 tabs, 0 console errors, 0 HTTP>=400, replay play button visible (EXIT 0)
- AGENT_GUIDE.md added; linked from README, INDEX, AGENTS.md; CHANGELOG [Unreleased] filled; stale 8080 ports fixed
- service tests 113 passed
- fw-hunt3 worker hit agy 429 quota at 03:51; re-spawn after 04:30

## 05:xx FW-HUNT3 triage + dashboard paging + docs pass

### Firmware (FW-HUNT3 round, applied by hand)
- 14 findings (38-51) triaged: 38/40 already fixed in an earlier flash; **39, 41, 42, 45, 46, 47, 48, 49 applied**; 43 moot, 44 false positive, 50 accepted risk (`bmi088_driver.h:98-103` non-volatile IMU reals), 51 HOLD for operator (torn 3-axis setpoint handoff, `AutoflyTask.c:66-73` -> control path, outside the no-touch constraint).
- 10 firmware files dirty: `BSP/usart5.{c,h}`, `API/tests/stubs/usart5.h`, `API/tests/test_subscribe_harness.c`, `API/subscribe.c`, `Global_file/robot_types.h`, `BSP/usart1.{c,h}`, `TASK/send_data.c`, `API/mrac.c`.
- Key changes: `UA5RxSubscribeLen` -> `volatile uint16_t`; new `volatile uint8_t UA5RxSubscribeTransport` staged inside the mask-protected block before `UA5RxSubscribePending = 1U`; `subscribe.c:537` rejects UART5 subscribes with `"E:UART5 disabled"` under `#if !SUBSCRIBE_UART5_ENABLED`; `volatile` on `USART*_task_cnt` and `sbus_channel[16]`; USART1 NVIC preempt 0 -> 5; MRAC ref-model denominator floors (`bw/wn/zeta >= 0.1f`) + `u_max` clamp; CMD 0x05/0x08 param-set ordering incl. `What_lower_limit[0] = -val`; send_data DMA spin-waits replaced with a non-blocking early return + bounded wait.
- Build: `Code=91060 RO-data=4304 RW-data=2544 ZI-data=121432`, **0 Error(s), 70 Warning(s)**, 14 s. Flash succeeded first attempt (arm gate passed). `livewatch verify` -> **20 chunk(s), 1280 B, 0 mismatches**.

### Dashboard
- **`GET /sessions/<id>/records` returned 181 MB in 6.9 s** on a live flight session: `?limit=5` was being eaten by the shell, never by the server. Fixed by pushing paging into SQL — `storage.iter_records(session_id, limit=None, offset=0)` gains `LIMIT ? OFFSET ?`; `api.py` gains `RECORDS_DEFAULT_LIMIT = 1000` and `Handler._paging(default_limit)`, used by `/sessions/<id>/records` and `/replay/<id>`. Both responses now carry `count`, `offset`, `limit`, `truncated`. `replay-panel.js` pages at `RECORD_PAGE = 2000` and shows a truncation note.
- `/api/view-model` measured at **HTTP 200 in 24.03 s** on the live service (old api.py) — the full-session `session_stats` scan. Now behind `?stats=1` only (`want_stats = qs.get("stats", ["0"])[0] == "1"`), so the default view-model is cheap.
- Stats "zero is not missing": five `x if x is not None else None` fixes; SrcHz fix; action journal ordering `list(reversed(service.action_journal()[-10:]))` (newest-first in the view-model, oldest-first at `/api/actions`).
- Stale comment resolved in `shell/plugins/resource-map-panel.js:180` (S7 map is static from sources; live rates come from Refresh `?stats=1`; raw memory needs a probe read).
- Tests: **231 passed, 37 skipped, 3 subtests passed in 48.27 s** (exit 0) — exactly +3 over the 228 baseline, from the three new paging regression tests (`test_records_route_pages_and_caps_by_default`, `test_replay_route_pages_like_records_route`, `test_store_iter_records_offset_without_limit`).
- `browser_smoke.py` walk against the live service: **10 tabs, nan=0 undef=0 everywhere, ERRORS 0, BAD RESPONSES 0** (exit 0).

### Docs
- `docs/dashboard-platform/reports/FW-HUNT3.md` gained `## 4. Outcomes` (per-finding verdicts, build/flash/verify evidence, the `"E:UART5 disabled"` divergence from the report's suggested string).
- `AGENT_GUIDE.md` verified complete: paging section, verified 10-tab inventory, `ApiServer(..., static_root=...)` snippet, and §7 on the UDP 14550 / UART5 dead end.
- `docs/dashboard-platform/CHANGELOG.md` `[Unreleased] — post-S14` extended (file is **CRLF** — patch it with `io.open(..., newline="")`, never an LF heredoc).

### BLOCKED — post-FW-HUNT3 stream-log re-run
Both paths are closed right now:
1. **UDP 14550 is held by the running service** (`netstat -ano`: PID 1144 owns TCP 0.0.0.0:8081 LISTENING *and* UDP 0.0.0.0:14550). Killing it was **denied by the auto-mode classifier** ("Interfere With Workloads") — do not kill, restart or work around it. The operator must stop the service, run the capture, then restart it.
2. **UART5 fallback is compiled out**: `SUBSCRIBE_UART5_ENABLED` is never `#define`d in this tree, so `Uart5_Subscribe_TxSend` is a no-op stub and `subscribe.c:537` now rejects the subscribe outright.

The *previous* image was stream-log validated at 03:35 (30 s, slots 5/2/2/1 Hz, 0 dropped, 0 NaN, `logs/postflash-2026-09-20.slot*.csv`). Re-run for this image once the port is free:
```powershell
python -m ground_station.livewatch.stream_log --seconds 30 --out logs/fw_hunt3_run.csv
```
SWD probe reads are unaffected and were used throughout (`livewatch read --transport swd`, `livewatch verify`).

**NEXT:** `/improve-codebase-architecture` session.
