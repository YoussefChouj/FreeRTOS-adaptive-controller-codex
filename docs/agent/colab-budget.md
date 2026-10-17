# Colab budget playbook (the units must last)

Written 2026-10-17 from six Argon research runs (`.agent-ops/briefs/cb-*.md` -> `.agent-ops/out/argon-cb-*.md`), with the
claims this plan rests on re-checked against the official Colab FAQ and the Google blog the same day. "UNVERIFIED" marks
the rest. The how-to (CLI, recipes) is `docs/agent/colab.md`. Balance on 2026-10-17: **100.00 compute units (CU)**.
The user will not buy more (memory `ultra-only-no-spend`): never run `colab pay`, never open a checkout page.

## Bottom line

1. **Free lanes first.** The laptop and Kaggle cost no units. Colab units are for jobs that need them.
2. **The drift net is CPU-bound today.** `nn_io.py` has no GPU code and trains its folds and seeds one after another,
   so a T4 would mostly wait. CPU runtimes cost about 20x less than a T4. Pay for a GPU only after the code batches many
   models at once and a short probe shows the GPU is cheaper per finished run.
3. **A runtime costs units for as long as it exists, idle or not.** `colab stop` the moment a job ends. End every
   working turn with `colab sessions` showing nothing.

## Facts this rests on

| Fact | Source (quote, date) |
|---|---|
| With units above 0, every runtime draws from the balance; free runtimes are for accounts "without a positive Colab compute unit balance" | research.google.com/colaboratory/faq.html, read 2026-10-17 |
| So the free T4 that worked at 0 CU (`colab.md`) is not free any more while units remain | same FAQ, same line |
| An attached GPU counts against usage even when the code does not use it ("switching to a standard runtime") | FAQ #gpu-utilization, 2026-10-17 |
| Only "Disconnect and delete runtime" returns the VM; a closed tab leaves it running until an idle timeout | FAQ, 2026-10-17 |
| Idle timeout length is not published; "Idle timeouts only apply if code execution terminates" | FAQ, 2026-10-17 |
| Pro and Pay As You Go VMs run "at most 12 hours"; Pro+ up to 24 h | FAQ, 2026-10-17 |
| At 0 units: "backend termination" of the running VM, then the account "will revert to the free-of-charge tier" | FAQ, 2026-10-17 |
| Eligible Google AI plans include "a monthly allotment of Colab compute units" (which plans, and how many, is not stated) | FAQ, 2026-10-17 |
| The user's accounts are NOT offered that allotment (tried several accounts). The 100 CU were bought once and do not refill | user, 2026-10-17 |
| Using a second free Colab account to get free runtimes is disallowed on every managed runtime: "using multiple accounts to work around access or resource usage restrictions" | FAQ, disallowed activities, 2026-10-17 |
| Ultra: "uninterrupted background execution and Premium GPU access" | developers.googleblog.com/colab-is-now-part-of-your-google-ai-plan, 2026-09-22 |
| Monthly plan units sometimes fail to refresh (issue open, 13 comments) | github.com/googlecolab/colabtools/issues/6127, last updated 2026-10-14 |
| Units expire 90 days after purchase | UNVERIFIED (the signup page needs a login). The user can check it at colab.research.google.com/signup |

## Burn rates (CU per hour)

Google does not publish them ("Colab does not publish these limits", FAQ). Community numbers, to be replaced by our own
measurements in the ledger below:

| Runtime | CU/h | 100 CU lasts | Source |
|---|---|---|---|
| CPU (2 vCPU, 13 GB) | 0.07 | ~1,400 h | varlog.info/colab-credit-consumption-comparison-2024, 2024-10-01 |
| CPU high-RAM (8 vCPU, 51 GB) | 0.14 | ~700 h | same |
| T4 | 1.58 (seen 1.19-1.96) | ~63 h | same |
| T4 high-RAM | 1.67 | ~60 h | same |
| L4 | 3.00 | ~33 h | same |
| A100 | ~7.5 (10.6 in 2024) | ~13 h | note.com/sugamore/n/ncea37d829827, 2026-05-23 |
| G4 | ~8.9 | ~11 h | same |
| H100, TPU v5e1 / v6e1 | UNVERIFIED | | |

## Lane order for a training job

