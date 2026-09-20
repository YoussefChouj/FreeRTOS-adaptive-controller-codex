# Operator feedback — dashboard walkthrough, 2026-09-20

Source: operator dictation while stepping through the live dashboard tab by tab.
Captured verbatim in intent; grouped and triaged by the supervisor. Nothing here
has been verified against the code yet — the **Status** column says so per item.

## What is already good (do not regress)

The operator called these out as working well. Treat them as protected surface:

- Overview tab overall.
- The **command panel design** ("actually it's very good").
- The **FFT spectrum** panel ("very good").
- The **bandwidth manager** concept ("very good").
- The **Telemetry Explorer** ("very good... I like it very much").

## Triage

Owner column: `DASH` = ground-station/UI change only. `FW` = the firmware does
not publish the data, so no UI change can fix it alone.

### A. Information architecture / redundancy

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| A1 | Sidebar shows only schema ID, session, sample count, last update. Operator wants **arm status, flight mode, battery voltage** there — the things you need at a glance. | DASH+FW | unverified |
| A2 | The telemetry-stream widget is repeated on Overview **and** Control. Redundant — sidebar already carries sample count. | DASH | unverified |
| A3 | Flight mode, battery, commands, rates, authority + state flags are scattered across tabs. Operator wants them **in one place**, with the critical few promoted to the sidebar. | DASH | unverified |

Operator's governing principle, quoted: *"I don't want redundant things among all
the tabs. The really important indicators should be in the sidebar so I can see
them if I want to."*

### B. Time series / plotting

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| B1 | Add **X / Y / Z position** traces. | DASH | unverified |
| B2 | Make the plot **configurable**: a dropdown with checkboxes to pick which variables are plotted. | DASH | unverified |
| B3 | Recording controls: **pause**, step back / replay, and **zoom into a region**. | DASH | unverified |

### C. Bandwidth manager / slots

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| C1 | Cannot add a slot to the active streams — **no visible button** to do it. | DASH | unverified |
| C2 | Deleting a slot does not actually delete it. | DASH | unverified |
| C3 | The UI asks for a **divider**. That is a firmware-internal unit. Operator wants to enter **Hz**. Quoted: *"Only someone who has knowledge about the firmware can understand what 1, 2, 3, or 4 can map to."* | DASH | **backend already exists** |

**C3 note:** this is already solved on the backend and just needs wiring.
`ground_station/platform/rate_planner.py` exposes
`choose_divider(desired_hz, cadence_hz)` and returns the honest achieved Hz
(`cadence / divider`), plus the rate gap. And the cadence is no longer ambiguous:
measured on hardware 2026-09-20 at **99.99994 Hz** in normal-flight mode
(`SystemCoreClock` 168 MHz, `g_send_prof.period_cycles` 1680001). So the UI can
offer Hz, show the achievable neighbours, and say plainly "you asked 30 Hz, you
will get 25 Hz (divider 4)". See `.claude_state.md` for the cadence resolution.

### D. Commands

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| D1 | One monolithic command form for everything. Should **branch per command**, exposing only that command's relevant parameters. | DASH | unverified |
| D2 | **No observability of the values currently running in the firmware.** Operator must know an index by heart to send a command. Quoted: *"I have no observability of some of the default values that are already running in the firmware so that's an issue."* | FW+DASH | unverified |

**D2 is the most dangerous item on this list.** Sending a parameter command
without being able to read the current value back is how you overwrite a tuned
gain with a typo. This should rank above most of the cosmetic work.

### E. Estimator tab

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| E1 | Tab shows an error: *"the EKF step has not been exposed by the firmware"* and renders **no data at all**. Either expose it or make the tab degrade honestly instead of erroring. | FW | unverified |
| E2 | No way to switch bias-estimation mode. Operator asks whether the three modes are **EKF / EMA / fixed-at-boot** — and whether a command exists to select among them. | FW+DASH | **open question** |

### F. Telemetry explorer

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| F1 | Tab is good and carries many variables. But the **tick count reads 0** when it should be a monotonic counter. | FW | unverified |

