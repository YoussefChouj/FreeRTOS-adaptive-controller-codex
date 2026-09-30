STATUS: done

Files changed:
- ground_station/analysis/flightlab/compare.py: Implemented flatten and compare logic (git diff, numeric deltas).
- ground_station/analysis/flightlab/report/render_md.py: Implemented build_blocks and markdown serialization.
- ground_station/analysis/flightlab/report/render_html.py: Implemented HTML serialization using build_blocks.
- ground_station/analysis/flightlab/tests/test_compare.py: Added test coverage for compare logic.
- ground_station/analysis/flightlab/tests/test_report.py: Added test coverage for render outputs and escapes.

Verification:
- python -m pytest ground_station/analysis/flightlab/tests/test_report.py ground_station/analysis/flightlab/tests/test_compare.py -q -> 4 passed
- python -m pytest ground_station/analysis/flightlab/tests -q -> 117 passed

Open questions / risks:
- NOT RUN: end-to-end (logs not on this host).
- Not verified: Real flight log structures, though they conform to schema.

SUBSTITUTIONS: none
