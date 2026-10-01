STATUS: done

Files changed:
* `docs/dashboard-platform/shell/plugins/campaign-panel.js`: Kept action errors in `cp-error`, routed state poll errors to `cp-poll-error`.
* `ground_station/service/tests/campaign_panel_harness.js`: Updated check 'h' to verify polling errors and independent action errors.
* `ground_station/platform/tests/test_capability_manifest.py`: Updated panel count to 19.
* `fix_*.js`: Removed via `git rm`.

Verification:
`node ground_station/service/tests/campaign_panel_harness.js`
> j No timer after teardown
> ALL CHECKS PASSED

`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/platform/tests/test_capability_manifest.py ground_station/service/tests/test_motor_bench_panel.py`
> ....                                                                     [100%]
> 4 passed in 8.39s

`PYTHONPATH=. python ground_station/platform/capability_manifest.py --check`
> Capability manifest is in sync.

`git status --short`
> M ground_station/service/tests/campaign_panel_harness.js

`git diff --stat 894e681`
> 9 files changed, 793 insertions(+), 1 deletion(-)

Open risks / Deviations:
Used PYTHONPATH=. for manifest check to resolve module import error.
SUBSTITUTIONS: none
