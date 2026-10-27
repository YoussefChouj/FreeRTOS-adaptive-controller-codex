# tools/: what each script is for

Before writing a new script, look here and in `docs/lab/COMMANDS.md` (lab commands live there, not here).
Research reproducers are frozen: their docs cite their numbers, so they are not moved or edited, only rerun.

## Gate and push (run by agents every commit)

| Script | Does |
|---|---|
| `check.sh` | The gate: `bash tools/check.sh --changed` runs the steps the diff needs and records the tree hash for the push guard |
| `check_routes.py` | Which check.sh steps a diff requires (used by `check.sh --changed`) |
| `agent_push_guard.py` | Pre-push guard for agents: stream doc present, the pushed sha passed check.sh |
| `land.sh`, `land_ci.py` | Land a branch: what to do next from GitHub's Actions results |
| `install-hooks.sh` | Installs the git hooks |
| `doc_paths.py` | Every repo path named in an agent-facing doc must exist |

## Firmware

| Script | Does |
|---|---|
| `host_tests.py` | Build and run every host C test (API/tests, tests/firmware_host) with gcc |
| `exe_cache.py` | Content-keyed cache of those host-built test exes |
| `fw_lint.py` | Lint ratchet for docs/firmware-coding-standard.md rules 1 and 2 |
| `row_meta.py` | Checks the tunable tables (`*_ROW` macros, docs/firmware-table-pattern.md) |
| `fw_equiv.py`, `fw_trace.py` | Prove a firmware refactor is behaviour-preserving / trace a file at a ref vs the tree |
| `stack_budget.py` | Static stack budget of every FreeRTOS task from the Keil call graph |

## Research reproducers (frozen; the doc says how to rerun)

| Script | Doc |
|---|---|
| `d3_mrac_wrap_sim.py` | docs/research/2026-10-22-d3-mrac-wrap.md |
| `d4_mrac_outer_inner_sim.py` | docs/research/2026-10-22-d4-outer-inner.md |
| `d5_truth4_batch.py` | docs/research/2026-10-24-drift-nn-v2-spec.md |
| `d6_effect_sizes.py` | docs/research/2026-10-22-d6-effect-sizes.md |
| `sysid_quality.py` | docs/research/2026-10-22-d7-sysid-quality.md |
| `analyze_z_cf_b1.py` | docs/lab/z-cf-rootcause-B1.md |
| `xyrm_fit.py` | tests/firmware_host/test_wp46_host.py |
| `kaggle_nnio/` | Drift NN training on Kaggle (run.py, sweep.py, stage.sh) |

## Elsewhere

- Flight analysis and the cross-day flight index: `ground_station/analysis/`, `python -m ground_station.lab.flight_index`.
- Autotune (sysid fit, design, plots): `python -m ground_station.autotune.cli <session> --loop L --axis A` (COMMANDS.md 4.7).
- `.agent-ops/` worker lanes (jules, oc, argon, spawn-worker, manager) are switched off since 2026-10-25 (AGENTS.md
  Authorizations table); kept in place because re-enabling one needs only the operator's yes, not a rebuild.
