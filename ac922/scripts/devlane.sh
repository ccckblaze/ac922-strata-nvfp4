#!/bin/bash
# AC922 dev lane: iterate on the leftover cards without touching the pinned lane.
#
# Two-lane policy (RUN.md "Development lane"):
#   production  GPUS 0,1,2,3 (B4, NB_CUT 0.69) or 0-5 (B6)   port 8091  strata-nvfp4-run.sh
#   dev lane    GPUS 4,5 (NUMA node8 NVLink triangle)         port 8092  this script
# Rules enforced here:
#   - Cards 4/5 may belong to another cooperating thread (I2V etc.): start refuses to launch
#     on top of any occupancy unless FORCE=1; `check` reports who holds the cards.
#   - NUMA: GPU3-5 sit on node8 (cpus 88-175), GPU0-2 on node0. The dev engine is bound to the
#     island its cards live on (cpunodebind + preferred mem), so its arena/staging does not
#     reach across the interconnect and steal node0 bandwidth from the production lane.
#     The dev lane does NOT use numabind.so/NB_CUT (that is the straddling-lane's arena split)
#     and does NOT re-run ple_park (page cache is shared read-only between lanes).
#   - stop kills only its own process tree — never the broad pkill used by the prod launcher.
# Sizing defaults shrink the host-tier budgets (RAM page cache for weights is shared with the
# prod lane, so the dev lane's real new footprint is KV host tier + expert cache VRAM).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
SRC=${SRC:-$REPO}
MODEL_DIR=${MODEL_DIR:-/data/work/Strata-data/models/nvfp4-gptq}
LOGD=${LOGD:-/data/champion}
BASE=${DEV_BASE:-$REPO/ac922/configs/strata-nvfp4-final.json}
RUN=${DEV_RUN_JSON:-/data/work/strata-nvfp4-dev.json}
PIDF=${DEV_PIDF:-/tmp/strata-nvfp4-dev.pid}
DEV_GPUS=${DEV_GPUS:-4,5}
DEV_PORT=${DEV_PORT:-8092}
DEV_MAX_CONTEXT=${DEV_MAX_CONTEXT:-131072}
DEV_KV_RESIDENT=${DEV_KV_RESIDENT:-16384}
DEV_CC_MIB=${DEV_CC_MIB:-32768}
DEV_CC_SLOTS=${DEV_CC_SLOTS:-4}
DEV_RESIDENT_GIB=${DEV_RESIDENT_GIB:-16}
DEV_MIN_AVAIL_GIB=${DEV_MIN_AVAIL_GIB:-32}
DEV_VISION=${DEV_VISION:-auto}          # auto|0|1
DEV_NUMA=${DEV_NUMA:-auto}              # auto|off|<node id>
mkdir -p "$LOGD"

. "$HERE/adopted-env.sh"

