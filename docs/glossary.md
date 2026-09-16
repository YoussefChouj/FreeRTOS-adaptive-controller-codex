# Glossary — ubiquitous language

Terms that carry a precise, project-specific meaning. When these words appear in ADRs,
contracts, code, or agent prompts, they mean exactly what is written here and nothing
looser. Started 2026-08-05 during the ADR-0012 / ADR-0013 design session.

Existing terms defined elsewhere are cross-referenced rather than duplicated:
identified plant structure and `K`/`p`/`T` live in [`sysid_results.md`](sysid_results.md);
the unit chain and module map live in [`../sim/README.md`](../sim/README.md).

---

## Plants and fidelity

**Plant** — anything implementing the ADR-0006 D3 seam `step(u_dict) -> state_dict`, with
`u_dict` in SI torque (Nm) / thrust (N) and `state_dict` carrying at minimum the rate
keys. The controller and adaptive law never know which plant is behind it.

**Plant ladder** — the ordered set of plants of increasing physical reality used to stage
validation: `IdentifiedPlant → MujocoPlant → RigPlant → free flight` (ADR-0012 D4). A
result is described by *how far up the ladder it has been reproduced*.

**Gain-matched plant** — a plant whose lumped input→output gain `K` equals that of the
target hardware, verified by running `sysid_analysis.py` against it (ADR-0012 D5). Only
gain-matched plants may produce priors intended for that target. `IdentifiedPlant` is
gain-matched *by construction* because it **is** the identified model.

**Lumped input→output gain (`K`)** — the `K` in `G(s) = K/(s(1+s/p))·e^{−sT}`. It couples
mixer torque effectiveness with airframe inertia and is **not** a physical inertia. This
is why weights are not portable across plants (see *plant-tagged prior*).

**Oracle** — a second, independent implementation kept specifically to cross-check the
primary one. `RigidBodyPlant` is the oracle for `MujocoPlant` (ADR-0012 D3). An oracle is
never the plant of record for a published result.

**RigPlant** — the model of the borrowed constrained test rig: fixed base, free in roll,
pitch, yaw and z. Adds pivot friction and rig inertia, removes thrust-attitude coupling,
and therefore has its own `(K, p, T)` distinct from free flight. Not a lower-fidelity
free-flight model — a *different* plant.

**Transport delay buffer** — the integer `N = round(T/dt)` sample ring buffer at the
actuator input. Mandatory on any plant used for prior learning (ADR-0012 D6). Its absence
makes a sim falsely stable and the resulting weights over-confident.

**Delay wrapper** — the engineering synonym for *transport delay buffer*; the reusable
actuator-input wrapper applied to every 6-DOF plant (ADR-0012 D6). Lifted out of the
`_AxisSim` reference implementation so that `RigidBodyPlant`, `MujocoPlant`, and
`RigPlant` all share one delay model.

---

## Priors and transfer

**Prior** (also **`Θ_prior`**) — a weight vector learned in simulation for one scenario on
one plant, supplied to a later run as a starting point or soft target. Not a
hyperparameter and not a gain: it lives in the same space and units as `What`.

**Plant-tagged prior** — a prior recorded together with the plant identity and the
`(K, p, T)` it was learned under (ADR-0012 D8). Applying a prior across plants without a
stated scaling is a defect.

**Plant tag** — the metadata block recording plant identity + `(K, p, T)` carried by
every prior. In ADR-0014 D1–D2 the plant tag is rescaling metadata, not a transfer
barrier: the dimensionless form `Θ̃` is the canonical stored prior; deployment on any
target is `Θ = Θ̃ / K_target`.

**Prior library** — the corpus of plant-tagged priors indexed by scenario, produced by the
prior factory. The artefact the runtime detector indexes into.

**Prior factory** — the deterministic pass that produces the library: for each scenario,
run it, let the existing adaptive law converge, record `Θ_final`. Contains no machine
learning; **the MRAC is the learner** (ADR-0013 D8).

**Mismatched-prior damage** — the measured harm from applying scenario A's prior while
actually in scenario B. A required experiment, because "a confidently wrong prior is worse
than no prior" is this thesis's honest failure mode.

---

## Runtime selection and injection

**Scenario** — a named, parameterised perturbation of the plant or the command
(`inertia_offset`, `disturbance_rejection`, …). In this project a scenario is what the
prior is *conditioned on*, so every scenario must in principle be distinguishable from
onboard telemetry.

**Mismatch signature** — the feature vector used to identify the active scenario at
runtime, built from windowed statistics of quantities the loop already computes: `e`, `ė`,
`Φ`, `u_nom`, `u_ad` (ADR-0013 D3). Requires no new sensors.

**Scenario centroid** — a stored prototype in mismatch-signature space, paired with one
prior. The `K` centroids are what soft attention computes similarity against.

**Soft attention (here)** — softmax over similarity between the current mismatch signature
and the `K` scenario centroids, yielding a convex blend of the `K` priors. Approximately
`K·D` MACs, affordable on an STM32F4 at 200 Hz. Not a transformer.

