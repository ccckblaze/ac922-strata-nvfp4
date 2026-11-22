#!/bin/bash
# lanecheck.sh [4|6]: read-only production cross-check.
#
# Run it every time the lane goes back to production, and every time a published number is
# questioned.  It sends no requests and restarts nothing: it grades the lane's own lines plus the
# box.  Exit code 0 all green, 1 any WARN, 2 any FAIL.
#
# The one rule this encodes: the engine log is appended across boots, so nothing may be read from it
# without anchoring on the newest `engine started:` banner first (the trap that let a respawning lane
# pass every gate).
set -u
CARDS=${1:-4}
# the ship cards come from the lane state (the 4-card map is 0,1,3,4 now, not the first four);
# an absent/stale state falls back to the old "first N cards" reading.
LANE_STATE=${LANE_STATE:-/tmp/strata-nvfp4-lane.state}
SHIP_GPUS=""
[ -f "$LANE_STATE" ] && SHIP_GPUS=$(sed -n 's/^gpus=//p' "$LANE_STATE" | tail -1)
LANE_ENV=${LANE_ENV:-/data/work/lane.env}
LOGD=${LOGD:-/data/champion}
ENG=${ENG:-$LOGD/strata-nvfp4-engine.log}
source /etc/profile.d/env.sh 2>/dev/null || true
[ -f "$LANE_ENV" ] && . "$LANE_ENV"
N=0; W=0; F=0
ok(){ echo "  ok    : $*"; N=$((N+1)); }
warn(){ echo "  WARN  : $*"; W=$((W+1)); }
fail(){ echo "  FAIL  : $*"; F=$((F+1)); }

P=$(pgrep -f 'build/strata --serv[e]' | head -1)
if [ -z "${P:-}" ]; then echo "LANE-CHECK-FAIL no engine process"; exit 2; fi
ok "engine pid $P, uptime $(ps -o etime= -p "$P" | tr -d ' ')"

OFF=$(grep -aob "engine started:" "$ENG" | tail -1 | cut -d: -f1)
BOOTS=$(grep -aob "engine started:" "$ENG" | cut -d: -f1 | awk -v o="$OFF" '$1>=o{c++} END{print c+0}')
SLICE=$(mktemp)
tail -c +$((OFF + 1)) "$ENG" > "$SLICE"
if [ "${BOOTS:-1}" -gt 1 ]; then
  fail "the engine restarted $BOOTS times inside this lane boot - numbers span processes"
else
  ok "single boot (no respawn hiding behind a green /health)"
fi
if grep -aq "out of memory" "$SLICE"; then
  fail "this boot carries $(grep -ac 'out of memory' "$SLICE") out-of-memory lines"
else
  ok "no out-of-memory lines in this boot"
fi
# The engine's own proof lines.  `disk tier:` is printed by the launcher into its restart log, not
# into the engine log, so the cold tier is proved here by what it actually does (a `disk hit:`), and
# the shape by the same anchor lane.sh uses (`STRATA_PIPED_MTP:`).
for probe in "STRATA_PIPED_MTP:" "serve: layer split: layers" "resident RAM mode:" "disk hit:"; do
  if grep -aq "$probe" "$SLICE"; then
    ok "$(grep -a "$probe" "$SLICE" | tail -1 | sed 's/^strata [a-z]*: //' | cut -c1-130)"
  else
    warn "no proof line in this boot for '$probe'"
  fi
done
if grep -aq "NUMA placement FAILED" "$SLICE"; then
  warn "$(grep -a 'NUMA placement FAILED' "$SLICE" | tail -1 | sed 's/.*FAILED: //' | cut -c1-100)"
fi

