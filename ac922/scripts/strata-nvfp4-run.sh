#!/bin/bash
# Strata AC922 NVFP4-GPTQ lane launcher (port 8091, engine 0.1.59 == this checkout).
#
# Repository version of the production script. The final baseline (ac922/PERFORMANCE.md,
# adopted ledger in ac922/DECISIONS.md) ran on this layout:
#   AC922_ROOT=/data/work   SRC=/data/work/strata-nvfp4-src (= engine checkout, same HEAD)
#   MODEL_DIR=/data/work/Strata-data/models/nvfp4-gptq      LOGD=/data/champion
# Here SRC defaults to this repo root, so "compile from here, run from here" works:
#   cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=70 && cmake --build build -j
#   bash ac922/scripts/strata-nvfp4-run.sh start
# GPUS env selects the geometry: default 0,1,2,3,4,5 (6-card symmetric: GPU0-2 = NUMA node0
# NVLink triangle, GPU3-5 = node8 triangle). GPUS=0,1,2,3 restores the 4-card lane.
# NB_CUT is the expert-arena NUMA split and must track the island ratio (0.5 for 3:3, 0.69 for 3:1).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
AC922_ROOT=${AC922_ROOT:-$REPO}
SRC=${SRC:-$REPO}
MODEL_DIR=${MODEL_DIR:-$AC922_ROOT/Strata-data/models/nvfp4-gptq}
LOGD=${LOGD:-$AC922_ROOT/champion-logs}
BASE=${STRATA_BASE:-$REPO/ac922/configs/strata-nvfp4-final.json}
RUN=${RUN_JSON:-$AC922_ROOT/strata-nvfp4.run.json}
PIDF=${PIDF:-/tmp/strata-nvfp4-run.pid}
NUMABIND_SO=${NUMABIND_SO:-$AC922_ROOT/numabind.so}
PLE_PARK=${PLE_PARK:-$HERE/ple_park.sh}
mkdir -p "$LOGD"
stop(){
  if [ -f "$PIDF" ]; then kill "$(cat $PIDF)" 2>/dev/null; fi
  pkill -f "strata-nvfp4.run.json" 2>/dev/null
  sleep 2; pkill -f "build/strata --serve" 2>/dev/null
  rm -f "$PIDF"; echo stopped
}
start(){
  # Volta kernel opt-ins (docs/NVIDIA_V100.md, 0.2.13) + the adopted ledger env, shared with
  # the dev lane — see adopted-env.sh (SM70_TABLE / PF_WMMA / FUSE_DECODE / ARGMAX_ROWS_SM70 /
  # select-simt / batchenv, all default-on unless the caller overrides).
  . "$HERE/adopted-env.sh"
  GPUS=${GPUS:-0,1,2,3,4,5}
  export CUDA_VISIBLE_DEVICES="$GPUS"
  export NB_CUT=${NB_CUT:-0.5}
  # >>> AC922 adopted env (DECISIONS.md: T10 NUMA bind) >>>
  # expert-arena NUMA placement via mmap hook: arena[0, NB_CUT)->node0 island, rest->node8 island.
  if [ -f "$NUMABIND_SO" ]; then export LD_PRELOAD="$NUMABIND_SO"; fi
  TS=$(date -u +%FT%TZ)
  LOG="$LOGD/strata-nvfp4-$TS.log"; echo "LOG=$LOG"; echo "GPUS=$CUDA_VISIBLE_DEVICES NB_CUT=$NB_CUT"
  # Rewrite the reference config onto this machine's layout (paths only; args untouched except
  # the batch pair — STRATA_BATCH / STRATA_BATCH_GROUPS rewrite them in place, so a 4-card boot
  # must pass 4/2 and a 6-card boot 6/3 explicitly if the defaults below ever change).
  python3.11 - "$BASE" "$RUN" "${STRATA_BATCH:-0}" "${STRATA_BATCH_GROUPS:-0}" "$SRC" "$MODEL_DIR" "$LOGD" <<'PY'
import json,os,sys
base,out,batch,groups,src,model_dir,logd=(sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),
                                          sys.argv[5],sys.argv[6],sys.argv[7])
c=json.load(open(base))
c["exe"]=os.path.join(src,"build","strata"); c["cwd"]=src
c["log"]=os.path.join(logd,"strata-nvfp4-engine.log")
a=[]
for x in c["args"]:
    a.append(x.replace("/data/work/strata-nvfp4-src", src).replace("/data/work/Strata-data/models/nvfp4-gptq", model_dir))
c["args"]=a
c["gpu"]=[int(x) for x in os.environ["CUDA_VISIBLE_DEVICES"].split(",")]
fix=lambda s: str(s).replace("/data/work/strata-nvfp4-src", src).replace("/data/work/Strata-data/models/nvfp4-gptq", model_dir)
if os.environ.get("VISION","1")=="0":
    c.pop("vision",None); print("vision=off")
elif isinstance(c.get("vision"),dict):
    v=c["vision"]
    for k in ("exe","mmproj","model"):
        if k in v: v[k]=fix(v[k])
    print("vision=%s mmproj=%s gpu=%s max_tokens=%s" % (os.path.basename(str(v.get("exe",""))), os.path.basename(str(v.get("mmproj",""))), v.get("gpu"), v.get("max_tokens")))
print("gpu=",c["gpu"])
def setflag(name,val):
    if name in c["args"]:
        c["args"][c["args"].index(name)+1]=str(val)
    else:
        c["args"]+=[name,str(val)]
if batch>=2: setflag("--batch",batch)
if groups>=2: setflag("--batch-groups",groups)
if os.environ.get("SPEC_MIN_P"): setflag("--spec-min-p",os.environ["SPEC_MIN_P"])
ncard=len(c["gpu"])
fbatch=int(c["args"][c["args"].index("--batch")+1]); fgroups=int(c["args"][c["args"].index("--batch-groups")+1])
if batch<2 and groups<2 and fbatch!=ncard:
    print("NOTE: effective --batch %d on %d cards (accepted shapes: 6-card 6/3, 4-card 4/2)" % (fbatch,ncard))
if fbatch%fgroups: print("WARNING: --batch %d not divisible by --batch-groups %d" % (fbatch,fgroups))
json.dump(c,open(out,"w")); print("batch=",batch)
PY
  # >>> AC922 adopted env (DECISIONS.md: PLE park) >>>
  if [ -x "$PLE_PARK" ]; then PLE_SPLIT="${PLE_SPLIT:-500}" "$PLE_PARK" "${PLE_SPLIT:-500}" || true; fi
  # <<< AC922 adopted env <<<
  ( cd "$SRC" && setsid /usr/bin/python3.11 -m serve.server --engine strata --config "$RUN" --host 127.0.0.1 --port 8091 >"$LOG" 2>&1 & echo $! > "$PIDF" )
  echo "started pid $(cat $PIDF)"
}
status(){
  echo "== server pid =="; [ -f "$PIDF" ] && cat "$PIDF" && ps -o pid,etime,rss,cmd -p "$(cat $PIDF)" 2>/dev/null | tail -1
  echo "== engine =="; pgrep -af "build/strata --serve" | head -1
  echo "== health =="; curl -s -m5 http://127.0.0.1:8091/health; echo
  echo "== gpu =="; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | head -6
  echo "== ram =="; awk '/MemAvailable/{print $2/1048576" GiB avail"}' /proc/meminfo
}
case "${1:-}" in
  start) start;; stop) stop;; restart) stop; sleep 2; start;; status) status;; *) echo "usage: $0 {start|stop|restart|status}";;
esac
