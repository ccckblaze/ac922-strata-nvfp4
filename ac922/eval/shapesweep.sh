#!/bin/bash
# One window-shape arm on the production lane: rewrite a config copy, boot it through the
# production shim, run the standard panel, capture the engine's window anatomy for that boot.
#
#   shapesweep.sh <TAG> <batch> <groups> [<extra-env>] [<panel-mode>]
#     panel-mode  full (default) | lite (no prefill-bound 262K point) | none (boot only)
#     env XARGS="flag value;--switch"  engine args the arm needs beyond the shape
#     env SPLIT="8,16,24,32,40"        explicit layer split (--trim-stage-weights needs it)
#
# The shipped config is never touched - every arm runs from a copy in note312. The lane is
# always reached through /root/strata-nvfp4-run.sh: that shim exports AC922_ROOT/SRC/MODEL_DIR,
# and the launcher's path rewrite builds the model locations from them, so calling the repo
# launcher bare makes every arm die later at "cannot open the text model".
set -u
R=/data/work/ac922-strata-nvfp4
N=/data/work/note312
BASECFG=$R/ac922/configs/strata-nvfp4-final.json
LANCHE=${LANCHE:-/root/strata-nvfp4-run.sh}
source /etc/profile.d/env.sh
TAG=${1:?tag}; B=${2:?batch}; G=${3:?groups}; EXTRA=${4:-}; MODE=${5:-full}
XARGS=${XARGS:-}; SPLIT=${SPLIT:-}
CFG=$N/shape-$TAG.json
cp "$BASECFG" "$CFG"
python3.11 - "$CFG" "$B" "$G" "$XARGS" "$SPLIT" <<'PY'
import json, sys
p, b, g, xargs, split = sys.argv[1:6]
c = json.load(open(p)); a = c["args"]
for k, v in (("--batch", b), ("--batch-groups", g)):
    a[a.index(k) + 1] = v
for pair in [x.strip() for x in xargs.split(";") if x.strip()]:
    parts = pair.split()
    name = parts[0]
    if len(parts) > 1:
        if name in a:
            a[a.index(name) + 1] = parts[1]
        else:
            a += [name, parts[1]]
    elif name not in a:
        a.append(name)                       # a valueless switch, e.g. --trim-stage-weights
if split:
    c["layer_split"] = split
json.dump(c, open(p, "w"), indent=1)
PY
echo "arm=$TAG batch=$B groups=$G mode=$MODE extra=[$EXTRA] xargs=[$XARGS] split=[$SPLIT] $(date +%H:%M:%S)"
bash $LANCHE stop > $N/sweep-$TAG.stop.log 2>&1
sleep 3
env GPUS=0,1,2,3,4,5 NB_CUT=0.5 STRATA_BASE=$CFG $EXTRA bash $LANCHE start > $N/sweep-$TAG.start.log 2>&1
code=000
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 4 -H "Authorization: Bearer $STRATA_API_KEY" http://127.0.0.1:8091/health)
  [ "$code" = "200" ] && break
  sleep 10
done
echo "health=$code $(date +%H:%M:%S)"
[ "$code" = "200" ] || { echo "ARM-BOOT-FAILED $TAG $code" >> $N/shapes.err; exit 1; }
grep -aoE "batch_slots=[0-9]+|batch_groups=[0-9]+|effective --batch [0-9]+ on [0-9]+ cards" $N/sweep-$TAG.start.log | tail -3
if [ "$MODE" != none ]; then
  bash $N/panel.sh "$TAG" "$MODE" >> $N/sweep-$TAG.out 2>&1 || { echo "ARM-PANEL-FAILED $TAG" >> $N/shapes.err; exit 1; }
  grep -qa "panel=$TAG done" $N/panel.log || { echo "ARM-PANEL-NO-MARKER $TAG" >> $N/shapes.err; exit 1; }
fi
# Anatomy needs STRATA_DECODE_TIMING=1 (the engine gates it), so this file is empty unless the
# arm asked for it. The bucket key - avg rows - is what says whether the shape really shared.
python3.11 $N/window_anatomy.py --boot last --min 2 > $N/anatomy-$TAG.txt 2>&1 || true
echo "arm=$TAG done $(date +%H:%M:%S)"
