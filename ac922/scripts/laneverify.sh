#!/bin/bash
# Lane evidence snapshot for the fidelity campaign (runbook 101.7). Read-only, zero GPU work.
# Prints numbers/flags/versions only -- never model text.
set -uo pipefail
T=$(date '+%F %T %Z')
U=${1:-http://127.0.0.1:8091}
LOG=${2:-/data/champion/strata-nvfp4-engine.log}
echo "== lane verify $T url=$U"
echo "-- health"
curl -s -m 8 "$U/health" | python3.11 -c '
import json,sys
d=json.load(sys.stdin)
for k in ("status","service","model","loaded","max_context","images","api_key"):
    if k in d: print("   %s=%s" % (k,d[k]))' 2>/dev/null || echo "   (health parse failed)"
echo "-- engine batch geometry (last)"
grep -o "batch (pipelined[^)]*)" "$LOG" 2>/dev/null | tail -1 | sed 's/^/   /'
echo "-- vram per card (MiB used/total)"
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits | sed 's/^/   /'
echo "-- compute apps"
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | sed 's/^/   /' || echo "   none"
echo "-- ram (GiB)"
free -g | awk 'NR==2{printf "   total=%s used=%s free=%s available=%s\n",$2,$3,$4,$7}'
echo "-- pinned/locked"
grep -E "^(MemAvailable|Active|Inactive)" /proc/meminfo | awk '{printf "   %s %.1f GiB\n",$1,$2/1048576}'
echo "-- containers"
echo "   podman_running=$(podman ps -q 2>/dev/null | wc -l) podman_all=$(podman ps -aq 2>/dev/null | wc -l)"
echo "-- engine versions"
for b in /data/work/strata-nvfp4-src/build/strata /data/work/strata-src/build/strata; do
  [ -x "$b" ] && echo "   $b -> $("$b" --version 2>/dev/null | head -1)"
done
echo "-- artifacts present"
for f in /data/work/nvfp4-sha.log; do
  [ -f "$f" ] && echo "   $f last=$(tail -1 "$f")"
done
echo "== end"
