# HMI Design Note — the operator-facing overview

Status: implemented for the overview panel (`shell/plugins/overview-panel.js`), task 20260921-044235.
This note is the brief for all later HMI work: it fixes the colour semantics, the
alarm model, the staleness thresholds and the naming convention, so future
panels do not invent a second convention.

The design goal, in the operator's own words: *"the GUI be improved to be like
the PLC-style HMI interfaces where me the user does not need to understand
deeply the code and the logic is clear to see."* A master's student running
experiments on a real aircraft needs at-a-glance answers to: is the system
healthy, what is it doing right now, and where is the problem.

## 1. Colour semantics — strict, reserved, no decorative use

| Colour | Meaning | Where it may appear |
|--------|---------|---------------------|
| green  | normal / live / active-on | stage border+flag, banner, pills, values |
| amber  | off-nominal: degraded, stale, not published, warning | same elements |
| red    | alarm: a condition that demands action now | banner, stage, values |
| grey (muted) | no data, or boolean OFF — the label says which | stage border, NO DATA cells, OFF pills |

Rules:

- These four colours are used for **nothing else** on the HMI page — no
  decoration, no branding, no chart colours. An operator must be able to trust
  that red means red.
- Grey means *absence*, never zero. A missing value renders the literal text
  `NO DATA` (stream absent) or `NOT PUBLISHED` (stream present, key absent) —
  never `0`, never `—`, never a frozen last value. This follows the
  `status-panel.js` convention (`null` → amber "NOT PUBLISHED").
- Boolean OFF renders grey with an explicit `OFF` suffix so it cannot be
  confused with NO DATA (which says `NO DATA` / `?`).

## 2. Alarm model

The banner shows the single worst active condition; the list below it shows all
active alarms (red first), then **recently cleared** alarms for 60 s after they
clear. A transient fault that disappears is the one that bites you, so a clear
is an event the operator sees, not a silent vanish.

Active alarm conditions (all derived from verified published keys):

| id | severity | condition | source |
|----|----------|-----------|--------|
| `vbat-low` | red | `status.vbat` < 15.0 V | firmware beeper threshold, `TASK/StabilizerTask.c:1329` (`Get_Voltage`) |
| `vbat-warn` | amber | `status.vbat` < 15.5 V | dashboard-only early warning, 0.5 V above the firmware beep (4S pack) |
| `sbus` | red | `status.sbus` ≠ 0 | `status.sbus` is `sbus_lost`; non-zero means the RC receiver link is down |
| `loss-<slot>` | red / amber | `loss_pct` > 5 % / > 1 % | shell loss thresholds, `plugin-api.md` |
| `stale-<slot>` | amber | slot age 2 s … TTL | see §3 |
| `estimator` | amber | `status.estimator_ready` = 0 | published by Frame A / Frame ID |
| `notelem` | amber | no streams at all | aircraft state unknown |

Severity ranking: red > amber; within a severity, list order is stable.
Banner text is the worst alarm verbatim, prefixed `⚠ ALARM —` (red) or
`WARNING —` (amber). No active alarms → green `SYSTEM NORMAL`.

## 3. Staleness model

| age since last update | stage state | alarm |
|----------------------|-------------|-------|
| < 2 s | green `LIVE` | — |
| 2 s … slot TTL (default 30 s) | amber `STALE` + age readout (`age 3.0 s`) | amber `stale-<slot>` |
| > slot TTL | grey `NO DATA` + `stale 35.0 s — frozen` | amber (still raised) |

Rationale:

- **2 s amber threshold.** The shell polls `/state` every 500 ms; Frame C runs
  at 50 Hz and Frame ID at 100 Hz. A healthy link updates every stage many
  times per second, so 2 s ≈ four missed polls — clearly off-nominal while
  short of dead. Crucially the *age is shown*, so a slow-but-alive link is
  distinguishable from a dead one at a glance.
- **30 s grey threshold** is `state.slot_freshness_ttl_ns` (the shell's own
  slot TTL, single source of truth). Past it the last value must not be
  presented as live — the value cell itself greys to `NO DATA`.
- Staleness is evaluated **per slot** (0, 1, 3), so a fault localises: if slot
  3 dies, the gyro/attitude/motor stages grey out while the MRAC stage (slot 0)
  stays green.

A 1 s self-tick keeps age readouts advancing even if the `/state` poll itself
dies, so a frozen number never looks live (cleared on panel destroy).

## 4. Naming convention

- Primary label is a plain-language noun phrase for the physical function:
  "Roll rate controller", "Filtered roll rate", "Battery" — never the symbol.
- Secondary text is the symbol an expert can trace, in mono: `pid.gyrox.U`,
  plus the frame that carries it (`Frame B · pid.gyro*.U`). Both are always
  shown together.
- Scanner words are uppercase and fixed: `LIVE`, `STALE`, `NO DATA`,
  `NOT PUBLISHED`, `LOSS n%`, `ARMED`, `OFF`.
