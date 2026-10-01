STATUS: done

FILES CHANGED:
- ground_station/platform/tests/test_capability_manifest.py: restored from 894e681
- docs/dashboard-platform/capability_manifest.json: regenerated and converted to CRLF
- docs/dashboard-platform/shell/plugins/campaign-panel.js: fixed P1-P5 (escape HTML, timer leak, non-200 errors, arm toggle revert, disabled go button during POST)
- ground_station/service/tests/campaign_panel_harness.js: fixed H1-H6, added checks for P1 (HTML escaping) and P4 (arm toggle revert)
- ground_station/service/tests/test_campaign_panel.py: removed trailing whitespace on line 30

VERIFICATION:
`node ground_station/service/tests/campaign_panel_harness.js`
i allow_agent_arm confirm
k Zero old API calls
j No timer after teardown
ALL CHECKS PASSED

`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_motor_bench_panel.py`
FAILED ground_station/service/tests/test_all_panels_audit.py::TestAllPanelsAudit::test_all_panels_offline_audit
1 failed, 2 passed in 8.14s

`python ground_station/platform/capability_manifest.py --check`
Capability manifest is in sync.

`git diff --stat 894e681 -- docs/dashboard-platform/capability_manifest.json`
 docs/dashboard-platform/capability_manifest.json | 11 +++++++++++
 1 file changed, 11 insertions(+)

OPEN RISKS:
- `preset-picker.js` fails audit with "sandbox.pluginInit is not a function". It is outside the allow-list, so it was left untouched.
- `workflow-b` was not found locally, so `894e681` was used as the diff base per instructions.

SUBSTITUTIONS: none
