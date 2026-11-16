#!/bin/bash
# qualgate.sh {baseline|check} [tag]: fixed regression fixture for every config/kernel change.
# Gates: det x3 same-hash; divprobe per-L hash vs REF (L=8/32 divergence = FAIL, L>=128 only = WARN);
# acc ratio drift >5% = FAIL.
#
# Two defects this closes (both found 2026-11-16 while gating the parked-slot capacity change):
#   - zero rows is never a verdict.  The fixtures lost auth when the lane started requiring
#     STRATA_API_KEY, every request 401'd, and the wrapper graded the empty result files as a
#     FAIL/PASS instead of aborting.  Missing rows or a missing key now exit 3.
#   - the engine log is the lane's own file.  "newest log in $LOGD" used to resolve to the serve
#     stdout log (or the other lane's), so pp/tg parsed as None while the run still looked clean.
set -u
MODE=${1:-check}; TAG=${2:-q$(date +%m%dT%H%M)}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${QG_OUT:-/data/work/qualgate}
LOGD=${LOGD:-/data/champion}
SB_URL=${SB_URL:-http://127.0.0.1:8091}
if [ -z "${ENG:-}" ]; then
  case "$SB_URL" in
    *8092) ENG="$LOGD/strata-nvfp4-dev-engine.log";;
    *)     ENG="$LOGD/strata-nvfp4-engine.log";;
  esac
fi
mkdir -p "$OUT"
[ -n "${STRATA_API_KEY:-}" ] || { echo "GATE-ERROR no-api-key (source the env that defines STRATA_API_KEY)"; exit 3; }
[ -r "$ENG" ] || { echo "GATE-ERROR unreadable engine log $ENG"; exit 3; }
echo "ENGINELOG=$ENG URL=$SB_URL"
SB_URL="$SB_URL" SB_ENG="$ENG" python3.11 "$HERE/sbench.py" det > "$OUT/$TAG.det" 2>&1
SB_URL="$SB_URL" SB_ENG="$ENG" python3.11 "$HERE/divprobe.py" "$TAG" > "$OUT/$TAG.probe" 2>&1
python3.11 - "$OUT/$TAG" "$MODE" <<"PY"
import json,os,sys
base,mode=sys.argv[1],sys.argv[2]
det=[json.loads(l) for l in open(base+".det") if l.startswith("{")]
probe=[json.loads(l) for l in open(base+".probe") if l.startswith("{")]
if len(det)<3 or len(probe)<8:
    print("GATE-ERROR rows: det=%d probe=%d -- a fixture failed, read %s.det / %s.probe"%(
          len(det),len(probe),base,base)); sys.exit(3)
hashes=[d["hash"] for d in det]
acc=[tuple(int(x) for x in d["acc"]) for d in det if d.get("acc")]
pm={str(p["tgt"])+"/"+str(p["L"]):p["h"] for p in probe}
ratios=[a[0]/a[1] for a in acc if a[1]>0]
s={"det_same":len(set(hashes))==1 and len(hashes)==3,"det":hashes,"acc_ratio":[round(r,4) for r in ratios],"probe":pm}
ref=os.path.join(os.path.dirname(base),"REF.json")
if mode=="baseline":
    json.dump(s,open(ref,"w"),sort_keys=True); print("BASELINE-WRITTEN",ref); sys.exit(0)
if not os.path.exists(ref): print("FAIL no-baseline"); sys.exit(2)
r=json.load(open(ref)); ok=True
if not s["det_same"]: ok=False; print("FAIL det-nondeterministic")
early=[k for k in pm if k in r["probe"] and r["probe"][k]!=pm[k] and int(k.split("/")[1])<=32]
late=[k for k in pm if k in r["probe"] and r["probe"][k]!=pm[k] and int(k.split("/")[1])>32]
for k in sorted(early,key=lambda x:(x.split("/")[0],int(x.split("/")[1]))): print("FAIL numeric-divergence",k)
if early: ok=False
if late and not early: print("WARN late-divergence",len(late),"cells")
if r["acc_ratio"] and ratios:
    dr=min(ratios)/max(r["acc_ratio"])
    if dr<0.95: ok=False; print("FAIL acc-drift",round(dr,3))
    else: print("acc-drift-ok",round(dr,3))
if hashes!=r["det"] and not early: print("NOTE det-hash-changed-but-early-cells-clean")
print("QUALGATE-"+("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
PY
