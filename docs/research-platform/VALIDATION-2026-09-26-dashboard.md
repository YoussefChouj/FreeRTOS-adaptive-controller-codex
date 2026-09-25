# Dashboard End-to-End Validation Report (2026-09-26)

## Check 1: Every Tab
| Tab | Render Time | Console Errs | Bad Reqs |
|---|---|---|---|
| Overview | ~1.06s | 0 | 0 |
| Control | ~1.02s | 0 | 0 |
| Estimator | ~1.03s | 0 | 0 |
| MRAC | ~1.03s | 0 | 0 |
| Telemetry | ~1.02s | 0 | 0 |
| Experiments | ~1.02s | 0 | 0 |
| Paths | ~1.03s | 0 | 0 |
| Bench | ~1.03s | 0 | 0 |
| Replay | ~1.03s | 0 | 0 |
| Diagnostics | ~1.03s | 0 | 0 |
| Approvals | ~1.02s | 0 | 0 |
| Terminal | ~1.02s | 0 | 0 |

*Note: The page takes >15 seconds to fully load initially due to sequential `fetch` requests for plugins in `index.html`. Screenshots were taken successfully.*

## Check 2: Redundancy
| Panel ID | Tabs | Notes |
|---|---|---|
| `panel-streams` | Overview, Control, Telemetry | Present on 3 different tabs. |

No duplicate panels showing the same data under different names were found.

## Check 3: Streaming
Tested using `GET /state` and `GET /health/slots` against the default active preset (flight_comprehensive).

| Slot | Vars Expected vs Present | Age | Missing? | Hz | Loss % |
|---|---|---|---|---|---|
| 0 | Expected: dashboard_frame_a (keys present) | 12.1 ms | FAIL (Missing dashboard_frame_a vars on screen) | 40 Hz | 1.27% |
| 1 | Expected: inner_loops (keys present) | 3.1 ms | None | 80 Hz | 0.57% |
| 2 | Expected: mrac_weights (keys present) | 4.1 ms | None | 50 Hz | 0.0% |
| 3 | Expected: ekf_all (keys present) | 23.7 ms | None | 50 Hz | 0.0% |

Values like `c.attitude`, `c.gyro_x`, and timestamps successfully changed when sampled twice 10s apart in the `/state` API. However, the UI did not reflect these changes (see Defect 1).

## Check 4: UI values match API
| Value | UI Screen | API `/state` | Match? |
|---|---|---|---|
| Attitude | `NOT PUBLISHED` | Present / Updating | FAIL |
| Gyro (X) | `NOT PUBLISHED` | Present / Updating | FAIL |
| Battery | `—` | Present / Updating | FAIL |
| Slot Rate | `—` | Present / Updating | FAIL |
| Loss % | `—` | Present / Updating | FAIL |

## Check 5: Flight-test dry runs (disarmed)
Using the flight-test panel UI (after waiting 20s for the panel to load):

| Run | Duration | Rows | Status | Output Path / Folder |
|---|---|---|---|---|
| t23_pid (PID, Analyse OFF) | 15s | N/A | N/A (Blank) | N/A |
| t23_adaptive (MRAC, Analyse ON) | 15s | 280,260 | `done` | `C:\Users\Acer\Desktop\UAV_lab\...` |

**N/A / Wrong Findings:**
*   **Label Field:** The UI does not expose a way to set the `label` parameter for `/api/recording/start`. Filling the "Notes" field (`#note-input` or `#flight-test-notes`) sets the `notes` payload parameter but leaves the `label` blank (`""`) on the backend.
*   **Inaccessible Artifacts:** The session folder, `metadata.json`, plots, and Key Results values (RPM, attitude-tracking) are written to a local Windows path on the host laptop (`C:\Users\Acer\...`). They are entirely inaccessible from the VPS via the dashboard API or UI (no route serves them). Hence, metadata and Key Results are marked N/A.

## Check 6: Performance
| Metric | Before | After |
|---|---|---|
| Service Process RSS | 74.9 MB | 75.0 MB |

## Ranked Defects
1. **Empty Sidebar UI** (Tab: All, Selector: `#card-state .stat-value`). **What I saw**: The sidebar is entirely unpopulated; all values are `NOT PUBLISHED` or `—` despite the API streaming live telemetry. **Suggested fix**: Wire the shell's telemetry processor so it receives the websocket/state data properly.
2. **Very slow plugin loading** (Route: `GET /plugins/*`). **What I saw**: 27 plugins are fetched sequentially using `await fetch(src)` in an `async` IIFE in `index.html`. With local latency, the page takes >15 seconds before the Flight-test UI (`record-control`) renders. **Suggested fix**: Load plugins in parallel with `Promise.all` or bundle them.
3. **Flight-test UI cannot label recordings** (Route: `POST /api/recording/start`). **What I saw**: The flight-test plugin adds a "Notes" input, but doesn't pass a `label` to the API, causing recordings to be saved with an empty label (`""`). **Suggested fix**: Add a dedicated "Label" input and map it to the API's `label` field.
4. **Flight-test results are inaccessible via API** (Route: `GET /api/flight_tests`). **What I saw**: The API returns Windows local paths (`C:\Users\...`) for reports, but provides no static route or endpoint to fetch the generated `report.json`, metadata, or plots remotely. **Suggested fix**: Expose a static file route for `/logs/flight_tests/` or embed report summaries in the API response.
5. **Duplicated panel-streams** (Tab: Overview, Control, Telemetry). **What I saw**: `panel-streams` is present on multiple tabs unnecessarily. **Suggested fix**: Remove the duplicate panel definitions from `index.html`'s default metadata or only register them to one workspace.

## Supervisor verification (laptop, local browser at 127.0.0.1:8081, 2026-09-26)
The ranked defects above are the T23 worker's claims over the SSH tunnel. Checked locally:

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Empty sidebar | FALSE | Local page shows DISARMED, Stabilize, 23.48 V, attitude -0.9/-2.0/41.8 deg. Worker read the DOM before data arrived over the tunnel. |
| 2 | Slow plugin load | Tunnel artifact | Locally 28 plugins load sequentially in 202-836 ms (avg 14 ms each, 0 overlap). Not a defect; parallel load optional. |
| 3 | No label input | TRUE, fixed | Backend accepted `label`, panel never sent it. Added `#flight-test-label` and forwarded it in the REC POST. |
| 4 | Reports not fetchable remotely | By design | Reports live on the laptop disk. Optional future endpoint. |
| 5 | `panel-streams` on 3 tabs | FALSE | Single node `#card-streams`, `data-workspaces="telemetry"`. Worker counted DOM presence, not visibility. |

Also found:
- The worker's claim that the preset is `flight_comprehensive` is wrong: the service runs `flight_test_adaptive`.
- PID dry run had no report because the worker's script unchecked Analyse. The session itself (`20260926-012004`) recorded 284,678 rows over 15.4 s.
- Real defect found: `metadata.json` had empty `session_id`, `preset` and `signal_map_used`. Fixed in `flight_report.py` (T22 merge plus role->present-key map). Re-validation: T24.

## NOT RUN
- Did not verify if JS heap size could be collected as it was not explicitly available via the /health API or Playwright without deeper Chrome DevTools Protocol instrumentation.
- Did not click Arm, Throttle, or command buttons (forbidden by safety rules).

SUBSTITUTIONS: none
