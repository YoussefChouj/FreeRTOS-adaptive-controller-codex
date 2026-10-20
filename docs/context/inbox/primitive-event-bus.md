# Proposal: an event-subscription primitive, the `_ROW()` pattern for behaviour

**Status:** proposal, nothing implemented. Filed 2026-10-20 from the
repo-architecture review. Read before accepting or rejecting: the argument
for is mostly about *where an agent's edit lands*, not about runtime cost.

## The argument

`API/pid.c` already ships one declarative primitive: the
`<THING>_ROW(...)` macro, checked by `tools/row_meta.py`. Adding a tunable
parameter is a one-line table row, not a five-file edit. That works because
the *thing being configured* (a gain) is data.

Behaviour is not data. Adding "freeze MRAC adaptation while the actuators are
saturated" or "log dense-trajectory divergence" today means opening central
files — `API/controller.c`, `API/mrac.c`, `USER/main.c` — which are exactly
the Tier-0 paths the pre-push guard human-gates and that parallel `ah` streams
collide over. An agent that wants to add one diagnostic rewrites the control
loop to do it.

A static, zero-allocation event bus lets the same change land in a new file
under a new directory, with the control loop emitting a fixed set of lifecycle
events and nothing else.

## What already exists (do not rebuild this)

| Existing | What it is | Why it is not the bus |
| --- | --- | --- |
| `API/subscribe.c/.h` | wire-protocol stream multiplexer: slots, ranges, dividers over UART3/WiFi | it multiplexes *telemetry frames on a link*, not in-process callbacks; `Subscribe_StreamTick` is driven by `Send_Task` |
| `registry/platform_registry.yaml` + `scripts/generate_platform_registry.py` | declared platform capabilities, code-generated | it registers *what a platform is*, not *what happened at runtime* |
| `<THING>_ROW(...)` | per-instance tunable rows | data, not behaviour |

The name collision with `subscribe` is real and is a cost of this proposal:
call the new thing `events` or `bus`, never `subscribe`.

## Firmware shape (Sketch only)

```c
/* API/events.h — static, fixed-capacity, no malloc, ISR-safe emit. */
typedef enum {
    EVT_ESTIMATE_UPDATED,
    EVT_CONTROL_STEP,
    EVT_ACTUATOR_SATURATED,
    EVT_LAND_GATE_STATE,
    EVT_FAULT,
    EVT__COUNT
} EventId_e;

typedef void (*EventFn)(EventId_e evt, const void *payload, uint8_t len);

/* One row per subscriber, exactly like <THING>_ROW(). */
#define EVENT_SUB_ROW(name, evt, cb)  {name, evt, cb, 0}

void Events_Emit(EventId_e evt, const void *payload, uint8_t len);  /* cheap if no subscriber */
uint8_t Events_SubCount(EventId_e evt);
```

Constraints that make this safe for Tier-0, and that the design must not
negotiate away:

- **Static capacity.** A fixed table, sized at compile time. No `malloc`, no
  FreeRTOS queue allocation per subscriber. The firmware already budgets
  every stack and the MSP tightly (`tools/stack_budget.py`); an event callback
  that runs in the control step inherits that budget and must be measured.
- **Dispatch cost must be bounded and visible.** With no subscriber, `Emit` is
  a counter check. With subscribers, the cost is the subscribers' — so the
  *subscriber* budget belongs in `stack-budget`, not in a comment.
- **ISR-safe emit, task-safe subscribe.** Saturation is detected in the
  control loop; a fault may be raised from an ISR. Emit from an ISR must not
  block and must not call a callback that blocks.
- **Payload is a pointer plus a length, copied into the fixed table**, not a
  live struct pointer into the control step's stack frame.

## Why this is not a free win, and the honest costs

1. **The control loop still gets edited once.** Emitting `EVT_CONTROL_STEP`
   means one line in `API/controller.c`. That is a Tier-0 edit requiring its
   own stream and human review — it does not become cheap. The win is that
  *subsequent* subscribers never edit the loop again.
2. **Callback order becomes part of the design.** With N subscribers on one
   event, order is a contract. Registration order must be deterministic, and
   "first subscriber wins" style arbitration (e.g. for actuator saturation)
   has to be explicit or it will be discovered in flight.
3. **Testing.** A bus is only as good as its tests; the SIL and host harness
  must be able to assert *that* an event fired, not just its payload.
4. **Duplication risk.** Some "subscribers" are genuinely control logic, not
  observation (freeze-on-saturation changes the law). Those must not be
  smuggled in as callbacks, or Tier-0 policy ends up living in subscriber
  files that the guard treats as low-risk.

## The agent-economics claim, precisely

The claim is narrow and worth stating exactly: a task's `Allowed-Paths` and
the files an agent must *read* both shrink. A telemetry or diagnostic agent
writes one file under a new directory, reads only the 30-line `events.h`, and
never opens the 1000-line control loop. Whether that converts to fewer merge
collisions is an empirical question for a later measurement, not a premise to
build on.

## Suggested next step (smallest useful slice)

Do not build the bus. Build the **counting stub**: `Events_Emit` that only
increments a per-event counter in an existing exported struct, wired into one
non-critical site (say `EVT_ESTIMATE_UPDATED`). If agents can then read "how
often did the EKF update fire" through the existing `livewatch`/telemetry path
without touching the loop, the primitive has already paid for itself once, and
the callback table is a much easier sell with that precedent in hand.

**Cure status:** filed; no code yet.