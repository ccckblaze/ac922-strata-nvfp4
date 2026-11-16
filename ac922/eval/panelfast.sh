#!/bin/bash
# The adoption check: the four points that decide a candidate, and nothing else.
# Use when the lane must be back in service soon (a full lite panel is ~11 min, this is ~5).
#   panelfast.sh <TAG>   - waits for the lane to answer /health 200, then measures
set -u
N=/data/work/note312
source /etc/profile.d/env.sh
TAG=$1
P=/usr/bin/python3.11
until [ "$(curl -s -o /dev/null -w '%{http_code}' -m 4 -H "Authorization: Bearer $STRATA_API_KEY" \
           http://127.0.0.1:8091/health)" = "200" ]; do sleep 10; done
for spec in "8000 6 256" "50000 6 256" "131000 6 192" "8000 1 256" "50000 1 256"; do
  set -- $spec
  AB_TIMEOUT=${AB_TIMEOUT:-600} $P $N/aggbench.py --tag "$TAG" --depth $1 --n $2 --gen $3 \
    >> $N/shapes.jsonl 2>> $N/shapes.err
done
echo "panel=$TAG done $(date +%H:%M:%S)" >> $N/panel.log