**Injection channel** — one of three orthogonal ways a prior can influence the controller
(ADR-0013 D4). They compose:

*   **Value channel** — `σ_prior` attractor `−σ_prior·(Θ − Θ_prior)`; determines *where
    the weights are pulled toward*.
*   **Authority channel** — additive feedforward `Θ_priorᵀΦ` with the adaptive law left
    bit-identical; determines *how much control the prior exerts directly*.
*   **Envelope channel** — scenario-conditioned `Γ`, `What_limit`, `e_deadzone`;
    determines *worst-case adaptation bounds*.

**`σ_prior`** — the leak rate toward the prior. Spans the whole design space:
`0` = today's baseline, `∞` = hard initialisation, intermediate = soft target. Distinct
from `σ_eff`, which is doing robustness duty and must not be repurposed.

**σ_prior attractor** — the non-zero fixed point `Θ* = Θ_prior` introduced by the value
channel `−σ_prior·(Θ − Θ_prior)`. UUB is preserved (Lyapunov argument carries over from
σ-mod); the basin shifts from `‖Θ‖` to `‖Θ − Θ_prior‖`. See ADR-0013 D5.

**Envelope** — the per-scenario bound set (`Γ`, `What_limit`, `e_deadzone`) that caps what
the adaptation is permitted to do. A safety result, reportable independently of whether
the value or authority channels improve tracking.

---

## Method and workflow

**Search bench** — the role of simulation in this project: cheaply explore controller
variants and produce priors. Optimised for throughput and determinism, *not* fidelity.
Contrast with **evidence**, which comes from the rig and free flight.

**Serial gate** — a check that must complete before parallel work fans out, because its
outcome can invalidate everything downstream. Current instance: the `What_lower_limit`
sign constraint (ADR-0013 D10).

**Agent-as-experimentalist** — the delegation boundary (ADR-0013 D9). Agents implement
variants, author scenarios, read `metrics.json`, and propose design-level next steps.
Numeric search is seeded deterministic code, never an LLM choosing values, because a
search procedure must be reproducible by a reader to be defensible.

---

## Dimensionless transfer (ADR-0014)

**Dimensionless prior (`Θ̃`, also written `Theta_tilde`)** — the canonical stored form of a prior. Because the matching
condition `K·Θ*ᵀΦ = −Δ` puts a `1/K` in every weight, a raw `Θ` in firmware command units
is plant-specific and cannot transfer. `Θ̃` divides the plant scales out; the raw `Θ` and
the plant's `(K, p, T)` are stored **alongside** it, never instead. Deployment on any
target is `Θ = Θ̃ / K_target`, per-slot.

**Declared basis dimension** — the physical dimension and normalisation scale carried by
each basis function as *data*, so `Θ̃` is computed mechanically rather than from a
hand-written per-slot table. Scales are symbolic references to config fields (`u_max`,
`J`, `mrac_to_mixer`, `e_sat`, `ref_model_bw`), never numeric literals — a hardcoded scale
would be plant-specific, defeating the purpose. This is what keeps regressor design open:
the conversion works for basis functions that do not exist yet.

**Regressor variant** — a named, registered `Φ` definition: an ordered slot list plus its
`NUM_BASIS`. The variant matching the compiled firmware is the **pinned baseline** and
keeps the golden-vector parity test; others are sim-only until promoted by their own spec.
Every prior records the variant that produced it, exactly as it records the plant tag.

**Characteristic rate** — the rate scale used to non-dimensionalise `x`. Needed because
`x·tanh x` carries an implicit 1 rad/s scale, so a vehicle operating at 20 rad/s and one at
5 rad/s sit on different parts of the curve and that slot does not transfer without it.
`e_sat` is the standing candidate; open in ADR-0014.

**Relative scenario parameterisation** — stating scenario magnitudes as fractions
(disturbance over `u_max`, inertia offset over `J`, aggression over achievable angular
acceleration) rather than absolutes. Mandatory: a 100 g offset mass is a 3× overload on a
33 g Crazyflie and a 7.7 % trim nuisance on JX_FLY, so under absolute magnitudes the same
scenario name denotes different physics per airframe. Non-dimensionalising the prior
without also non-dimensionalising the scenario is worse than doing neither.

**Airframe ensemble** — the distribution of plants used to test invariance, generated by
seeded runtime randomisation of `mujoco.MjModel` (`body_mass`, `body_inertia`, `site_pos`,
actuator `gear`), plus Menagerie's two real quadrotors as external-validity members.
Randomisation, not selection: closeness in mass or appearance is not closeness in `K` —
Skydio X2 is within 2 % of JX_FLY's mass with a 30 % pitch-arm error.

**Transfer matrix** — for a scenario, the grid of (learn on plant A → apply on plant B)
outcomes. Uninterpretable without both bracketing references: the no-prior baseline and
B's own learned prior.

**Damage threshold** — the plant mismatch (e.g. `K_A/K_B`) beyond which a transferred prior
performs worse than no prior at all. The deployable safety statement, and the quantified
form of *"a confidently wrong prior is worse than no prior."*
