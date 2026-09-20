# P0 verdicts — independent verification of `firmware-preflight-findings.md`

The 13 findings in `docs/firmware-preflight-findings.md` were produced by one
agent reading source. A confidently wrong P0 costs the operator more than
silence does. This file records only findings verified a second time, against
source, with the lines quoted.

**Status: 4 of 5 P0s verified. Finding 2 is still an unverified claim.**
Findings 1, 3, 4 and 5 were each verified against source by the supervisor on
2026-09-21 after two worker waves spawned to do it died on provider quota.
Do not act on Finding 2 until it is checked the same way.

---

## Finding 1 [P0] — SBUS link loss / failsafe unhandled: **CONFIRMED**

Verified by the supervisor 2026-09-21. Two independent halves, both hold.

`BSP/usart1.c:66` accepts a frame on start byte and end byte only:

```c
if ((datatmp[0] == 0x0F && (datatmp[24] == 0x00 || datatmp[24] == frame_end[frame_cnt])))
```

Byte 23 — which carries the SBUS **frame-lost (bit 2)** and **failsafe (bit 3)**
flags — is never inspected. The parser reads `sbus_channel[15]` from bytes 21/22
(`:85`) and stops there.

This matters because a receiver that has lost its transmitter **keeps streaming
frames**, with the failsafe bit set and the channel values held or defaulted. So
the only existing detector — the 500 ms silence timeout that sets `sbus_lost` at
`TASK/RemoterTask.c:47,51,53` — never fires on RF loss. It fires on *receiver*
loss (unplugged cable, dead receiver), which is the rarer failure.

And even if it did fire, nothing acts on it: `Check_Fly_Mode`
(`TASK/RemoterTask.c:134-149`) gates the emergency path on channel 9 alone —

```c
if( sbus_channel[9] <= 500 )   /* -> FLIGHT_EVENT_DANGEROUS_STOP */
```

— and never consults `sbus_lost`. Its only readers are `API/rc_input.c:196,263`,
`TASK/AutoflyTask.c:226` and `TASK/send_data.c:1048`.

**Net: RC link loss has no failsafe path.** The aircraft continues on the last
received stick values indefinitely.

---

## Finding 3 [P0] — uncommanded spool-up via height glitch: **PARTLY — mechanism and consequence confirmed, stated trigger is wrong in both directions**

Verified by the supervisor 2026-09-21.

### What is confirmed

The transition and the missing interlock are exactly as reported.
`TASK/StabilizerTask.c:585-608`, inside the `FLIGHT_STATE_ARMED` branch:

```c
else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE)
{
    if (Ctrler.Z_posPID.FB > 0.2f)
        flight_phase = FLIGHT_PHASE_FLYING;

    /* IDLE motors: hold until pilot or policy pushes THR above 20%. */
    if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < 0.2f)
        Set_IDLE_Motors();
    ...
}
else if (flight_phase == FLIGHT_PHASE_FLYING)
{
    Set_PWM_Motors();
}
```

The `FLYING` branch has **no throttle interlock at all**. Once the phase flips,
`Set_PWM_Motors()` runs every tick with the stick at zero, and
`Throttle_out = Ctrler.Z_ratePID.U + Throttle_th` with `Throttle_th = 2950`
(`:799`, `:823`) — hover baseline, from an IDLE of 2150.

There is no automatic way back. Every assignment to `flight_phase` in the
codebase:

```
StabilizerTask.c:580  -> FLIGHT_PHASE_LANDED    (end of a LANDING sequence)
StabilizerTask.c:593  -> FLIGHT_PHASE_FLYING    (this bug)
flight_fsm.c:44,45,48,49 -> GROUND_IDLE         (disarm / dangerous-stop / recover)
RemoterTask.c:160     -> FLIGHT_PHASE_LANDING   (requires RC ch5 gesture, and
                                                 only while already FLYING)
```

Low throttle does **not** return the aircraft to `GROUND_IDLE`. Recovery
requires a deliberate pilot action.