while IFS=$'\t' read -r k rest; do
  case "$k" in
    no-rows) warn "no completed requests in this boot yet - send one request before grading rates";;
    requests) ok "$rest";;
    short-band)
      ru=${rest##*reuse_med_pct=}
      ru=${ru%%[[:space:]]*}
      if awk -v x="$ru" 'BEGIN{exit !(x+0 < 50)}'; then
        warn "short turns re-prefill their own history ($rest) - check CC_MIN_TOKENS against traffic depth"
      else
        ok "short-band reuse healthy ($rest)"
      fi;;
    deep-band)
      ru=${rest##*reuse_med_pct=}
      ru=${ru%%[[:space:]]*}
      if awk -v x="$ru" 'BEGIN{exit !(x+0 < 80)}'; then
        warn "deep chains are not coming back from cache ($rest)"
      else
        ok "deep-band reuse ($rest)"
      fi;;
    vram)
      used=${rest%%|*}; other=${rest#*|}
      ok "VRAM MiB used - ship cards [$used] / other cards [$other]"
      if [ -n "$other" ]; then
        hi=$(echo "$other" | tr '/' '\n' | sort -n | tail -1)
        if [ "${hi:-0}" -gt 1000 ]; then
          warn "a non-ship card is using ${hi} MiB - someone else is on it"
        else
          ok "non-ship cards idle (<1 GiB each)"
        fi
      fi;;
    ram-avail-gib)
      if [ "${rest:-0}" -lt 24 ]; then fail "MemAvailable ${rest} GiB (<24 GiB)"; else ok "MemAvailable ${rest} GiB"; fi;;
    *) ok "$k: $rest";;
  esac
done < <(python3 - "$SLICE" "$CARDS" "$SHIP_GPUS" <<'PY'
import re, statistics as st, subprocess, sys
slice_path, cards = sys.argv[1], int(sys.argv[2])
ship = [int(x) for x in sys.argv[3].split(",") if x.strip().isdigit()] if len(sys.argv) > 3 else []
txt = open(slice_path, errors="replace").read()
def emit(*a):
    print("\t".join(str(x) for x in a))
pat = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \([\d.]+ tok/s\), "
                 r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")
rows = [m.groups() for m in (pat.search(l) for l in txt.splitlines()) if m]
if not rows:
    emit("no-rows", "0")
else:
    ptok = [int(r[0]) for r in rows]
    reuse = [100.0 * int(r[1]) / int(r[0]) if int(r[0]) else 0.0 for r in rows]
    tg = [float(r[6]) for r in rows]
    emit("requests", "n=%d ptok_med=%d ptok_max=%d" % (len(rows), int(st.median(ptok)), max(ptok)))
    for band, lo, hi in (("short-band", 0, 32768), ("deep-band", 32768, 1 << 30)):
        sel = [(u, g) for p, u, g in zip(ptok, reuse, tg) if lo <= p < hi]
        if sel:
            emit(band, "n=%d reuse_med_pct=%.1f tg_med=%.1f"
                 % (len(sel), st.median([u for u, _ in sel]), st.median([g for _, g in sel])))
    a = sum(int(r[7]) for r in rows); m = sum(int(r[8]) for r in rows)
    if m:
        emit("draft-accept", "%.1f%% over %d proposals" % (100.0 * a / m, m))
for rx, key in ((r"KV streaming: ([\d.]+)% of (\d+) block reads hit VRAM", "kv-hit"),
                (r"the OS read ([\d.]+) MB from storage \((\d+) major faults\)", "file-tier")):
    found = re.findall(rx, txt)
    if found:
        emit(key, "%s%% / %s" % found[-1] if key == "kv-hit" else "%s MB, %s faults" % found[-1])
cc = re.findall(r"conversation cache: ([^\n]*)", txt)
if cc:
    emit("conv-cache", cc[-1][:120])
try:
    out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                                   "--format=csv,noheader,nounits"], universal_newlines=True)
    used = [x.strip() for x in out.strip().splitlines()]
    if not ship:
        ship = list(range(cards))
    emit("vram", "/".join(used[i] for i in ship) + "|" + "/".join(used[i] for i in range(len(used)) if i not in ship))
except Exception:
    pass
avail = None
for line in open("/proc/meminfo"):
    if line.startswith("MemAvailable"):
        avail = int(line.split()[1]) // 1048576
emit("ram-avail-gib", avail)
PY
)
rm -f "$SLICE"
echo "LANE-CHECK cards=$CARDS ok=$N warn=$W fail=$F"
[ "$F" -gt 0 ] && exit 2
[ "$W" -gt 0 ] && exit 1
exit 0
