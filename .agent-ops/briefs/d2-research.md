# D2 - research brief (web + academic; READ-ONLY on the repo; output one markdown digest)

Context: STM32F4 quadrotor (~micro size, indoor, EKF + optical flow + phone-camera truth), cascaded control:
position outer loop -> attitude -> rate. Nominal controllers selectable: PID, LQR, MPC, ADRC. Thesis: a
configurable multi-time-scale MRAC layer that is controller-agnostic (wraps any nominal). 10-21 flights: MRAC on
PID made no visible difference; LQR/MPC/ADRC (+MRAC) oscillated. Repo docs: docs/ (grep MRAC, thesis), firmware TASK/.

Answer with QUOTED sources (paper title, authors, year, venue, arXiv/DOI, the exact sentence you rely on).
Prefer peer-reviewed / authoritative (Lavretsky & Wise "Robust and Adaptive Control", Narendra & Annaswamy,
Hovakimyan L1, NASA/Boeing flight tests, Neural-Fly O'Connell et al. Science Robotics 2022, Mellinger & Kumar,
Faessler, Kaufmann, Tal & Karaman). Mark anything you could not source UNVERIFIED.

1. MRAC augmentation of a non-PID baseline (LQR, MPC, ADRC): the reference model should equal the nominal
   closed loop (Lavretsky "baseline + adaptive augmentation"). How is the reference model / error dynamics /
   Lyapunov P matrix built from the baseline (A - B K for LQR)? What happens if the reference model does not
   match the baseline closed loop (adaptive layer fights the baseline)? MPC and ADRC as baselines: known results,
   pitfalls (ESO already estimates the lumped disturbance -> adaptation redundant/conflicting?).
2. Where to put the adaptation in a cascade: outer (position/velocity) loop only, inner (attitude/rate) only, or
   both; time-scale separation conditions; published quadrotor comparisons and which won and why.
3. Neural-Fly (O'Connell 2022) and similar learned-residual-force methods: are pretrained weights public (repo
   URL, licence, input features, network size, output units)? Can they transfer to a micro quad (different mass,
   props), and what is the published fine-tune / online-adaptation recipe (DAIML, last-layer adaptation)?
4. Trajectory feasibility: for a figure-8 (lemniscate) of lobe radius r at period T, peak accel / tilt formula;
   what tilt and accel budgets do small indoor quads use; how tracking error scales with speed when the position
   loop bandwidth is ~0.3-0.5 Hz and the estimate has ~100 ms latency.
5. System identification of quadrotor dynamics for simulation: recommended model structure (rate loop as 1st/2nd
   order + delay, translational drag), validation metrics (VAF/NRMSE on held-out flights, residual whiteness),
   and minimum excitation (chirp / PRBS / doublets).

Output: write .agent-ops/out/d2-research.md (under 300 lines): a 5-line bottom line, then one section per question
with a recommendation for this drone, then the source list. Last line: `D2 DONE` or `D2 BLOCKED: <why>`.
Do not edit or commit repo files other than that output.
