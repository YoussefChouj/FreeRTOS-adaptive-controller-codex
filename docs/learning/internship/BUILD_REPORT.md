# Build report: internship hub v1 (QA pass, 2026-10-24)

The v1 build is commit 70994a31, by a Cursor agent. That agent was stopped mid-loop and left uncommitted
edits: hash routing plus a desktop layout in `app.js`, `style.css` and `SPEC.md` (+180/−39). This QA pass
checked both versions, kept the edits, found no bugs, and fixed nothing.

## What works (checked 2026-10-24)

| check | result |
|---|---|
| `node --test docs/learning/internship/tests/` | 11 pass, 0 fail (every row in SPEC sections 4 and 5) |
| `python tools/validate_data.py` | `OK: data is valid (47 glossary entries, 15 lessons)`, exit 0 |
| `--data <copy>` with a duplicate glossary id | `PROBLEM: duplicate glossary id 'feikong' at glossary.json[1]`, exit 1 |
| `--data <copy>` with `lessons/L01.md` deleted | `PROBLEM: progress.json lessons[0] lesson file does not exist: data/lessons/L01.md` (also for lessons[1]), exit 1 |
| all 6 views render at `http://localhost:8765` | yes, no console errors |
| L01 math | 25 KaTeX blocks, 0 `.katex-error`, no raw `$` left in the text; both sims mounted in the lesson |
| glossary | 47 rows, 47 ▶ buttons enabled (3 zh voices installed); ▶ speaks with `lang zh-CN`, rate 0.8, voice Microsoft Huihui |
| flashcards | "show answer" flips the card; "knew it" moved it to box 2, due 2026-10-25; still box 2 after a reload (`lih.srs.v1`), due count 46/47 |
| tilt sim, defaults (m 2, ax 3, ay 4, az 0) | T 22.02 N, θ 27.01°, φ 53.13°: matches SPEC 4 row 1 |
| zloop sim, defaults | lowest z −0.476 m, settle 9.03 s, final T 24.52 N: matches SPEC 5 |
| hash routing | each view link sets `aria-current`; Back returns to the previous view; a reload keeps the hashed view |
| scroll restore | L01 scrolled to 900 → `#roadmap` (0) → Back → 900 |
| 375 px width | `scrollWidth` = `clientWidth` = 375 (no horizontal scroll); saved at `shots/mobile-375.png` with headless Edge |
| network | 17 requests, all to `localhost:8765` (page, lib, KaTeX fonts, sims, data) |

Vendored libraries: `lib/katex.min.js` 275,414 B, `lib/katex.min.css` 23,335 B, `lib/marked.min.js` 35,479 B,
`lib/fonts/` 20 woff2 files (304 KB).

## Decisions in this pass
- **Kept the loop edits.** They implement hash routing (each view is an `<a href="#…">`, `#lesson/<id>`,
  `hashchange`, scroll position kept per screen) and the desktop layout (1280 px column, sticky tab bar at
  760 px and wider, lesson body capped at 78ch, two-column glossary and break-point grid at 1000 px and
  wider). Every behaviour listed above was checked with the edits in place.
- **Kept the SPEC.md edits.** They describe only that built behaviour, and no measured number changed.
- I tried to compare with the edits stashed, but the browser served its cached copy of the `app.js` module,
  so the result says nothing. The committed version used button tabs. The edited version is a superset, and it passed.

## Left over
- Nothing blocking.
- Not checked: dark theme contrast, the "no zh voice" fallback tip (this machine has zh voices), the
  side-view toggle, the zloop presets and the heavy-payload saturation, beyond the code reading.
- With the network unplugged (SPEC 7.5), I only checked that every request goes to localhost. I did not test with the network actually off.
