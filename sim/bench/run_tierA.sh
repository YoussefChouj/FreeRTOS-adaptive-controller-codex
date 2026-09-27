#!/bin/bash
# Tier A (+ 3-layer, Tier B) tuning + test eval under protocol P1/P2 (ledger): 2 x 64 evals each, at most 2 jobs at a time.
#   bash run_tierA.sh [tag ...]      (default: all)   logs -> results/logs/<tag>.log
cd "$(dirname "$0")"; mkdir -p results/logs
declare -A C=([indi]=INDI [l1]=L1 [mrac_s6]=MRAC_S6 [mrac_s10]=MRAC_S10 [mrac_rbf6]=MRAC_RBF6
              [mrac_rbf12]=MRAC_RBF12 [mrac_rbf24]=MRAC_RBF24 [se3_eso]=SE3ESO
              [mrac3l_unrouted]=MRAC3L_Unrouted [mrac3l_reactive]=MRAC3L_Reactive [mrac3l_predictive]=MRAC3L_Predictive
              [mrac3l_both]=MRAC3L_Both [mrac_crm]=MRAC_CRM [mrac_composite]=MRAC_Composite [mrac_sataware]=MRAC_SatAware
              [mrac_proj]=MRAC_Proj)
job() {
  local t=$1 c=tierA:${C[$1]} s=results/pid_tuned_tune.json
  if [ "$t" = se3_eso ]; then python bench.py tune $c --tag se3_eso_s1 && s=results/se3_eso_s1_tune.json || return 1; fi
  python tune2.py $c --start $s --tag $t && python bench.py eval $c --params results/${t}_tune.json --split test --tag $t
}
tags=${@:-se3_eso indi l1 mrac_s6 mrac_s10 mrac_rbf6 mrac_rbf12 mrac_rbf24}
for t in $tags; do
  while [ "$(jobs -rp | wc -l)" -ge 2 ]; do wait -n; done
  (job $t > results/logs/$t.log 2>&1; echo "$t rc=$?") &
done
wait
