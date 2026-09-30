# Workflow B - ground-station facts digest

Worktree `.worktrees/wfb`, branch `workflow-b`, HEAD `2e20b6f`. All paths relative to worktree root.
Read-only survey; nothing run against hardware or port 8081. "MEASURED" is used only where a file itself says so.
Citations are `file:line`. UNCONFIRMED = not verified by reading code this session.

## 1. Telemetry

**Frame types** (`docs/telemetry-protocol.md`): subscribe request `0xCC 0xDE 0x21 LEN_HI LEN_LO NRANGES [divider][transport][slot] N*(addr u32, size u16, count u16) CRC8_XOR` (:139-150).
Replies: `0x08` schema, `0x09+slot` data (so 0x09..0x0C = slots 0..3), `0x7F` error e.g. over link budget (:165).
- Max 4 slots, each own rate (:162-164). One request per slot, replaces that slot's ranges (:150). Max 62 ranges/slot, request 506 B vs 512 B RX staging (:151-152).
- Max payload per data frame 2032 B = 508 float32 (1024 B before the 2026-09-29 firmware); hosts read limits from ELF `s_stream_staging.ranges`, `stream_buf` (:156-158).
- `divider = round(send_hz/desired_hz)` (:159). Per-frame overhead 12 B = 6 header + 4 source timestamp + 2 CRC16 (`ground_station/livewatch/log_frames.md:82`).
- UART5 subscribe ingress is compiled out (`SUBSCRIBE_UART5_ENABLED = 0`); requests must go over WiFi/USART3 (:141-142).
- Firmware `SUBSCRIBE_SEND_TASK_HZ 200U` (`API/subscribe.h:271`). But presets yaml header says actual cadence ~80 Hz in MIXED mode (`ground_station/livewatch/multi_slot_presets.yaml:14-24`); protocol doc says ~80 Hz before / ~100 Hz after Phase 0 flash (`docs/telemetry-protocol.md:62-64`). The earlier 258.7 Hz figure is retracted as a host polling artefact (:62-64). Effective divider-rate: `measured_max = 80/int(200/hz)` (presets yaml :17-19).
- Telemetry modes via cmd 0x0F idx 100/101/102 = legacy / mixed (boot default) / subscribe_only (`ground_station/comm/wifi_bridge.py:1950-1955`); subscribe_only expected ~200 Hz (presets yaml :23) - UNCONFIRMED that it has been measured.

**Link throughput** (documented):
| Figure | Value | Status | Cite |
|---|---|---|---|
| USART3 actual baud | 913043 (BRR 0x2E), 91304 B/s wire | arithmetic | telemetry-protocol.md:251-256 |
| UDP payload | ~90363 B/s, 98.8 % of wire, 0.00 % loss | MEASURED (alphabet ladder `scratchpad/micoair_ladder.py`) | :297-303 |
| Uplink command ceiling | ~1050 Hz (9 B frames) | MEASURED 2026-08-19 | :281-283 |
| JustFloat downlink | 100.2 Hz, 14716 B/s (16.1 %) | MEASURED 2026-08-19 | :275-277 |
| Firmware subscribe budget | USART3 95 % of 92160 = 87552 B/s; UART5 20 % of 11520 = 2304 B/s | design constants | log_frames.md:72-73 |
| UART5 real ceiling | ~1600 B/s (2055 B/s -> 14 % dropped; 1580 B/s -> 0 dropped) | MEASURED | log_frames.md:93-103 |
| "USART3 ~6700 B/s measured" | comment only | UNCONFIRMED | log_frames.md:41 |
The doc mixes 91304 / 91500 / 92160 B/s for USART3; treat as approximate.
Frame cost formula: `(12 + payload_bytes) * 100 / divider` B/s (log_frames.md:36-40, 100 = nominal Send_Task Hz in that doc).

