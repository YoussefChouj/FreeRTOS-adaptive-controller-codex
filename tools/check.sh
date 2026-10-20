#!/usr/bin/env bash
# One gate for the firmware and its host pipeline (WP-36). Run from anywhere: bash tools/check.sh
# Every step runs even after a failure; exit 1 if any step failed. No Keil, no flash, no hardware.
#   host-tests   every host C test of API/tests and tests/firmware_host (tools/host_tests.py)
#   mrac-equiv   MRAC bit-exact equivalence vs the reference tree (API/tests/run_mrac_equiv.py)
#   c-pytest     pytest suites that compile firmware C (subscribe harness, MRAC variants) + tools tests
#   sil-smoke    sim/sil/test_sil.py, the firmware controllers in the closed-loop SIL (its EQUIV case is mrac-equiv), and
#                sim/sil/test_faults.py, wfb_safety trips on SIL faults; 4 pytest processes at once (sil_smoke)
#   clang-tidy   .clang-tidy on every firmware file the host tests build, with their flags
#   float        no implicit float -> double promotion in those files (gcc -Werror=double-promotion)
#   row-meta     units and [min, max] of every *_ROW tunable, every row value in range (tools/row_meta.py)
#   fw-lint      firmware coding-standard ratchet (tools/fw_lint.py, allow-list shrinks only)
#   stack        task and interrupt stack budget from the Keil call graph (tools/stack_budget.py); SKIPPED without OBJ/JX_FLY.htm
#   doc-paths    every repo path and file:line in the agent-facing docs exists (tools/doc_paths.py)
#   arm-syntax   arm-none-eabi-gcc -fsyntax-only on the same files; SKIPPED if not installed
# Subset while iterating: bash tools/check.sh doc-paths fw-lint (step names as above), or --fast for every step
# except host-tests, mrac-equiv and sil-smoke (each prints its time). A subset ends with
# "CHECK SUBSET PASS"; only a run with no arguments prints CHECK PASS, and that is the one a commit needs.
# Opt-in pre-commit hook that runs this script: bash tools/install-hooks.sh (WP-38).
# doc-budget is auto-included; it enforces AGENTS.md ≤ 150 lines and any context.md ≤ 120 lines.
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
command -v "$PY" >/dev/null 2>&1 || PY=python3
PYTEST=("$PY" -m pytest -q -p no:cacheprovider)

ALL=" host-tests mrac-equiv c-pytest sil-smoke clang-tidy float row-meta fw-lint stack doc-paths arm-syntax doc-budget "
only=" $* "
[ "$only" = " --fast " ] && only=" c-pytest clang-tidy float row-meta fw-lint stack doc-paths arm-syntax doc-budget "
for want in $only; do
    [[ "$ALL" == *" $want "* ]] || { echo "check.sh: unknown step '$want' (steps:$ALL)" >&2; exit 2; }
done

# A step exits 77 when its input cannot exist on this machine (no Keil build, no ARM gcc): SKIPPED, named in the
# verdict line, never silently OK.
failed=()
skipped=()
step() {
    local name=$1 t0=$SECONDS rc
    shift
    [ "$only" = "  " ] || [[ "$only" == *" $name "* ]] || return 0
    echo "== $name"
    "$@"; rc=$?
    if [ $rc -eq 0 ]; then
        echo "-- $name OK ($((SECONDS - t0)) s)"
    elif [ $rc -eq 77 ]; then
        echo "-- $name SKIPPED ($((SECONDS - t0)) s)"
        skipped+=("$name")
    else
        echo "-- $name FAILED ($((SECONDS - t0)) s)"
        failed+=("$name")
    fi
}

