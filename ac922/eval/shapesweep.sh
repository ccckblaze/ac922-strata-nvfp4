#!/bin/bash
# One window-shape arm: boot the production lane with a given --batch/--batch-groups, run the
# standard decode panel, append one row per measurement.  The shipped config stays untouched;
# the arm's config is a copy rewritten by sed, fed in through STRATA_BASE.
#
# usage: shapesweep.sh <TAG> <batch> <groups> [<extra-env-assignments>]
#   extra env goes to the launcher (STRATA_COALESCE_PREFILL=4 STRATA_DECODE_TIMING=1 ...)
set -u
R=/data/work/ac922-strata-nvfp4
N=/data/work/note312
BASECFG=$R/ac922/configs/strata-nvfp4-final.json
source /etc/profile.d/env.sh
TAG=$1; B=$2; G=$3; EXTRA=${4:-}
CFG=$N/shape-$TAG.json
cp "$BASECFG" "$CFG"
python3.11 - "$CFG" "$B" "$G" <<'PY'
import json,sys
p,b,g=sys.argv[1:4]
c=json.load(open(p)); a=c["args"]
for k in ("--batch","--batch-groups"): a[a.index(k)+1]=b if k=="--batch" else g
json.dump(c,open(p,"w"))
PY
echo "arm=$TAG batch=$B groups=$G extra=[$EXTRA] $(date +%H:%M:%S)"
bash $R/ac922/scripts/strata-nvfp4-run.sh stop > $N/sweep-$TAG.stop.log 2>&1
sleep 3
env GPUS=0,1,2,3,4,5 NB_CUT=0.5 STRATA_BASE=$CFG $EXTRA bash $R/ac922/scripts/strata-nvfp4-run.sh start > $N/sweep-$TAG.start.log 2>&1
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 4 -H "Authorization: Bearer $STRATA_API_KEY" http://127.0.0.1:8091/health)
  [ "$code" = "200" ] && break
  sleep 10
done
echo "health=$code $(date +%H:%M:%S)"
[ "$code" = "200" ] || { echo "ARM-FAILED $TAG"; exit 1; }
grep -aoE "batch_slots=[0-9]+|effective --batch [0-9]+ on [0-9]+ cards" $N/sweep-$TAG.start.log | tail -2
python3.11 $N/panel.sh "$TAG"
echo "arm=$TAG done $(date +%H:%M:%S)"
