#!/bin/bash
# Self-consistency sentinel (the gate Wave Pp proved we needed): for every prefill-shape / chunking /
# pipeline change, the lane must keep (a) cold output == cached output inside one boot, at 8.5K/64K/131K,
# and (b) the same cold hash across boots.  Run with the lane already up: selfcheck.sh [TAG].
#
# Same two fixes as qualgate.sh: authenticate, and name the lane's own engine log.  The engine pid also
# comes from the lane pidfile tree now, because a bare pgrep -f "build/strata --serve" can land on the
# other lane's engine and report its environment as this lane's.
set -u
TAG=${1:-sc$(date +%m%dT%H%M)}
HERE=$(cd "$(dirname "$0")" && pwd)
LOGD=${LOGD:-/data/champion}
PIDF=${PIDF:-/tmp/strata-nvfp4-run.pid}
export SB_URL=${SB_URL:-http://127.0.0.1:8091}
case "$SB_URL" in
  *8092) export SB_ENG=${SB_ENG:-$LOGD/strata-nvfp4-dev-engine.log};;
  *)     export SB_ENG=${SB_ENG:-$LOGD/strata-nvfp4-engine.log};;
esac
[ -n "${STRATA_API_KEY:-}" ] || { echo "GATE-ERROR no-api-key (source the env that defines STRATA_API_KEY)"; exit 3; }
OUT=/data/work/cc-$TAG.out
python3.11 "$HERE/cccheck.py" "$TAG" > "$OUT" 2>&1 || { echo "GATE-ERROR cccheck failed"; tail -3 "$OUT"; exit 3; }
grep -q '^CC-' "$OUT" || { echo "GATE-ERROR no CC- rows in $OUT"; tail -3 "$OUT"; exit 3; }
root=$(cat "$PIDF" 2>/dev/null)
descendants(){ local p kids; for p in "$@"; do kids=$(pgrep -P "$p" 2>/dev/null || true); [ -n "$kids" ] && { echo $kids; descendants $kids; }; done; }
pid=""
for k in $root $(descendants "$root"); do
  tr '\0' ' ' < /proc/$k/cmdline 2>/dev/null | grep -q "build/strata --serve" && pid=$k
done
echo "SELFCHK $TAG pid=${pid:-unknown} env=$(tr '\0' '\n' < /proc/${pid:-1}/environ 2>/dev/null | grep -aoE 'STRATA_SPLIT_PF_[A-Z]+=[0-9.]+' | tr '\n' ',') $(grep -a '^CC-' "$OUT")"
grep -ao '"tgt": [0-9]*|"cold": "[0-9a-f]*"|"cold_eq_cache": [a-z]*' "$OUT" | paste - - -
