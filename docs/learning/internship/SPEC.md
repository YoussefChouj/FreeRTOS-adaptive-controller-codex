# Internship learning hub: spec (v1, 2026-10-24)

**Purpose:** one local web page for preparing for the Lead-Intelligent drone-algorithm internship, which is run in Chinese. It shows the M0–M6 roadmap, the Chinese glossary with audio and flashcards, lesson notes with math, open break points, and two small simulators.
**Owner of content:** Claude, the tutor, writes everything under `data/` after each lesson. The page only reads it. The builder agent never edits the meaning of anything in `data/`.

## 1. Layout (all paths relative to `docs/learning/internship/`)
```
SPEC.md  AGENT_PROMPT.md  README.md  open.bat
index.html  app.js  style.css
sims/tilt_core.mjs   sims/tilt.js      # W1: pure math + canvas view
sims/zloop_core.mjs  sims/zloop.js     # W2: pure sim + canvas plots
lib/                                    # vendored libs (exact versions, see 2)
data/glossary.json  data/progress.json  data/lessons/L01.md   # EXISTS, Claude-owned
tools/validate_data.py
tests/tilt_core.test.mjs  tests/zloop_core.test.mjs
```

## 2. Stack rules
- Static files only. No build step, no npm install, no framework.
- Plain ES modules. Serve with `python -m http.server 8765` from this folder; `open.bat` does that and opens `http://localhost:8765/`.
- The user is in China, where public CDNs may be blocked, so vendor every library into `lib/`. Download each once from cdnjs and pin the exact version:
  - KaTeX **0.16.11**: `katex.min.js`, `katex.min.css`, `fonts/`
  - marked **12.0.2**: `marked.min.js`
- No three.js. W1 draws its 3D view on a 2D canvas with a simple projection.
- Light and dark themes come from CSS variables on `:root`, plus `prefers-color-scheme`.
- **Desktop browser first.** The page is read on a desktop browser, so the layout must use the width: a 1280 px content column, a sticky tab bar, and a readable prose measure (the lesson body is capped at 78ch and centred). The phone layout is the fallback, not the target.
- The layout must still work at 375 px width with no horizontal scroll.
- Wrap every `localStorage` read and write in try/catch. The page must still work when storage is unavailable.

## 3. Views (real links + hash routing)

The view is in the URL, so a view can be bookmarked, shared, opened in a new tab and
reached with the browser's Back and Forward buttons.

- Each view is a link `<a href="#roadmap">`, not a button. The selected one carries `aria-current="page"`.
- Routes: `#roadmap`, `#lessons`, `#glossary`, `#flashcards`, `#breakpoints`, `#sims`, and `#lesson/<id>` for one open lesson.
- A `hashchange` handler applies the route. On boot the hash wins over the remembered tab, so a shared link opens the view it names; with no hash the last tab (from `localStorage`, key `lih.tab.v1`) is used.
- Switching views must not lose the reading position: keep the scroll offset per screen (a view, plus the lesson open inside it) and restore it when the reader comes back. Do not jump to the top on every switch.
- The `localStorage` key `lih.tab.v1` still remembers the last view for the next visit.

1. **Roadmap**
   - Show `progress.modules` M0–M6 as cards, each with en/zh title, goal and JD keywords.
   - Under each module, list its `progress.lessons` with a status chip (planned / taught / quizzed / passed) and their scores.
   - Clicking a lesson that has a `file` opens it in the Lessons view.
2. **Lessons**
   - Render the lesson's Markdown with marked and KaTeX.
   - To stop marked from mangling math, extract `$$…$$` (display) and `$…$` (inline) into placeholders first. Run marked. Then put back `katex.renderToString` output, with `throwOnError: false`.
   - Replace `<!-- sim:tilt -->` and `<!-- sim:zloop -->` with the live widget.
3. **Glossary**
   - A search box (matches zh, pinyin with or without tones, and en), a module filter, and a kind filter (term / phrase).
   - Each row shows: hanzi (large), tone-coloured pinyin, en, module/lesson tag, and a ▶ audio button.
4. **Flashcards**
   - Leitner system with 5 boxes and review intervals of 0, 1, 3, 7 and 14 days. Storage key `lih.srs.v1` as `{id: {box, due}}`.
   - Two directions: zh→en, and en→zh with a pinyin hint on demand.
   - Buttons "knew it" (box+1) and "missed" (back to box 1). Show the count due today.
   - Cards default to every glossary entry; a filter can limit them by lesson.
5. **Break points**
   - Show `progress.breakpoints`, open ones first, with a status chip (open / retested / fixed) and the date, lesson, topic and gap.
   - Also show the per-lesson quiz scores as a small table.
6. **Sims**
   - Both widgets on one page, for free play.

### Pinyin tone colours
- Split each pinyin word into syllables with a regex built from the standard initials and finals. The tone is set by the marked vowel (ā/á/ǎ/à and so on; no mark = neutral).
- Colours: tone 1 red, 2 orange, 3 green, 4 blue, neutral grey. Keep the contrast readable in both themes.
- When the number of syllables matches the number of hanzi in `zh` (count CJK characters only), colour the matching hanzi too. When it doesn't match, colour the pinyin only and don't throw.

### Audio
- `speechSynthesis` with `lang = 'zh-CN'`, using a zh voice when one exists, at rate 0.8.
- With no zh voice, disable ▶ and show a one-line tip: install a Chinese voice in Windows Settings → Time & language → Speech.

## 4. W1, tilt and thrust vector (`sims/tilt_core.mjs`, pure functions)
`thrustFromAccel(m, ax, ay, az, g = 9.81)` returns `{Fx, Fy, Fz, T, thetaDeg, phiDeg}`, where:
- `F = m·(ax, ay, az + g)`
- `T = |F|`
- `θ = atan2(√(ax²+ay²), az+g)`
- `φ = atan2(ay, ax)`

