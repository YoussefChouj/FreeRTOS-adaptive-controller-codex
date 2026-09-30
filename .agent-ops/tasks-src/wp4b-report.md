# Task wp4b: flightlab report, ledger, compare (+ tests)

You are a worker in a git worktree of this repo. Quality bar: production code a senior reviewer merges unchanged.

## Guardrails (hard)
- EDIT ONLY: `ground_station/analysis/flightlab/report/render_md.py`, `report/render_html.py`, `ledger.py`,
  `compare.py` (package root, next to pipeline.py), `tests/test_report.py`, `tests/test_ledger.py`,
  `tests/test_compare.py`, `.agent-ops/out/wp4b.md`. `report/__init__.py` exists (empty): leave it. Do NOT edit
  pipeline.py, __main__.py, registry.py, config/, schema/, plugins/, rules/, conftest.py, test_pipeline.py,
  test_contract_edges.py, docs/. A parallel worker writes rules/: do not create rule files.
- Never invent metric paths: read `schema/metrics.schema.json`, `pipeline.py` (analyze ~L230, build_metrics,
  REPORTS_DIR, LEDGER_PATH, REPO_ROOT), `__main__.py`, `registry.py` (Recommendation.to_dict, SEVERITIES).
- None / missing sections render as "n/a" or are omitted; never crash on a skipped plugin; never write 0.0 for
  unknown. No broad `except`, no dead code, no placeholder comments, no TODOs. Stdlib only (+ what the package
  already imports). Module docstrings state the contract.
- No network, nothing on port 8081, no hardware. Read `logs/` only as stated below.
- Commit ONLY allow-listed files on your worktree branch (`git add <files> && git commit -m ... -- <files>`).
  Never commit `logs/` or `docs/flights/`.

## Contracts (called by the unmodified pipeline.py / __main__.py)
- `render_md.render(metrics, recs, figs, out_dir) -> Path` writes `out_dir/report.md`;
  `render_html.render(metrics, recs, figs, out_dir) -> Path` writes `out_dir/report.html`.
  recs = list[Recommendation] (use `.to_dict()`), figs = list[Path] (PNG files, normally under out_dir/figs).
- `ledger.read_rows(path) -> list[dict]` ([] when the file is missing); `ledger.upsert(metrics, recs, path) -> None`;
  `ledger.rebuild(logs_dir=None) -> str` (__main__ calls it with no args and prints the result).
- `compare.compare(a, b) -> str` (path of the written compare.md; __main__ prints it).
- `from .loaders import LoadError` (what __main__ maps to exit code 2). Read `pipeline.REPORTS_DIR`,
  `pipeline.LEDGER_PATH`, `pipeline.REPO_ROOT` at CALL time (tests monkeypatch them), never bind them at import.

## render_md / render_html (one content model, two serializers)
render_md exposes `build_blocks(metrics, recs, figs, out_dir) -> list` of small typed blocks (heading, paragraph,
table(header, rows), image(rel posix path, alt), details(summary, blocks), bullet list). `render_md.render`
serializes them to Markdown; `render_html.render` imports build_blocks and serializes the same blocks to one
self-contained HTML file: `html.escape` every text, inline CSS only, images as `data:image/png;base64,...`,
no external URL anywhere. Markdown table cells escape `|` and replace newlines. Floats `%.4g`, None "n/a".
Content, in order: title; flight header table (every key of metrics["flight"] + controller.mrac_mode,
controller.ctrl_select, schema_version); summary (segment intervals with durations, steady count and total s,
recommendation counts per severity, plugins run / skipped (with reason) / failed); recommendations table
(severity, id, category, target, action, factor, confidence, rationale) with each rec's evidence in a details
block; data-quality slot table (columns = union of scalar slot keys, first-seen order) + stuck/nan var lists +
clock drift; loops tables for airborne and steady (rows = loops, columns = union of scalar stat keys); figures
(path relative to out_dir, posix); warnings list; then one details block per plugin in plugins_run with a
flattened scalar table (path | value) of metrics[plugin] via `compare.flatten` (exhaustive, future-proof).