**One correction to the report's timing:** in the tick where the phase flips,
the throttle interlock below it still runs, so `Set_IDLE_Motors()` is called.
The spool-up happens on the *next* tick. At a 5 ms loop this is immaterial to
the outcome, but the fix must gate the *transition*, not the motor call.

### Where the report is wrong

The report's trigger is "a transient height reading > 20 cm … hand movement
under the ToF sensor". **A transient is the one thing that cannot cause this.**
The height feeding `Z_posPID.FB` is not raw. `StabilizerTask.c:425-457` applies
three layers before `:465` copies the result:

```c
/* Layer 1: median-of-3. */
/* Layer 2: band gate. */     if( alt_med >= 5U && alt_med <= 500U )
/* Layer 3: rate-aware jump gate. */
float gate = 0.05f + 0.15f * fabsf(Ctrler.Z_ratePID.Des);
if (gate > 0.20f) gate = 0.20f;
if( fabsf(h_new - ano_of.of2_raw_h) < gate || s_alt_reject_cnt >= 20U )
```

Note `of2_raw_h` is a misleading name — it holds the *filtered* value, and
`:465 Ctrler.Z_posPID.FB = ano_of.of2_h;` is a copy of it, not of the raw
sample. A single spurious sample is killed by the median; a jump larger than
5–20 cm is killed by layer 3.

### Where the report is understated

Two real triggers survive all three layers, and neither is exotic:

1. **Sustained obstruction.** `s_alt_reject_cnt >= 20U` is an escape hatch: after
   20 consecutively rejected samples the jump gate is bypassed and the new
   height is accepted regardless. At 5 ms per sample that is **100 ms**. A box
   slid under an armed aircraft, or a person standing under it, clears this.
2. **Slow ramp.** Lifting an armed aircraft by hand raises the height by well
   under 5 cm per 5 ms sample, so every step passes the jump gate cleanly. This
   is the most likely real-world trigger and the report does not mention it.

And in bench mode the consequence is *worse* than in flight: `:799` sets
`Throttle_th = bench_mode_active ? 3200 : 2950`, so a bench spool-up targets a
**higher** baseline than a flight one. The code comment at `:587-591` states
explicitly that bench mode does not block the transition.

### Verdict

Severity **stays P0**. The report's proposed fix — require pilot throttle as
well as height before leaving `GROUND_IDLE` — is correct and should gate the
transition itself. Picking up an armed aircraft is the trigger to write the fix
against, not a sensor glitch.

---

## Finding 5 [P0] — silent reuse of stale IMU samples: **PARTLY — every mechanism confirmed, the consequence is misdescribed**

Verified by the supervisor 2026-09-21.

### Confirmed exactly as reported

`BSP/spi.c:62-73` returns a value indistinguishable from data on timeout:

```c
USHORT16 spi2_read_write_byte(USHORT16 txc)
{
    uint32_t to = 10000U;
    while (((SPI2->SR & SPI_SR_TXE) == 0) && --to);
    if (!to) return 0U;
    ...
    if (!to) return 0U;
    return SPI2->DR;
}
```

`0U` is both "bus timed out" and "the sensor sent 0x00". No caller can tell them
apart, and `GetValue()` (`API/bmi088_driver.c:183`) is `void` — it returns no
status and checks none.

`sensor.sensor_ok` is **never cleared at runtime**. Every write in the tree:

```
API/bmi088_driver.c:126,135,176   -> 0U   (inside bmi088_init)
API/bmi088_driver.c:179           -> 1U   (end of bmi088_init)
BSP/BSP.c:41                      -> 0U   (boot, if bmi088_init fails)
```

Its only consumers are `API/imu_update.c:151` and `IMU_EstimatorReady()` at
`:217`, which the FSM uses to **block arming**. So it is an arm-time gate that a
mid-flight IMU failure can never revoke.

`Warning` is dead as reported: written at `bmi088_driver.c:407,409,411`, read
nowhere in the tree.

### Where the report is wrong

The stated consequence is "the Mahony filter continuously integrates stale
angular rates, causing rapid orientation divergence … immediately flipping the
aircraft." That is not what the timeout path produces.