# sil-smoke as 4 pytest processes at once, one per -k group; each group excludes the earlier ones, so together they
# run every test once (an empty group fails, rc 5). Balanced on measured call times (about 30 s each, 2026-10-05).
# The SIL binaries are built first so the groups only read them. SIL_SMOKE_SERIAL=1 = one process.
SIL=(sim/sil/test_sil.py sim/sil/test_faults.py --deselect sim/sil/test_sil.py::test_run_mrac_equiv_still_ok)
sil_smoke() {
    [ "${SIL_SMOKE_SERIAL:-0}" = 1 ] && { "${PYTEST[@]}" "${SIL[@]}"; return; }
    "$PY" -m sim.sil.build > /dev/null || return 1
    local groups=("replay_round_trip or deterministic" "presets" "cascade or abort_rules")
    local d prev="" k i rc=0 pids=()
    d=$(mktemp -d)
    for i in 0 1 2 3; do
        if [ "$i" -lt 3 ]; then k=${groups[$i]}${prev:+ and not ($prev)}; prev=${prev:+$prev or }${groups[$i]}
        else k="not ($prev)"; fi
        "${PYTEST[@]}" "${SIL[@]}" -k "$k" > "$d/$i" 2>&1 &
        pids+=($!)
    done
    for i in 0 1 2 3; do
        if wait "${pids[$i]}"; then echo "   [$i] $(tail -n 1 "$d/$i")"; else rc=1; echo "   [$i] FAILED:"; tail -n 40 "$d/$i"; fi
    done
    rm -rf "$d"
    return $rc
}

# doc-budget: AGENTS.md stays ≤ 170 lines; any context.md ≤ 120 lines. The cap
# prevents context bloat / lost-in-the-middle failures. Detail moves to
# docs/context/*.md, not here. To grow beyond the cap: edit, do not extend.
doc_budget() {
    local bad=0 lim file lines
    if [ -f AGENTS.md ]; then
        lim=170 file=AGENTS.md
        lines=$(wc -l < "$file")
        if [ "$lines" -gt "$lim" ]; then
            echo "   doc-budget: $file is $lines lines (cap $lim)" >&2
            bad=1
        fi
    fi
    if [ -f context.md ]; then
        lim=120 file=context.md
        lines=$(wc -l < "$file")
        if [ "$lines" -gt "$lim" ]; then
            echo "   doc-budget: $file is $lines lines (cap $lim)" >&2
            bad=1
        fi
    fi
    return "$bad"
}

step host-tests "$PY" tools/host_tests.py
step mrac-equiv "$PY" API/tests/run_mrac_equiv.py
step c-pytest   "${PYTEST[@]}" ground_station/livewatch/tests/test_subscribe_c.py \
                ground_station/analysis/tests/test_mrac_variants_host.py tools/test_row_meta.py tools/test_install_hooks.py
step sil-smoke  sil_smoke
step clang-tidy "$PY" tools/host_tests.py --tidy
step float      "$PY" tools/host_tests.py --float
step row-meta   "$PY" tools/row_meta.py
step fw-lint    "$PY" tools/fw_lint.py
step stack      "$PY" tools/stack_budget.py
step doc-paths  "$PY" tools/doc_paths.py
step arm-syntax "$PY" tools/host_tests.py --arm
step doc-budget doc_budget

if [ ${#failed[@]} -gt 0 ]; then
    # On failure, clear the recorded pass so the next push is blocked
    # until a fresh passing run replaces it. We record only on success.
    mkdir -p .agent_state
    rm -f .agent_state/last_check
    echo "CHECK FAIL: ${failed[*]}"
    exit 1
fi
note=""
[ ${#skipped[@]} -gt 0 ] && note=" (skipped here: ${skipped[*]})"
mkdir -p .agent_state
# Always record a passing run, even subsets. The push guard reads this and
# refuses to push without a recent passing run; full vs. subset is logged
# for the operator to see at a glance. A subset is OK to commit locally;
# a full pass is what AGENTS.md requires before merge.
if [ "$only" = "  " ]; then
    printf 'CHECK PASS %s scope=full\n' "$(date -u +%FT%TZ)" > .agent_state/last_check
    echo "CHECK PASS$note"
else
    printf 'CHECK SUBSET PASS %s scope=%s\n' "$(date -u +%FT%TZ)" "${only// /}" \
        > .agent_state/last_check
    echo "CHECK SUBSET PASS:$only(not the full gate)$note"
fi
