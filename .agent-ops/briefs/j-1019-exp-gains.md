# Fix 10-19 E1..E4 exp_active 1-flight lag, F1 wait_txn grace, and preset TRAJ debrief

Read AGENTS.md first. Touch ONLY `TASK/StabilizerTask.c`, `API/mrac.c`, `ground_station/service/campaign_fly.py`, and `ground_station/service/tests/test_campaign_fly.py`. Do not touch `USER/`, `OBJ/`, or `logs/`.

## Context & Root Causes (2026-10-19 Lab)
1. **Observation 3 (`1017-E1..E4` refused at `set exp_id: exp <name>: exp_active=X (want Y)`, 1 flight behind):**
   - `campaign_fly` calls `wait_for_arm` (operator RC-arms the drone on the pad, so `FlightFSM_GetState() == FLIGHT_STATE_ARMED` while `flight_phase == FLIGHT_PHASE_IDLE` and `g_wfb_status.prim_state == 0`) before starting `campaign_runner`.
   - When `campaign_runner` sends `set exp_id` (`VAR_WRITE 50`), `g_exp_id` updates immediately, but `Exp_Step` (`TASK/StabilizerTask.c:L1656-L1676`) calls `Keil_OnGround()` (`TASK/StabilizerTask.c:L1371-L1375`), which requires `FlightFSM_GetState() != FLIGHT_STATE_ARMED && flight_phase == FLIGHT_PHASE_IDLE`. Because the drone is already RC-armed on the pad, `Exp_Step` refuses to copy `g_exp_id -> g_exp_active` until the operator disarms after the abort, leaving `g_exp_active` always 1 flight behind (`255 -> 4 -> 5 -> 6`).
2. **Observation 2 (`1017-F1` 12-gain write abort / `wait_txn` grace window):**
   - In `ground_station/service/campaign_fly.py:L901`, `wait_txn` has `deadline = min(deadline, _clock() + ACK_GRACE_S)`, which never extends `deadline` when an ACK arrives near the end of `timeout_s`.
3. **Preset figure-8 `TRAJ no samples` and `debrief` event abort fields:**
   - In `debrief_lines` (`campaign_fly.py:L1284-L1292`), Keil trajectory presets (`preset figure8`) run while `g_wfb_status.prim_state == 2` (`HOVER`), so `debrief_lines` prints `TRAJ   no samples` and lumps figure-8 tracking error into `HOVER`.
   - In `fly_one` (`campaign_fly.py:L1415`), `events.log("debrief", ...)` does not record `aborted` / `abort_reason` / `air_s`, causing `labday.py` to treat aborted flights as `OK`.

## Required Changes
1. **Firmware (`TASK/StabilizerTask.c` & `API/mrac.c`):**
   - In `TASK/StabilizerTask.c` around `L1371-L1375` and `L1656-L1676`: allow `Exp_Step` to latch `g_exp_id -> g_exp_active` whenever `flight_phase == FLIGHT_PHASE_IDLE && g_wfb_status.prim_state == (float)WFB_PRIM_IDLE` (on the pad before takeoff climb starts, whether disarmed or RC-armed in idle). Follow C89/C99 block-top declaration rules.
   - In `API/mrac.c:L608,L627`: fix the 2 `clang-tidy` signed-shift warnings by casting `k` to unsigned (`1ULL << (uint32_t)k`).
2. **Host pre-arm `exp_id` write (`ground_station/service/campaign_fly.py`):**
   - Add a helper `preapply_exp_id(base, sc)` called in `fly_one` BEFORE `wait_for_arm` (while the drone is disarmed on the ground): if `sc` has an `exp` step (or a `set` step with `var == "exp_id"`), write `VAR_WRITE` index `50` (`exp_id`) via `POST /api/vars/write` (`{"index": 50, "value": float(st.args["id"]), "author": "fly:pre_exp"}`) so `g_exp_active` latches on the ground before the operator arms by RC (working even before reflashing firmware!). In `fly_one`'s `finally:` block, if `preapply_exp_id` wrote an `exp_id != 255`, write `50 -> 255.0` after disarm to restore default controller mode.
3. **`wait_txn` grace deadline (`ground_station/service/campaign_fly.py:L883-L905`):**
   - Track `ack_extended = False`; the first time `ack` is seen, set `deadline = max(deadline, _clock() + ACK_GRACE_S)` and `ack_extended = True` so an ACK near the end of `timeout_s` gets the full `ACK_GRACE_S` window for `"applied"` confirmation.
4. **Preset `TRAJ` classification & `debrief` event fields (`ground_station/service/campaign_fly.py:L1273-L1307, L1413-L1418`):**
   - In `debrief_lines`: classify an airborne sample as `"TRAJ"` when `prim(s) == "TRAJ"` OR `s.get("g_wfb_status.traj_state") == 3` OR when `prim(s) == "HOVER"` and the xy setpoint `Ctrler.locxPID.Des` / `Ctrler.locyPID.Des` is moving (`hypot(dx_des, dy_des) / dt > 2.0` cm/s relative to the previous sample). Exclude those preset-trajectory samples from `"HOVER"`.
   - In `fly_one` (`L1415`): add `aborted=bool(any(f.get("abort_reason") for f in st.get("flights") or []))`, `abort_reason=next((f.get("abort_reason") for f in st.get("flights") or [] if f.get("abort_reason")), None)`, and `air_s=round(air, 1)` to `events.log("debrief", ...)`.
5. **Tests (`ground_station/service/tests/test_campaign_fly.py`):**
   - Add unit tests for:
     - `wait_txn` extending the deadline when `ack` arrives near `timeout_s` before `applied`.
     - `preapply_exp_id` writing `index=50` before `wait_for_arm` and resetting to `255` on exit.
     - `debrief_lines` separating moving-setpoint preset samples (`prim_state=2`) into `TRAJ` instead of `HOVER`.
     - `events.log("debrief", ...)` recording `aborted=True` and `abort_reason`.

## Verification
Run and report in the PR:
- `bash tools/check.sh --fast`
- `python -m pytest ground_station/service/tests/test_campaign_fly.py -q`
