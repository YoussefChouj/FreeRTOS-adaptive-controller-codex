# Workflow B: Autonomous Flight Campaign Runbook

**IMPORTANT:** Live (non-sim) campaigns are not wired yet; Go on a non-sim service returns 503 HTTP status.

## Operator Steps

Follow these steps in order to execute an autonomous tuning campaign:

1. **Install the phone app** (`adb install`).
2. **Bench-check yaw sign** to ensure correct orientation.
3. **Clamp the phone and start recording** (video log).
4. **Mark the end wall and pad centre** for visual reference.
5. **Label packs**: Ensure battery packs are labelled matching their IDs in `packs.yaml`.
6. **Review crash/abort thresholds**:
   Check the PROPOSED limits in `ground_station/service/abort_monitor.py` which are not flight-validated yet:
   - Position error: `0.25` m for `0.3` s
   - Tilt limit: `35.0` deg for `0.2` s
   - Oscillation RMS limit: `60.0` dps over `1.0` s window
   - Saturation fraction limit: `0.5` over `1.0` s window
   - Stale telemetry timeout: `0.5` s
   - Minimum battery (soc_min_pct): `30.0`
   - Max consecutive aborts (max_consecutive_aborts): `2`
7. **Write a campaign** using the flight-campaign skill.
8. **Open the Campaign panel** in the dashboard.
9. **Set `allow_agent_arm`** to enable the agent to arm the drone.
10. **Per-battery Go**: For each battery pack, complete the checklist and approve Go.
11. **Pause / Land / Abort**: Use these controls during the campaign if a manual override is needed.
12. **RC ch10 kill**: Use the RC hardware switch as the ultimate fallback kill.

## End Statuses and What to Do Next

The campaign runner can finish in several states. The operator reads these from `GET /api/campaign/state` in the `status` and `reason` fields. Review the state to decide your next action:

- **complete**: The campaign finished all its planned flights successfully. 
  - *Next Action*: Review tuning results and wrap up.
- **arm_refused**: The agent tried to arm but `allow_agent_arm` was false.
  - *Next Action*: Enable `allow_agent_arm` and retry, or investigate why it was refused.
- **operator_stop**: The operator manually clicked Pause or Land, or denied the `wait_for_go` prompt.
  - *Next Action*: Start a new Go after checking the drone, or start a new campaign.
- **operator_needed**: The runner encountered a condition requiring human intervention. Reason strings include:
  - `"operator abort"`
  - `"cooldown_min_s not met"`
  - `"battery not resting"`
  - `"param write refused"`
  - `"landing timeout; revert flash pending"`
  - `"landing timeout"`
  - `"abort level 3 or consecutive"`
  - *Next Action*: Diagnose the issue physically (check battery, check drone state, rescue drone) before continuing.
- **error**: An unexpected Python exception crashed the runner thread.
  - *Next Action*: Check dashboard logs, fix the error, and restart.
- **gate_refused**: A requested code change was rejected by CodeGate (e.g. touching protected files).
  - *Next Action*: Review the rejected diff and fix the code change to comply with safety rules.