**Presets** (`ground_station/livewatch/multi_slot_presets.yaml`, 125 lines): `presets:` :29 -> 5 names: `flight_comprehensive` :30, `mrac_characterization` :55, `thrust_validation` :72, `flight_cascade_drift` :90, `flight_test_adaptive` :111.
Format per preset: `description`, optional `measured_max_hz: {slot: hz}`, `slots: [{slot: N, manifest: <name from manifests.yaml>, hz: N}]` (:30-54). Contract REQUIRED_SYNC_VARS: manifest union must cover `DroneStatus.ARM_Status, DroneStatus.FlyMode, real_voltage, xTickCount`; `MultiSlotPresetManager.get()` raises ValueError otherwise (:6-13).
Run: `python -m ground_station.livewatch.capture_preset <name> --secs N` (writes raw hex CSVs, see below).

**Manifests** (`ground_station/livewatch/manifests.yaml`): entries `name: {doc, hz, vars:[dotted symbol...]}`. `mrac_weights` :127 (default 50 Hz), `mrac_signals` :160, `mrac_signals_minimal` :818, plus `dashboard_frame_a`, `inner_loops`, `ekf_all`, `sync`. Hand-edited YAML, no generator.

**CSV formats**:
- `stream_log`: header `["t_src_ms","t_host_s","seq"] + columns_for(schema)` (`stream_log.py:422`, also :188, :267, :544); columns are symbol paths, arrays expand `name[i]` (:119-132). One file per slot via `_slot_path` (:595, :487). `run_groups` :482 (per-group rates); flag `--group "RATE:SYM[:N][,...]"` (:604); `--vofa HOST:PORT` (:628). No manifest/sidecar JSON is written by stream_log (no `json` write in file; grep found none) - UNCONFIRMED beyond grep.
- `capture_preset`: `slot{n}_{manifest}_{hz}hz_{timestamp}.csv`, columns `sample_idx,tick,data_hex` (raw payload hex; `tick` is a 0 placeholder) (`capture_preset.py:415-420,448-453`). Decoding needs the manifest layout. "Byte-identical to dashboard raw CSVs" per its docstring.
- Dashboard recording sessions write `manifest.json` (metadata, stopped/started times, subscribe layout) (`docs/dashboard-platform/AGENT_GUIDE.md:157,172`).

**MRAC shadow / adaptive weights** (streamable by DWARF name):
- Struct `MRAC_AxisState_t` (`API/mrac.h:223-260`): `xm, xm_dot, x, r, e, Phi[], Theta[], Whatf[], u_nom, u_ad, u_def, x_prev, xdot_f, e_dot`. Global `mrac_state.{pitch,roll,yaw,z_rate}` (`MRAC_State_t` :260-264). Example names: `mrac_state.roll.Theta[0]` (manifests.yaml :127-135), `mrac_state.roll.e`, `.e_dot`, `.u_nom` (:160-170). `MAX_NUM_BASIS = MRAC_CAPACITY` (mrac.h:47), <= 16 (:50).
- Flags struct `MRAC_FeatureFlags_t` as `mrac_flags.*` (mrac.h:266-282), incl. `adaptation_on`, `axis_enable_{pitch,roll,yaw}`, **`output_injection_on`** = runtime shadow-mode gate (0 = MRAC learns, motors see pure PID; 1 = u_ad injected) (:277). Compile-time twin `ENABLE_MRAC_OUTPUT_INJECTION` (:59).
- Set at runtime via cmd 0x0F MULTIPLEX_FLAGS, idx 10 = `output_injection_on` (`ground_station/platform/firmware_contract.py:355-369`). Flag vars in presets: manifests.yaml :703-704, :789-791.
- MAVLink-style frames MRAC_WEIGHTS 10001 (142 B), EKF_STATES 10002, CTRL_DEBUG 10003 exist per doc but "not flashed" as of doc date (telemetry-protocol.md:257-267, :299+) - STALE/UNCONFIRMED.

## 2. Command sender

