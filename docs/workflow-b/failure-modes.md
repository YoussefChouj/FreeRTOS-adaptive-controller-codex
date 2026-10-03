# Workflow B failure modes (WP-23)

Every failure the agent can meet on a live launch: what the operator sees, the one field the agent reads, what
the agent does, and the test that covers it. Rule: one `campaign_preflight` before Go, one `campaign_state` per
flight; never ad-hoc probes. "Tell the fix" = say the row's `fix` to the operator word for word and stop.

Tests are under `ground_station/service/tests/` unless a path is given.

## Before Go: `campaign_preflight` rows

| # | Symptom | Detect (field) | Agent does | Test |
|---|---|---|---|---|
| 1 | `arm_state` "armed" with the drone on the pad (live 2026-10-03). Cause found: `arm_state()` read the sidebar alias `status.arm` (also fed by the legacy Frame A decoder) before `DroneStatus.ARM_Status`; the firmware variable now decides | preflight `arm_state`: value lists every fresh raw source (`DroneStatus.ARM_Status 0 (slot 1, 0.1 s); status.arm 1 (slot …)`), `SOURCES DISAGREE` when they differ | red: tell the fix. If `DroneStatus.ARM_Status` is 1 the drone really is armed: operator disarms by RC | `test_campaign_preflight.py::test_arm_rows`, `::test_arm_state_prefers_the_firmware_variable` |
| 2 | No link / slots silent / bridge missing | preflight `link` (per-slot age, dropped, loss, stream check) | tell the fix (power, WiFi, restart 8081) | `test_campaign_preflight.py::test_link_rows` |
| 3 | Firmware on the drone may not match `OBJ/JX_FLY.axf` (unflashed build in custody, axf rebuilt after 8081 started, streamed `build_id` differs) | preflight `firmware` | tell the fix (flash skill, or restart 8081) | `::test_firmware_rows` |
| 4 | `g_wfb_status` not streaming / stale / not IDLE / a `safety_trip` latched (the abort monitor would trip level 3 at takeoff) | preflight `wfb_status` | tell the fix; refresh once (it primes the core log plan) | `::test_wfb_rows`, `test_campaign_live.py::test_readiness_still_refuses_when_the_fc_never_streams_status` |
| 5 | RC link lost | preflight `rc_link` false (`sbus_lost 1`) | tell the fix | `::test_rc_rows` |
| 6 | RC link unknown: `sbus_lost` is not on the core stream | preflight `rc_link` pass `null` (amber) | covered by checklist item `rc_ready`; not a stop | `::test_rc_rows` |
| 7 | Drone off the pad origin (optical-flow origin set at power-on) | preflight `position` (x, y, z m, distance, tolerance 0.30 m PROPOSED) | tell the fix (pad centre, power-cycle in place) | `::test_position_rows` |
| 8 | Battery not streaming, unknown pack id, pack below the SoC gate | preflight `battery` | tell the fix (ids from packs.yaml / swap pack) | `::test_battery_rows` |
| 9 | A campaign is already flying | preflight `runner` | wait, or Pause / Land | `::test_runner_row` |
| 10 | Launch copy missing, invalid, its log plan does not fit, or its pack differs from the pack on the drone | preflight `log_plan` | rerun `campaign_launch` with the right `--pack` | `::test_log_plan_rows`, `test_campaign_launch.py` |
| 11 | No single read of position, RC, battery and stream freshness | `GET /api/campaign/vitals` (the preflight rows read the same snapshot) | read it instead of probing symbol by symbol | `::test_vitals_reads_everything_together`, `::test_routes_answer_in_one_call` |

## Tooling

| # | Symptom | Detect (field) | Agent does | Test |
|---|---|---|---|---|
| 12 | Panel click silently did nothing: the browser blocked `window.confirm` | gone: campaign, approval queue, command and path panels use a two-click confirm ("Confirm <action>?" for 5 s) and show every failed action in the panel | ask the operator to read the red line in the panel | `campaign_panel_harness.js` checks p, r; `path_panel_harness.js` check 20 |
| 13 | Dashboard runs stale plugin JS after a service change | every static file is sent `Cache-Control: no-cache, must-revalidate` | a plain reload is enough | `test_campaign_preflight.py::test_plugin_js_is_never_cached` |
| 14 | MCP call during an 8081 restart raised a raw URLError | tool reply `{"error": "8081 unreachable … Retry …", "retry": true}` (a GET retries once first) | wait a few seconds, call again | `test_agent_mcp.py::test_http_reports_unreachable_8081_and_restart` |
| 15 | 8081 restarted between two calls: runner and plan state reset | tool reply carries `notice: 8081 restarted since the last call` (header `X-GS-Instance` changed) | read `campaign_state` before anything else | same test |
| 16 | MCP server process itself is stale (a new tool such as `campaign_preflight` missing) | the tool is not in the tool list | ask the operator to use the Campaign panel (same rows, same Go) or restart the session | none (session level) |
| 17 | Two 8081 instances at once (Windows `SO_REUSEADDR` let both bind) | the second start prints `[service] REFUSED: port 8081 already has a listener` and exits 2 before opening the drone link; the HTTP server binds exclusively | stop the old one first (the message says how) | `test_instance_guard.py` |

## Go

