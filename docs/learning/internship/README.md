# Internship learning hub

A single local web page for preparing for the Lead-Intelligent drone-algorithm
internship (run in Chinese). It shows the M0–M6 roadmap, the Chinese glossary
with audio and flashcards, the lesson notes with math, open break points and
two small simulators.

Everything is static: no build step, no npm install, no framework. Plain ES
modules plus two vendored libraries.

## Run it

```
open.bat
```

That serves this folder with `python -m http.server 8765` and opens
<http://localhost:8765/>. Any static server works.

## Layout

```
SPEC.md  AGENT_PROMPT.md  README.md  open.bat
index.html  app.js  style.css
sims/tilt_core.mjs   sims/tilt.js      # W1: pure math + canvas view
sims/zloop_core.mjs  sims/zloop.js     # W2: pure sim + canvas plots
lib/                                    # vendored libs (KaTeX 0.16.11, marked 12.0.2)
data/glossary.json  data/progress.json  data/lessons/L01.md   # tutor-owned
tools/validate_data.py
tests/tilt_core.test.mjs  tests/zloop_core.test.mjs
```

## Data

`data/` is owned by the tutor (Claude). The page only reads it; the builder
never changes the meaning of anything in there. Validate it with:

```
python tools/validate_data.py            # the shipped data
python tools/validate_data.py --data DIR # another data directory
```

The validator exits 0 when everything passes and prints each problem
otherwise.

## Tests

```
node --test tests/
```

`tests/index.js` is a small compatibility shim: on Node 22+ a positional
directory argument to `node --test` is resolved as a module instead of being
searched for test files, so the shim re-imports the `.mjs` tests to keep
`node --test <dir>` working.

## Libraries

Vendored into `lib/` because public CDNs can be blocked in China:

- KaTeX 0.16.11 — `lib/katex.min.js`, `lib/katex.min.css`, `lib/fonts/*.woff2`
- marked 12.0.2 — `lib/marked.min.js` (UMD, global `marked`)

No request leaves `localhost:8765`.