Wire: legacy `0xCC 0xDD CMD IDX float32-LE CRC8(xor of bytes[2:])`; newer transaction envelope sync `0xCC 0xDF`, results `0xAA 0xBB` (`ground_station/platform/transactions.py:16-17`, legacy still supported :3).
| Module | Function | Transport |
|---|---|---|
| `ground_station/comm/wifi_bridge.py` (`class WifiBridge` :173) | `send_command(cmd_id:int, index:int, value:float)->None` :464 (queue, thread-safe; drained by `_flush_queue` :1494, framed by `_send_cmd_frame` :1925) | UDP 14550 to `_wifi_host` (AP 192.168.4.1) |
| same | `send_transaction(cmd_id, index, value, flags=0)->int txid` :468; `poll_transaction_result(timeout=0.0)->Optional[TransactionResult]` :476; `_send_transaction_frame` :1938 | UDP |
| same | `set_telemetry_mode_now(mode)` :1955 ("legacy"/"mixed"/"subscribe_only"); `_send_subscribe_bytes` :1048; `_send_keepalive_nudge` :1507 | UDP |
| `ground_station/comm/serial_bridge.py` (`class SerialBridge` :303) | `_pack_command_frame(cmd:dict)` :1425 (accepts cmd_id/id, index/idx, value/val); no public `send_command` def (queue-based); command comment table :28-60 | COM serial (UART5) |
| `ground_station/platform/transactions.py` | `build_command`:68, `parse_command`:81, `build_result`:96, `parse_result`:106, `TransactionLedger`:153; enums `Outcome`:22, `RejectReason`:28 | codec |
| `ground_station/service/gateway.py` | `CommandGateway(bridge, event_sink)`; `submit(command_id, index=0, value=0.0, flags=0)->txid` :13; `poll(timeout=0.0)->Result|None` :21 | via bridge |
| `ground_station/service/core.py` | `submit_command(command_id, index=0, value=0.0, flags=0)->int` :842, `poll_command(timeout)` :903 (gateway created :290) | HTTP path `POST /commands` (api.py:2088) |
- Command/param table: `ground_station/platform/firmware_contract.py:175-501` (`CommandSpec(id,name,description,params=(CommandParam(idx,name,type,lo,hi),...))`), ids 0x01..0x1F. Examples: 0x01 PID_GAIN (axis 0-6, gain_type 0-2, value 0-200) :175; 0x04 FLIGHT_MODE_ABORT :205; 0x06 VIRTUAL_STICK :229; 0x07 BENCH_MODE :242; 0x0A/0B/0C/0x11 path cmds; 0x0D ABORT_ALL_PATHS :330; 0x0E SDK_ARM_AUTHORITY :340; 0x0F MULTIPLEX_FLAGS :355; 0x16 MOTOR_BENCH :458; 0x1E OF_BIAS_MODE :492.
- **No dedicated Python arm/idle helpers.** Arm = cmd 0x0E idx 0 (val>=0.5 arm authority, <0.5 release); motor idle = 0x0E idx 1 (val>=0.5 enable idle PWM; requires ARMED + GROUND_IDLE) (firmware_contract.py:340-346; serial_bridge.py:41). Only readbacks exist: `SerialBridge.get_last_arm_status()` serial_bridge.py:405; `core.arm_state()` -> "armed"/"disarmed"/"unknown" fail-closed, staleness 2 s `ARM_STALE_NS` (core.py:812-838), `is_disarmed()` :838.
- Arm gate in `submit_command`: 0x16 and 0x1E idx=0 require disarmed (core.py:846-850).

## 3. Dashboard service (`ground_station/service`, port 8081; never POST to live service)

**Routes** (`api.py`, 2665 lines, stdlib `ThreadingHTTPServer`): hand if/elif chains, plus doc map.
```
# api.py:431  _ROUTE_MAP = {...}  (must stay in sync; test_http_api_routes_endpoint checks; docs at :450-525)
# api.py:1402 (in do_GET :1225)   elif route == "/api/paths": import ground_station.service.path_library as pl; self._json(200, {"paths": pl.list_paths()})
# api.py do_POST :2062            elif route == "/api/foo": body=...; self._json(200, ...)   (POST examples /commands :2088, /subscribe :2109, /experiments :2285)
```
Other anchors: `/api/routes` :1674, `/health` :1229, `/slots` :1440, `/state` :1461, `/sessions` :1476, `/api/manifest` (:1659 reads `docs/dashboard-platform/capability_manifest.json`). Static fallback maps `/plugins/foo.js` under `docs/dashboard-platform/shell` (:2035-2060, root ~:2589).
Adding a route or panel changes the capability manifest -> drift test fails until regenerated (section 8).

