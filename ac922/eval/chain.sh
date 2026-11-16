#!/bin/bash
# Queue shape arms back to back on the production lane.
#
#   chain.sh WAIT <panel-tag> <ceiling-s>
#   chain.sh ARM <tag> <batch> <groups> <mode> <extra-env> "<XARGS>" "<SPLIT>"
#
# XARGS/SPLIT are handed to shapesweep as its engine-arg rewrite (SPLIT only matters with
# --trim-stage-weights, which needs an explicit layer split, not auto).  An arm that fails to
# boot or fails its panel is logged and skipped; the queue never dies with one arm.
set -u
N=/data/work/note312
source /etc/profile.d/env.sh
while [ $# -gt 0 ]; do
  case "$1" in
    WAIT)
      tag=$2; ceil=$((SECONDS + ${3:-1200})); shift 3
      until grep -qa "panel=$tag done" $N/panel.log 2>/dev/null; do
        [ $SECONDS -lt $ceil ] || { echo "chain: TIMEOUT waiting for panel $tag $(date +%H:%M:%S)" >> $N/chain.log; break; }
        sleep 20
      done
      echo "chain: settled $tag $(date +%H:%M:%S)" >> $N/chain.log ;;
    ARM)
      tag=$2; b=$3; g=$4; mode=$5; extra=$6; xa=${7:-}; sp=${8:-}; shift 8
      echo "chain: arm $tag start $(date +%H:%M:%S)" >> $N/chain.log
      env XARGS="$xa" SPLIT="$sp" bash $N/shapesweep.sh "$tag" "$b" "$g" "$extra" "$mode" \
        > $N/chain-$tag.out 2>&1
      rc=$?
      echo "chain: arm $tag end rc=$rc $(date +%H:%M:%S)" >> $N/chain.log ;;
    *) shift ;;
  esac
done
echo "chain: queue finished $(date +%H:%M:%S)" >> $N/chain.log
