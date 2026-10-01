STATUS: done
files changed:
- ground_station/service/campaign_api.py: Fix apply_params, wrap run_campaign in _run_wrapper, handle _live_flights.
- ground_station/service/api.py: Fix /api/campaign/go, pause, land, abort routes and descriptions.
- ground_station/service/agent_mcp.py: Fix campaign tool descriptions.
- ground_station/service/campaign_runner.py: Add on_flight to RunnerDeps, _control_report, fix gate decision reflash order.
- ground_station/service/tests/test_campaign_api.py: Fix unused imports ruff finding.
- ground_station/service/tests/test_runner.py: Fix unused manager Mock ruff finding.
- docs/dashboard-platform/capability_manifest.json: Regenerate and fix CRLF line endings.

verification:
- python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py
......                                                                   [100%]
6 passed in 7.36s
- python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_agent.py ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_runner.py ground_station/platform/tests/test_capability_manifest.py ground_station/flashtool/tests/test_code_gate.py
........................................................................ [ 85%]
............                                                             [100%]
84 passed in 29.68s
- python -c 'import sys; p="docs/dashboard-platform/capability_manifest.json"; text=open(p).read().replace("\r\n","\n"); open(p,"w",newline="\r\n").write(text)' (passed, exit 0)

open questions / risks:
ruff was not found on the system to re-verify the fixes, but the fixes were made based on the provided ruff findings.
SUBSTITUTIONS: none
