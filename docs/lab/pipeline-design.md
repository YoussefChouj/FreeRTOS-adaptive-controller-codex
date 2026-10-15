# Lab pipeline design (agreed 2026-10-14, grilling session)

Goal: test controller hypotheses on the drone fast. The lab day is for flying and reading results only;
building, debugging and simulation happen at night.

## The loop

idea -> flight-ready (night) -> flown (lab) -> verdict (30 s after landing, day report)

| Stage | What exists after this design |
|---|---|
| Idea -> flight-ready | catalog entry, sim run overnight (light firmware-in-loop sim), image built + rehearsed, `lab-ready-<date>` tag |
| Lab | `lab up` (bring-up + 5 min check), one console all day, fixed queue of cards, pack bookkeeping |
| Verdict | per-landing card debrief (budget 30 s, measured), day report page, videos to Drive |

## Decisions (user, 2026-10-14)

1. Build a thin slice of all three stages, then deepen what hurts.
2. Experiment = **card** file: hypothesis, one `.fly` flight path, `variant <name> set ...` lines,
   `order interleave repeats N`, `metric <name> pass <rule>`. A `.fly` is a flight path only.
3. Variants switch **on the ground** only (the firmware already refuses a controller switch while armed).
4. **Screen** (1 flight per variant, ranking only) then **confirm** (3+ each, interleaved; all 3 beat all 3
   of the baseline by the margin = verdict, about 1 in 20 by luck).
5. Overnight: an agent simulates tomorrow's candidates; the morning report ranks them and proposes the day queue.
   Simulator = light firmware-in-loop (Python dynamics + the host-compiled C controller code, fitted to logs).
   Research first (adopt RotorPy / gym-pybullet-drones or build). Also research: Gazebo-in-WSL value,
   Google AI Pro/Ultra cloud credits, the "drone bench" agent-controls-hardware benchmark.
6. Batteries: 1x P5300 + 4x P4000 (4000 mAh, operator confirmed 2026-10-15); charger 2 at a time, ~1 h each; ~130 s flights, 2 per pack; rest 2x the
   flight time before the next takeoff. Console: resting voltage 20 s after landing; swap when below the next
   flight's minimum or 15.0 V, or after 2 flights; holds the next flight until the rest ends; tracks pack
   states (charged / charging with 1 h timer / flown). A pack swap reboots the drone: nothing may break.
   2026-10-15 (operator): two pack types only, type 1 and type 2 (`--pack 1|2`). Flights are counted on the
   pack in the drone, a swap asks its type, and the charger is the operator's (no charger states or timer).
7. Lab-ready: night rehearsal of the full session against a fake drone, then tag `lab-ready-<date>`; the lab
   runs that tag. A lab bug gets a workaround or the card is skipped, goes on a list, is fixed that night.
8. Debrief within 30 s of landing (measured): flight metric, per-variant table, confirm status, warning flags,
   next flight / pack / rest. Day report page in plain words (for learning), archived with the logs.
9. Queue order is **fixed** (a low pack means a swap, never a reorder).
10. Videos (.mkv/.mp4) move to `G:\My Drive\UAV_lab\<date>\` at day end (ask before the first upload).
    CSV logs and reports stay on D:. Drive cache is `D:\GoogleDriveCache` (C: is safe).
11. Firmware variants: modules with a declared RAM budget; modules that never run together share one RAM slot;
    an image manifest lists modules per image when they do not fit together; the drone reports its image id;
    cards name their image; the console flashes a prebuilt, night-tested image only disarmed and with an OK.
    STM32F407ZG: flash 157 KB / 1 MB used, RAM (RW+ZI) 148 KB / 192 KB (128 KB + 64 KB CCM).
12. **Catalog** `docs/lab/catalog.yaml`: every module, image and card with status
    (idea -> sim-passed -> flown -> confirmed) and links to flights in docs/flights/ledger.csv. The console and
    the night agent read and update it; the console refuses anything not in it. `lab catalog` prints it.
13. Night: Claude Code on the laptop orchestrates and does the main work in one session; VPS workers only do
    research, simulation batches and monitoring, and their results are checked before use.
14. The pipeline logs itself whenever it runs (events, timings, errors, operator keys) for tuning and debugging.
15. **Early end** (a flight stops before its flight path ends): at landing the operator presses one key.
    `c` = the controller misbehaved: counts as a fail, not re-flown. `x` = an outside cause (link, camera,
    battery, operator input): not counted, re-flown next with the same variant; a second outside fail on the
    card skips the card to the fix list. Fixed before flying, so re-flying cannot hide a bad controller.
16. First pipeline day (2026-10-15): the unflown 2026-10-14 plan as 3 cards (fusion hover A1-A2, pad landing
    B1-B2, xy controller screen C1-C5), then a confirm of the xy winner vs PID (3 each, interleaved): about 15
    flights. Day capacity from the pack facts: about 25 flights in 5-6 h (estimate, not measured).

## Defaults (proposed 2026-10-14, user did not object)

| Topic | Default |
|---|---|
| Commands | `lab up` checks everything, then runs the day's queue; `lab end`, `lab catalog`; `fly` stays for one-off flight paths |
| Variants | may set ground settings (camera fusion mode) as well as firmware values |
| Screen | the baseline flies first and last (catches drift, like C1/C5) |
| Confirm pick | the console picks the screen winner by the card's metric; one key vetoes it (next best) |
| Live view | VOFA+ gets a standard flight tab plus the card's metric signals |
| Video | cut into one clip per flight; the clips go to Drive |
| Claude at the lab | quiet unless asked; the console does not need Claude |

## Glossary (use these words; older words in brackets are retired from new screens)

| Word | Meaning |
|---|---|
| card | an experiment file `docs/lab/cards/<name>.card`: hypothesis, flight path, variants, order, metric + pass rule |
| flight path | a `.fly` file (`ground_station/service/campaigns/`): takeoff, moves, landing, log plan; no variants [campaign] |
| variant | one named set of settings a card compares (firmware values or ground settings) |
| baseline | the variant the others are compared to (usually PID) |
| screen | one flight per variant, baseline first and last; ranking only, no verdict |
| confirm | 3+ flights each of the screen winner and the baseline, interleaved; verdict = every winner flight beats every baseline flight by the card's margin |
| queue | the day's fixed list of flights built from the cards [plan, workflow A/B/C] |
| early end | a flight that stops before its flight path ends; tagged `c` (controller) or `x` (outside) |
| pack | one battery, id from `packs.yaml` |
| image | one firmware build (flash file) with a set of modules |
| module | one firmware feature with a RAM budget (a controller, an estimator) |
| catalog | `docs/lab/catalog.yaml`: modules, images, cards and their status |
| fake drone | a program that answers like the drone over WiFi, for night rehearsals |
| rehearsal | the whole lab session run against the fake drone; passing it earns the tag `lab-ready-<date>` |
| path preset | a built-in path shape in a `.fly` (circle, figure8); stream preset = a named variable set to stream (vofa_studio/presets) |
