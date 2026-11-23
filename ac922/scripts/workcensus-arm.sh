#!/bin/bash
# One census arm: boot the lane with a given layer split, drive the phases, print the summary.
# Usage (detached per runbook 328):
#   setsid nohup bash ac922/scripts/workcensus-arm.sh <layer-split> <nonce> \
#       > /data/work/census-<nonce>.log 2>&1 < /dev/null &
# boot ~100 s + measure ~8 min.  An empty <layer-split> keeps the shipped geometry (9,24,37): the
# launcher treats a present-but-empty STRATA_LAYER_SPLIT as an override, so it is exported only when set.
set -u
source /etc/profile.d/env.sh
SPLIT="${1-}"; NONCE="${2-}"
[ -n "$NONCE" ] || { echo "usage: $0 <layer-split|''> <nonce>"; exit 2; }
cd /data/work/ac922-strata-nvfp4
[ -n "$SPLIT" ] && export STRATA_LAYER_SPLIT="$SPLIT"
bash ac922/scripts/lane.sh 4 restart > /data/work/census-$NONCE-boot.log 2>&1
echo "boot rc=$?"
grep -aE "GEOMETRY-(PASS|FAIL)" /data/work/census-$NONCE-boot.log | tail -2
i=0
until curl -sf -H "Authorization: Bearer $STRATA_API_KEY" http://127.0.0.1:8091/health >/dev/null 2>&1; do
    i=$((i+1)); [ $i -gt 40 ] && { echo NO-HEALTH; exit 1; }; sleep 5
done
echo HEALTH-OK
HERE="$(cd "$(dirname "$0")" && pwd)"
env CARDS="${CARDS:-0,1,3,4}" python3.11 "$HERE/workcensus.py" "$NONCE" > /data/work/census-$NONCE.out 2>&1
echo "measure rc=$?"
python3.11 "$HERE/workcensus-sum.py" "$NONCE"