| Order | Lane | Cost | Use when |
|---|---|---|---|
| 1 | Laptop CPU (`nn_io.py` as is) | free | up to ~1 h; mind the AC drop under load (memory `laptop-power-fragility`) |
| 2 | Kaggle notebook, CPU or GPU (T4 x2 / P100) | free. Account settings, read by the user 2026-10-17: Kaggle GPU 00:00 / 30 hrs, TPU 00:00 / 20 hrs, private datasets 0 B / 214.75 GB, phone verified. Weekly reset and 12 h a session: community sources, UNVERIFIED | long or unattended runs: `kaggle kernels push`, then `status`, then `output`. SET UP 2026-10-17: Kaggle CLI 2.2.4 in WSL (`~/.local/bin/kaggle`, `uv tool`), token in `~/.kaggle/access_token` (mode 600), user `yousschouj`, auth checked. MEASURED 2026-10-17: the full TCN LOFO with UTIL pretraining (`tools/kaggle_nnio`, tag `_kaggle_lofo_tcn5h_s5`) ran 25.3 min on the free CPU kernel (4 CPUs, torch 2.11 CPU), about 30 min from push to COMPLETE, 0 GPU hours and 0 CU used |
| 3 | Colab CPU runtime (`colab new`, no `--gpu`) | 0.07 CU/h | when the laptop is busy or the run is long, and Kaggle is out of hours |
| 4 | Colab T4 | ~1.6 CU/h | only after the batching change below, and only if the probe says it is cheaper per run |
| 5 | L4 / A100 / G4 / H100 | 3-18 CU/h | not for this model. Only for a big model that does not fit a T4 |

No other free lane fits: SageMaker Studio Lab is a 4 h browser-only session, Google Cloud and Paperspace need a card,
Hugging Face compute Spaces need a paid plan, and Lightning's no-card GPU credit is a small one-off
(Argon `cb-b2-lanes`, mostly from search summaries, UNVERIFIED verbatim).

Second pass 2026-10-17 (Argon `cb-b3-more-lanes`, CEO-checked; the user is at HIT, China, no card, ever):

| Lane | Verdict | Checked |
|---|---|---|
| Modal Starter | $30/month confirmed on modal.com/pricing. Card: the page does not say; third-party sites conflict ($1 at signup + $29 after a card, vs no card). Treat as card-gated | partly |
| Modal for Academics | "up to $10k free compute credits" for graduate students (modal.com/pricing). Card waiver and approval of a `hit.edu.cn` email UNVERIFIED | amount only |
| Lightning AI free tier | ~15 credits/month, phone check, no card (third-party; lightning.ai returned 503). No academic offer found. A Chinese phone number may fail | no |
| Google TRC | TPU only (no CUDA, so no Isaac Lab), and needs a GCP billing account with a card | Argon only |
| Azure for Students | $100 with no card, but 0 GPU quota on the student tier | Argon only |
| NVIDIA Academic Grant / GitHub Pack | faculty PI only / no usable GPU offer. HIT is on the US Entity List | Argon only |
| Thunder Compute students | $20 credit, US universities only: out | yes |
| HIT HPC | Argon's `computing.hit.edu.cn` is the Faculty of Computing site, not an HPC centre (CEO curl). Advisor sign-off and PI billing are typical in China (SYSU 4 CNY/GPU-h), HIT's own rules UNVERIFIED | no |

Verdict: nothing external beats Kaggle without a card. The one real lead is HIT's campus HPC: ask the advisor or the
lab whether it has a GPU allocation, and what it costs.

## Session rules (every Colab job)

1. **Probe first.** Run about 300 iterations on the Colab CPU runtime and on a T4 (each well under 0.1 CU). Cost of a full
   run = CU/h x (probe time x full iters / probe iters). Pick the cheaper lane, not the faster one, unless time matters.
2. **Pack data on D: first.** The UTIL pretraining set is 2.0 GB of CSV (79 files). Convert it locally to one `.npz` of the
   20 Hz features that `util_raw()` keeps, and upload that. Never install or download big things on a paid VM.
3. **One named VM** (`-s drift`), plain size (no `--high-mem` unless 13 GB is too small), `--timeout` on every `colab run`.
4. **Checkpoint and download as you go** (`/content/out/`). A 12 h limit or a 0-unit stop loses what is left on the VM.
5. **Stop it.** `colab stop -s drift` right after the last download. Then `colab sessions` must be empty.
6. **Log it** in the ledger: date, runtime, minutes, `colab usage` balance before and after. Our measured rate replaces the
   community number in the table above.
7. **No refill.** The user's accounts get no monthly allotment, so every unit spent is gone. Still run `colab usage`
   before and after each session for the ledger.