### G. Experiments

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| G1 | Only "controls" and "step response", entered as free text. Should be a **dropdown of experiment types**. | DASH | unverified |

Ties into WP5 clause (c) (declarative experiment plans), which is still open.

### H. Paths

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| H1 | The path-planning plot renders something meaningless / not working. Should show the drone's **live X and Y** position (Z optional — operator explicitly said 2D is fine and easier than 3D). | DASH+FW | unverified |

### I. Bench

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| I1 | Motor control is scaled in **RPM 1–1000**. The hardware is driven by **PWM, roughly 2000–4000**. The current scale is confusing and wrong in units. | DASH | unverified |
| I2 | RPM feedback panel is permanently stuck on *"waiting for telemetry feedback"*. | FW | unverified |

**I1 is safety-adjacent** — a motor control whose displayed units do not match
the values actually sent is a hazard, not a cosmetic bug. Verify what the widget
actually transmits before changing the label.

### J. FreeRTOS resources

| # | Item | Owner | Status |
| --- | --- | --- | --- |
| J1 | Panel reports that some values are **placeholders** and the keys are not available yet. | FW | unverified |

## The pattern underneath — **this hypothesis was WRONG. Corrected 2026-09-20 22:30.**

~~Most of the `FW`-owned items (E1, F1, I2, J1, D2) are the same problem wearing
different hats: the dashboard is asking for variables the firmware does not
publish in the subscribe schema.~~

**That was the supervisor's hypothesis and verification disproved it.** Two
workers — one static source/ELF investigation, one live browser walk — plus the
supervisor's own re-check of the load-bearing claims found:

| Item | Assumed | Actually |
| --- | --- | --- |
| E1 Estimator | firmware doesn't publish EKF | **PUBLISHED_UI_BUG** — slot 0 already carries `s_ekf.x[0..8]`; the bridge drops them |
| F1 tick count | firmware doesn't publish ticks | **PUBLISHED_UI_BUG** — `xTickCount` is published; the decoder reads a u32 as float32 |
| I2 RPM feedback | firmware doesn't publish rpm | **PUBLISHED_UI_BUG** — published in two frames; panel reads keys the bridge never emits |
| J1 FreeRTOS | firmware doesn't publish | **EXPORTED_NOT_PUBLISHED** — 7 of 8 globals exist in the ELF but sit in no subscribe slot. 1 (`rtos.usart3_tx_bytes`) genuinely absent |
| D2 param readback | needs firmware work | genuinely missing — but a generic scalar read path (`0x20`/`0x21` address subscribe + DWARF resolver) **already exists** |

**Consequence: the firmware rebuild-and-flash that was being prepared would have
been almost entirely wasted work.** Three of five are ground-station bugs. J1 is
a schema/slot config change, not firmware code. Only one truly missing key
(`usart3_tx_bytes`) and possibly D2's plumbing would justify touching firmware
at all.

The honest-degradation requirement survives, but as a **much smaller** job: it
applies to the handful of fields that really are absent (`ekf.pos_*` — this is a
9-state EKF with no position states — `estimator.filter_status`, `cov_*`,
`usart3_tx_bytes`), not to whole panels. A panel that shows `0` for a counter
that is simply absent is still worse than one that says it has no data.

## Verification results — what the operator got right and wrong

**Confirmed:** A1, A2, C3, F1, G1 (the free-text part), H1, I1, I2, J1.

**Not confirmed — the report was inaccurate as stated:**

- **C1** — there *is* a visible, enabled "+ Request Slot" button. The real
  defect is different and worth more: the form has **no variable/range picker**,
  so it submits empty ranges. Fixing the reported bug would have fixed nothing.
- **E1** — the error banner text differs from the dictation, and the tab does
  render 4 live raw-IMU values, so "no data at all" overstates it.

**Could not test by design:** C2, D1, D2 — these mutate state, and the journey
runner is GET-only with a click whitelist per `IMPROVEMENT_SPEC` line 58.

