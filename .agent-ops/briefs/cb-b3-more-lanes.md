# Research: more no-card compute lanes, incl. student and academic programs (web only)

Context: a graduate student doing quadcopter adaptive-control research (PyTorch drift net now, GPU 3D physics sims
later: MuJoCo MJX / Isaac Lab / gym-pybullet-drones). They pay for Google AI Ultra only and will NOT add a card or pay
anything anywhere. Already covered, skip: Kaggle, Colab, SageMaker Studio Lab, Paperspace, Hugging Face, GCP $300 trial,
Lightning base free tier. Paid marketplaces (Vast.ai, RunPod, AutoDL) are out. A pasted summary claims the items below;
verify or refute each from the provider's own pages. Do NOT spawn subagents; at most 10 web fetches. Give each claim as:
claim, URL, quote under 15 words, source date. Mark anything unsourced UNVERIFIED. Answer in at most 40 lines.

Claims to check:
1. Modal Starter: "$30/month free compute", "no credit card required", resets monthly. Is a card needed to get or use
   the $30? GPU types. Can a local CLI start jobs unattended (`modal run`)?
2. Modal for Academics: "up to $10,000" credits for grad students. Eligibility, how to apply, card needed?
3. Lightning AI academic: "30 free credits every month (up to 80 GPU hours)" with a university email, "80% off Pro".
   Credits per month, card or phone needed, does a Studio persist installed packages between sessions?
4. Google TPU Research Cloud (TRC): "100% free", 30-90 day grant. Does it need a Google Cloud billing account with a
   card? Which charges fall outside TRC (VM disks, storage, network)? PyTorch support (PyTorch/XLA) vs JAX only.
5. Azure for Students: "$100 free", card needed? Can a student subscription get any GPU VM quota at all?
6. Any other no-card program worth one line: GitHub Student Developer Pack compute offers, NVIDIA Academic Grant
   Program (hardware/cloud credits for researchers).

End with a table: lane | card needed | free amount | GPU | unattended CLI | catch. Then a one-line verdict: which, if
any, add real free capacity beyond Kaggle for (a) the drift net and (b) GPU 3D physics sims.

Added 2026-10-17 (user): they are an international graduate student at Harbin Institute of Technology (HIT), China,
with a university email; HPC access unknown. 7. Does HIT have a high-performance computing center (HPC / 高性能计算中心)
that gives graduate students free GPU time? How to apply, GPU types, Slurm/Apptainer? 8. For items 1-6, flag any that
refuse sign-up from China or need a non-Chinese student email.