On timeout the bytes come back `0`, so the sample is **zero, not stale**. A zero
gyro integrates no rotation, and the accelerometer correction is skipped
outright by the guard at `API/imu_update.c:110`:

```c
if((Acc_X_Real != 0.0f) || (Acc_Y_Real != 0.0f) || (Acc_Z_Real != 0.0f))
```

Worth noting this guard also disposes of a worse failure I went looking for:
`invSqrt` of a zero-norm vector at `:117` would yield inf and then `0 * inf =
NaN` through the whole quaternion. The guard prevents it. Good code.

So a full SPI outage **freezes** the attitude estimate rather than diverging it.
That is still a P0 — the estimate stops tracking an aircraft that is still
rotating, the controller acts on a stale attitude, and recovery delivers a large
step into the loop — but the failure signature an operator or a log would see is
the opposite of the one described. Anyone debugging from this report would look
for divergence and find a flatline.

**Genuinely stale** data needs a different fault: `IMUSample_Task` hanging, or
the sensor freezing while its registers still read out. Those are real and
unguarded, but they are not the SPI timeout.

### What the report missed, and it is worse than either

A **partial** SPI failure — some bytes timing out, others succeeding — yields a
sample that is neither zero nor stale but *plausible*. An MSB that times out to
`0x00` with a valid LSB produces a small, well-formed reading that passes the
`:110` guard and every downstream sanity check. Nothing in the firmware can
detect it.

### Verdict

Severity **stays P0**. The proposed fixes are right, and fix 2 (detect frozen or
timed-out reads) should key on *both* signatures — a zero sample and an
unchanging one — because the code produces both by different routes.

---

## Finding 4 [P0] — Mahony correction mutating the global `Gyro_*_Real`: **PARTLY — mechanism confirmed exactly, magnitude overstated, blast radius understated**

Verified by the supervisor 2026-09-21.

### Confirmed exactly as reported

Every line the report quotes is accurate. `API/imu_update.c:134-136`:

```c
Gyro_X_Real += kp_eff * ex + exInt;
Gyro_Y_Real += kp_eff * ey + eyInt;
Gyro_Z_Real += kp_eff * ez + ezInt;
```

There is exactly **one** raw writer in the tree — `API/bmi088_driver.c:396`, inside
`Sensor_Data_Prepare()` (`:346`):

```c
Gyro_X_Real = Gyro_X_Ori*0.0174533f;
```

and the rate loop consumes the same global at `TASK/StabilizerTask.c:485-487`, as
quoted. The scheduling claim holds: `Global_file/creat_task.h:25,34,43` puts
`IMU_DataDeal_TASK_PRIO`, `IMUSAMPLE_TASK_PRIO` and `STABILIZER_Task_TASK_PRIO`
all at **4**, and `USER/main.c:355,374,391` gives them 1 ms, 1 ms and 5 ms
periods. So a 200 Hz consumer samples a 1 kHz two-task ping-pong between equals.

### Where the report is wrong

**1. The magnitude is ~6x overstated.** The report says "during maneuvers,
`kp_eff * ex` can exceed 0.5 rad/s (~30 deg/s)". `Kp = 0.5f`
(`API/imu_update.c:20`) and `ex` is a component of the cross product of two
**unit** vectors (`:121-123`), so `|kp_eff * ex| <= 0.5` rad/s is the absolute
geometric ceiling, reached only at 90 degrees of apparent error. A realistic 10
degrees gives `ex ~ 0.17` and a contamination of **~0.09 rad/s (5 deg/s)**. Real,
but not the number in the report.

**2. Half of what it calls corruption is a correction.** `exInt` is Mahony's
**gyro-bias estimate** (`:127-129`, `Ki = 0.001f`). Adding it to `Gyro_X_Real`
makes the rate the PID sees *bias-compensated*. Only the `kp_eff * ex` term —
attitude innovation fed into a rate loop — is harmful. The fix is still right,
but it will also *remove* bias compensation the inner loop is currently getting
for free, and that is worth knowing before flying the change.

**3. It does not accumulate.** `Sensor_Data_Prepare()` overwrites
`Gyro_*_Real` from raw every 1 ms, so the injection is a per-tick disturbance,
never a drift. The report does not claim drift, but "corrupted" invites that
reading.