`singleAxis(m, thetaDeg, g = 9.81)` returns `{T, Tvert, Thoriz, a}` with `T = mg/cosθ`, `Tvert = T·cosθ`, `Thoriz = T·sinθ`, `a = g·tanθ`.

View (`sims/tilt.js`):
- Sliders for m (0.5–5 kg), ax, ay (−8…8) and az (−5…5).
- Readouts for T, θ, φ and F components.
- A canvas with an X-frame drone tilted to match, a thrust arrow, and its vertical and horizontal components drawn and labelled **T·cosθ (vertical)** and **T·sinθ (horizontal)**. Drag to orbit the camera.
- A toggle for "side view (one axis)" that uses `singleAxis` with a θ slider.
- Chinese labels next to the English: 推力 T, 倾角 θ, 方向 φ, 分量.

Test vectors (tolerance ±0.01, angles ±0.05°):

| call | expected |
|---|---|
| `thrustFromAccel(2, 3, 4, 0)` | T 22.02, θ 27.01, φ 53.13 |
| `thrustFromAccel(2, 3, 4, 2)` | T 25.65, θ 22.94 |
| `thrustFromAccel(2, 0, 0, 0)` | T 19.62, θ 0 |
| `singleAxis(1.5, 20)` | T 15.66, a 3.57 |

## 5. W2, payload step on the vertical loop (`sims/zloop_core.mjs`)
`simulate(params)` returns arrays `{t, z, vz, T, Tcmd}`. It must use this exact algorithm so the test numbers reproduce.
```
defaults: g=9.81, m_nom=2.0, m_add=0.5, t_add=2.0, dt=0.002, t_end=12,
          Kp=1.0, Kv=3.0, Ki=2.0, useI=true, hoverFF=true, openLoop=false,
          tau=0.05, Tmax=2*m_nom*g, z_sp=0
state: z=0, vz=0, T=m_nom*g, I=0, t=0
loop while t < t_end:
  m   = (t < t_add) ? m_nom : m_nom + m_add
  e   = z_sp - z;  vsp = Kp*e;  ev = vsp - vz
  if useI: I += Ki*ev*dt
  a   = Kv*ev + (useI ? I : 0)
  Tcmd = openLoop ? m_nom*g : (hoverFF ? m_nom*(g + a) : m_nom*a)
  Tcmd = clamp(Tcmd, 0, Tmax)
  T  += (Tcmd - T)*dt/tau            # motor lag
  vz += (T/m - g)*dt;  z += vz*dt;  t += dt   # semi-implicit Euler
  push (t, z, vz, T, Tcmd)
```
View (`sims/zloop.js`):
- Sliders for Kp, Kv, Ki, m_add, Tmax and tau. Toggles for useI, hoverFF and openLoop.
- Three stacked plots (z, vz, T versus t), with the step time marked and the weight line `(m_nom+m_add)·g` on the T plot.
- Readouts: lowest z, final z, the time after which |z| stays ≤ 2 cm, and the final T.
- Labels: 高度 z, 速度 vz, 推力 T, 积分项 on/off.
- Presets: "P only", "PI", "open loop", "heavy payload (m_add 2.5)". The heavy one should show saturation: Tcmd clipped at Tmax.

Test values (measured 2026-10-24 with a reference Python run of the algorithm above):

| case | expected |
|---|---|
| openLoop, z at t = 3.0 s | −0.987 ±0.01 m |
| useI=false, final z | −0.8175 ±0.002 m (analytic Δm·g/(m_nom·Kv·Kp)) |
| defaults (Ki 2), lowest z | −0.476 ±0.01 m |
| defaults (Ki 2), last time \|z\| > 0.02 m | 9.03 ±0.1 s |
| Ki 1, final z at 12 s | −0.042 ±0.005 m (still not settled) |
| Ki 4, lowest z / settle time | −0.363 ±0.01 m / 6.96 ±0.1 s |
| defaults, final T | 24.5 ±0.1 N |

## 6. Data contract (the builder reads these files and never changes their meaning)
- `glossary.json`: an array of `{id, kind: "term"|"phrase", zh, pinyin, en, module: "M0".."M6", lesson}`. Ids are unique. Optional fields are `example_zh`, `example_en` and `note`.
- `progress.json`: `{updated, target, modules[], lessons[], breakpoints[]}`.
  - Lesson: `{id, module, en, zh, status, scores{}, file?, date?, sims?[]}`.
  - Break point: `{date, lesson, topic, gap, status}`.
- Lessons are Markdown under `data/lessons/`, with `$…$` math and `<!-- sim:x -->` markers.
- `tools/validate_data.py` (standard library only) checks:
  - JSON parses
  - required fields are present
  - ids are unique
  - `module` is M0–M6
  - each lesson `file` exists
  - each `sims` id is in {tilt, zloop}
  - each status is in its allowed set
  - each break point's `lesson` exists
  - each glossary `lesson` exists in the lessons list

  It exits 0 when everything passes and prints each problem otherwise.

## 7. Acceptance (the builder runs all of these and pastes the output in its report)
1. `node --test docs/learning/internship/tests/` passes: every table row in sections 4 and 5.
2. `python docs/learning/internship/tools/validate_data.py` exits 0 on the shipped data.
3. Corrupting a copy of the data makes the validator exit non-zero. Test a duplicate id and a missing lesson file, using a temp copy and never the real files.
4. Served with `open.bat`, all 6 tabs render with no console errors, L01 shows rendered math and both live widgets, and the glossary shows 47 entries. Check this in a real browser, plus one screenshot at 375 px width.
5. With the network off, the page still renders, because the libraries are vendored.
