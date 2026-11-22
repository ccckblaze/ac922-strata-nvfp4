#!/bin/bash
# AC922 dev lane: iterate on the leftover cards without touching the pinned lane.
#
# Two-lane policy (RUN.md "Development lane"):
#   production  GPUS 0,1,3,4 (B4, NB_CUT follows the islands: 2+2 -> 0.5) or 0-5 (B6)   port 8091
#   dev lane    GPUS 2,5 (one card per island - the same straddle the ship lane has)      port 8092
# Rules enforced here:
#   - The dev cards may belong to another cooperating thread (I2V etc.): start refuses to launch
#     on top of any occupancy unless FORCE=1; `check` reports who holds the cards.
#   - NUMA: GPU0-2 sit on node0, GPU3-5 on node8 (cpus 88-175).  A dev lane whose cards live on
#     one island is bound to it (cpunodebind + preferred mem).  A dev lane that SPANS islands
#     (the default 2,5) mirrors the production straddle instead: numabind.so + NB_CUT = the node0
#     card share, so dev prices the same memory behaviour the ship lane has.  Either way dev does
#     NOT re-run ple_park (page cache is shared read-only between lanes).
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
DEV_GPUS=${DEV_GPUS:-2,5}
DEV_PORT=${DEV_PORT:-8092}
DEV_MAX_CONTEXT=${DEV_MAX_CONTEXT:-131072}
DEV_KV_RESIDENT=${DEV_KV_RESIDENT:-16384}
DEV_CC_MIB=${DEV_CC_MIB:-32768}
DEV_CC_SLOTS=${DEV_CC_SLOTS:-4}
DEV_RESIDENT_GIB=${DEV_RESIDENT_GIB:-16}
DEV_MIN_AVAIL_GIB=${DEV_MIN_AVAIL_GIB:-32}
DEV_VISION=${DEV_VISION:-auto}          # auto|0|1
DEV_NUMA=${DEV_NUMA:-auto}              # auto|off|<node id>
DEV_FLAGS=${DEV_FLAGS:-}                # extra engine flags, ";"-separated "flag value" pairs
# Window shape, for decode-first arms: DEV_BATCH=0 drops --batch/--batch-groups entirely so the
# engine admits one request at a time, which is the precondition of DEV_PIPELINE=N
# (--pipeline-windows N: legal only on a two-stage split). DEV_BATCH_GROUPS pins rows per group.
# With neither set the historical shape is reproduced exactly: batch = card count, groups = 1.
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
  if [ -z "$node" ]; then
    # the cards span islands (or the probe failed): the straddle mirror is decided by the caller
    echo "STRADDLE"
    return
  fi
  echo "numactl --cpunodebind=$node --preferred=$node"
}
start(){
  local st; check; st=$?
  if [ "$st" -ne 0 ] && [ "${FORCE:-0}" != "1" ]; then
    echo "refusing to start: dev cards occupied (see BUSY lines). Use FORCE=1 only after"; echo "confirming the holder is yours to preempt — the dev cards may serve another thread (I2V)."; exit 2; fi
  local NA; NA=$(numa_args)
  if [ "$NA" = "STRADDLE" ]; then
    # one card per island: same mechanism as the ship lane - the mmap hook splits the expert arena
    local SO=${NUMABIND_SO:-/data/work/numabind.so} frac
    frac=$(/usr/bin/python3.11 - "$DEV_GPUS" <<'PY'
import subprocess,sys
want=[x.strip() for x in sys.argv[1].split(",") if x.strip()]
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
node={}
for l in subprocess.check_output(["nvidia-smi","--query-gpu=index,pci.bus_id","--format=csv,noheader"],text=True).splitlines():
    i,b=[x.strip() for x in l.split(",",1)]
    if i not in want: continue
    a=norm(b); got=None
    for p in ("/sys/bus/pci/devices/%s/numa_node"%a,"/sys/bus/pci/devices/%s/device/numa_node"%a):
        try:
            v=open(p).read().strip()
            if v and v!="-1": got=v; break
        except Exception: pass
    node[i]=got if got is not None else via_topo(i)
if any(v is None for v in node.values()): print("0.5"); sys.exit(0)
lo=min(node.values())
print("%.4f" % (sum(1 for v in node.values() if v==lo)/len(node)))
PY
)
    if [ -f "$SO" ]; then
      export LD_PRELOAD="$SO" NB_CUT="${frac:-0.5}"
      echo "NUMA bind: straddle mirror (LD_PRELOAD $SO, NB_CUT=${NB_CUT:-0.5})"
    else
      echo "NUMA bind: WARNING straddling cards without $SO - arena placement falls to first touch"
    fi
    NA=""            # the straddle binds through LD_PRELOAD, not a numactl prefix: never let the
                     # literal "STRADDLE" marker reach `setsid $NA` (dev 2,5 died this way 11-22:
                     # setsid tried to execute it as the command word)
  elif [ -n "$NA" ]; then
    echo "NUMA bind: $NA"
  else
    echo "NUMA bind: none (numactl unavailable) — DEV_NUMA to override"
  fi
  /usr/bin/python3.11 - "$BASE" "$RUN" "$SRC" "$MODEL_DIR" "$LOGD" "$DEV_GPUS" "$DEV_MAX_CONTEXT" "$DEV_KV_RESIDENT" "$DEV_CC_MIB" "$DEV_CC_SLOTS" "$DEV_RESIDENT_GIB" "$DEV_VISION" <<'PY'
import json,os,sys
base,out,src,model_dir,logd,gpus,mctx,kvr,ccmib,ccslots,resgib,vision=sys.argv[1:13]
c=json.load(open(base)); glist=[int(x) for x in gpus.split(",") if x!=""]
c["gpu"]=glist
c["exe"]=os.path.join(src,"build","strata"); c["cwd"]=src
c["log"]=os.path.join(logd,"strata-nvfp4-dev-engine.log")
fix=lambda s: str(s).replace("/data/work/strata-nvfp4-src",src).replace("/data/work/Strata-data/models/nvfp4-gptq",model_dir)
c["args"]=[fix(x) for x in c["args"]]
if isinstance(c.get("vision"),dict) and "exe" in c["vision"]: c["vision"]["exe"]=fix(c["vision"]["exe"])
def setflag(name,val):
    if name in c["args"]: c["args"][c["args"].index(name)+1]=str(val)
    else: c["args"]+= [name,str(val)]
setflag("--max-context",mctx); setflag("--kv-resident",kvr)
setflag("--conversation-cache-mib",ccmib); setflag("--conversation-cache-slots",ccslots)
setflag("--resident-budget-gib",resgib)
setflag("--spec-min-p",os.environ["DEV_SPEC_MIN_P"]) if os.environ.get("DEV_SPEC_MIN_P") else None
def delflag(name):
    while name in c["args"]:
        i=c["args"].index(name); del c["args"][i:i+2]
# The geometry must match the dev card count: serve reads the config's "layer_split" key while the
# engine gets --layer-split in args, and both must describe len(glist) cards. The base config is
# written for the shipping lane (4 or 6 cards), so when its shape does not fit, recompute an even
# split from the native gguf's block_count. DEV_LAYER_SPLIT overrides; "off" drops the split.
def gguf_block_count(path):
    import struct
    fmts={0:("<B",1),1:("<b",1),2:("<H",2),3:("<h",2),4:("<I",4),5:("<i",4),6:("<f",4),7:("<?",1),
          10:("<Q",8),11:("<q",8),12:("<d",8)}
    def rd(f,fmt): return struct.unpack(fmt,f.read(struct.calcsize(fmt)))[0]
    def rstr(f):
        l=rd(f,"<Q"); return f.read(l)
    try:
        with open(path,"rb") as f:
            if f.read(4)!=b"GGUF": return None
            rd(f,"<I"); rd(f,"<Q"); nkv=rd(f,"<Q")
            for _ in range(nkv):
                k=rstr(f).decode("utf-8","replace"); t=rd(f,"<I")
                if t in fmts: v=rd(f,fmts[t][0])
                elif t==8: rstr(f); v=None
                elif t==9:
                    et=rd(f,"<I"); cnt=rd(f,"<Q")
                    if et in fmts: f.read(cnt*fmts[et][1])
                    elif et==8:
                        for _ in range(cnt): rstr(f)
                    else: return None
                    v=None
                else: return None
                if k.endswith("block_count") and isinstance(v,int): return v
    except OSError:
        return None
    return None
n=len(glist)
cur=str(c.get("layer_split","auto"))
vals=[int(x) for x in cur.split(",")] if cur and cur!="auto" else []
split=os.environ.get("DEV_LAYER_SPLIT")
if not split and len(vals)!=n-1:
    if n==1:
        split="off"
    else:
        a=c["args"]; L=gguf_block_count(a[a.index("--native")+1]) if "--native" in a else None
        if L: split=",".join(str(L*i//n) for i in range(1,n))
        else: sys.exit('devlane: config layer_split %r does not fit %d cards and the layer count is unreadable; set DEV_LAYER_SPLIT' % (cur,n))
if split=="off":
    c["layer_split"]="auto"; delflag("--layer-split")
elif split:
    c["layer_split"]=split; setflag("--layer-split",split)
# The window shape is chosen first so DEV_FLAGS keeps the last word. It used to be forced after the
# DEV_FLAGS loop, which made every shape arm unreachable from the environment: --pipeline-windows
# needs the engine to admit one request at a time, and the forced --batch blocked exactly that.
batch=os.environ.get("DEV_BATCH",str(len(glist)))
if batch=="0":
    delflag("--batch"); delflag("--batch-groups")
else:
    setflag("--batch",batch); setflag("--batch-groups",os.environ.get("DEV_BATCH_GROUPS","1"))
if os.environ.get("DEV_PIPELINE"): setflag("--pipeline-windows",os.environ["DEV_PIPELINE"])
for pair in os.environ.get("DEV_FLAGS","").split(";"):
    parts=pair.split()
    if len(parts)==2: setflag(parts[0],parts[1])
    elif len(parts)==1 and parts[0]:
        if parts[0] not in c["args"]: c["args"].append(parts[0])
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
  *) echo "usage: $0 {start|stop|restart|status|check}   (env: DEV_GPUS DEV_PORT DEV_MAX_CONTEXT DEV_CC_MIB DEV_RESIDENT_GIB DEV_VISION DEV_NUMA DEV_LAYER_SPLIT FORCE)";;
esac
