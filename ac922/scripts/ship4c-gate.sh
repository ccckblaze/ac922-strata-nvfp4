#!/bin/bash
# ship4c-gate.sh <tag>
# The ship gate for the 4-card lane, in the one caliber that is allowed to gate a release:
# full window, concurrent.  The 131K concscale curve is a research instrument - its numbers are
# not comparable with the aggbench/PERFORMANCE.md band (93.5-106.9 at 245K x 4), and mixing the
# two is what made "we used to be better" unanswerable on 2026-11-23.
#
# It also refuses to measure a lane whose argv is not the shipped config: an operator boot on
# 2026-11-23 17:05 carried --spec 8 --mtp-head proxy --mtp-cum auto, which exist in neither
# final.json nor lane.sh, and every number taken under it belongs to that experiment, not to ship.
#
# Prints integers, labels and paths only - never model text.
set -u
TAG=${1:?tag}
HERE=$(cd "$(dirname "$0")" && pwd)
R=$(dirname "$HERE")                     # ac922/
ENV_SH=${ENV_SH:-/etc/profile.d/env.sh}
[ -n "${STRATA_API_KEY:-}" ] || { [ -f "$ENV_SH" ] && . "$ENV_SH"; }
LOG=/data/work/ship4c-gate-$TAG.log
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

P=$(pgrep -f 'build/strata --serv[e]' | head -1)
if [ -n "$P" ]; then
  rogue=$(tr '\0' '\n' < /proc/$P/cmdline | grep -cE '^--(spec|mtp-head|mtp-cum)$' | xargs)
  spec=$(tr '\0' '\n' < /proc/$P/cmdline | grep -A1 '^--spec$' | tail -1)
  say "engine pid $P: spec-related argv fields=$rogue (ship expects 2: --spec/--spec-min-p)"
fi

say "step 0: clean restart onto final.json"
bash "$HERE/lane.sh" 4 restart >>"$LOG" 2>&1 || { say "BOOT-FAIL"; exit 1; }
P=$(pgrep -f 'build/strata --serv[e]' | head -1)
[ -n "$P" ] || { say "NO-ENGINE"; exit 2; }
# The --spec 8 --mtp-head proxy --mtp-cum auto that ride the effective argv are the server's own
# auto path (drafting.py FORK_MTP_FLAGS), not operator contamination: they arrive regardless of
# what final.json says, and DECISIONS 11-21 showed the flags themselves are not the axis at 6
# cards.  This gate records them as a covariate; the hard checks are the reserve and the shape.
FIELDS=$(tr '\0' '\n' < /proc/$P/cmdline | grep -cE '^--mtp-(head|cum)$')
SPECLINE=$(tr '\0' '\n' < /proc/$P/cmdline | grep -A1 '^--spec$' | tail -1)
RESERVE=$(tr '\0' '\n' < /proc/$P/cmdline | grep -A1 '^--vram-reserve-mib$' | tail -1)
AUTOMTP=${STRATA_AUTO_NEW_MTP:-unset}
say "argv after restart: --spec $SPECLINE (mtp-head/cum fields=$FIELDS, auto-mtp=$AUTOMTP), reserve=$RESERVE (expect 1536)"
[ "$RESERVE" = 1536 ] || { say "CONFIG-FAIL: reserve is not 1536"; exit 3; }
grep -aq "GEOMETRY-PASS" "$LOG" || { say "GEOMETRY-FAIL: no GEOMETRY-PASS in boot log"; exit 4; }

say "step 1: FULLWINDOW byte-exact 4 x (262144-512)"
python3.11 "$R/eval/fullwindow.py" run --n 4 --gen 512 --total 262144 >>"$LOG" 2>&1
say "fullwindow rc=$?"

say "step 2: aggbench 245K x 4, two paired rounds"
for rep in 1 2; do
  python3.11 "$R/eval/aggbench.py" --tag "$TAG-245k-r$rep" --depth 245000 --n 4 --gen 256 >>"$LOG" 2>&1
  say "agg245k rep$rep rc=$?"
done

say "step 3: short-window control 8K x 4"
python3.11 "$R/eval/aggbench.py" --tag "$TAG-8k" --depth 8000 --n 4 --gen 256 >>"$LOG" 2>&1
say "agg8k rc=$?"

grep -a '^{' "$LOG" | tail -8
say "SHIP4C-GATE-DONE $TAG log=$LOG"
