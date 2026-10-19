# Research: how Google Colab compute units are billed in 2026 (read-only: web)

Context: the user bought Colab compute units themselves. `colab usage` (google-colab-cli 0.7.4) shows "Current balance:
100.00 compute units, Usage rate 0.00/hr, Active assignments 0" on 2026-10-17. We do not know yet whether this is a
Colab Pro monthly plan or Pay As You Go. The user will not buy more, so the 100 units must last as long as possible.
The CLI can start: CPU (default), `--high-mem` (Pro/Pro+ only), `--gpu T4|L4|G4|H100|A100`, `--tpu v5e1|v6e1`.

Answer each item with an official Google source if one exists (colab.research.google.com/signup, the Colab FAQ,
research.google.com/colaboratory/faq.html, Google support pages, the colabtools GitHub), else a dated community source
from 2025-2026. Prefer the newest. Give each claim as: claim, URL, quote under 15 words, date of the source.

1. Burn rate in compute units per hour for each runtime: CPU standard, CPU high-RAM, T4 (standard and high-RAM), L4,
   G4, A100 (40 and 80 GB if both), H100, TPU v5e1, v6e1. Give the official numbers if they exist, plus measured ones.
2. Expiry: do Pay As You Go units expire (90 days?), do Pro monthly units roll over or reset, what happens to unused
   units if a plan is cancelled. Where in the Colab UI can the user see the plan type and when units expire?
3. Billing: is a connected but idle runtime charged at the full rate? Billing granularity (per second, minute?). Does
   "disconnect" stop the charge, or only "disconnect and delete runtime" / terminate? Idle timeout and maximum runtime
   life for Pro and for Pay As You Go. Is background execution (runs after the browser closes) Pro+ only?
4. When the balance hits 0: is a running VM killed, and does the account fall back to the free tier?
5. With units above 0, does a T4 still come free (free-tier quota), or does every GPU session draw from the balance?
   Can a paying user pick a free runtime on purpose to save units?
6. Known ways people burned units by mistake (forgotten sessions, high-RAM by default, and so on).

Answer in at most 40 lines. Mark anything you could not source as UNVERIFIED. End with a one-line verdict: the three
rules that save the most units.
