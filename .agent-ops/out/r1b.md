STATUS: done
FILES CHANGED: none (web research task, strictly no code changes)
VERIFICATION: curl and grep checks passed for all quotes.
OPEN QUESTIONS: Hardware integration not verified (no hardware).

## Topics
### A. Controller plug-in interfaces
Bumpless transition | Smooth transition via integrator reset | https://ardupilot.org/dev/docs/copter-adding-custom-controller.html | "the main controller reset function is called" | verified
RC switch integration | Safe in-flight fallback | https://ardupilot.org/dev/docs/copter-adding-custom-controller.html | "switching between main and custom controller with RC switch" | verified
Axis selection | Targeted control application | https://ardupilot.org/dev/docs/copter-adding-custom-controller.html | "Bitmask to choose which axis to use the custom controller output" | verified
### B. MRAC robustness practice
Projection operator | Bounding adaptation gains | https://arxiv.org/abs/1112.4232 | "The projection algorithm is frequently used in adaptive control" | verified
L1 Adaptive modification | Robustness to unmodeled dynamics | https://arxiv.org/abs/2004.00152 | "L1 adaptive controller robustifies the architecture" | verified
Dead-zone modification | Mitigating noise | https://arxiv.org/abs/1609.03016 | "assistance of a dead zone-like modification of the update law" | verified
### C. Flight test comparison metrics
Tracking RMSE | Quantifies overall error | https://arxiv.org/abs/2607.00024 | "yields a mean payload-tracking RMSE of" | verified
Predictive tuning RMSE | Measures disturbance rejection | https://arxiv.org/abs/2608.23887 | "predictive tuning reduces position tracking RMSE" | verified
Control effort TV | Trade-off against tracking accuracy | https://arxiv.org/abs/2512.13170 | "minimize key performance indicators (KPIs) related to tracking accuracy, control effort" | verified

Recommended interface fields and metrics to adopt:
1. `controller_reset()`: Adopt bumpless transition to avoid jerky motion (Row 1).
2. `rc_switch_channel`: Adopt safe fallback to baseline PID via RC (Row 2).
3. `axis_mask`: Adopt bitmask to apply MRAC selectively per axis (Row 3).
4. `projection_bound`: Adopt parameter bounds to prevent gain drift (Row 4).
5. `dead_zone_limit`: Adopt dead-zone to ignore sensor noise (Row 6).
6. `tracking_rmse` & `control_effort`: Adopt RMSE and TV to quantify performance vs actuation cost (Rows 7 & 9).
