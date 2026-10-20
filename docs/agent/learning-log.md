# Learning log: the operator's break points

One line per gap found while coaching (`docs/agent/engineering-rigor.md`): date | skill # | topic | the gap | fixed?
Skill numbers: 1 formulation, 2 constraints, 3 anti-patterns, 4 coupling, 5 model fidelity, 6 V-model verification.

| Date | Skill | Topic | Gap | Fixed? |
|---|---|---|---|---|
| 2026-10-20 | 1, 6 | velocity feed-forward | Thought the FF was a constant tuned to the figure-8 (it is the reference's own derivative, every tick). The real risk is that FF can hide a weak feedback loop; test with FF on/off and disturbance runs. | explained 2026-10-20 |