descendants(){ # recursive child list of a pid
  local p kids out
  for p in "$@"; do
    kids=$(pgrep -P "$p" 2>/dev/null || true)
    [ -n "$kids" ] && { echo $kids; descendants $kids; }
  done
}
own_pids(){
  [ -f "$PIDF" ] || return 0
  local root=$(cat "$PIDF" 2>/dev/null) || return 0
  [ -n "$root" ] || return 0
  echo "$root $(descendants "$root" | tr '\n' ' ')"
}
check(){
  /usr/bin/python3.11 - "$DEV_GPUS" "$(own_pids)" <<'PY'
import subprocess, sys
dev = [x for x in sys.argv[1].split(",") if x != ""]
own = {int(p) for p in sys.argv[2].split()}
uu = {}
for l in subprocess.check_output(["nvidia-smi","--query-gpu=index,uuid","--format=csv,noheader"],text=True).splitlines():
    i,u = [s.strip() for s in l.split(",",1)]; uu[u]=i
busy = {}
for l in subprocess.check_output(["nvidia-smi","--query-compute-apps=gpu_uuid,pid,used_memory","--format=csv,noheader"],text=True).splitlines():
    u,p,mem = [s.strip() for s in l.split(",",2)]
    idx = uu.get(u)
    if idx in dev and int(p) not in own:
        busy.setdefault(idx,[]).append((int(p),mem))
avail = float([l for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0].split()[1])/1048576
for idx,who in sorted(busy.items()):
    for pid,mem in who:
        try: cmd = " ".join(open("/proc/%d/cmdline"%pid).read().split("\0"))[:120]
        except Exception: cmd = "?"
        print("BUSY gpu%s pid=%d %s | %s" % (idx,pid,mem,cmd))
print("CHECK avail=%.1fGiB devgpus=%s own=%s" % (avail, dev, sorted(own) or "-"))
sys.exit(1 if busy else 0)
PY
}
numa_args(){
  case "$DEV_NUMA" in
    off) return 0;;
    [0-9]*) echo "numactl --cpunodebind=$DEV_NUMA --preferred=$DEV_NUMA"; return 0;;
  esac
  # derive the single NUMA node of the dev cards: sysfs numa_node with the 8-digit PCI domain
  # normalised (nvidia-smi prints 00000035:04:00.0, sysfs wants 0035:04:00.0); fall back to
  # the "NUMA Affinity" column of `nvidia-smi topo -m` addressed by header position.
  local node
  node=$(/usr/bin/python3.11 - "$DEV_GPUS" <<'PY'
import subprocess,sys
want={x.strip() for x in sys.argv[1].split(",") if x.strip()}
def norm(b):
    p=b.split(":"); p[0]="%04x"%int(p[0],16); return ":".join(p).lower()
topo=None
def via_topo(idx):
    global topo
    try:
        if topo is None: topo=subprocess.check_output(["nvidia-smi","topo","-m"],text=True).splitlines()
        hdr=[c.strip() for c in topo[0].split("\t")]
        k=[j for j,c in enumerate(hdr) if "NUMA Affinity" in c]
        if not k: return None
        for r in topo[1:]:
            f=r.split("\t")
            if f and f[0].strip()=="GPU"+idx: return f[k[0]].strip() or None
    except Exception: pass
    return None
nodes=set()
for l in subprocess.check_output(["nvidia-smi","--query-gpu=index,pci.bus_id","--format=csv,noheader"],text=True).splitlines():
    i,b=[x.strip() for x in l.split(",",1)]
    if i not in want: continue
    a=norm(b); got=None
    for p in ("/sys/bus/pci/devices/%s/numa_node"%a,"/sys/bus/pci/devices/%s/device/numa_node"%a):
        try:
            v=open(p).read().strip()
            if v and v!="-1": got=v; break
        except Exception: pass
    nodes.add(got if got is not None else via_topo(i))
if len(nodes)==1 and None not in nodes: print(nodes.pop())
PY
)
  [ -n "$node" ] && echo "numactl --cpunodebind=$node --preferred=$node"
}
start(){
  local st; check; st=$?
  if [ "$st" -ne 0 ] && [ "${FORCE:-0}" != "1" ]; then
    echo "refusing to start: dev cards occupied (see BUSY lines). Use FORCE=1 only after"; echo "confirming the holder is yours to preempt — cards 4/5 may serve another thread (I2V)."; exit 2; fi
  local NA; NA=$(numa_args)
  [ -n "$NA" ] && echo "NUMA bind: $NA" || echo "NUMA bind: none (mixed island or numactl unavailable) — DEV_NUMA to override"
  /usr/bin/python3.11 - "$BASE" "$RUN" "$SRC" "$MODEL_DIR" "$LOGD" "$DEV_GPUS" "$DEV_MAX_CONTEXT" "$DEV_KV_RESIDENT" "$DEV_CC_MIB" "$DEV_CC_SLOTS" "$DEV_RESIDENT_GIB" "$DEV_VISION" <<'PY'
import json,os,sys
base,out,src,model_dir,logd,gpus,mctx,kvr,ccmib,ccslots,resgib,vision=sys.argv[1:13]
c=json.load(open(base)); glist=[int(x) for x in gpus.split(",") if x!=""]
c["gpu"]=glist
c["exe"]=os.path.join(src,"build","strata"); c["cwd"]=src
c["log"]=os.path.join(logd,"strata-nvfp4-dev-engine.log")
fix=lambda s: str(s).replace("/data/work/strata-nvfp4-src",src).replace("/data/work/Strata-data/models/nvfp4-gptq",model_dir)
c["args"]=[fix(x) for x in c["args"]]
def setflag(name,val):
    if name in c["args"]: c["args"][c["args"].index(name)+1]=str(val)
    else: c["args"]+= [name,str(val)]
setflag("--max-context",mctx); setflag("--kv-resident",kvr)
setflag("--conversation-cache-mib",ccmib); setflag("--conversation-cache-slots",ccslots)
setflag("--resident-budget-gib",resgib)
setflag("--spec-min-p",os.environ["DEV_SPEC_MIN_P"]) if os.environ.get("DEV_SPEC_MIN_P") else None
setflag("--batch",str(len(glist))); setflag("--batch-groups","1")
venv=os.path.join(src,"build-vision","bin","strata-vision")
if vision=="0" or (vision=="auto" and not os.path.exists(venv)) or not isinstance(c.get("vision"),dict):
    c.pop("vision",None)
else:
    v=c["vision"]
    for k in ("exe","mmproj","model"):
        if k in v: v[k]=fix(v[k])
print("gpu=",c["gpu"]," vision=", "on" if "vision" in c else "off", " max_context=",mctx)
json.dump(c,open(out,"w"))
PY
  local TS; TS=$(date -u +%FT%TZ)
  local LOG="$LOGD/strata-nvfp4-dev-$TS.log"; echo "LOG=$LOG"
  # Detach fully: the wrapper subshell must not linger (a `cmd && ext &` list is not exec'd
  # by bash, which both holds caller pipes open and records the wrapper pid, not serve's).
  # Close stdin, send the wrapper's own fds to the bit bucket, then resolve serve's real pid
  # from the unique --config path.
  ( cd "$SRC" && setsid $NA /usr/bin/python3.11 -m serve.server --engine strata --config "$RUN" --host 127.0.0.1 --port "$DEV_PORT" </dev/null >"$LOG" 2>&1 & ) >/dev/null 2>&1
  sleep 2
  local sp; sp=$(pgrep -f "serve.server --engine strata --config $RUN" | head -1)
  [ -n "$sp" ] && echo "$sp" > "$PIDF"
  echo "dev lane started pid ${sp:-unknown} port $DEV_PORT"
}
stop(){
  if [ -f "$PIDF" ]; then
    local pids; pids="$(cat $PIDF) $(descendants "$(cat $PIDF)" | tr '\n' ' ')"
    echo "stopping dev tree: $pids"; kill $pids 2>/dev/null || true
    for _ in $(seq 1 45); do
      sleep 2
      local used; used=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | awk -F', ' -v g=" ${DEV_GPUS//,/ } " 'index(g," "$1" "){s+=$2} END{print s+0}')
      [ "${used:-0}" -lt 1400 ] && break
    done
    rm -f "$PIDF"; echo "dev lane stopped (dev-card VRAM sum ${used} MiB)"
  else echo "no dev pidfile"; fi
}
status(){
  echo "== dev serve pid =="; [ -f "$PIDF" ] && cat "$PIDF" && ps -o pid,etime,rss,cmd -p "$(cat $PIDF)" 2>/dev/null | tail -1
  echo "== health =="; curl -s -m5 "http://127.0.0.1:$DEV_PORT/health"; echo
  echo "== dev-card VRAM =="; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | awk -F', ' -v g=" ${DEV_GPUS//,/ } " 'index(g," "$1" "){print}'
  check || true
}
case "${1:-}" in
  start) start;; stop) stop;; restart) stop; sleep 2; start;; status) status;; check) check;;
  *) echo "usage: $0 {start|stop|restart|status|check}   (env: DEV_GPUS DEV_PORT DEV_MAX_CONTEXT DEV_CC_MIB DEV_RESIDENT_GIB DEV_VISION DEV_NUMA FORCE)";;
esac
