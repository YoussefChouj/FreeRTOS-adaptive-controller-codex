# Lab friction retro, 2026-10-18 (fix after the lab)

**Bottom line.** The operator was at the lab for about an hour before the first flight (the operator's count). The cause was
that the day was **built at the lab instead of before it**. The day file was committed at 17:15, after the operator had
arrived. Nine more lab-path commits followed until 18:35. The service that ran the preflight was started at 17:13 and never
picked any of them up. The fix is a **lab-ready gate**: run it the evening before. Arriving at the lab should take
three steps: power on, `.\labday <date>`, fly.

Think of a pilot's preflight checklist: it is done on the ground before boarding, not while the passengers wait in their seats.

## What happened (commit times are measured, from `git log`)

| Time | Event |
|---|---|
| 08:15-09:10 | Learning session Q6-Q8 (gap ledger) |
| 11:01-11:33 | Three Jules jobs started for the thesis-flight prep, then a pause for travel |
| 17:13 | 8081 service started (this process ran until 18:48) |
| 17:15 | **Day file for 10-18 committed** (thesis flights), at the lab |
| 17:23-18:35 | 9 more lab-path commits: cam.cmd, the mrac_theta group (17:37), slow-50 memory plan, mem1-mem6 order, memory_premise.py, carry-over, cooldown, origin reset, metadata on all flights |
| ~18:43 | mem1: camera ready, floor check OK, "FLYING", then **preflight FAIL** `unknown group 'mrac_theta'`; the operator had RC-armed |
| 18:48 | Service restarted on current code; mem1 ready to fly |

## Friction patterns

| # | Pattern | What it cost | Root cause | After-lab fix |
|---|---|---|---|---|
| P1 | Day not frozen before the lab | The whole first hour | The prep relied on Jules PRs that were not merged by travel time, so it was rebuilt inline at the lab | Prep deadline the evening before. A flight whose code is not merged by then is **cut from the day**, not built at the lab |
| P2 | Stale service | A failed arm on mem1 and a restart | The service keeps the code it started with. `ensure_service` only checks that something answers on 8081 | The service reports its git sha and start time (`/api/version`). `labday` and `fly` compare it with HEAD and restart the service while disarmed (or refuse) on a mismatch |
| P3 | Dry run ≠ the real path | False green: 21/21 OK, then FAIL in flight | `--dry-run` validates in-process with fresh code and skips the service preflight. It does not print the B/s budget either, so a scratch `budget.py` had to be written | The dry run calls the **service** preflight for every flight and prints the log-plan table (slots, Hz, B/s, dropped) |
| P4 | Preflight runs too late | Camera, floor check and the arm all came before the FAIL | The preflight runs per flight, after the camera block | `labday` preflights **all** flights at start, before the camera. The arm prompt never appears while a preflight is failing |
| P5 | Questions answered at the lab | Long replies while the operator waited | No run sheet explained the day up front: whether presets send only parameters, origin reset, WiFi status, fusion mode, logged variables and why | `labday <date> --sheet` writes the run sheet from the day file: per flight what, why, steps, fusion mode, log plan, budget and stop points. The operator reads it the evening before, and questions come in before the lab |
| P6 | Features requested at the lab | Each one was built while the operator waited | Standing needs (metadata on every flight, origin reset, cooldown, per-flight note, carry-over) were found on the day | All of these are now labday defaults (done today). **Rule:** a new request at the lab goes into `docs/lab/post-lab-<date>.md` unless it blocks the next flight |
| P7 | Bench checks that need a powered drone | Stream budget and link check done ad hoc at the lab | Only possible with the drone powered | `labday --bench`: a scripted step of about 5 min with PASS/FAIL (link check, 0-drop measure at the day's worst plan), run first on arrival |
| P8 | Agent context bloat during the lab | Several compactions mid-lab, each followed by slow re-loading | One session did both the building and the lab ops | The lab day runs in a **fresh session** started from the state file. Prep happens in its own session. At the lab, replies are command first and short |
| P9 | Worker plan changed at the lab | Jules jobs dropped ("stop using the Jules workers"), so everything went inline | Worker output was not ready, or not trusted, by the deadline | The same deadline as P1: worker PRs are merged and QA'd the evening before, or their flights are cut |
| P10 | An aborted flight logged as OK | mem1 aborted at L1 (`takeoff refused at preset var_write ... idx 26: no result in 1.0 s`), yet labday printed `mem1 done: OK` and would have moved on to mem2 | `bad_end` (labday.py ~194) checks the fly exit code, the debrief and `status != complete` only; the campaign status is `complete` even when its one flight aborts | `bad_end` also reads metrics.json: any flight outcome `abort*` or airtime 0 s counts as BAD |
| P11 | One lost WiFi reply refuses a takeoff | A wasted arm on mem1 | Preset and `set` VAR_WRITEs had no resend, unlike the MRAC flags (`_send_retry`, 10-15) | **Fixed at the lab** (04a36224, it blocked the next flight): `_var_write_retry` resends a timed-out write, max 3 tries, never a reject. After the lab: audit every other single-shot command on the takeoff path |

## The lab-ready gate (build after the lab)

`.\labready <date>`, run the evening before. It prints READY or a NOT READY list:

1. The day file is committed, the tree is clean on lab-path files, and HEAD is tagged `lab-<date>` (the freeze point).
2. The firmware on the drone matches the built axf. This also covers the stale-axf ELF test, so flash before the lab day.
3. The service is restarted on HEAD, and `/api/version` equals HEAD.
4. The service preflight passes for every flight, including carried ones.
5. The log-plan table is printed for every flight (slots, Hz, B/s of 70042, dropped).
6. Scoped tests pass for every lab-path change since the last lab day.
7. The run sheet is generated (`--sheet`), and the operator has read it and answered its open questions.
8. If the drone is reachable: `--bench` PASS. If not, `--bench` becomes the first step at the lab.

**At the lab:** power on → `.\labday <date>` (gate re-check: sha, preflight all, bench) → fly. The target is the first flight
within 15 min of arrival (PROPOSED, not measured).

## Lab-day rules (for the agent)

- Code freeze on lab-path files during the lab day. The only exception is a fix that blocks the next flight. Any such
  commit means the service is restarted (disarmed) before the next arm. Once P2 is built, labday does this itself.
- New ideas go to the post-lab list, not into the code.
- Replies put the command first, then a few lines. Long explanations belong in the run sheet, written the evening before.
