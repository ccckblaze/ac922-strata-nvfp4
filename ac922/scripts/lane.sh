#!/bin/bash
# One entry point for AC922 NVFP4 lane geometry: lane.sh <4|6> <start|stop|restart|status> [--reanchor]
#
# Why this exists.  A geometry switch is not one variable: the launcher consumes the card list, the
# NUMA cut, the batch pair, the machine layout (SRC/MODEL_DIR/LOGD/RUN_JSON/PIDF) and the NUMA shim
# path.  Getting the *geometry* right and the *layout* wrong boots a lane that looks healthy and is
# silently not comparable — measured 2026-11-17, when calling the repo launcher directly skipped
# LD_PRELOAD=numabind.so and the engine answered with "NUMA placement FAILED ... remote reads".
# This script pins the layout, refuses a boot that cannot match the anchored numbers, and proves the
# geometry from the engine's own words instead of from the command line we typed.
#
# lane4.sh / lane6.sh are the shortcuts; they only choose the card count.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
LANE_ENV=${LANE_ENV:-/data/work/lane.env}          # machine layout: AC922_ROOT/SRC/MODEL_DIR/LOGD/RUN_JSON/PIDF
[ -f "$LANE_ENV" ] && . "$LANE_ENV"
RUN="$HERE/strata-nvfp4-run.sh"
ENV_SH=${ENV_SH:-/etc/profile.d/env.sh}
[ -n "${STRATA_API_KEY:-}" ] || { [ -f "$ENV_SH" ] && . "$ENV_SH"; }
CARDS=${1:-}; ACTION=${2:-start}; shift 2 2>/dev/null || true
STATE=${STATE:-/tmp/strata-nvfp4-lane.state}       # engine-log byte offset of the boot we started
REANCHOR=0
for a in "$@"; do [ "$a" = "--reanchor" ] && REANCHOR=1; done
PORT=${PORT:-8091}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-900}              # a cold boot locks ~58 GiB of experts: 3-7 min
FREE_TIMEOUT=${FREE_TIMEOUT:-240}
RESERVE_MIB=${RESERVE_MIB:-900}
QUALGATE_DIR=${QUALGATE_DIR:-/data/work/qualgate}
EVAL_DIR=${EVAL_DIR:-$REPO/ac922/eval}

case "$CARDS" in
  4) GPUS=${GPUS:-0,1,2,3};     EXPECT_GROUPS=2 ;;
  6) GPUS=${GPUS:-0,1,2,3,4,5}; EXPECT_GROUPS=3 ;;
  *) echo "usage: $(basename $0) <4|6> <start|stop|restart|status> [--reanchor]"; exit 2 ;;
esac
export GPUS CUDA_VISIBLE_DEVICES="$GPUS"
IFS=, read -ra G <<< "$GPUS"