**Panels** (JS plugins, `docs/dashboard-platform/shell/plugins/*.js`, 30 files; contract `docs/dashboard-platform/shell/plugin-api.md`):
```
// shell/plugins/my-panel.js:   window.__PLUGIN_INIT__ = function (api) { /* build DOM in api-provided container, api.subscribe(...), api.submitCommand(cmd,idx,val) */ };
//                              window.__PLUGIN_DESTROY__ = function () { /* cleanup */ };
//                              window.__registerPlugin__('My Panel', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__, {workspace:'paths', gates:['connected']});
// shell/index.html:676-707    add 'plugins/my-panel.js' to PLUGIN_FILES (shell fetch+evals each; failure only console.warn). Register fn at index.html:657-674.
```
Real examples: streams-panel.js:653,668,685 (no meta); path-panel.js:3910 ('Path Planning'). `meta.workspace` in overview|control|estimator|mrac|telemetry|experiments|paths|bench|replay|diagnostics; `gates` in connected|disarmed|fresh|schema|command (plugin-api.md). `.agent-ops/served/path-panel.js` is a tracked copy of unknown role - UNCONFIRMED.

**MCP tools** (`.mcp.json` server `dashboard` -> `python -m ground_station.service.agent_mcp`, stdio JSON-RPC, stdlib only):
```
# agent_mcp.py:30  TOOLS: list[dict] = [{"name":"get_state","description":..., "inputSchema":{...}}, ...]   (13 tools :32-133)
# agent_mcp.py:240 def _call_tool(name,args): if-chain, e.g. `if name == "get_state": status,payload = _http("GET","/api/agent/state")`
# agent_mcp.py:196 handle(); tools/call at :224. Docstring :18: no tool changes control mode/allow_agent_arm, approves anything, or sends raw command outside a plan.
```
Tools: get_state, list_actions, run_plan, get_plan, cancel_plan, say, wait_for_operator, get_recording, list_sessions, analyze_session, explain_symbol, ui_navigate, ui_highlight, file_finding. Typo "safe|crtial" at :42.
**Plan step actions** (registry, `agent.py`): `UI_ACTION_SPECS` :54 (switch_tab, highlight, ..., ui_navigate), `SERVICE_ACTION_SPECS` :120 (subscribe, subscribe_preview, recording_start/stop, preset_apply, say, wait_ms, wait_for, `command` :195 args `{command_id,index,value,flags}`). Spec = `{risk, where, description, args(JSON schema)}`. New action = add spec entry there; new MCP tool = TOOLS entry + `_call_tool` branch (+ service route if needed).

**Plans / autonomy**: `POST /api/agent/plans {title, goal?, source, steps}` (api.py:520, 2378; `?queue:true` enqueues, 201/409/400); `GET /api/agent/plans` :1982, `/plans/<id>` :1987; cancel :2405; approvals `POST /api/agent/approvals/<plan>/<step>/approve|reject` :2430, `GET /approvals` :1997; `GET|POST /api/agent/control` :1972/:2353 `{mode?,allow_agent_arm?,tier0_access?,source}` -> 423 while off, 403 if an `agent:` source sets allow_agent_arm/tier0_access. Modes `MODES=("off","supervised","autonomous")` agent.py:284; `TIER0_ACCESS=("partial","full")` :288. Defaults (memory only, reset on restart): `mode="supervised"` :618, `allow_agent_arm=False` :619, `tier0_access="partial"` :620.
**Tier-0 gating** (`agent.py`): `CRITICAL_ARM_MOTOR_THROTTLE={0x06,0x07,0x0E,0x16}` :232; `CRITICAL_PARAM_WRITE={0x01,0x02,0x03,0x05,0x08,0x09,0x12,0x13,0x15,0x19,0x1E}` :233; `PARAM_WRITE_TIER` ~:245 (every entry tier 0; unknown command = tier 0); `FLAG_TIER1_TO_TIER0` :258 (`command_flags` ~:272: 0x1E idx0 val>=2.0 = EKF-OF bias into position loop); `classify_command` :405; never critical: 0x0D, 0x04. `_step_needs_approval` ~:897-910: not autonomous -> any critical step needs approval; autonomous + arm/motor/throttle -> approval unless `allow_agent_arm`; autonomous + param write -> released only if `tier0_access=="full"`, else tier1_to_tier0 flag or tier 0 needs approval. **`allow_agent_arm`** lives at `agent.py:619` (set via `set_allow_agent_arm` :800; operator-only :715-746), also `agent_permissions.py:10,128,192-193`, api.py route docs :475,:519. Path commands 0x0A/0B/0C/0x11 are not in the critical sets above (UNCONFIRMED whether classified elsewhere via `PARAM_WRITE_TIER`).

