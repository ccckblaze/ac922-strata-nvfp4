#!/usr/bin/env bash
# Launcher for concscale.py.  The batch shape is a boot-level choice, so a scaling arm is chosen here
# and never edited into the config (H.22: run.sh forces the auto-shape unless STRATA_BATCH* is set).
set -uo pipefail
REPO=${REPO:-/data/work/ac922-strata-nvfp4}
source /etc/profile.d/env.sh 2>/dev/null || true
PORT=${PORT:-8091}
[ -n "${STRATA_API_KEY:-}" ] || { echo "FATAL: no STRATA_API_KEY" >&2; exit 1; }
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $STRATA_API_KEY" \
       "http://127.0.0.1:$PORT/health")
echo "pre-flight health=$code  batch=${STRATA_BATCH:-auto} groups=${STRATA_BATCH_GROUPS:-auto}"
[ "$code" = "200" ] || { echo "FATAL: lane $PORT unhealthy" >&2; exit 1; }
exec python3.11 "$REPO/ac922/scripts/concscale.py" "$@"
