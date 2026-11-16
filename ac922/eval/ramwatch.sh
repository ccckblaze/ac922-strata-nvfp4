#!/bin/bash
# Sample RAM / engine liveness / window rate while a sweep runs unattended.
# An OOM-killed engine mid-sweep costs the night and the whole queue, so the queue carries a watch.
#   ramwatch.sh [duration-s] [outfile]
set -u
DUR=${1:-1500}
OUT=${2:-/data/work/note312/ram.log}
ENGLOG=${ENGLOG:-/data/champion/strata-nvfp4-engine.log}
engine_pid(){
  local p c
  for p in $(ls /proc | grep -E '^[0-9]+$'); do
    c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null) || continue
    case "$c" in *build/strata\ --serve*) echo "$p"; return;; esac
  done
  echo DEAD
}
end=$((SECONDS + DUR))
while [ $SECONDS -lt $end ]; do
  av=$(awk '/MemAvailable/{printf "%.0f", $2/1048576}' /proc/meminfo)
  ep=$(engine_pid)
  rs=$(grep -a 'rows/s (admissions' "$ENGLOG" 2>/dev/null | tail -1 | sed -E 's/.*= ([0-9.]+) rows.*/\1/')
  echo "$(date +%H:%M:%S) avail_gib=$av engine=$ep rows_s=${rs:-NA}" >> "$OUT"
  sleep 30
done