## 4. Path code

- `git branch -a --contains 202e9da` -> main, review-fixes, **win/paths3d**, workflow-b (current), worktree-agent-a0ed3122bf5e2a28f, worktree-agent-ada7aecefc04ec1b7. `202e9da` = "Implement desired telemetry trace, 3D ribbon lines, tracking metrics, and scrubber". `merge-base --is-ancestor 202e9da HEAD` = yes; `git log HEAD..win/paths3d` empty -> **win/paths3d is fully merged into HEAD**. Caveat: main is ahead (3ccf8c0, incl. 9270e2c replay-desired fix) of this worktree HEAD 2e20b6f.
- **Python** `ground_station/service/path_library.py` (116 lines): schema comment :9-16 `{id(uuid), name, created_at, points:[{x,y,z}], type:"custom", spacing:5.0}`; `PATHS_DIR=Path("logs/paths")` :17; `list_paths` :22, `get_path` :33, `save_path` :44, `delete_path` :58, `resample_path(points, spacing)` :66, `smooth_path(points, iterations=1, alpha=0.5)` :103 (Laplacian). Only `list_paths`/`get_path` are called (GET `/api/paths` api.py:1402, `/api/paths/<id>` :1406). **No callers of save/delete/resample/smooth anywhere in ground_station/ or sim/**; no POST/PUT/DELETE for paths although `_ROUTE_MAP` text says "list or create" (:458) / "get, update or delete" (:459).
- **Panel** `docs/dashboard-platform/shell/plugins/path-panel.js` (~3910 lines): library is browser localStorage `LIB_KEY='pp_paths_v1'` :2020 (max 50, `PATH_MAX_POINTS=5000`), deliberately network-free (:2017-2019, harness CHECK 6). `serializePath` :2029 -> `{name, created_at, type:'custom', room:{w,d,h}, points:[{x,y,z}]}`; `parsePathFile` :2041 accepts array or `{points}`, z default 0. JS `resamplePath(pts, step)` :1722, `smoothPolyline` :1032 (independent of the Python ones).
- **Execute flow**: `EXEC_KINDS=['hover','line','circle','figure8']`, `STOP_STEPS=[[0x0B,7,0],[0x0C,6,0],[0x11,7,0]]` (~:1824). Firmware map (SDK mode only; x/y cm, z m; comment :1818-1822): hover 0x0A idx0-3 x,y,z,yaw + idx4 execute; line 0x0B idx0-2 centre,3 amp,4 freq Hz,5 dur,6 axis,7 active; circle 0x0C idx0-2 centre,3 radius,4 omega,5 dur,6 active; figure8 0x11 idx0-2 centre,3 amp,4 omega,5 dur,6 type,7 active. `executePlan` :1827 -> `{steps:[[cmd,idx,val]...]}` or `{error}` (rejects non-horizontal/tilted plane, hover without yaw); `runSteps`/`sendNextStep` ~:1880 send one step at a time via `_api.submitCommand`, waiting for applied result; `executePath` :1956 requires `_sdk===1` (flymode telemetry, :2330; "Take SDK authority first (cmd 0x0E)"), then `window.confirm`; `stopPath` :1973. **Arbitrary waypoint paths are NOT flyable** - only the 4 presets (error text :1830). Server has no path-upload command.
- Known open: replay desired z is not /100 while x/y and actual z are (units unverified) (`.claude_state.md`, main repo).

## 5. `sim/bench/bench.py` (178 lines)

- Import: `import sys; sys.path.insert(0,'sim/bench'); import bench` (worked from repo root; printed `0.05 bench_v1 {'tune': 96, 'test': 165}`). Needs numpy + siblings `plant.py`, `scen.py`. `main()` guarded by `__main__`.
- CLI: `python bench.py eval <mod:Class> [--params P.json] [--split test] --tag T`; `tune <mod:Class> --tag T` -> `sim/bench/results/<tag>_<split>.json`, `<tag>_tune.json` (47 files present).
- Constants: `BENCH_VERSION='bench_v1'` :15; `CHUNK={'tune':96,'test':165}` :17; `SEED0` :18; `SAT_HI,SAT_LO,SAT_BUDGET = 3995, 2005, 0.05` :19; `POP,GENS,SIGMA0,TUNE_SEED = 8,8,0.2,0` :20 (64 evals); `N_BOOT,FAM_MARGIN = 2000, 1.10` :21.
- `metrics(L, ref, rowlist)` :35 -> per-row dict `traj, fam, seed, diverged, rmse(inf if diverged), rmse_xy, rmse_z, rmse_yaw, max_err, sat (fraction motor samples >=3995 or <=2005), effort, tilt_max, xtrack(zigzag only)`; scores only t >= `scen.T_HOLD`=3.0 s (scen.py:9; T_END 20.0, Z0 1.0).
- **J** `objective(rows)` :76: `J = median(rmse) + 0.25*mean(min(rmse,2.0)) + 5.0*max(0, mean(sat) - SAT_BUDGET)`.
- `PARAMS = {name: (default, lo, hi, 'log'|'lin')}`, <=14 knobs (example `fwpid.py:30` `'pos_kp': (1.0, 0.25, 4.0, 'log')`); `to_x` :82 / `from_x` :90 normalise. Controllers subclass `plant.Controller`; `step(obs)->dict(U=(B,3), thr=(B,))`; `DT_C=0.005` (200 Hz).
- Frozen (do not edit): plant.py, scen.py, fwpid.py, bench.py, bench_v1.json, calib_*, everything in sim/adaptive_compare/, `sim/bench/CONTROLLER_API.md`. New controllers: `sim/bench/ctrl_<name>.py`. Acceptance (`report.py:6`): div rate <= ref, sat <= 0.05, every family median <= 1.1 x ref.

## 6. flightlab (`ground_station/analysis/flightlab/`, spec `docs/analysis/flightlab-spec.md`, 234 lines)

- Invoke: `python -m ground_station.analysis.flightlab {analyze <stem|meta.json> [--out DIR] [--pdf] [--no-html] [--no-ledger] | compare A B | ledger --rebuild}` (`__main__.py`); exit 0 ok, 2 load failure, 1 internal error. API `pipeline.analyze(src, out_dir=None, pdf=False, html=True, ledger=True, cfg=None)` `pipeline.py:230`.
- Outputs: default dir `REPORTS_DIR = logs/vofa/reports/<flight>`; `metrics.json` (:254, validated vs `schema/metrics.schema.json`), `recommendations.json` (:261), md/html reports; ledger `docs/flights/ledger.csv` (`LEDGER_PATH`); `SCHEMA_VERSION=1`; plugins `PLUGIN_KEYS` = data_quality, loops, motors, battery, position, spectrum, mrac.
- `recommendations.json` = `{schema_version, flight, analyzed_at, recommendations:[...], rules_skipped, rules_failed}`.
- **Recommendation** (`registry.py:53`, dataclass, `.to_dict()`): `id, severity, category, target, action, factor, evidence(dict), rationale, confidence`. Enums: severity critical/warn/info; category data/pid/mrac/hardware/battery/logging; action increase/decrease/investigate/add_to_preset/enable/disable; confidence low/medium/high. `@register_rule(requires=[dotted metric paths])` for `fn(metrics, cfg, ctx)->list[Recommendation]`, `ctx={"ledger_rows","log"}`; `@register_plugin` (requires/run/figures); `sort_recommendations` critical>warn>info then id. Thresholds `config/rules.yaml` (DQ-DROP, PID-OSC factor 0.85, PID-LAG 1.15, MOT-CLAMP, BAT-LOW, MRAC-READY/AUTH/CHATTER/DRIFT/WORSE :41-45).
- **MISSING in this worktree**: `compare.py`, `ledger.py`; `rules/__init__.py` and `report/__init__.py` are empty; no `render_md.py`/`render_html.py`. `pipeline.analyze` imports `.ledger` (:247) and `.report.render_md` (:263), so `analyze`, `compare`, `ledger` cannot run here until the WP4a/WP4b modules exist (consistent with commits 2f35e29/3ccf8c0 which only add briefs).
- Existing tests: `flightlab/tests/` test_battery, test_contract_edges, test_data_quality, test_metrics, test_model, test_motors, test_mrac, test_pid_loops, test_pipeline, test_position, test_registry, test_segments, test_spectrum, test_vofa.

## 7. artifact_custody and flashtool

- `ground_station/flashtool/artifact_custody.py` (155 lines): `CACHE_DIRNAME=".flashtool-cache"` :33 (under `OBJ/`); `FLASHED_TRIPLE=("JX_FLY.axf","JX_FLY.hex","JX_FLY.map")` :38; `IDENTITY_METADATA=".build_identity.json"` :43; frozen `CustodyState(snapped, triple_present, cache_path, reasons)` :47; `cache_dir(obj_dir)` :56; `snapshot(obj_dir)` :61 (copy triple + identity sidecar to cache; idempotent overwrite); `commit(obj_dir)` :90 (delete cache = target now matches disk); `restore(obj_dir)` :103 (byte-exact copy back, then delete cache; no-op with reason if no cache); `has_snapshot(obj_dir)` :152. Records only file copies - no log/ledger. Tests `flashtool/tests/test_artifact_custody.py`.
- Used by `safe_flash.py`: import :48; `build()` snapshots first (:320-329); restore on failure/abort (:497,503,512,520); commit after successful flash (:523-525).
- `rebuild_and_flash.py` (496 lines) does NOT use artifact_custody; it has its own `.prev-flashed` snapshot (`SNAPSHOT = OBJ/.prev-flashed` :52, `ARTIFACTS` :53, `snapshot_artifacts` :76, `restore_artifacts` :88) plus `archive_artifact` :96 (copies `.axf` to `OBJ/archive/JX_FLY_<crc32>.axf`).
- Entry points: `python -m ground_station.flashtool.rebuild_and_flash [--yes] [--incremental] [--port COM6] [--arm-port] [--attempts 3] [--force]` (`main` :351; without `--yes` builds only). Funcs: `uv4_resident` :64, `build(rebuild=True, timeout=900)` :120, `target_alive` :197, `arm_status(elf, votes=9)` :201, `elf_matches_target` :234, `arm_status_from_telemetry(port, seconds=2.0)` :255, `flash(attempts=3, timeout=600, reset_fn=None)` :300. Exit codes 2 UV4 resident, 3 build failed, 4 uvoptx not restored, 5 target dark, 6 not disarmed/unreadable, 7 flash failed after retries, 8 dark after flash. Log `sf.LOG_DIR/rebuild.log`.
- `python -m ground_station.flashtool {gate|build|flash|verify|all}` -> `safe_flash.main` :455 (`GateResult` :71, `SafetyGate` :86, `_run_uv4` :157, `build` :304, `flash` :368, `verify_ekf` :413). Sibling modules: build_id, preflight, project_integrity, target_power, compile_commands, defender_build.ps1.

## 8. Tests

- `pytest.ini` (root): defaults plus `norecursedirs = *.egg .* _darcs build CVS dist node_modules venv {arch} logs scratch`. Root `tests/` has `agent_ops/` (test_vps_bridge.py/.sh), `firmware_host/` (C host tests test_controller.c, test_ekf_gate.c, test_idle_decouple.c + stubs). Package tests live beside code: `ground_station/service/tests/` (84 entries; 54 `test_*`, 26 `*harness*.js` plus all_panels_audit.js, verify_honest_panels.js), `ground_station/service/test_agent_permissions.py`, `ground_station/analysis/flightlab/tests/`, `ground_station/flashtool/tests/`, `ground_station/platform/tests/` (incl. `rtos_panel_harness.js`, `test_capability_manifest.py`), `ground_station/comm/tests/` (incl. `status_sidebar_harness.js`), `ground_station/agent_map/tests/`, `sim/`.
- **JS panel harnesses**: Node DOM harnesses (fake `document`/`window`, stub `shellApi`, panel script executed in a VM context) run as `node ground_station/service/tests/<name>_harness.js` (exit 0; prints `ALL CHECKS PASSED`) or through their pytest wrapper `test_<name>.py`, which locates `node` (PATH, else `/mnt/c/Program Files/nodejs/node.exe`), runs it with `cwd`=repo root, timeout 120 s, and asserts exit 0 + `ALL CHECKS PASSED` + named check strings (`test_path_panel.py:14-60`). Pattern documented in `docs/dashboard-platform/AGENT_GUIDE.md:69-80`. If node is missing the wrapper skips.
- **Manifest regeneration**: `python -m ground_station.platform.capability_manifest` (writes `docs/dashboard-platform/capability_manifest.json`; `--check` exits 1 on drift; `--out PATH`) (`capability_manifest.py:628-676`). Drift is enforced by `ground_station/platform/tests/test_capability_manifest.py:21` `test_capability_manifest_no_drift`; the manifest indexes panels, `_ROUTE_MAP`, commands, telemetry keys, DWARF symbols from `OBJ/JX_FLY.axf`, so adding a panel/route/command requires regenerating it. `python -m ground_station.agent_map build` -> `docs/agent-map/agent_map.json` (gitignored; `AGENT_MAP_SPEC.md:33`). `python -m ground_station.flashtool.compile_commands` (AGENTS.md). `python -m ground_station.analysis.flightlab ledger --rebuild` (currently broken here, section 6). livewatch `manifests.yaml`/`multi_slot_presets.yaml` are hand-edited.

## 9. Claude skill file format

- **`.claude/skills/` does not exist in this worktree**: `.claude/` is gitignored (`.gitignore:43`), so skills exist only in the main checkout: `../../.claude/skills/stream-log/SKILL.md` and `../../.claude/skills/agy-delegate/SKILL.md` (untracked). A skill added in the worktree will not be committed unless the ignore is overridden.
- Format: directory `<name>/SKILL.md` with YAML frontmatter `name:` and `description:` (folded `>` block allowed), then Markdown body. Example (`stream-log/SKILL.md:1-8`): `name: stream-log`, `description: >` "Log firmware variables ... Use when the user asks to log/record/capture ...". `agy-delegate` uses a single-line `description:`. Only two fields seen.
- Repo-tracked reference docs (not skills, same frontmatter style): `docs/skills/livewatch.md:1-8` (`name`, `description: >`), also capture-multislot.md, micoair-connect.md and `*-purpose.md` variants.

## Surprises / doc-vs-code discrepancies
1. `/api/paths` is GET-only; `path_library` save/delete/resample/smooth are orphaned; `_ROUTE_MAP` docs overstate CRUD.
2. Path panel library is browser localStorage only; only hover/line/circle/figure8 are flyable (no waypoint upload).
3. flightlab `analyze/compare/ledger` cannot import here (ledger.py, compare.py, render_md missing).
4. No arm/idle helper exists in Python; arm is raw cmd 0x0E idx 0/1. `rebuild_and_flash` bypasses `artifact_custody` (only `safe_flash` uses it).
5. In autonomous mode `tier0_access="full"` releases all param writes (incl. EKF-into-control 0x1E) without approval; the flag defaults to "partial" and resets on restart.
