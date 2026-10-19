# Research: get the most training per Colab compute unit for a tiny sequence model (read-only: web + this repo)

Context: the job is the drone drift net in `ground_station/analysis/nn_io.py` (PyTorch). Models: GRU or TCN, hidden 32,
1-2 layers (`build()`), trained by `fit()` / `train()` with leave-one-flight-out over 28 flights at 20 Hz (truth files
about 112 KB in total) plus a pretraining set `util_load()` (UTIL_DIR). Typical run: iters 1500, 2-3 seeds, per fold.
We have 100 Colab compute units and want them to last. A T4 costs about 2 units an hour (UNVERIFIED, we will measure).

Part 1 (repo). Read `nn_io.py` around `fit()`, `train()`, `seq()`, `load_all()`, `util_load()`. Report with file:line:
batch size and how a batch is built, whether one step processes one flight or many, how many forward passes a full
`train` run makes, device handling (`.to(device)`? cuda?), and what part is Python-loop bound. Estimate the work per
run (iters x folds x seeds x sequence length) and say whether a GPU can even be busy with it.

Part 2 (web). For models this small, is a T4 faster than a laptop CPU? Cite measured benchmarks or maintainer
statements on small RNNs / TCNs on GPU vs CPU (kernel launch overhead, cuDNN GRU at small batch).

Part 3 (web). Ways to fill the GPU with small models, with sources: train many seeds and folds at once as one batched
model (`torch.func.stack_module_state` + `vmap`, grouped conv for a TCN, or N processes sharing one GPU), larger
batches, AMP fp16 on T4, `torch.compile` on T4, CUDA graphs. Which gives the biggest win here, and what does it need
in `nn_io.py`?

Part 4. Session hygiene: what wastes paid minutes (pip installs, data upload, cold start, idle VM) and how to cut it.
Is a short timing probe first (CPU runtime vs T4, a few hundred iters) the right way to choose the hardware?

Answer in at most 40 lines. Mark anything you could not source as UNVERIFIED. End with a one-line verdict: run it on the
laptop, on a Colab CPU runtime, or on a T4, and the one code change that most raises the work done per unit.
