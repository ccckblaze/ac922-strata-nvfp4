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
# The shipped shape is 4-card (GPU0-3, NUMA node0's NVLink triangle plus one card on node8); cards
# 4/5 stay free for the dev lane. GPUS=0,1,2,3,4,5 boots the 6-card symmetric geometry
# (GPU0-2 = node0 triangle, GPU3-5 = node8 triangle). batch/batch-groups and NB_CUT follow the
# card count automatically, so a geometry switch is one env var and nothing else.
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
descendants(){ # recursive child list of a pid (same helper devlane.sh uses, so both lanes are symmetric)
  local p kids
  for p in "$@"; do
    kids=$(pgrep -P "$p" 2>/dev/null || true)
    [ -n "$kids" ] && { echo $kids; descendants $kids; }
  done
}
own_pids(){
  [ -f "$PIDF" ] || return 0
  local root; root=$(cat "$PIDF" 2>/dev/null) || return 0
  [ -n "$root" ] || return 0
  echo "$root $(descendants "$root" | tr '\n' ' ')"
}
check(){ # refuse to share cards: report any compute context on $GPUS that this lane does not own
  /usr/bin/python3.11 - "${GPUS:-0,1,2,3,4,5}" "$(own_pids)" <<'PY'
import os, subprocess, sys
want=[x.strip() for x in sys.argv[1].split(",") if x.strip()!=""]
own={int(p) for p in sys.argv[2].split()}
uu={}
for l in subprocess.check_output(["nvidia-smi","--query-gpu=index,uuid","--format=csv,noheader"],text=True).splitlines():
    i,u=[s.strip() for s in l.split(",",1)]; uu[u]=i
busy={}
for l in subprocess.check_output(["nvidia-smi","--query-compute-apps=gpu_uuid,pid,used_memory","--format=csv,noheader"],text=True).splitlines():
    u,p,mem=[s.strip() for s in l.split(",",2)]
    if uu.get(u) in want and int(p) not in own:
        try: os.kill(int(p),0)          # a context the driver still lists for a dying process is free
        except OSError: continue
        busy.setdefault(uu[u],[]).append((int(p),mem))
print(("cards busy: "+", ".join("%s<-%s>"%(k,v) for k,v in sorted(busy.items()))) if busy else "cards clear")
sys.exit(1 if busy else 0)
PY
}
stop(){
  # Lane-scoped shutdown, pidfile tree only.  The old broad `pkill -f "build/strata --serve"` also
  # killed the dev lane's engine: measured 2026-11-16, a production `stop` took out the 8092 engine
  # while its supervisor stayed alive and went on answering /health 200 with nothing behind it.
  local pids p alive eng
  pids="$(own_pids)"
  if [ -n "${pids// /}" ]; then
    eng=""
    for p in $pids; do tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q "build/strata --serve" && eng="$eng $p"; done
    echo "stopping lane tree: $pids"; kill $pids 2>/dev/null || true
    sleep 3
    alive=""
    for p in $pids; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done
    [ -n "$alive" ] && { echo "forcing:$alive"; kill -9 $alive 2>/dev/null || true; }
    # A killed engine keeps its VRAM for a few seconds.  A `start` issued inside that window sees its
    # own dead process as a foreign occupant and the guard above refuses it, so wait for release.
    if [ -n "$eng" ]; then
      for _ in $(seq 1 45); do
        live=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d " ")
        hit=0; for p in $eng; do echo "$live" | grep -qx "$p" && hit=1; done
        [ "$hit" = 0 ] && break; sleep 2
      done
      echo "lane contexts released"
    fi
  fi
  pkill -f -- "$RUN" 2>/dev/null   # stale-pidfile fallback; $RUN is this lane's own config path
  rm -f "$PIDF"; echo stopped
}
start(){
  # Volta kernel opt-ins (docs/NVIDIA_V100.md, 0.2.13) + the adopted ledger env, shared with
  # the dev lane — see adopted-env.sh (SM70_TABLE / PF_WMMA / FUSE_DECODE / ARGMAX_ROWS_SM70 /
  # select-simt / batchenv, all default-on unless the caller overrides).
  . "$HERE/adopted-env.sh"
  GPUS=${GPUS:-0,1,3,4}
  export CUDA_VISIBLE_DEVICES="$GPUS"
  IFS=, read -ra _G <<< "$GPUS"
  # NB_CUT is the arena's node0 share, so it follows the cards' real islands, not the card count:
  # the fraction of $GPUS whose NUMA node is the lowest-numbered one (2+2 and 3+3 give 0.5, the old
  # 3+1 gives 0.75-ish).  sysfs numa_node with the 8-digit PCI domain normalised, `topo -m` fallback.
  _CUT=$(/usr/bin/python3.11 - "$GPUS" <<'PY'
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
if any(v is None for v in node.values()):
    print(""); sys.exit(0)
lo=min(node.values())
print("%.4f" % (sum(1 for v in node.values() if v==lo)/len(node)))
PY
)
  [ -z "$_CUT" ] && { case ${#_G[@]} in 4) _CUT=0.69 ;; *) _CUT=0.5 ;; esac; echo "NB_CUT probe failed; card-count fallback $_CUT"; }
  export NB_CUT=${NB_CUT:-$_CUT}
  # The default geometry is 6-card, so a bare `start` on a box whose 4/5 cards belong to the dev lane
  # (or to a cooperating thread) used to silently straddle all six.  Refuse unless the cards are free.
  if [ "${FORCE:-0}" != 1 ]; then
    occ=$(check 2>&1) || { echo "REFUSING to start: $occ"; echo "Free GPUS=$GPUS, or pass FORCE=1 to override."; exit 1; }
    echo "check: $occ"
  fi
  # >>> AC922 adopted env (DECISIONS.md: T10 NUMA bind) >>>
  # expert-arena NUMA placement via mmap hook: arena[0, NB_CUT)->node0 island, rest->node8 island.
  if [ -f "$NUMABIND_SO" ]; then export LD_PRELOAD="$NUMABIND_SO"; fi
  TS=$(date -u +%FT%TZ)
  LOG="$LOGD/strata-nvfp4-$TS.log"; echo "LOG=$LOG"; echo "GPUS=$CUDA_VISIBLE_DEVICES NB_CUT=$NB_CUT"
  # Rewrite the reference config onto this machine's layout (paths only; args untouched except the
  # batch pair, which defaults to the accepted shape for the card count: 4-card 4/4, 6-card 6/3 (the
  # 4-card half of that is the 2026-11-21 sweep, runbook 337 - see the auto-shape block below).
  # STRATA_BATCH / STRATA_BATCH_GROUPS still override it explicitly for experiments.)
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
# The shipping "layer_split" is tuned for the 4-card lane (9,24,37).  Six cards have no tuned
# shape in this repo's line; serve wants one rising number per extra GPU, so a non-4 card lane
# falls back to "auto" (placed by each card's free VRAM) - exactly what the historical 6-card
# boots recorded.  STRATA_LAYER_SPLIT overrides; "auto" keeps the engine's own default.
if len(c["gpu"]) != 4 or "STRATA_LAYER_SPLIT" in os.environ:
    sp = os.environ.get("STRATA_LAYER_SPLIT", "auto")
    c.pop("layer_split", None)
    while "--layer-split" in c["args"]:
        i = c["args"].index("--layer-split"); del c["args"][i:i+2]
    if sp != "auto":
        c["layer_split"] = sp; setflag("--layer-split", sp)
ncard=len(c["gpu"])
if batch<2 and groups<2 and ncard in (4,6):
    # 4-card shape stays 4 groups of 1 row.  Re-confirmed 2026-11-23 against 4/2 with a harness of four
    # INDEPENDENT ~53K/131K sessions (the traffic the operator actually runs; the 2026-11-21 sweep used a
    # shared-prefix aggbench, which answers a different question): aggregate tok/s n=4 - 8K 145.6 (4/4) vs
    # 121.0 (4/2), 131K 81.8 vs 72.7; single stream identical (53K 66.1 vs 65.9).  4/2 keeps PIPED_MTP with
    # 2 rows/window and still loses: on 4 stages the multi-row QSA scoring costs more than the shared
    # expert read saves.  Lesson H.32: one 4/4 sample read 40.7 at 53K - live traffic was on the lane;
    # contention, not shape.  6-card stays 6/3.
    batch,groups=(4,4) if ncard==4 else (6,3)
    print("auto batch shape for %d cards: %d/%d" % (ncard,batch,groups))
if batch>=2: setflag("--batch",batch)
if groups>=2: setflag("--batch-groups",groups)
if os.environ.get("SPEC_MIN_P"): setflag("--spec-min-p",os.environ["SPEC_MIN_P"])
if os.environ.get("RESIDENT_GIB") is not None:
    # RESIDENT_GIB=<n> sizes the arena; RESIDENT_GIB=off removes the flag, which is the only honest
    # arena-off shape: the engine rejects `--resident-budget-gib 0` ("needs N > 0"), and with no flag
    # at all it takes the automatic RAM copy - the experts live in the page cache, one copy shared
    # across processes, which is what a multi-replica layout would read anyway (serve/replicas.py
    # lists this flag as ARENA_INCOMPATIBLE).  Measured 2026-11-22: the 0 form never boots.
    _rb = os.environ["RESIDENT_GIB"]
    if _rb in ("off", "none", "-"):
        while "--resident-budget-gib" in c["args"]:
            i = c["args"].index("--resident-budget-gib"); del c["args"][i:i+2]
        print("resident arena: OFF (no flag: the engine keeps the experts in the shared page cache)")
    else:
        setflag("--resident-budget-gib", _rb)
# warm-resume geometry: a continuation re-reads back to the last checkpoint, and checkpoints land on
# prefill chunk boundaries spaced ceil(PROMPT_CACHE_EVERY / chunk) chunks, so BOTH knobs have to move
# together (EVERY <= chunk) or the gap floor stays at the chunk.  Unset = the shipped config's value.
if os.environ.get("PREFILL_CHUNK"): setflag("--prefill",os.environ["PREFILL_CHUNK"])
if os.environ.get("PROMPT_CACHE_EVERY"): setflag("--prompt-cache-every",os.environ["PROMPT_CACHE_EVERY"])
# EXTRA_ARGS is the generic passthrough for engine flags, so a verification arm never needs its own
# knob.  Form: space-separated tokens; `--flag=value` sets a value flag (any earlier value pair is
# dropped first), a bare `--flag` adds a boolean.  Processed last, so it wins over the knobs above.
# Nothing happens when the variable is unset, which is the production path.
for _tok in os.environ.get("EXTRA_ARGS", "").split():
    if not _tok.startswith("--"):
        c["args"].append(_tok); continue
    _name, _sep, _val = _tok.partition("=")
    while _name in c["args"]:
        _i = c["args"].index(_name)
        _j = _i + 2 if _i + 1 < len(c["args"]) and not c["args"][_i+1].startswith("--") else _i + 1
        del c["args"][_i:_j]
    c["args"] += [_name] + ([_val] if _sep else [])
if os.environ.get("EXTRA_ARGS"): print("extra args:", os.environ["EXTRA_ARGS"])
if os.environ.get("PROMPT_CACHE"): setflag("--prompt-cache",os.environ["PROMPT_CACHE"])
# parked-conversation capacity: the ceiling that actually bites under concurrency is the SLOT count,
# not the RAM budget (measured 2026-11-16: 98.5% of cache evictions land at parked==slots, with the
# footprint at 17-42% of --conversation-cache-mib).  Raising CC_SLOTS is ~free per conversation; keep
# CC_MIB explicit so the worst case stays inside the box's real headroom.  Unset = shipped values.
if os.environ.get("MTP_DIR"): setflag("--mtp",os.environ["MTP_DIR"])
if os.environ.get("CC_SLOTS"): setflag("--conversation-cache-slots",os.environ["CC_SLOTS"])
if os.environ.get("CC_MIB"): setflag("--conversation-cache-mib",os.environ["CC_MIB"])
# conversation-disk cold tier (plan v2, 2026-11-20).  DISK_DIR turns the layer on; the rest are
# optional and default to the engine's own values.  DISK_PERSIST=1 keeps conversations for the next
# start and DISK_SYNC=N flushes idle N s in (0 = only at QUIT).  Pure launcher passthrough: the
# engine flags exist since the 0.2.15 line (generate.cpp:1327-1341), nothing here changes numbers.
def boolflag(name):
    if name not in c["args"]: c["args"]+=[name]
if os.environ.get("DISK_DIR"):
    setflag("--conversation-disk-dir",os.environ["DISK_DIR"])
    if os.environ.get("DISK_MIB"): setflag("--conversation-disk-mib",os.environ["DISK_MIB"])
    if os.environ.get("DISK_TTL"): setflag("--conversation-disk-ttl-min",os.environ["DISK_TTL"])
    if os.environ.get("DISK_SYNC"): setflag("--conversation-disk-sync-s",os.environ["DISK_SYNC"])
    if os.environ.get("DISK_PERSIST"): boolflag("--conversation-disk-persist")
if os.environ.get("CC_MIN_TOKENS"): setflag("--conversation-cache-min-tokens",os.environ["CC_MIN_TOKENS"])
# serve-level request defaults (not engine flags): REASONING_BUDGET lands in the run config, where
# serve/runconfig.py reads "reasoning_budget_tokens" and caps every request's thinking (#123).  At the
# 262K ceiling the shipped clients ask for <=512 output tokens, and an uncapped thinking reply eats the
# whole budget and returns nothing ("the reply reached max tokens while still thinking") - observed as
# the 19:52-19:54 retry loop, one 1-token generation every ~16 s, no answer, no traffic served.
if os.environ.get("REASONING_BUDGET"):
    c["reasoning_budget_tokens"] = int(os.environ["REASONING_BUDGET"])
    print("reasoning budget:", os.environ["REASONING_BUDGET"])
# CC_PARK_GAIN=N parks a conversation only after it advanced N tokens past the copy it would supersede
# (engine default 0 = park every turn).  With 0, every turn of a long conversation pays a whole-snapshot
# copy that the next turn drops as superseded: measured 2026-11-23 on four ~131 K conversations, 2,017 MiB
# written per re-issue, 0.7-0.9 GB/s of NVMe for 1.5-1.9 s each, and the batch step time inflated from
# 19 ms to 45 ms on the same boot.  Pure passthrough; nothing here changes numbers.
if os.environ.get("CC_PARK_GAIN"): setflag("--conversation-cache-park-gain",os.environ["CC_PARK_GAIN"])
# RAM floor for parking/save/restore.  The engine measures it against MemAvailable, and this box
# over-reports MemAvailable by the HBM-window mapping (the 341 GiB trap); MEM_LIMIT_MIB pins the
# engine's RAM view to the real DDR so the floor means what it says.  CC_FLOOR=auto computes
# max(20% of REAL, 16 GiB) with REAL from REAL_RAM_MIB or MemTotal - sum(nvidia-smi memory.total).
if os.environ.get("MEM_LIMIT_MIB"): setflag("--memory-limit-mib",os.environ["MEM_LIMIT_MIB"])
if os.environ.get("CC_MIN_FREE_MIB"): setflag("--conversation-cache-min-free-mib",os.environ["CC_MIN_FREE_MIB"])
if os.environ.get("DISK_DIR"):
    print("disk tier: dir=%s mib=%s ttl=%s sync=%s persist=%s min-tokens=%s mem-limit=%s" %
          (os.environ["DISK_DIR"],os.environ.get("DISK_MIB","engine-default"),
           os.environ.get("DISK_TTL","engine-default"),os.environ.get("DISK_SYNC","engine-default"),
           "yes" if os.environ.get("DISK_PERSIST") else "no",
           os.environ.get("CC_MIN_TOKENS","0"),os.environ.get("MEM_LIMIT_MIB","unset")))
    print("park gain: %s tokens (0 = the engine's park-every-turn default)" %
          os.environ.get("CC_PARK_GAIN","0"))
if os.environ.get("CC_FLOOR","").lower() == "auto":
    real=int(os.environ.get("REAL_RAM_MIB") or 0)
    if not real:
        mt=int([l for l in open("/proc/meminfo") if l.startswith("MemTotal")][0].split()[1])
        import subprocess
        out=subprocess.run(["nvidia-smi","--query-gpu=memory.total","--format=csv,noheader,nounits"],
                           capture_output=True,text=True).stdout.split()
        real=mt-sum(int(x) for x in out)
    floor=max(real//5,16384)
    setflag("--conversation-cache-min-free-mib",floor)
    print("floor: REAL=%d MiB FLOOR=%d MiB" % (real,floor))
fbatch=int(c["args"][c["args"].index("--batch")+1]); fgroups=int(c["args"][c["args"].index("--batch-groups")+1])
if fbatch!=ncard:
    print("NOTE: effective --batch %d on %d cards is not an accepted shape (4-card 4/2, 6-card 6/3)" % (fbatch,ncard))
if fbatch%fgroups: print("WARNING: --batch %d not divisible by --batch-groups %d" % (fbatch,fgroups))
json.dump(c,open(out,"w")); print("batch=",batch)
PY
  # >>> AC922 adopted env (DECISIONS.md: PLE park) >>>
  if [ -x "$PLE_PARK" ]; then PLE_SPLIT="${PLE_SPLIT:-500}" "$PLE_PARK" "${PLE_SPLIT:-500}" || true; fi
  # >>> AC922 adopted env (H.35(b): factory draft arm) >>>
  # STRATA_AUTO_NEW_MTP=0 makes serve.server leave the factory --spec 6/--spec-min-p alone; unset
  # keeps the 0.2.15 auto path, which rides --spec 8 --mtp-head proxy --mtp-cum auto even on a
  # split, although drafting.py's own docstring restricts those flags to "one request at a time,
  # not a split".  The A/B is this lane's first honest measurement of that question.
  if [ -n "${STRATA_AUTO_NEW_MTP:-}" ]; then export STRATA_AUTO_NEW_MTP; fi
  # <<< AC922 adopted env (factory draft arm) <<<
  # The new session leader writes its own pid: `setsid` forks when the caller is already a
  # process-group leader, so `$!` can name the short-lived setsid parent instead of the server
  # (measured 2026-11-24: pidfile held the script's pid, not the serve pid).
  # Bind: the server answers on every address, so the LAN front door
  # (ac922-strata-lan-proxy.service) is retired and this holds :8091 outright. 0.0.0.0 is on
  # purpose, not the NIC address: `status`/`health` probe 127.0.0.1:8091, and a loopback-only
  # bind left the routable address to the pump. The key still gates every endpoint but /health,
  # and the firewall decides which sources may arrive; BIND_HOST overrides for a test lane.
  BIND_HOST=${BIND_HOST:-0.0.0.0}
  ( cd "$SRC" && setsid /usr/bin/bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$PIDF" /usr/bin/python3.11 -m serve.server --engine strata --config "$RUN" --host "$BIND_HOST" --port 8091 >"$LOG" 2>&1 & )
  for _ in $(seq 1 20); do [ -s "$PIDF" ] && break; sleep 0.2; done
  echo "started pid $(cat $PIDF 2>/dev/null)"
}
status(){
  echo "== server pid =="; [ -f "$PIDF" ] && cat "$PIDF" && ps -o pid,etime,rss,cmd -p "$(cat $PIDF)" 2>/dev/null | tail -1
  echo "== engine =="; for e in $(own_pids); do tr '\0' ' ' < /proc/$e/cmdline 2>/dev/null | grep -q "build/strata --serve" && ps -o pid,etime,rss --no-headers -p $e; done
  echo "== health =="; curl -s -m5 ${STRATA_API_KEY:+-H "Authorization: Bearer $STRATA_API_KEY"} http://127.0.0.1:8091/health; echo
  echo "== cards =="; check 2>&1 || true
  echo "== gpu =="; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | head -6
  echo "== ram =="; awk '/MemAvailable/{print $2/1048576" GiB avail"}' /proc/meminfo
}
case "${1:-}" in
  start) start;; stop) stop;; restart) stop; sleep 2; start;; status) status;; *) echo "usage: $0 {start|stop|restart|status}";;
esac
