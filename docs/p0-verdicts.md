# P0 verdicts — independent verification of `firmware-preflight-findings.md`

The 13 findings in `docs/firmware-preflight-findings.md` were produced by one
agent reading source. A confidently wrong P0 costs the operator more than
silence does. This file records only findings verified a second time, against
source, with the lines quoted.

**Status: 2 of 5 P0s verified. Findings 2, 4 and 5 are still unverified claims.**
Two worker waves spawned to verify them died on provider quota (agy
`Individual quota reached`, ark `exceeded the 5-hour usage quota`, both resetting
around 10:20–10:53 on 2026-09-21). Do not act on 2, 4 or 5 until they are
checked the same way.

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

## Findings 2, 4, 5 [P0] — **NOT VERIFIED**

Still single-source claims from `docs/firmware-preflight-findings.md`:

- **2** — OF/ToF disconnect or mid-flight freeze causes runaway position
  integration and zero-rate climb.
- **4** — raw gyro rate feedback injected with Mahony angle error and integral
  bias mutating `Gyro_*_Real`.
- **5** — silent reuse of stale IMU samples on dropped/corrupt SPI reads.

Finding 3 is the reason to check them rather than act on them: its mechanism was
real, but its stated trigger was wrong, and the filtering that made it wrong was
40 lines above the cited range.