| # | Symptom | Detect (field) | Agent does | Test |
|---|---|---|---|---|
| 18 | Go for a pack the runner is not waiting for (before WP-23 the runner waited forever) | 409 `the campaign's first flight uses pack X, not Y` / `the runner waits for pack X, not Y` | relaunch with the right `--pack`, or Go the waiting pack | `test_campaign_api.py::test_wp23_go_for_the_wrong_pack_is_refused`, `::test_wp23_banner_phase_and_total` |
| 18b | Go while the runner flies (before WP-23 it was kept as a grant, so after a failed auto-next check the next flight started without the operator's decision) | 409 `the runner is flying, not waiting for a go` | Go only when `campaign_state` says `waiting_for_go` | `test_campaign_api.py::test_wp23_go_while_flying_is_refused` |
| 19 | Deps not ready: no gateway, `g_wfb_status` missing or stale | 409 `campaign deps not ready: …` | run the preflight, tell the red row's fix | `test_campaign_live.py::test_factory_refuses_without_link` |
| 20 | Agent Go without the operator's quote / checklist not all true | 403 / 409 | quote the operator's go message verbatim; confirm every item | `test_campaign_api.py::test_go_refused_for_agent_source`, `::test_go_refused_for_unticked_checklist` |

## During the run (`campaign_state`: `status`, `reason`, `banner`, `phase`)

| # | Symptom | Detect (field) | Agent does | Test |
|---|---|---|---|---|
| 21 | First flight sat `min_rest_s` (60 s) after Go with the drone RC-armed: the pack rest counted from campaign start. Fixed: a pack that has not flown is rested | `phase` `pack gate <pack> (resting V): REST: …; waiting` while it waits | nothing: it waits up to `flight_timeout_s` | `test_runner.py::test_wp23_fresh_pack_is_rested_and_pack_failures_name_the_pack` |
| 22 | Pack gate refuses (SoC, unknown pack, rest not reached) | `operator_needed`, `reason` `pack <id>: SOC:/REST:/INPUT: …` | tell the operator; swap pack, new launch copy | same test |
| 23 | Battery voltage not streaming (before WP-23 a bare `error`) | `operator_needed`, `reason` `battery: battery voltage is not streaming (real_voltage)` | check the link; relaunch | `test_runner.py::test_wp23_battery_not_streaming_is_a_readable_reason` |
| 24 | Motor cooldown not reached | `operator_needed`, `reason` `cooldown: x s since landing < y s` | relaunch later | `test_runner.py::test_wp23_phases_and_arm_refused_reason` (phase), `test_runner.py` cooldown path |
| 25 | Stream check failed right before takeoff (before WP-23 `reason` was empty) | `arm_refused`, `reason` `stream check failed before takeoff: slot N silent` | check the link; relaunch | `test_campaign_api.py::test_wp23_arm_refused_reason_names_the_stream_check` |
| 26 | No log plan on the stream / stream not named (stale name table after a reflash) | `operator_needed`, `reason` `capture: …` / `capture: stream not named: slot N: …` | restart 8081 after a reflash; relaunch | `test_campaign_outputs.py::test_runner_refuses_to_fly_unlogged`, `::test_live_capture_refuses_to_fly_unnamed_streams` |
| 27 | Takeoff refused at IDLE: the drone is not RC-armed | flight `abort_reason` `takeoff refused at idle: … (operator arms by RC first)` | ask the operator to arm by RC, then say go | `test_campaign_live.py::test_live_takeoff_without_rc_arm_is_refused` |
| 28 | Firmware landed the drone mid-step (fence, trip) | flight `abort_reason` `firmware landing during <phase> (safety_trip N)` | report; the runner already landed | `test_fly_scenario.py::test_firmware_landing_mid_hold_is_reported` |
| 29 | Step timeout | flight `abort_reason` `timeout: <phase>` | report | `test_runner.py::test_j_consecutive_timeout_aborts` |
| 30 | Abort monitor level 1: `tilt`, `position_error`, `oscillation`, `motor_saturation`, `stale_telemetry`, `nonfinite:*` | flight `abort_level` 1, `abort_reason` | report; the runner landed; two in a row end the run | `test_abort_monitor.py` (`test_tilt` … `test_stale`) |
| 31 | Abort level 3: `firmware:<TRIP>`, `battery`, `consecutive_aborts:*` | `operator_needed`, `reason` `abort level 3 or consecutive` | stop; tell the operator; do not relaunch on your own | `test_abort_monitor.py::test_firmware_trip`, `::test_consecutive_aborts` |
| 32 | Landing not confirmed | `operator_needed`, `reason` `landing timeout` | operator lands / kills by RC | `test_runner.py::test_p_landing_timeout_revert_flight` |
| 33 | Auto-next check failed: not idle, `safety_trip`, not RC-armed, KF not streaming or diverged | `waiting_for_go`, chat `PAUSED before flight N/M: <reason>` | ask: continue (new go quote) or land / abort | `test_runner.py::test_fly_mode_failed_check_pauses_for_go`, `test_campaign_live.py::test_live_health_fails_closed` |
| 34 | Operator pause / land / abort | `operator_stop` (`operator pause` / `operator land`) or `operator_needed` (`operator abort`) | report | `test_campaign_api.py::test_abort_ends_in_operator_needed`, `::test_land_ends_in_operator_stop` |
| 35 | Runner crashed | `error`, `reason` `<Type>: <message>` | report the reason; outputs are still written | `test_campaign_api.py::test_runner_error_shows_error_status` |
| 36 | Outputs failed | `outputs_dir` null, chat `campaign outputs failed: …` | say "outputs failed"; raw recordings are in `logs/sessions/` | `test_campaign_outputs.py` |
