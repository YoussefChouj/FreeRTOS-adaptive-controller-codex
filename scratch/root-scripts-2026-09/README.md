# Ad-hoc debug scripts moved out of the repo root (2026-10-05)

One-off probes and patch scripts from the 2026-09-20..22 dashboard work (commits 67157b7, 98ea50b), plus a stale
`AGENTS.md` backup. Nothing imports them. Kept for reference; pytest does not collect `scratch/` (pytest.ini).
Some import `ground_station` relative to the repo root: run those from the root with `PYTHONPATH=.`.
