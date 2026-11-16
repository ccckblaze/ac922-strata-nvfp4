#!/bin/bash
# AC922 fidelity eval suite. Run ONLY inside an exclusive engine window; no-op when the server is down.
# MODE=smoke|full  THINK=0|1  LIMITS overrides per task via <TASK>_LIM  MCLIM caps multiple-choice sets
set -uo pipefail
URL=${SB_URL:-http://127.0.0.1:8091}
MODE=${MODE:-smoke}
D=/root/ac922-eval
LOGD=$D/out/$(date +%Y%m%d-%H%M%S)_$MODE
curl -s -m6 "$URL/health" >/dev/null 2>&1 || { echo "engine not reachable at $URL -> abort (do not force during debugging)"; exit 1; }
mkdir -p "$LOGD"
echo "MODE=$MODE URL=$URL LOGD=$LOGD"
T=${THINK:-1}

run() { # run <task> <limit> <maxtok> [extra args...]
  local t=$1 lim=${2} mt=${3}; shift 3
  local over="${t^^}_LIM"
  lim=${!over:-$lim}
  echo "== $t (n=$lim think=$T mt=$mt $*) =="
  nice -n10 python3.11 $D/eval_runner.py "$t" --limit "$lim" --thinking "$T" --max-tok "$mt" \
      --tag "$MODE" --out "$LOGD/$t.jsonl" "$@" 2>&1 | tee "$LOGD/$t.log" | tail -3
}

# v2 (2026-11-16): multiple-choice must NOT run with thinking on. Measured on the NVFP4 lane:
# cmmlu 24/24 and ceval 8/24 hit the 400-token cap, so the "Answer: X" line never appears and the
# score measures the budget, not the model (ceval's untruncated 16/16 were all correct).
# MC therefore runs THINK=0 with a 1200-token budget. Reasoning/code tasks keep $T.
runmc() { # runmc <task> <limit> [extra args...]
  local t=$1 lim=${2}; shift 2
  local over="${t^^}_LIM"
  lim=${!over:-$lim}
  echo "== $t (n=$lim think=0 mt=${MC_MT:-1200} $*) =="
  nice -n10 python3.11 $D/eval_runner.py "$t" --limit "$lim" --thinking 0 --max-tok "${MC_MT:-1200}" \
      --tag "$MODE" --out "$LOGD/$t.jsonl" "$@" 2>&1 | tee "$LOGD/$t.log" | tail -3
}

if [ "$MODE" = "smoke" ]; then
  run gsm8k 16 2000
  run math 16 6000
  run aime 8 12000
  run humaneval 16 2000
  run humaneval_plus 16 2000
  run mbpp 16 2000
  run mbpp_plus 16 2000
  run ifeval 16 2000
  runmc ceval 24 --shot 5
  runmc cmmlu 24 --shot 5
  run lcb 6 3000
else
  run gsm8k 1319 4000
  run math 500 8000
  run aime 60 16000
  run humaneval 164 3000
  run humaneval_plus 164 3000
  run mbpp 974 3000
  run mbpp_plus 378 3000
  run ifeval 541 3000
  MC=${MCLIM:-1200}
  runmc ceval "$MC" --shot 5
  runmc cmmlu "$MC" --shot 5
  run lcb "${LCB_LIM:-342}" 3000
  if [ -x /data/work/strata-nvfp4-src/tools/needle_bench.py ]; then
    echo "== needle =="
    nice -n10 python3.11 /data/work/strata-nvfp4-src/tools/needle_bench.py \
      --lengths 32k,128k,262k --depths 10,50,90 --url "$URL" --out "$LOGD/needle.json" 2>&1 | tail -5
  fi
fi
echo "SUITE-DONE logdir=$LOGD"
