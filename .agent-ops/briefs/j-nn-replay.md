# NN offline replay: any-slot loader, saved checkpoints, drift report for the debrief

Read AGENTS.md first. Files: `ground_station/analysis/nn_io.py` (you own all changes to it), new
`ground_station/analysis/nn_replay.py`, tests in `tests/test_nn_io.py` and a new `tests/test_nn_replay.py`, a small hook in
`ground_station/analysis/flight_debrief.py`. There is no `logs/` on your VM: every test uses synthetic slot CSVs written
to tmp_path. Torch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`; torch tests use
`pytest.importorskip("torch")`.

Background. The model input is the 17 channels of `NN4_CH` (video_truth.py: 12 in slot 1, 5 in slot 2), turned by
`nn_io.seq` into causal 20 Hz block means (`RATE_HZ`, window 1/RATE_HZ, NaN -> 0, empty block -> last sample).
`fit` returns `(net, mu, sd, feats)`; `predict` gives the body-frame velocity residual p (N, 2) m/s; `corrected` integrates
it onto the EKF track. Training logs had slot 1 at 100 Hz and slot 2 at 50 Hz. New lab flights may carry the channels in
any slot at any rate (20 to 200 Hz), and nothing saves a trained model yet.

1. Any-slot loader: find each NN4_CH channel by column name in whichever `<stem>.slot<k>.csv` has it (first match wins,
   report which slot and the measured rate per channel), keep the drone clock (t_src_ms), and build the same 20 Hz block
   grid. Missing channel or rate < 20 Hz -> a clear error naming it. Existing callers (`seq`, `nn4_data` users) keep
   their results bit-for-bit on the old slot layout: add a regression test for that.
2. Rate invariance test: one synthetic signal set sampled at 25, 50 and 100 Hz gives block-mean features that agree
   within a stated tolerance (derive it, e.g. from the signal bandwidth), and identical predictions within it.
3. Checkpoint format: `save_model(model, path, meta)` / `load_model(path)` in nn_io, one torch file holding
   `{"format": "nnio-v1", "state_dict", "cfg": {D, kind, H, head, layers, k}, "mu", "sd", "feats", "rate_hz",
   "channels": the 17 NN4_CH names in F order, "v_col": V_COL, "sha256": hash of the weights+norm arrays}`.
   `train` gains `--save <path>`. Loading checks the format, hash and channel list. The onboard C exporter (separate
   task) reads exactly this format, so keep the keys as written.
4. `python -m ground_station.analysis.nn_replay <stem> --truth <csv t_s,x_m,y_m> --ckpt <file> [--out dir]`
   (also accept a truth4 flight name). Report in `nn_replay.md` + `nn_replay.json`: EKF-only vs NN-corrected drift
   against truth (use `drift_score` and the existing d60 metric), residual |p| p50/p90/p99 and max (these set the onboard
   ACTIVE cap), fraction of steps above 5 cm/s, and two PNGs (xy tracks; |p| and drift vs time). Exit non-zero with a
   clear message on a channel or hash mismatch.
5. flight_debrief: if a flight directory has the slot CSVs, a truth CSV and a configured checkpoint, add an "NN shadow"
   section from nn_replay; otherwise skip silently. Its existing tests stay green.
Do not touch USER/, OBJ/, API/ or logs/. Run `python -m pytest tests/test_nn_io.py tests/test_nn_replay.py
ground_station/analysis/tests/test_flight_debrief.py -q` (and any debrief test file you touch) and list results in the PR.