## ledger.py (spec section 8)
Columns: flight, started_at, analyzed_at, git, notes, preset, mrac_mode, duration_s, airborne_s, worst_drop_pct,
v_rest_start, v_min_airborne, `e_rms_steady_<loop>` for each loop of `pipeline.load_config()["loops"]` (yaml
order), clamp_hi_frac, yaw_pair_pct, n_warn, n_critical. Sources (measured on flight16 metrics): flight.name,
flight.started_at, datetime.now().isoformat(timespec="seconds"), flight.git, flight.notes, flight.preset,
controller.mrac_mode, flight.duration_s, sum(t1 - t0 for [t0, t1] in segments.airborne) (None when airborne is
None; an empty list is a measured 0), data_quality.worst_drop_pct, battery.v_rest_start, battery.v_min_airborne,
loops.<loop>.steady.e_rms, motors.airborne.clamp_hi_frac, motors.steady.yaw_pair_pct, count of recs by severity.
upsert: key (flight, started_at); replace the matching row else append; sort by started_at (empty last), then
flight; header = canonical columns, then any extra columns already in the file (kept, their order); floats
"%.6g", None "", csv with lineterminator "\n", utf-8; write to a temp file in the same dir, then os.replace;
create parent dirs. rebuild(logs_dir=None): logs_dir defaults to pipeline.REPO_ROOT/"logs"/"vofa"; ledger path =
pipeline.LEDGER_PATH; order `*.meta.json` chronologically (started_at from the meta file using the same key the
vofa loader reads, else natural filename order); move an existing ledger to a backup first; run
`pipeline.analyze(meta, ledger=True)` per log in order (MRAC-WORSE needs earlier flights already in the ledger);
catch ONLY LoadError per log (list it as skipped); on any other exception restore the backup and re-raise; on
success delete the backup; return a short summary (path, n analyzed, skipped names + reasons).

## compare.py (spec section 8)
`flatten(obj, prefix="") -> dict`: dotted paths, list indices as segments, scalar leaves only. `compare(a, b)`:
each input is a metrics.json path, a report dir holding metrics.json, or a flight name
(`pipeline.REPORTS_DIR/<name>/metrics.json`); missing -> raise LoadError. Write
`pipeline.REPORTS_DIR/compare_<A>_vs_<B>/compare.md` (A, B = flight.name, sanitized to [A-Za-z0-9_.-]): header
(both flights, presets, git, mrac_mode); top 20 numeric changes by |delta %|; full numeric table (path, A, B,
delta, delta %; delta % "n/a" when A == 0; bools are not numeric); changed non-numeric values; keys only in A,
only in B; preset var diff: metrics.json carries no var list, say so in one line; `git log --oneline A..B` via
subprocess list args (no shell), cwd pipeline.REPO_ROOT, timeout 10 s, "-dirty" stripped, each hash validated
against ^[0-9a-f]{7,40}$; any failure becomes a one-line note in the file. Return str(path).

## Tests (pytest, tmp_path, monkeypatch pipeline.REPORTS_DIR / LEDGER_PATH / REPO_ROOT; no files under logs/)
Use conftest fixtures (build_log, make_log, hover_log, cfg) and `pipeline.build_metrics` / plugins to get a
schema-valid metrics dict; construct Recommendation objects directly. Cover: md + html written and returned;
`|` and `<script>` in notes escaped; a skipped plugin section and None values do not crash; html has a base64 image
and no "http://" / "https://"; ledger upsert insert / replace / sort / extra-column kept / None -> "" / missing
file -> []; rebuild order, LoadError skip, backup restored on another exception (monkeypatch pipeline.analyze);
compare path / dir / name resolution, missing -> LoadError, delta % n/a, only-in-A/B, invalid git hash -> note.
Run the full tree: `python -m pytest ground_station/analysis/flightlab/tests -q` must pass.

## End-to-end (report the output; do not commit what it writes)
M = C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\logs\vofa
`python -m ground_station.analysis.flightlab analyze "<M>\flight16.meta.json"` and the same for flight15 (or the
newest other *.meta.json), then `python -m ground_station.analysis.flightlab compare flight15 flight16`. Check
report.md, report.html (open it: self-contained, figures visible as data URIs), the worktree
docs/flights/ledger.csv rows, compare.md. rules/ is empty in your worktree, so 0 recommendations is expected.

## Report `.agent-ops/out/wp4b.md` (commit it)
Branch, commit hash, changed files; verbatim pytest tail; e2e command outputs; first 40 lines of the flight16
report.md; the ledger CSV; the compare.md head; decisions you made where this brief was silent.
SUBSTITUTIONS: none (or name them). NOT RUN: <steps> if any.