What the report gets right and matters more than the magnitude: because the
5 ms consumer samples an unsynchronised 1 kHz pair, the contaminated fraction
varies tick to tick. It is **broadband sampling jitter on the disturbance**, not
a steady offset — the worse of the two for a rate loop.

### Where the report is understated

**1. There is an 8x gain boost it never mentions — and a guard that saves it.**
`API/imu_update.c:32,34,106` boost `kp_eff` to **4.0** at t=0, decaying linearly
over 10 s. During that window the injection ceiling is 4 rad/s (229 deg/s).
Arming cannot happen in it: `API/flight_fsm.c:40` gates
`FLIGHT_EVENT_ARM_REQUEST` on `IMU_EstimatorReady()`, and `:151` only sets
`g_estimator_ready` once `s_settled` — which `:144` can only latch **after**
`s_boot_t > IMU_FAST_WINDOW` (10 s) — or the 30 s hard timeout. Both are past the
boost. **The worst case cannot coincide with flight.** Neither the hazard nor the
guard is in the report.

**2. The blast radius is not just the rate PID.** The code's own comment at
`TASK/StabilizerTask.c:483` says this feedback is what "the rate PID, MRAC, and
the system-ID frame all consume", and `TASK/send_data.c:730` feeds the same
global into `Ekf9_Predict`. For an operator running MRAC adaptation and system
identification, this contaminates the **regressor and the identified plant
model** — it corrupts the experiment, not only the control loop. That is the
consequence that should lead this finding for this project.

**3. The real trigger is translation, not attitude error.** The report's trigger
is "attitude error `ex, ey` non-zero". But `ex` is the cross product of the
*measured acceleration* direction with the estimated gravity direction, and the
accelerometer measures gravity **plus linear acceleration**. In any translational
manoeuvre `ex` is large even when the attitude estimate is perfect. The
contamination is not an occasional consequence of poor estimation; it is
guaranteed whenever the aircraft accelerates.

### Noted while verifying — hypothesis, not a finding

`API/ekf.h:5,20` gives the shadow EKF states `[v_body, b_a_body, b_g_body]` — it
estimates its own gyro bias, from a gyro that already has Mahony's `exInt` bias
correction applied. Two estimators correcting the same bias, one of them fed a
term that jitters at the task-race rate. Whether this contributes to the F2
shadow-EKF velocity blow-up is **untested** and stated here only as a lead.

### Verdict

Mechanism **confirmed**. The fix (three locals for the quaternion integration,
never touch the globals) is three lines and carries no risk beyond item 2 above,
so it should be made. But the report's stated consequence — "can trigger
high-frequency oscillations or loss of attitude stabilization" — is **not
supported** at `Kp = 0.5`. Keep the priority; rewrite the justification around
experiment integrity.

---

## Found while verifying Finding 4 — CMD 0x18 "force recalibration" does not

`TASK/send_data.c:1871` sets `g_estimator_ready = 0U` under the comment "Force
cold cal to re-run from top". `s_boot_t` and `s_settled` are `static` in
`API/imu_update.c:46,48` and are **not** reset. On the next 1 ms Mahony tick,
`:151` recomputes `g_estimator_ready` from `s_settled`, which is still `1U`, and
the flag returns to 1. **The reset lasts under one millisecond.**

Not P0 — 0x18 is refused outside `GROUND_IDLE`/`DisArmed` (`:1847-1849`), and the
estimator genuinely is settled, so nothing unsafe follows. But the comment at
`:1845` claims a reset that does not happen, and an operator using 0x18 to force
re-convergence after moving the aircraft will not get one.

---

## Finding 2 [P0] — **NOT VERIFIED**

Still a single-source claim from `docs/firmware-preflight-findings.md`: OF/ToF
disconnect or mid-flight freeze causes runaway position integration and
zero-rate climb.

Findings 3, 4 and 5 are the reason to check it rather than act on it. In all
three the mechanism was real and the stated trigger, magnitude or consequence
was wrong — and in each case the correcting code was outside the cited line
range.
