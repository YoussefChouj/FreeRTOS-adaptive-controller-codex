# Forensics brief lab1018-forensics: why every 1-2 flights needed a manual 8081 restart (lab 2026-10-18)

Read-only. No edits to tracked files, no commits, no builds, never POST to 8081 or touch the drone/probe.
You may run read-only Python/grep over logs; scratch files only under `.agent-ops/out/scratch-lab1018/`.
160K token ceiling (GEMINI.md rule 10). Output: `.agent-ops/out/argon-lab1018-forensics.md`, at most ~2000 words.
Every claim cites a log line (file + timestamp) or code (file:line). Mark guesses UNVERIFIED.

## What the operator saw (2026-10-18, ~16:40-22:45 local, 6-8 flights, plans lab-m-memory-u0/u100, lab-z-ground-effect)

- Going from one flight to the next was hard: after 1-2 flights `fly` stopped working until they closed and
  reopened the 8081 service (`python -m ground_station.service --port 8081 --lab`), every time.
- One restart hung: `campaign_fly.ensure_service` (ground_station/service/campaign_fly.py:88-123) killed a stale
  8081 and the new one died with `PermissionError [WinError 10013]` binding UDP 14550 (comm/wifi_bridge.py:~424)
  because the labday live logger `ground_station/livewatch/stream_log.py` (DEFAULT_USART3_UDP_PORT 14550, :57,
  plus `--relay-port 47800`) already owned it. Fly waited 60 s and exited.
- After another restart: link True but slots 1-3 were 17 s old, arm_state "unknown", fly printed NO ARM SIGNAL.
- mem1 aborted `stale_telemetry` 0.1 s after `traj_go` while the telemetry CSV max gap was 0.11 s (ground-side
  false trip; a 0.3 s hold is now shipped, f51d3de3, but the root cause is unknown).
- Some flights had no camera fixes sent at all; some flight paths were "horrible".
- Terminal UX: a dry run before every flight (should be once per session); the countdown overwrites other output.

## Evidence

- `logs/sessions/20261018-*/` (events.jsonl and friends), `logs/campaigns/*20261018*`, `logs/campaigns/launch/*1018*`
- `logs/*mem*-lab-m-memory-*.slot*.csv`, `logs/video_truth/{mem1..mem6,z1}-*/` (tracker logs, cam_N.mkv sizes)
- any service / stream_log / labday output files under logs/ written 2026-10-18 (find them by mtime)
- code: ground_station/service/ (campaign_fly.py, campaign_live.py, campaign_runner.py, abort_monitor.py,
  __main__/app), ground_station/comm/wifi_bridge.py, ground_station/livewatch/stream_log.py,
  ground_station/lab/labday.py + labday.cmd, ground_station/lab/camera_truth.sh, ground_station/analysis/live_tracker.py

## Questions

1. **Flight table.** One row per fly attempt today: start time, plan, service pid/instance if logged, outcome
   (completed / abort reason / never armed / hung), camera fixes sent count, telemetry max gap, what the operator
   had to do before the next one. Count the manual restarts you can prove.
2. **What breaks between flights.** After a good flight, what state in the running 8081 stops the next `fly`?
   Candidates to confirm or kill with evidence: subscription plan not re-sent or overwritten (the logger and 8081
   both talk to the module on 14550 / 47800), runner state not back to idle, a stuck thread or lock, latest_values
   not refreshed, the WiFi module serving only one peer. Name the code path.
3. **Port 14550 ownership.** List every process that binds or sends to UDP 14550 / 47800 in today's workflow and
   in what order labday starts them. Propose one owner design (who binds, who relays, what happens on an 8081
   restart) with the code sites to change.
4. **stale_telemetry root cause.** How campaign_live computes `age_s` (clock source, which symbol), whether a
   blocking var_write / preset_write holds a lock that freezes latest_values, and the `age_s = inf` path for a
   missing freshness symbol (nonfinite:age_s trips undebounced). Which one fits mem1's timing?
5. **No camera fixes.** Which flights sent none and why (tracker restarts "restarts 3", 0-byte cam_N.mkv,
   fix relay via 8081 :47800 while 8081 was restarting, FixScheduler window).
6. **Terminal UX.** In labday/fly, where the dry run is triggered per flight, and which writer (countdown `\r`
   loop?) collides with other prints. file:line for each.
7. **Anything else** that looks like a hidden bug or friction from today's logs, ranked by how much lab time it cost.

## Deliverable shape

1. Bottom line: 6 lines max. 2. Flight table. 3. One section per question with evidence and a proposed fix
(file:line, what to change, how to test it). 4. Ranked fix list. 5. UNVERIFIED items.
