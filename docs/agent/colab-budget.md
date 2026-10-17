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
| 2 | Kaggle notebook, CPU or GPU (T4 x2 / P100) | free, ~30 GPU h a week (community source, UNVERIFIED), 12 h a session (kaggle.com/docs/notebooks) | long or unattended runs: `kaggle kernels push`, then `status`, then `output`. Needs a Kaggle account with a verified phone and an API token, which only the user makes |
| 3 | Colab CPU runtime (`colab new`, no `--gpu`) | 0.07 CU/h | when the laptop is busy or the run is long, and Kaggle is out of hours |
| 4 | Colab T4 | ~1.6 CU/h | only after the batching change below, and only if the probe says it is cheaper per run |
| 5 | L4 / A100 / G4 / H100 | 3-18 CU/h | not for this model. Only for a big model that does not fit a T4 |

No other free lane fits: SageMaker Studio Lab is a 4 h browser-only session, Google Cloud and Paperspace need a card,
Hugging Face compute Spaces need a paid plan, and Lightning's no-card GPU credit is a small one-off
(Argon `cb-b2-lanes`, mostly from search summaries, UNVERIFIED verbatim).

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
7. **Watch for a monthly top-up.** Run `colab usage` at the start of each Colab day. A jump at a month boundary means the
   Ultra plan adds a monthly allotment (FAQ). Then use that allotment each month instead of saving it.

## The drift net (`ground_station/analysis/nn_io.py`), checked 2026-10-17

| Fact | Where |
|---|---|
| CPU only: `torch.set_num_threads(4)`, no `.cuda()` or `.to(device)` anywhere | `nn_io.py:251` |
| `fit()`: batch 16 windows of 90 s at 20 Hz (1,800 steps), built in a Python loop each iteration | `nn_io.py:431-464` |
| Folds and seeds run one after another | `nn_io.py:535`, `:543` |
| LOFO with the default 2 seeds: about 23 folds x 2 = 46 fits x 1,500 iters = 69,000 steps | Argon `cb-c1-repo` (fold count from the pool, not run) |
| Model ~5-7k parameters (H=32, 1 layer) | `nn_io.py:356-399` |

Before a GPU can help: put the data tensors on the device once, build the window indices on the device (no per-iteration
NumPy copies), and train all seeds (and folds) as one batched model. The GPU details are in the next section.

## GPU techniques for small models

UNVERIFIED until Argon `cb-c2-gpu` is read and added here.

## Ledger (measured, append one line per Colab session)

| Date | Runtime | Minutes | CU before | CU after | CU/h measured | Job |
|---|---|---|---|---|---|---|
| 2026-10-17 | none | 0 | 100.00 | 100.00 | | balance check only |
