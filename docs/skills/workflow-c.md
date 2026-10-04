# Workflow C: fly, debrief, recommend, repeat

Workflow B flown one flight at a time, with a debrief after every landing. The skill is
`.claude/skills/workflow-c/SKILL.md` (invoke `/workflow-c`); the analysis is
`ground_station/analysis/flight_debrief.py` (tests: `ground_station/analysis/tests/test_flight_debrief.py`).

| step | who | what |
|---|---|---|
| launch | agent | one-flight fly campaign (`next.yaml`) via `campaign_launch`, preflight, checklist |
| arm + go | operator | arms by RC, says go; agent calls `campaign_go` |
| debrief | agent | `python -m ground_station.analysis.flight_debrief <outputs_dir> --run logs/workflow-c/<run>` |
| brief | agent | sends `debrief.md` + `plots/tracking.png` + `plots/analysis.png`, bottom line + findings |
| decide | operator | picks the next flight (proposed / repeat / other / stop) |
| tune | agent + operator | accepted gain change as a `run_plan` CMD 0x01 step on the ground; operator approves in the queue |

The debrief measures the HOVER hold (`campaign_outputs.flight_metrics`), adds attitude and rate spectra, rate
tracking error, per-motor means, telemetry gaps and the state/trip timeline, then runs the `RULE_ROW` table
(thresholds PROPOSED, not yet checked against flown data). Each finding names its evidence, why it matters, the
recommendation, and a bounded gain step (15 %, clamped to the `PID_CMD_ROW` bounds) when a CMD 0x01 gain can
address it. `history.jsonl` lets the next debrief judge each change: better / worse / no clear effect at a 10 %
margin on the metric the change targeted; a worse change is reverted on the next flight.

Next flight order: revert a change that made things worse; else A/B the top gain change on the same flight;
else repeat after a blocking fix; else the next hover ladder rung (z 0.5/20 s, 0.7/20 s, 1.0/30 s, 1.3/40 s);
after the ladder, the `pid_ref` or `livetune_rate_rp` campaign.
