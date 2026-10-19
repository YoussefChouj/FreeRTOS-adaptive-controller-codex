# Research: free GPU compute that needs no payment card, for a user with Google AI Ultra (read-only: web)

Context: the user pays only for Google AI Ultra and bought 100 Colab compute units once. They will not buy anything
else and will not add a card anywhere. The goal: run small PyTorch training jobs (GRU/TCN drift nets, minutes to a few
hours each) on free lanes first, and spend paid Colab units only when that is clearly the best option.
`docs/agent/colab.md` (this repo) has what we found so far; read its "Zero compute units" section first.

Give each claim as: claim, URL, quote under 15 words, date of the source. Prefer official pages and 2025-2026 sources.

1. Does Google AI Ultra (2026) include a Colab benefit (Colab Pro, compute units, priority GPUs, Gemini in Colab)?
   What do official Google pages say? What is the status of github.com/googlecolab/colabtools issue #6127?
2. Kaggle Notebooks: weekly GPU and TPU hours, GPU types (T4 x2, P100?), max session length, whether phone verification
   is needed, whether any card is needed, and whether a job can be started from a script (`kaggle kernels push`,
   then fetch the outputs) with no browser.
3. Others: Amazon SageMaker Studio Lab, Lightning AI free tier, Paperspace Gradient free, Hugging Face (ZeroGPU,
   free Spaces), Google Cloud free tier / trial, Colab free tier itself. For each: GPU type, free hours, card needed
   yes/no, scriptable yes/no, a catch.
4. Which of these can run a long unattended job (more than 1 hour) without a browser tab open?

Answer in at most 40 lines. Mark anything you could not source as UNVERIFIED. End with a one-line verdict: the order of
lanes to try for a 1-3 hour GPU training job, with paid Colab units last unless a reason says otherwise.