- Every number carries its unit, taken from the firmware assignment site:
  `c.gyro_*` rad/s, `pid.gyro*.FB` deg/s (`StabilizerTask.c:485-487` applies
  `RAD2DEG`), `c.roll/pitch/yaw` deg, `c.earth_x/y`, `c.altitude` m,
  `motor.rpm_*` rpm, `status.vbat` V, `ekf.vel_*` m/s, `ekf.bias_gyro_*`
  rad/s. Dimensionless normalised mixer commands are labelled `cmd`
  explicitly rather than left bare (`pid.gyro*.U`, `mrac.*.u_ad`); `mrac.*.e`
  is rad/s for pitch/roll/yaw (`API/mrac.c:561-564` feeds MRAC `x` from
  gyro rates in rad/s).

## 5. Binding table (verified against `ground_station/comm/wifi_bridge.py`)

Slot routing (`ground_station/service/telemetry_adapter.py:157`): tag `a`→slot 0,
tags `id`/`b`→slot 1, tag `c`→slot 3.

| Panel element | key | slot | published at (wifi_bridge.py) | plain label |
|---|---|---|---|---|
| strip | `status.arm` | 0/1 | 1656, 1688, 1456, 1228 | ARM |
| strip | `status.flymode` | 0/1 | 1657, 1689, 1458, 1229 | Flight mode |
| strip | `status.vbat` | 0/1 | 1237, 1387 | Battery |
| strip | `status.rc_authority` | 0/1 | 1661, 1459, 1233 | RC AUTH pill |
| strip | `status.sbus` | 0 | 1660, 1690, 1230 | SBUS pill |
| strip | `status.estimator_ready` | 0/1 | 1663, 1461, 1235 | ESTIMATOR pill |
| stage | `c.gyro_x/y/z` | 3 | 1428 | Gyro sensor |
| stage | `pid.gyrox/gyroy/gyroz.FB` | 1 | 1379 | Rate filter (flown) |
| stage | `c.roll / c.pitch / c.yaw` | 3 | 1427 | Attitude estimate |
| stage | `c.earth_x / c.earth_y / c.altitude` | 3 | 1429 | Position estimate |
| stage | `pid.gyrox/gyroy/gyroz.U` | 1 | 1381 | Rate controllers |
| stage | `mrac.roll/pitch.u_ad`, `mrac.roll/pitch.e` | 0 | 1652-1655, 1241-1246 | MRAC augmentation |
| stage | `motor.rpm_0..3` | 3 | 1424 + 158-159 | Motors |
| shadow | `ekf.vel_x/y/z`, `ekf.bias_gyro_x/y/z` | 0 | 1249-1257 (from `s_ekf.x[0..8]`) | Shadow EKF |

Deliberately **not rendered**:

- `ekf.pos_*` — does not exist. `s_ekf` is a 9-state filter with no position
  states. Faking it is forbidden; the position estimate stage uses Frame C's
  `c.earth_x/y/c.altitude` instead.
- Anything from frame `0x05` — UART5/serial only; `wifi_bridge.py` has no
  branch for it, so nothing routed through it ever arrives over WiFi.
- `mav.*` keys (MAVLink 10001-10003, `ekf.roll_rad` etc.) — decoded by the
  bridge (`wifi_bridge.py:1539-1630`) but not in `_LEGACY_TAG_TO_SLOT`, so the
  service discards them; they never reach `state.streams`.
- `pid.z_rate.*`, `pid.locx/locy.*`, `pid.roll/pitch/yaw` angle-loop outputs —
  published by Frame B but omitted from the diagram for readability; they
  remain visible in the expert panels.

## 6. Shadow-mode rule

`s_ekf` is shadow mode: its values may be **made visible** (the Shadow EKF box)
but never wired into a control path, setpoint or motor output. The box carries a
permanent `SHADOW — display only, not in any control path` badge.

## 7. What a mature ground station has that this dashboard still lacks

Prioritised — highest operator value first:

1. **Attitude indicator (artificial horizon).** The single most
   information-dense flight instrument; roll/pitch/yaw are already published
   (`c.roll/pitch/yaw`). An SVG bank/pitch ladder needs no new data.
2. **Pre-flight checklist.** The gate model already exists
   (`api.getGates()`: connected, disarmed, fresh, schema, command) plus
   estimator-ready and battery thresholds — surface it as a tick/cross list
   the operator runs before every flight.
3. **Persistent alarm history with timestamps and export.** Alarms are
   currently derived per-render; a session-scoped log (with acknowledge /
   silence, the PLC standard) makes post-flight analysis possible.
4. **Trend-on-demand from any value.** Click a value cell → sparkline; the
   time-series panel already has the buffer machinery to reuse.
5. **Battery trend / time-to-empty.** Voltage slope is derivable from the
   existing `status.vbat` history; even without current telemetry a falling
   slope over the session is actionable.
6. **Altitude and vertical-speed tapes**, plus a motor RPM bar group —
   raw numbers are published; only presentation is missing.
7. **Frame 0x05 quantities** (if any become operationally important) are
   unreachable over WiFi today — they would need firmware or bridge changes,
   and until then must render as not published rather than be faked.
