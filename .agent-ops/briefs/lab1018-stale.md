> RUN RULE (rerun 2): do the reading yourself. Do NOT dispatch codebase-scout or any subagent and do NOT end your turn with 'waiting for its report': the first run did that and returned nothing. Your turn ends only when `.agent-ops/out/argon-lab1018-stale.md` holds the finished report.

# Research brief lab1018-stale: what freezes the service's stream values for >0.5 s at traj_go

Read-only. No edits, no commits, never POST to 8081 or touch the drone. 15-minute budget, 160K token ceiling
(GEMINI.md rule 10). Output: `.agent-ops/out/argon-lab1018-stale.md`, at most ~1200 words. Every claim cites
file:line or a log line with timestamp. Mark guesses UNVERIFIED.

## Facts already verified (do not re-derive)

- Abort sample age = `time.time_ns()` minus the per-key `_key_ts` of the newest copy of each `_FRESHNESS_SYMS`
  symbol (`ground_station/service/campaign_live.py:57,188-215`, `ground_station/service/core.py:901-922`).
- `_key_ts` is host receive time set at ingest (`core.py:688,780,847` pass `time.time_ns()`/`now`;
  `telemetry_adapter.py:~436-470` merges per key).
- Lab 2026-10-18 flight 08 (mem1, ~21:28-21:29, `logs/sessions/20261018-212*`/events.jsonl, the matching
  `logs/*mem1*.slot*.csv`): abort `stale_telemetry` (finite age > stale_s 0.5 s) 0.1 s after `traj_go`, while the
  telemetry CSV max gap was 0.11 s. Flights 03/04/15 also tripped stale_telemetry on the ground (air 0.0 s).

## Questions

1. Which thread ingests bridge frames into `_streams` (core.py ~:680-850), and what can block it: a lock it shares
   with `submit_command`, `command_result`, `latest_values`, preset/var writes, schema/DWARF resolution, slot
   re-subscribe, or the CSV writer? Name each lock and every holder that can hold it for >0.1 s.
2. What does `traj_go` do on the host (campaign_runner / campaign_live: CMD_TRAJ submit, any re-subscribe or
   preset write in the same step)? Could it hold such a lock, or make the freshness symbols' slot re-subscribe so
   the newest copy stops updating?
3. Does the CSV writer read from the same `_streams`, or from the bridge directly? (If directly, the CSV can be
   gap-free while `_streams` is frozen.)
4. From flight 08's logs: which freshness symbol was old and by how much, if any log records it.
5. Fix proposal: the smallest change that keeps ingest running during command waits, with a unit test shape.

## Deliverable

Bottom line (4 lines), then one short section per question, then the fix with file:line.