## What the paid units are for

The user bought them for the big jobs: GPU 3D physics simulations to test controller ideas and hypotheses. Those may
need a bigger GPU (L4 / A100) or long uninterrupted runs, which the free lanes do not offer. So small and medium jobs
(the drift net, timing probes) go laptop -> Kaggle, and the units stay saved for the simulations.

## The drift net (`ground_station/analysis/nn_io.py`), checked 2026-10-17

| Fact | Where |
|---|---|
| CPU only: `torch.set_num_threads(4)`, no `.cuda()` or `.to(device)` anywhere | `nn_io.py:251` |
| `fit()`: batch 16 windows of 90 s at 20 Hz (1,800 steps), built in a Python loop each iteration | `nn_io.py:431-464` |
| Folds and seeds run one after another | `nn_io.py:535`, `:543` |
| LOFO with the default 2 seeds: 21 folds (pool 20 + the pool fit) x 2 = 42 fits x 1,500 iters = 63,000 steps | measured 2026-10-17: `load_all()` gives 24 flights (4 skipped), pool 20, hold 3, load 67 s |
| **Laptop baseline (measured 2026-10-17, torch 2.14.1+cpu, 4 threads, defaults):** TCN 0.077 s/iter = 1.9 min a fit = **1.35 h** for LOFO x 2 seeds. GRU 1.40 s/iter = 35 min a fit = **24.5 h** | scratch `time_fit.py` (100 TCN / 30 GRU iters after a 10-iter warm-up). UTIL pretraining time not measured |
| Model ~5-7k parameters (H=32, 1 layer) | `nn_io.py:356-399` |

Before a GPU can help: put the data tensors on the device once, build the window indices on the device (no per-iteration
NumPy copies), and train all seeds (and folds) as one batched model. The GPU details are in the next section.

## GPU techniques for small models

From Argon `cb-c2-gpu` (2026-10-17). The quotes in the first column were re-read on the source pages the same day.

| Point | Source (quote) |
|---|---|
| One tiny model on a GPU wastes it: each step is many small kernel launches waiting on Python. PyTorch says to train such models on the CPU | docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html: "For small scale models or memory-bound models, such as DLRM, training on CPU is also a good choice." |
| Biggest win: train all fold x seed models as one batched model (`torch.func.stack_module_state` + `functional_call` + `torch.vmap`). The tutorial measured 1.40 ms without vmap and 626.57 µs with it, for 10 small models | docs.pytorch.org/tutorials/intermediate/ensembling.html |
| TCN path (our best result): `nn.Conv1d` works under vmap. A grouped `Conv1d(groups=N)` does the same job without vmap | vmap: same tutorial. Grouped conv speed vs vmap: UNVERIFIED |
| GRU path: whether `nn.GRU` (cuDNN) runs batched under vmap is UNVERIFIED (the tutorial does not cover RNNs). Keep the GRU on the CPU, or write the GRU cell by hand as batched matmuls | UNVERIFIED |
| Dropout under vmap needs `randomness="different"` so each model draws its own mask | PyTorch `torch.vmap` API (not re-read today) |
| `torch.compile(mode="reduce-overhead")` uses CUDA graphs to cut launch overhead. Compiling costs paid minutes, so use it only on runs of an hour or more | tuning guide: "CUDA graphs are a way to keep computation within the GPU without paying the extra cost of kernel launches" |
| AMP fp16 on a T4: "up to 3x overall speedup on Volta and newer GPU architectures", but only once batching keeps the GPU busy. H=32 is a multiple of 8, so Tensor Cores apply | tuning guide |
| Several processes sharing one T4 duplicate CUDA contexts and VRAM: worse than one batched model | UNVERIFIED |

Size check for the batched TCN (46 models x 16 windows x 1,800 steps x 32 channels, fp32): about 170 MB per layer of
activations, far below the T4's 16 GB. So one T4 can hold every LOFO fit at once.

Order of work: (1) time one fit on the laptop; (2) batch the models with vmap on the CPU first (that alone cuts the
Python loop); (3) then run the batched code on a free Kaggle GPU. Colab units only if Kaggle cannot do it.

## Ledger (measured, append one line per Colab session)

| Date | Runtime | Minutes | CU before | CU after | CU/h measured | Job |
|---|---|---|---|---|---|---|
| 2026-10-17 | none | 0 | 100.00 | 100.00 | | balance check only |
