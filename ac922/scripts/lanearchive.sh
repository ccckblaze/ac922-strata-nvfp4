#!/bin/bash
# One folder per lane boot, taken while the boot's log is still its own.
#
# Why: every comparison after the fact (power9 vs power8 cpu columns, a geometry change's wake
# behaviour, a restart's cause) needs the boot's *own* evidence - the log slice between this boot's
# first byte and the next boot's first, plus the exact engine arguments (run.json), the lane
# environment, and the time window.  The engine log is one appended file, so the slice boundary is
# the byte offset lane.sh recorded at start (state `off=`); after the next boot those bytes are
# indistinguishable from the next process's.  lane.sh calls this before stop/restart.
#
# Never archives secrets: lane.env is copied with the API key line stripped.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
LANE_ENV=${LANE_ENV:-/data/work/lane.env}
[ -f "$LANE_ENV" ] && . "$LANE_ENV"
STATE=${STATE:-/tmp/strata-nvfp4-lane.state}
LOGD=${LOGD:-/data/champion}
RUN_JSON=${RUN_JSON:-/data/work/strata-nvfp4.run.json}
ARCH=${LANE_ARCHIVE:-/data/work/lane-archive}
LOG=$LOGD/strata-nvfp4-engine.log
[ -f "$STATE" ] || { echo "archive: no lane state file - nothing to archive"; exit 0; }
[ -f "$LOG" ]   || { echo "archive: no engine log at $LOG"; exit 0; }
off=$(sed -n 's/^off=//p' "$STATE" | head -1); off=${off:-0}
cards=$(sed -n 's/^cards=//p' "$STATE"); gpus=$(sed -n 's/^gpus=//p' "$STATE")
bts=$(sed -n 's/^ts=//p' "$STATE")
size=$(stat -c %s "$LOG"); [ "$off" -gt "$size" ] && off=0
id="$(date +%Y%m%dT%H%M%S)-${cards:-?}c-boot$(echo "${bts:-unknown}" | tr -d ':Z-')"
dir="$ARCH/$id"; mkdir -p "$dir"
tail -c +"$((off+1))" "$LOG" | gzip > "$dir/engine.log.slice.gz"
cp "$STATE" "$dir/lane.state"
[ -f "$LANE_ENV" ] && grep -av "API_KEY" "$LANE_ENV" > "$dir/lane.env"
[ -f "$RUN_JSON" ] && cp "$RUN_JSON" "$dir/run.json"
slog=$(ls -t "$LOGD"/strata-nvfp4-2*.log 2>/dev/null | head -1)
[ -n "$slog" ] && cp "$slog" "$dir/serve.stdout.log"
cat > "$dir/manifest.json" <<JSON
{"boot_started_utc":"${bts:-unknown}","archived_utc":"$(date -u +%FT%TZ)",
 "cards":"${cards:-?}","gpus":"${gpus:-?}","log_slice_from_byte":$off,
 "log_total_bytes_at_archive":$size,
 "engine_exe":"$(sed -n 's/.*"exe": "\([^"]*\)".*/\1/p' "$RUN_JSON" 2>/dev/null | head -1)",
 "lane_sh_commit":"$(git -C "$HERE/../.." rev-parse --short HEAD 2>/dev/null)"}
JSON
ls -1dt "$ARCH"/*/ 2>/dev/null | tail -n +41 | xargs -r rm -rf
echo "ARCHIVED $dir ($(du -sh "$dir" | cut -f1), slice $(( size - off )) bytes)"
