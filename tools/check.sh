#!/usr/bin/env bash
# One gate for the firmware and its host pipeline (WP-36). Run from anywhere: bash tools/check.sh
# Every step runs even after a failure; exit 1 if any step failed. No Keil, no flash, no hardware.
#   host-tests   every host C test of API/tests and tests/firmware_host (tools/host_tests.py)
#   mrac-equiv   MRAC bit-exact equivalence vs the reference tree (API/tests/run_mrac_equiv.py)
#   c-pytest     pytest suites that compile firmware C (subscribe harness, MRAC variants) + tools tests
#   sil-smoke    sim/sil/test_sil.py, the firmware controllers in the closed-loop SIL (its EQUIV case is mrac-equiv), and
#                sim/sil/test_faults.py, wfb_safety trips on SIL faults (about 20 s)
#   clang-tidy   .clang-tidy on every firmware file the host tests build, with their flags
#   row-meta     units and [min, max] of every *_ROW tunable, every row value in range (tools/row_meta.py)
#   arm-syntax   arm-none-eabi-gcc -fsyntax-only on the same files; skipped with a message if not installed
# Opt-in pre-commit hook that runs this script: bash tools/install-hooks.sh (WP-38).
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
command -v "$PY" >/dev/null 2>&1 || PY=python3
PYTEST=("$PY" -m pytest -q -p no:cacheprovider)

failed=()
step() {
    local name=$1 t0=$SECONDS
    shift
    echo "== $name"
    if "$@"; then
        echo "-- $name OK ($((SECONDS - t0)) s)"
    else
        echo "-- $name FAILED ($((SECONDS - t0)) s)"
        failed+=("$name")
    fi
}

step host-tests "$PY" tools/host_tests.py
step mrac-equiv "$PY" API/tests/run_mrac_equiv.py
step c-pytest   "${PYTEST[@]}" ground_station/livewatch/tests/test_subscribe_c.py \
                ground_station/analysis/tests/test_mrac_variants_host.py tools/test_row_meta.py tools/test_install_hooks.py
step sil-smoke  "${PYTEST[@]}" sim/sil/test_sil.py sim/sil/test_faults.py --deselect sim/sil/test_sil.py::test_run_mrac_equiv_still_ok
step clang-tidy "$PY" tools/host_tests.py --tidy
step row-meta   "$PY" tools/row_meta.py
step fw-lint    "$PY" tools/fw_lint.py
step arm-syntax "$PY" tools/host_tests.py --arm

if [ ${#failed[@]} -gt 0 ]; then
    echo "CHECK FAIL: ${failed[*]}"
    exit 1
fi
echo "CHECK PASS"
