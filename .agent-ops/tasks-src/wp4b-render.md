# Task wp4b-render: flightlab compare.py + report/render_md.py + report/render_html.py (+ tests), slice A of WP4b

Read `.agent-ops/tasks-src/wp4b-report.md` first: its Guardrails, Contracts, "render_md / render_html",
"compare.py" and "Tests" sections bind you. This slice implements compare and the two renderers (render_md needs
`compare.flatten`). A parallel worker writes ledger.py: do not create it or tests/test_ledger.py.

EDIT ONLY: `ground_station/analysis/flightlab/compare.py`, `ground_station/analysis/flightlab/report/render_md.py`,
`ground_station/analysis/flightlab/report/render_html.py`, `ground_station/analysis/flightlab/tests/test_compare.py`,
`ground_station/analysis/flightlab/tests/test_report.py`, `.agent-ops/out/wp4b-render.md` (your report).

A previous attempt was rejected. Its defects, do not repeat them:
1. `r.get(...)` on `Recommendation` objects. They are dataclasses (`registry.py`): use `r.to_dict()`.
2. Broad `except Exception` / bare `except`. In compare's git step catch only `subprocess.SubprocessError`,
   `OSError` and a failed hash validation, each turned into a one-line note.
3. Four thin tests. Cover every item the WP4b "Tests" section lists for md/html and compare, each asserting
   concrete content: md + html files written and paths returned; `|` in notes escaped in md; `<script>` in notes
   escaped in html; a skipped plugin and None values render "n/a" or are omitted without crashing; html contains
   `data:image/png;base64,` for a real tiny PNG in figs and no `http://` / `https://`; flatten on nested dict + list;
   compare resolves a metrics.json path, a report dir and a flight name; missing -> LoadError; delta % "n/a" when
   A == 0; bools not numeric; only-in-A and only-in-B listed; an invalid git hash becomes a note (no subprocess run).
4. Read `pipeline.REPORTS_DIR`, `pipeline.REPO_ROOT` at call time (tests monkeypatch them).

Acceptance (put the verbatim tails in your report):
- `python -m pytest ground_station/analysis/flightlab/tests/test_report.py ground_station/analysis/flightlab/tests/test_compare.py -q`
- `python -m pytest ground_station/analysis/flightlab/tests -q` (whole package; nothing else may break)
- End-to-end: NOT RUN on the remote host (logs are not there); the supervisor runs it.
