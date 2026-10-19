# Jules task j-lab-thesis: thesis flights for lab day 2026-10-18 (Python + .fly + docs only, no firmware)

Repo rules: AGENTS.md. Base branch overnight-2026-10-08. Do not touch API/, TASK/, USER/, OBJ/.
Context: thesis = configurable MRAC blocks (block 1 physics regressor, block 2 time-scale gate, block 3 feedforward).
Two questions the lab must answer: (1) does today's MRAC FORGET a condition after flying another one (the premise
for building the block-2 gate, which freezes inactive experts)? (2) which vertical forces are identifiable on Z
(ground effect, vertical drag, battery sag, tilt loss) so they can become Z block-1 features?

## Deliverables
1. `ground_station/service/campaigns/lab_m_memory_u100.fly` and `lab_m_memory_u0.fly`, same script, A->B->A:
   takeoff 0.8 m, hold 30 s (A1, hover), Gerono figure8 a60 fast laps for about 30 s (B, reuse the
   lab_h_speed_ladder_l1 settings), hold 30 s (A3), land. u100 uses `mrac_injection=100`, u0 uses `mrac_injection=0`
   (check the accepted values in fly_file.py). Airborne time must stay under the 120 s firmware cap with margin
   (target <= 105 s incl. takeoff and landing; compute it). Log groups must include mrac_3l and camera_fusion.
   Header comment: purpose, the pre-registered rule (item 3), "UNVERIFIED: never flown".
2. Z identification flights, using ONLY verbs and VAR_WRITE setpoints the parser already supports
   (ground_station/service/fly_file.py, campaign_fly.py, campaign_runner.py):
   - `lab_z_ground_effect.fly`: hover holds at several heights (target 0.15, 0.3, 0.5, 0.8, 1.2 m, about 12 s each,
     zero XY motion). If the DSL cannot change height mid-flight, make one file per height (`lab_z_ge_h015.fly` ...)
     and say so in the PR body.
   - `lab_z_vz_steps.fly`: vertical-speed steps (about +/-0.2 and +/-0.4 m/s) between 0.6 and 1.4 m, outside ground
     effect. If the DSL cannot command vertical speed or height steps, do NOT add parser or firmware features: write
     the file as a comment-only stub that explains the gap, and list it in the PR body.
   All Z files: `mrac_injection=0` so the logged thrust is the nominal controller only; log thrust_model,
   velocity_loops, camera_fusion; airborne time under 105 s each.
3. `ground_station/analysis/memory_premise.py` (CLI `python -m ground_station.analysis.memory_premise <sessions...>`):
   per session find segments A1, B, A3 from the logged trajectory phase (or from script timing if there is no phase
   flag) and compute: A1 steady position-error rms (last 10 s of A1); A3 transient peak and rms over its first 5 s;
   re-convergence time until a 2 s rolling rms <= 1.2 x A1 steady rms; weight drift during B =
   ||W(end B) - W(end A1)|| / ||W(end A1)||. Group by arm (u100 vs u0): mean, spread (max-min), n.
   Pre-registered decision, printed verbatim: "FORGETTING PRESENT if, in u100, A3 transient rms exceeds A1 steady rms
   by >= max(15%, 2 x spread) AND weight drift during B >= 10%, AND u0 does not show the same excess. Otherwise the
   block-2 memory experiment would be uninformative (useless-test case)." Needs n >= 3 per arm, else print
   "INSUFFICIENT n".
4. Tests (pytest): every new .fly parses with the existing parser and passes
   `python -m ground_station.analysis.traj_metrics check <file>` (or the existing equivalent); airborne-time
   estimate <= 105 s; memory_premise on synthetic sessions (one with a forgetting spike, one without, one with
   n < 3) gives the three expected verdicts. `ruff check` clean on new files.
5. Append a section `## Thesis flights (added 10-18)` to `docs/lab/run-sheet-2026-10-18.md`: a table with command,
   question answered, decision rule (PROPOSED), and this priority order with a cut line after about 26 flights:
   Step 0 (reflash + ladder bench) -> M1-M6 interleaved u100,u0,u100,u0,u100,u0 -> Z ground effect -> Z vz steps ->
   S1-S3 (sysid) -> torque-injection sysid if that firmware PR is merged -> H0-H2 -> E1-E4 -> I1 drain run at the
   end of each pack -> A1-A12 subset. Mark every number PROPOSED unless measured. Note which flights need firmware
   that is not flashed yet.

## Acceptance
No C changes. Scoped pytest on the new tests passes; ruff clean; PR body lists any DSL gap found and the computed
airborne time per file.
