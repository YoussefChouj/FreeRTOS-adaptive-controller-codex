# Onboard NN: float32 C module (SHADOW / ACTIVE), weight exporter, golden-vector tests

Read AGENTS.md first. This runs inside flight firmware (STM32, Cortex-M4F single-precision FPU, Keil ARMCC 5.06, C99 subset):
quality and exact agreement with the Python model matter more than speed of delivery. New files only:
`API/nn_vel.c`, `API/nn_vel.h`, `API/nn_vel_weights.h` (generated), `API/tests/test_nn_vel_harness.c` (wire it into
`tools/host_tests.py` like the other harnesses), `tools/nn_export.py`, `tests/test_nn_export.py`.
Do not edit nn_io.py, USER/, OBJ/, BSP/ or logs/; the firmware task integration is done separately.

Model (copy the maths from `ground_station/analysis/nn_io.py: build`, kind "gru", head "scale" or "add", layers 1-2):
x_n = (F - mu)/sd on the selected `feats`; h0 = tanh(W_inp x_n + b); PyTorch GRU (gate order r, z, n in weight_ih/hh:
r = sig(W_ir x + b_ir + W_hr h + b_hr), z = same, n = tanh(W_in x + b_in + r*(W_hn h + b_hn)), h = (1-z)*n + z*h);
o = W_out h + b_out; head scale: px = 0.3*o0*vx - 0.3*o1*vy + 0.05*o2, py = 0.3*o0*vy + 0.3*o1*vx + 0.05*o3
(v = EKF body velocity, the raw block means of F columns V_COL 12, 13); head add: p = (o0, o1). Dropout is identity.
Features F (17 channels, NN4_CH order in video_truth.py) are 20 Hz block means: the firmware calls
`nn_vel_accum(const float x[17])` every control tick (NaN -> 0 as in Python), and `nn_vel_step()` once per 50 ms closes
the block (empty block -> last sample), runs one model step, and returns p.

Checkpoint input (written by nn_io.save_model, another task; for your tests build one yourself with `nn_io.build` and
torch.save): `{"format": "nnio-v1", "state_dict", "cfg": {D, kind, H, head, layers, k}, "mu", "sd", "feats", "rate_hz",
"channels", "v_col", "sha256"}`. `tools/nn_export.py <ckpt> -o API/nn_vel_weights.h` writes static const float arrays
(row-major, `f` suffix, full precision via repr of float32), dims as #defines, the channel names as a comment, and the
model hash as a string constant. Reject unsupported cfg (tcn, layers > 2, rate != 20) with a clear error.

Firmware API and safety (no malloc, no VLAs, no recursion, no double maths in the hot path, static state only):
- `nn_vel_reset()` at takeoff (h = 0, block cleared). One step per 20 Hz tick.
- Mode per flight: NN_OFF / NN_SHADOW / NN_ACTIVE. SHADOW computes and logs p but outputs zero correction.
  ACTIVE outputs p clamped to a magnitude cap (direction kept: p * min(1, cap/|p|)); cap set per flight.
- Latch to SHADOW until `nn_vel_reset` on: PID-fallback flag, any NaN/inf in input, state or output, a step later than
  1.5 ticks or missing, |p| above a hard limit before the cap. Record the first latch reason (enum) and the step count.
- Expose for logging: mode, latched reason, p (raw), p_applied, step counter, last step time (us).
Tests: the exporter writes golden vectors (seeded random model + random sequence of 200 steps, including a NaN input and
an empty block) as a C header; the harness runs nn_vel on them and checks max abs error vs torch (state that bound,
target <= 1e-5 relative to output scale); latch tests for every reason; cap keeps direction; reset clears state.
Also report MACs per step, flash bytes of the weights and RAM for H = 32, D = 17 (computed, label them so).
Run `python tools/host_tests.py` and `python -m pytest tests/test_nn_export.py -q` and list results in the PR.