**E2 is answered.** The three modes are real and are **fixed-at-boot / EMA /
EKF** via `g_of_bias_mode` 0/1/2 (`TASK/StabilizerTask.c:57-97`). A selector
command exists: **`0x1E`**, index 0 selects the mode, index 1 freezes
(`send_data.c:1833-1840`, `firmware_contract.py:476-484`, already referenced in
`command-panel.js:60`). The current mode is transmitted in Frame OF
(`send_data.c:993-994`). **But "UI wiring only" was wrong — corrected
2026-09-21.** Frame `0x05` is shipped by `DMA1_Stream7` (`send_data.c:1307`)
whose peripheral is `UART5->DR` (`BSP/usart5.c:126`), so it goes out the
**serial** link, not USART3/WiFi. `serial_bridge.py:1007-1019` decodes it;
`wifi_bridge.py` has **no `0x05` branch at all**. Over the transport the
dashboard actually uses, that readback never arrives. The live mode is read
instead via the generic symbol-subscribe path — which is D2's mechanism, now
built: `CommandParam.symbol` (`firmware_contract.py:80`) maps `0x1E` idx 0/1
to `g_of_bias_mode` / `g_of_bias_ema_freeze`, both DWARF-verified as 1-byte
integers by `platform/tests/test_command_symbol_map.py`.

## A caution about worker-reported findings

Both workers returned `rc=0` and confident reports. Re-checking the
load-bearing claims against source found one **fabricated**:

- Claimed: "motor-bench destroy handler POSTs cmd-22 zeros for all 4 motors on
  page beforeunload." **False.** `beforeunload` appears in no `.js` file in the
  repo; `motor-bench-panel.js` contains no `fetch(`, no `destroy` and no
  `unload` handler. It does contain an explicit operator-pressed abort button
  (`CMD_ID_ABORT_ALL`, `:333`) that sends zeros, and a genuine arm interlock
  (`api.isDisarmed()`, `:287`; `api.gatedCommand(..., ['disarmed'])`, `:293`).
  The abort button was most likely mis-reported as an unload handler.
- Claimed: "slot-manager auto-fires POST `/subscribe/preview` on page load."
  **True** — `_schedulePreview()` at `slot-manager-panel.js:621`, POST at
  `:151` — but deliberate, documented in the comment, and a pure-compute
  validation endpoint with no drone effect. Worth knowing, not a bug.

A fabricated safety finding is the most expensive kind of error here: it would
have sent us to fix a hazard that does not exist while the real interlock went
unexamined. Treat worker safety claims as unverified until checked against
source.

Most of the `DASH`-owned items are a second single problem: **operator-facing
units and controls.** Divider instead of Hz, RPM instead of PWM, free text
instead of a dropdown, missing add button, a delete that does not delete.

## Operator's closing question

> *"Can the workers take screenshots and navigate through the dashboard as I am
> doing right now? ... Maybe in a more efficient way, not even taking
> screenshots, maybe even programmatically, like navigating, clicking buttons."*

**Yes — and the machinery already exists and was verified this session.**
`ground_station/service/journey.py` (the WP6 journey runner) drives a browser
programmatically: `navigate`, `click` (whitelisted), `assert_text`,
`is_visible`, `screenshot`, plus capture of console messages and HTTP errors,
writing a `report.json` with per-step pass/fail naming expected vs actual.
`python -m ground_station.service.browser_smoke` is the existing entry point.

**What it can verify unattended** — a large share of this backlog, because these
are all "does the page render the right thing" questions: C1 (no add button),
E1 (error banner present), F1 (tick reads 0), H1 (plot empty/degenerate),
I1 (displayed motor range), I2 (stuck waiting message), J1 (placeholder text),
A1/A2 (which widgets appear on which tab).

**What it deliberately cannot do:** the runner is GET-only with a click
whitelist *by design*, so it cannot press apply/arm/submit. That is why C2
("delete actually deletes"), D1/D2 (command submission) and anything that
mutates flight state stay operator-supervised. This is the safety property from
`IMPROVEMENT_SPEC` line 58, not a missing feature — do not "fix" it by widening
the whitelist.
