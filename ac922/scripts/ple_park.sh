#!/bin/bash
# Park the PLE page-cache bytes across the two NVLink islands of the AC922 (GPU0-2=node0, GPU3-5=node8).
# Why this exists: mbind cannot move file pages on this box (measured 2026-11-15: after fadvise(DONTNEED),
# mbind {interleave 0,8} and mbind {bind:8} both still land 100% of a 2 GiB window on node0), but pages DO
# follow the CPU that reads them in. The engine's main thread is pinned to CPU 0, so all 48.8 GiB of
# ple-fp8.gguf sit on node0 while node8 idles; node0 then decodes with ~0.8 GiB free and every window
# allocation is a remote read.
# usage: ple_park.sh [k0]        k0 = per-mille of the file to keep on node0 (default $PLE_SPLIT, else 500)
#        PLE_SPLIT=off ple_park.sh
# Read-only for the file itself; costs one full NVMe re-read of the PLE (~25 s).
set -u
PL=${PLE_FILE:-/data/work/Strata-data/models/nvfp4-gptq/ple-fp8.gguf}
k0=${1:-${PLE_SPLIT:-500}}
case "$k0" in off|0off|"") exit 0 ;; esac
case "$k0" in (*[!0-9]*) echo "ple_park: k0 must be 0..1000"; exit 2 ;; esac
[ -r "$PL" ] || { echo "ple_park: cannot read $PL"; exit 2; }
bs=33554432; mb=1048576
total=$(stat -c %s "$PL"); blocks=$(( total / bs )); c0=$(( blocks * k0 / 1000 )); rest=$(( ( total - blocks * bs ) / mb ))
fp(){ awk -v N=$1 '/FilePages:/{printf "%.1f", $4/1048576}' /sys/devices/system/node/node$1/meminfo; }
echo "ple_park: k0=$k0 file=$(( total / mb ))MiB blocks=$blocks node0=$c0 before(fp0=$(fp 0),fp8=$(fp 8))GiB"
if [ "$(pgrep -c -f 'build/strata --[s]erve')" != "0" ]; then
  echo "ple_park: an engine is running; its resident PLE pages cannot be dropped, refusing to half-do it" >&2
  exit 3
fi
python3.11 - "$PL" <<'PY'
import os,sys
fd=os.open(sys.argv[1],os.O_RDONLY)
os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
os.close(fd)
PY
t0=$(date +%s)
if [ "$c0" -gt 0 ] && [ "$c0" -lt "$blocks" ]; then
  numactl --cpunodebind=8 dd if="$PL" of=/dev/null bs=$bs skip=$c0 count=$(( blocks - c0 )) status=none &
  p8=$!
  numactl --cpunodebind=0 dd if="$PL" of=/dev/null bs=$bs count=$c0 status=none
  wait $p8
  [ "$rest" -gt 0 ] && numactl --cpunodebind=8 dd if="$PL" of=/dev/null bs=$mb skip=$(( blocks * bs / mb )) count=$rest status=none
elif [ "$c0" -eq 0 ]; then
  numactl --cpunodebind=8 dd if="$PL" of=/dev/null bs=$bs count=$blocks status=none
  [ "$rest" -gt 0 ] && numactl --cpunodebind=8 dd if="$PL" of=/dev/null bs=$mb skip=$(( blocks * bs / mb )) count=$rest status=none
else
  numactl --cpunodebind=0 dd if="$PL" of=/dev/null bs=$bs count=$blocks status=none
  [ "$rest" -gt 0 ] && numactl --cpunodebind=0 dd if="$PL" of=/dev/null bs=$mb skip=$(( blocks * bs / mb )) count=$rest status=none
fi
echo "ple_park: done in $(( $(date +%s) - t0 ))s after(fp0=$(fp 0),fp8=$(fp 8))GiB"
