STATUS: done

Files changed:
- docs/dashboard-platform/shell/plugins/campaign-panel.js: Implemented the Campaign panel according to spec.
- ground_station/service/tests/campaign_panel_harness.js: Implemented the offline Node harness for campaign-panel.
- ground_station/service/tests/test_campaign_panel.py: Wrapped the offline harness in pytest.
- docs/dashboard-platform/shell/index.html: Registered campaign-panel.js and added 'Campaign' to workspace map.
- ground_station/platform/capability_manifest.py: Registered 'Campaign' panel in manifest defaults, plugin list, and telemetry keys map.
- docs/dashboard-platform/capability_manifest.json: Regenerated via capability_manifest module.
- ground_station/platform/tests/test_capability_manifest.py: Updated panel count check from 18 to 19 to reflect the newly added panel.

Verification:
`node ground_station/service/tests/campaign_panel_harness.js`
j No timer after teardown
ALL CHECKS PASSED

`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/platform/tests/test_capability_manifest.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_motor_bench_panel.py`
FAILED ground_station/service/tests/test_all_panels_audit.py::TestAllPanelsAudit::test_all_panels_offline_audit
1 failed, 4 passed in 8.17s

`node ground_station/service/tests/node_harness.js`
  ✓ index.html change flagged as reload
ALL GREEN

`python ground_station/platform/capability_manifest.py --check`
Capability manifest is in sync.

Open risks/questions:
- `test_all_panels_audit.py` fails on `preset-picker.js` due to `sandbox.pluginInit is not a function`. This failure is already present in the baseline (894e681) and is unrelated to the new `campaign-panel.js` (which successfully passes the audit).
SUBSTITUTIONS: none
