#!/bin/bash
# ship6c.sh <floor> <tag> [mode]
# Boot the 6-card lane with a given CC_MIN_TOKENS, prove the shape, and price the bands.
# mode=full : geometry+lanecheck+fullwindow(6x262136)+agg 8Kx6 x3+agg 245Kx6 x2+turnprobe+parkpressure
# mode=short: agg 8Kx6 x2 + turnprobe (control arm, boot-level pair)
set -u
MIN=${1:?floor}
TAG=${2:?tag}
MODE=${3:-full}
R=/data/work/ac922-strata-nvfp4/ac922
ENVF=/data/work/lane.env
say(){ echo "[$(date +%H:%M:%S)] $*"; }

grep -v '^export CC_MIN_TOKENS' "$ENVF" > "$ENVF.new" \
  && echo "export CC_MIN_TOKENS=$MIN" >> "$ENVF.new" \
  && mv "$ENVF.new" "$ENVF" \
  || { echo "ENVEDIT-FAIL"; exit 9; }
say "lane.env floor -> $MIN"

bash "$R/scripts/lane.sh" 6 restart > "/data/work/ship6c-$TAG.boot.log" 2>&1
RC=$?
say "lane6 restart rc=$RC"
grep -aoE 'GEOMETRY-PASS[^"]*|GEOMETRY-FAIL[^"]*' "/data/work/ship6c-$TAG.boot.log" | tail -1
PID=$(pgrep -f 'build/strata --serv[e]' | head -1)
[ -z "$PID" ] && { echo "NO-ENGINE"; exit 8; }
say "engine pid $PID floor=$(tr '\0' '\n' < /proc/$PID/cmdline | grep -A1 'conversation-cache-min-tokens' | tail -1) batch=$(tr '\0' '\n' < /proc/$PID/cmdline | grep -A1 '^--batch$' | tail -1)/$(tr '\0' '\n' < /proc/$PID/cmdline | grep -A1 'batch-groups' | tail -1)"
KEY=$(tr '\0' '\n' < /proc/$(pgrep -f 'python3.11 -m serve.server' | head -1)/environ | sed -n 's/^STRATA_API_KEY=//p' | head -1)
export STRATA_API_KEY="$KEY"
say "key len ${#KEY}"

if [ "$MODE" = full ]; then
  bash "$R/scripts/lanecheck.sh" 6 > "/data/work/ship6c-$TAG.lanecheck.log" 2>&1
  say "lanecheck: $(tail -1 /data/work/ship6c-$TAG.lanecheck.log)"
  python3.11 "$R/eval/fullwindow.py" run --n 6 --gen 512 --total 262144
  say "FULLWINDOW rc=$?"
fi
for rep in 1 2; do
  python3.11 "$R/eval/aggbench.py" --tag "$TAG-8k" --depth 8000 --n 6 --gen 256
  say "agg8k rep$rep done rc=$?"
done
if [ "$MODE" = full ]; then
  for rep in 1 2; do
    python3.11 "$R/eval/aggbench.py" --tag "$TAG-245k" --depth 245000 --n 6 --gen 256
    say "agg245k rep$rep done rc=$?"
  done
  python3.11 "$R/eval/parkpressure.py" > "/data/work/ship6c-$TAG.parkpressure.log" 2>&1
  say "parkpressure rc=$? verdict=$(grep -aoE 'PARKPRESSURE-(PASS|FAIL)[^"]*' /data/work/ship6c-$TAG.parkpressure.log | tail -1)"
  grep -a '^{' "/data/work/ship6c-$TAG.parkpressure.log" | tail -3
fi
python3.11 "$R/eval/turnprobe.py" --depths 2000,4000,8000,32000,64000
say "SHIP6C-DONE $TAG mode=$MODE"
