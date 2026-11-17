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
REANCHOR=0
for a in "$@"; do [ "$a" = "--reanchor" ] && REANCHOR=1; done
PORT=${PORT:-8091}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-900}              # a cold boot locks ~58 GiB of experts: 3-7 min
FREE_TIMEOUT=${FREE_TIMEOUT:-240}
RESERVE_MIB=${RESERVE_MIB:-900}
QUALGATE_DIR=${QUALGATE_DIR:-/data/work/qualgate}
EVAL_DIR=${EVAL_DIR:-$REPO/ac922/eval}

case "$CARDS" in
  4) GPUS=${GPUS:-0,1,2,3};   EXPECT_BATCH="2 groups of 2"; EXPECT_STAGES=4 ;;
  6) GPUS=${GPUS:-0,1,2,3,4,5}; EXPECT_BATCH="3 groups of 2"; EXPECT_STAGES=6 ;;
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
verify(){ local L; L=$(engine_log); [ -f "$L" ] || { log "GEOMETRY-FAIL no engine log at $L"; return 1; }
  local shape stages split numa
  shape=$(tr '\r' '\n' < "$L" | grep -a "batch (pipelined" | tail -1)
  split=$(tr '\r' '\n' < "$L" | grep -a "serve: layer split: layers" | tail -1)
  stages=$(echo "$split" | grep -o "CUDA[0-9]" | sort -u | wc -l | xargs)
  numa=$(tr '\r' '\n' < "$L" | grep -ac "NUMA placement FAILED\|the split runs with remote reads")
  log "engine log: $L"
  log "  shape : ${shape:-<none>}"
  log "  split : ${split:-<none>}"
  log "  numa  : ${numa} FAILED/remote-read lines"
  echo "$shape" | grep -q "$EXPECT_BATCH" || { log "GEOMETRY-FAIL expected '$EXPECT_BATCH' on $CARDS cards"; return 1; }
  [ "$stages" = "$CARDS" ] || { log "GEOMETRY-FAIL engine reports $stages stages, expected $CARDS"; return 1; }
  [ "$numa" = 0 ] || { log "GEOMETRY-FAIL NUMA shim not effective: this boot is not comparable"; return 1; }
  log "GEOMETRY-PASS $CARDS cards ($GPUS), $EXPECT_BATCH"
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
