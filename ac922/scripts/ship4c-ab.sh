#!/bin/bash
# ship4c-ab.sh <tag>  — one paired full-window A/B on the 4-card lane.
# Arm 1: shipped default (the auto path rewrites drafting to --spec 8 --mtp-head proxy --mtp-cum
# auto, H.35(b): never A/B'd on this lane, and drafting.py's own docstring says "not a split").
# Arm 2: STRATA_AUTO_NEW_MTP=0, the factory --spec 6 --spec-min-p 0.7.
# Each arm: clean boot -> GEOMETRY-PASS -> fullwindow byte-exact 4x(262144-512) -> aggbench
# 245K x 4 twice -> aggbench 8K x 4.  The lane ends on the shipped default, whichever arm wins
# the verdict is written by the caller from $LOG lines.  Numbers, labels, hashes only.
set -u
TAG=${1:?tag}
HERE=$(cd "$(dirname "$0")" && pwd)
R=$(dirname "$HERE")
ENV_SH=${ENV_SH:-/etc/profile.d/env.sh}
[ -n "${STRATA_API_KEY:-}" ] || { [ -f "$ENV_SH" ] && . "$ENV_SH"; }
LOG=/data/work/ship4c-ab-$TAG.log
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

run_arm(){ # $1 arm label, $2 auto-mtp env value ("" = default)
  local arm=$1 val=$2
  say "=== ARM $arm: STRATA_AUTO_NEW_MTP=${val:-unset} ==="
  if [ -n "$val" ]; then export STRATA_AUTO_NEW_MTP="$val"; else unset STRATA_AUTO_NEW_MTP; fi
  bash "$HERE/lane.sh" 4 restart >>"$LOG" 2>&1 || { say "$arm BOOT-FAIL"; return 1; }
  local P=$(pgrep -f 'build/strata --serv[e]' | head -1)
  [ -n "$P" ] || { say "$arm NO-ENGINE"; return 2; }
  local spec=$(tr '\0' '\n' < /proc/$P/cmdline | grep -A1 '^--spec$' | tail -1)
  local mtpf=$(tr '\0' '\n' < /proc/$P/cmdline | grep -cE '^--mtp-(head|cum)$')
  say "$arm effective argv: --spec $spec mtp-head/cum fields=$mtpf"
  python3.11 "$R/eval/fullwindow.py" run --n 4 --gen 512 --total 262144 >>"$LOG" 2>&1
  say "$arm fullwindow rc=$?"
  for rep in 1 2; do
    python3.11 "$R/eval/aggbench.py" --tag "$arm-245k-r$rep" --depth 245000 --n 4 --gen 256 >>"$LOG" 2>&1
    say "$arm agg245k r$rep rc=$?"
  done
  python3.11 "$R/eval/aggbench.py" --tag "$arm-8k" --depth 8000 --n 4 --gen 256 >>"$LOG" 2>&1
  say "$arm agg8k rc=$?"
}

run_arm A-default ""
run_arm B-factory 0
say "restore shipped default boot"
bash "$HERE/lane.sh" 4 restart >>"$LOG" 2>&1 && say "FINAL-BOOT-OK" || say "FINAL-BOOT-FAIL"
say "SHIP4C-AB-DONE $TAG log=$LOG"
