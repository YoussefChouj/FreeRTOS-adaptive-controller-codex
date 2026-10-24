# Prompt for the builder agent (paste everything below the line into a new Cursor agent)

---

You are building a small local study web page. This is an isolated web task inside a firmware repo.

## Where
- Repo root: `D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex` (your window may show the same repo under another path; that is fine).
- Your folder: `docs/learning/internship/`. **Write only inside this folder.**

## What to read (and nothing else)
1. `docs/learning/internship/SPEC.md`. This is the whole job. Build exactly to it.
2. `docs/learning/internship/data/glossary.json`, `data/progress.json`, `data/lessons/L01.md`. This is the content the page shows.

Do not explore the rest of the repo. It is firmware (C, Keil), a Python ground station and lab tooling, and none of it is relevant. The firmware, flashing, lab and service rules in `AGENTS.md` don't apply to this task. Never run flash, lab, `fly`, service or Keil commands.

## Rules
- **`data/` belongs to the tutor.** Never change the meaning of anything in it. If the validator finds a real problem in the data, report it and don't fix it yourself.
- **The test numbers in SPEC sections 4 and 5 are measured.** If a test fails, fix the code, not the expected number. Only if you are sure a spec number is wrong: stop, and report your own measurement next to the spec's.
- No npm install, no build step, no framework. Plain ES modules plus the two vendored libraries.
- Build no features beyond the spec. Ask nothing: where the spec is silent, pick the simplest option and note it in the report.

## Vendoring the libraries (SPEC section 2)
The user is in China, so public CDNs can fail. Download each library once into `lib/` and commit the files.
- KaTeX 0.16.11:
  - `https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.js`
  - `https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.css`
  - Parse `katex.min.css` for every `url(fonts/....woff2)` and download each one from `.../KaTeX/0.16.11/fonts/<name>.woff2` into `lib/fonts/`. woff2 alone is enough: browsers pick it first.
- marked 12.0.2: `https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js` (UMD; exposes the global `marked`, used as `marked.parse`).
- If cdnjs fails, use the same version from jsdelivr (`https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/...` and `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js`). If both fail, retry through the local proxy at `http://127.0.0.1:7897` (for example `curl -x http://127.0.0.1:7897 ...`).
- In the report, list each vendored file with its size.

## Acceptance (SPEC section 7). Run all five and paste the real output
1. `node --test docs/learning/internship/tests/`: all pass, covering every table row in SPEC sections 4 and 5.
2. `python docs/learning/internship/tools/validate_data.py`: exit 0.
3. Copy `data/` to a temp folder outside the repo. Make the validator point at it (add a `--data <dir>` argument). Corrupt it twice: once with a duplicate glossary id, once with a missing lesson file. Show a non-zero exit and the printed problem each time. Never touch the real `data/`.
4. Serve with `open.bat` (or `python -m http.server 8765` from the folder). Open `http://localhost:8765/` in a real browser (use your browser tool if you have one):
   - All 6 tabs render.
   - L01 shows rendered math and both live widgets.
   - The glossary shows 47 entries.
   - The console has no errors.
   - Save one screenshot at 375 px width to `docs/learning/internship/shots/mobile-375.png`.

   If you have no browser tool, say so plainly in the report. Don't claim this step passed.
5. Block the network, or prove no request leaves localhost: list the page's network requests and show every one goes to `localhost:8765`.

## Finish
- Write `docs/learning/internship/BUILD_REPORT.md` with:
  - what you built (file list)
  - the vendored files and their sizes
  - each acceptance command with its pasted output
  - every place where you chose something the spec didn't say
  - anything left undone
- Commit **only your folder**. Other agents work in this same checkout, and main has many unrelated modified files.
  ```
  git add docs/learning/internship
  git diff --cached --name-only        # every line must start with docs/learning/internship/
  git commit -m "learning: internship hub v1 (tabs, glossary audio, flashcards, tilt + zloop sims, tests, validator)" -- docs/learning/internship
  ```
  If `git diff --cached --name-only` shows any other path, stop and report. Don't unstage other people's work.
- **Don't push.** End by replying "DONE" plus the commit hash and a 5-line summary.
