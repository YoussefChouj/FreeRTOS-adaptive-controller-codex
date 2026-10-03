"""Live in-flight PID tuning (WP-28): CMA-ES over rate-loop gains, one candidate per few-second window.

cmaes.py       dependency-free CMA-ES (ask/tell, mirrored sampling, box repair, seeded)
cost.py        per-window cost J from the window's telemetry samples
supervisor.py  per-sample safety checks: trip (revert, infeasible) or stop (revert, end the run)
loop.py        step config and the session state machine the campaign runner ticks
"""
