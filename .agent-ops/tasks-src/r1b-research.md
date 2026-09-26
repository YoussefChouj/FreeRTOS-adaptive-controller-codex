# R1b — Web research, strict sources (no code changes)

A previous run cited bare domains ("https://px4.io"). That is rejected. Rules:
- Every row needs a FULL deep URL (path to the exact page, e.g. https://docs.px4.io/main/en/...), and a verbatim quote
  under 25 words that appears on that page. Before writing the row, run `curl -sL --max-time 20 <url> | grep -F "<6+ word
  fragment of the quote>"`; keep the row only if grep matches. Put `verified` or `unverified` at the end of each row.
- Prefer primary sources: docs.px4.io, ardupilot.org/dev or copter docs, github source files, arXiv/AIAA/IEEE abstracts.

Context: STM32F407 quadrotor, FreeRTOS, cascaded PID (angle outer, rate inner) + MRAC on rate loop. Planned variants:
structured MRAC, RBF-NN MRAC, 3-layer adaptive stack. Need a runtime-selectable controller interface in C89.

## Topics
A. Controller plug-in interfaces: ArduPilot AC_CustomControl (CC_TYPE, CC_AXIS_MASK, backend init/update/reset,
   how it hands off from/to the main controller, bumpless reset), PX4 module/controller selection. Interface shape and
   what they log on switch.
B. MRAC robustness practice: projection operator, sigma-mod, e-mod, dead-zone, adaptation-rate and parameter bounds,
   fallback to baseline PID. Lavretsky & Wise, Hovakimyan & Cao (L1), flight-test papers on quadrotor MRAC / RBF-NN.
C. How adaptive controllers are compared in flight test: metrics (tracking RMSE, control effort / TV, adaptation
   transient, settling), test inputs (doublets, chirps, step), repeatability.

## Output
`.agent-ops/out/r1b.md`, under 90 lines. Per topic 3-6 rows `pattern | benefit here | URL | "quote" | verified`.
End with a 6-line list: the interface fields and metrics you would adopt, each tied to a row above.
Commit the digest on your branch.