log(){ printf '%s\n' "$*"; }
health(){ curl -s -m5 ${STRATA_API_KEY:+-H "Authorization: Bearer $STRATA_API_KEY"} "http://127.0.0.1:$PORT/health" | head -c 200; }
health_code(){ curl -s -m5 -o /dev/null -w "%{http_code}" ${STRATA_API_KEY:+-H "Authorization: Bearer $STRATA_API_KEY"} "http://127.0.0.1:$PORT/health"; }
card_mem(){ nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits; }
holders(){ # which pids hold which cards, so a switch never silently lands on a busy card
  nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader,nounits 2>/dev/null \
  | while IFS=, read -r u p m; do
      u=$(echo "$u" | xargs); idx=$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | awk -F', ' -v u="$u" '$2==u{print $1}')
      echo "card ${idx:-?} pid $(echo "$p" | xargs) $(echo "$m" | xargs) MiB"
    done
}
wait_free(){ local t0=$(date +%s) busy
  while [ $(( $(date +%s) - t0 )) -lt "$FREE_TIMEOUT" ]; do
    busy=$(card_mem | awk -F', *' -v g="$GPUS" 'BEGIN{split(g,a,",");for(i in a)m[a[i]]=1} $1 in m && $2>'"$RESERVE_MIB"'{print}')
    [ -z "$busy" ] && { log "cards released ($GPUS)"; return 0; }
    sleep 5
  done
  log "TIMEOUT waiting for cards to release:"; echo "$busy"; return 1
}
wait_health(){ local t0=$(date +%s)
  while [ $(( $(date +%s) - t0 )) -lt "$HEALTH_TIMEOUT" ]; do
    [ "$(health_code)" = "200" ] && { log "health 200 after $(( $(date +%s) - t0 )) s"; return 0; }
    sleep 10
  done
  log "TIMEOUT: lane never answered /health within ${HEALTH_TIMEOUT}s"; return 1
}
engine_log(){ # the launcher writes the real path into the run json; trust that, not a guess
  local rj=${RUN_JSON:-$REPO/strata-nvfp4.run.json}
  [ -f "$rj" ] && python3.11 -c "import json;print(json.load(open('$rj'))['log'])" 2>/dev/null
}
engine_pid(){ pgrep -f "build/strata --serve" | head -1; }
boot_slice(){ # THIS boot's engine-log lines only.  The engine log is appended across boots, so
  # "the last shape line" and "any NUMA line in the file" are both stale by construction: measured
  # 2026-11-17, a 6-card boot read back as "2 groups of 2" because the newest runtime stats line
  # still belonged to the 4-card lane that ran before it.  Prefer the recorded byte offset; fall
  # back to the last per-boot startup marker when someone booted without this script.
  local L=$1 off=0 n
  [ -f "$STATE" ] && off=$(sed -n 's/^off=//p' "$STATE" | head -1)
  if [ "${off:-0}" -gt 0 ] 2>/dev/null && [ "$off" -le "$(stat -c %s "$L")" ]; then
    tail -c +$((off + 1)) "$L"
  else
    n=$(grep -an "pipelined groups of" "$L" | tail -1 | cut -d: -f1)
    sed -n "${n:-1},\$p" "$L"
  fi | tr '\r' '\n'
}
verify(){ local L; L=$(engine_log); [ -f "$L" ] || { log "GEOMETRY-FAIL no engine log at $L"; return 1; }
  local S shape split stages remote firsttouch p pre vis
  S=$(boot_slice "$L")
  # The startup line, not the runtime one: the runtime `batch (pipelined, ...)` stats line only
  # exists once traffic has flowed, and when it does it can belong to an earlier boot.
  shape=$(echo "$S" | grep -a "STRATA_PIPED_MTP:" | tail -1)
  split=$(echo "$S" | grep -a "serve: layer split: layers" | tail -1)
  stages=$(echo "$split" | grep -o "CUDA[0-9]" | sort -u | wc -l | xargs)
  remote=$(echo "$S" | grep -ac "the split runs with remote reads")
  firsttouch=$(echo "$S" | grep -ac "NUMA placement FAILED")
  p=$(engine_pid)
  pre=""; vis=""
  [ -n "$p" ] && pre=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -m1 "^LD_PRELOAD=")
  [ -n "$p" ] && vis=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -m1 "^CUDA_VISIBLE_DEVICES=")
  log "engine log: $L (pid ${p:-none})"
  log "  shape : ${shape##*generate: }"
  log "  split : ${split##*serve: }"
  log "  shim  : ${pre:-LD_PRELOAD=<unset>}"
  log "  numa  : ${remote} remote-read lines, ${firsttouch} first-touch placement notes"
  echo "$shape" | grep -q "$EXPECT_GROUPS pipelined groups of 2 slots" ||
    { log "GEOMETRY-FAIL expected $EXPECT_GROUPS pipelined groups of 2 slots on $CARDS cards"; return 1; }
  [ "$stages" = "$CARDS" ] || { log "GEOMETRY-FAIL engine reports $stages stages, expected $CARDS"; return 1; }
  [ "${vis#*=}" = "$GPUS" ] || { log "GEOMETRY-FAIL engine sees ${vis:-nothing}, expected $GPUS"; return 1; }
  case "$pre" in *numabind.so*) ;; *) log "GEOMETRY-FAIL the NUMA shim is not in this process: numbers are not comparable"; return 1;; esac
  [ "$remote" = 0 ] || { log "GEOMETRY-FAIL $remote remote-read lines: arena placement did not hold"; return 1; }
  # "NUMA placement FAILED: this pack has no per-role byte sizes ... placed by first touch" is a
  # property of the pack, printed on every boot including the anchored ones.  Report it, do not
  # gate on it -- gating on it would fail every honest boot and teach people to ignore the script.
  [ "$firsttouch" = 0 ] || log "  note  : expert complement placed by first touch (pack has no per-role sizes) - known, not a regression"
  log "GEOMETRY-PASS $CARDS cards ($GPUS), $EXPECT_GROUPS groups of 2, shim loaded"
}
reanchor(){ # REF is per geometry (DECISIONS.md): keep the old anchor, write this shape's, then check
  local ref="$QUALGATE_DIR/REF.json" keep="$QUALGATE_DIR/REF-${CARDS}card-$(date +%Y%m%d).json"
  [ -f "$ref" ] && { cp "$ref" "$keep"; log "previous REF preserved as $(basename $keep)"; }
  ( cd "$EVAL_DIR" && bash qualgate.sh baseline "lane$CARDS" | tee "$QUALGATE_DIR/lane$CARDS-baseline.log" | tail -3 )
  ( cd "$EVAL_DIR" && bash qualgate.sh check "lane$CARDS"   | tee "$QUALGATE_DIR/lane$CARDS-check.log"   | tail -4 )
}

case "$ACTION" in
  start)
    if [ ! -f "${NUMABIND_SO:-${AC922_ROOT:-/data/work}/numabind.so}" ]; then
      log "REFUSING: NUMA shim missing — a boot without it logs remote reads and cannot be compared"
      log "  expected at ${NUMABIND_SO:-${AC922_ROOT:-/data/work}/numabind.so}; build it (RUN.md step 2) or set LANE_ENV"
      exit 1
    fi
    log "layout: SRC=${SRC:-$REPO} LOGD=${LOGD:-default} GPUS=$GPUS"
    L0=$(engine_log); OFF=0; [ -f "$L0" ] && OFF=$(stat -c %s "$L0")
    { echo "off=$OFF"; echo "cards=$CARDS"; echo "gpus=$GPUS"; echo "ts=$(date -u +%FT%TZ)"; } > "$STATE"
    occ=$(bash "$RUN" status 2>/dev/null | sed -n '/== cards ==/,$p' | head -4)
    [ -n "$occ" ] && log "occupancy before: $occ"
    bash "$RUN" start || exit 1
    wait_health || exit 1
    verify || exit 1
    card_mem | sed -n '1,6p'
    [ "$CARDS" = 4 ] && { log "leftover cards 4/5:"; holders | grep -E "card 4|card 5" || log "  free"; }
    [ "$REANCHOR" = 1 ] && reanchor
    ;;
  stop)   bash "$RUN" stop;;
  restart) bash "$RUN" stop; wait_free; sleep 5; "$0" "$CARDS" start $([ $REANCHOR = 1 ] && echo --reanchor);;
  status) bash "$RUN" status; verify;;
  *) log "usage: $(basename $0) <4|6> <start|stop|restart|status> [--reanchor]"; exit 2;;
esac
