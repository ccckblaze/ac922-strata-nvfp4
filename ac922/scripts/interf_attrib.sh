#!/usr/bin/env bash
# Launcher for interf_attrib.py: production lane, one boot, detached.
# Refuses to run against a lane that is not healthy, and never prints the key.
set -uo pipefail
REPO=${REPO:-/data/work/ac922-strata-nvfp4}
source /etc/profile.d/env.sh 2>/dev/null || true
PORT=${PORT:-8091}
if [ -z "${STRATA_API_KEY:-}" ]; then
  echo "FATAL: STRATA_API_KEY not available from /etc/profile.d/env.sh" >&2
  exit 1
fi
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $STRATA_API_KEY" \
       "http://127.0.0.1:$PORT/health")
echo "pre-flight health=$code"
[ "$code" = "200" ] || { echo "FATAL: lane $PORT is not healthy" >&2; exit 1; }
pid=$(pgrep -f "strata --serve" | head -1)
echo "engine pid=$pid  log=$(stat -c %s /data/champion/strata-nvfp4-engine.log) bytes"
exec python3.11 "$REPO/ac922/scripts/interf_attrib.py" "$@"
