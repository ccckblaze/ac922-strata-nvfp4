#!/bin/bash
# Adopted environment for any AC922 NVFP4 lane (production or dev). Sourced by
# strata-nvfp4-run.sh and devlane.sh; every var honours a caller-provided value.
# Ledger: ac922/DECISIONS.md (SM70 tables, select-simt, batchenv).
export STRATA_SM70_TABLE=${STRATA_SM70_TABLE:-1} STRATA_PF_WMMA=${STRATA_PF_WMMA:-1}
export STRATA_FUSE_DECODE=${STRATA_FUSE_DECODE:-1} STRATA_ARGMAX_ROWS_SM70=${STRATA_ARGMAX_ROWS_SM70:-1}
# >>> AC922 adopted env (DECISIONS.md: select-simt) >>>
export STRATA_SELECT_SIMT=${STRATA_SELECT_SIMT:-2}
export STRATA_SELECT_ALT_MIN_TOKENS=${STRATA_SELECT_ALT_MIN_TOKENS:-65536}
# <<< AC922 adopted env <<<
export STRATA_PREFILL_STREAM_AHEAD=${STRATA_PREFILL_STREAM_AHEAD:-0}
# >>> AC922 adopted env (DECISIONS.md: batchenv) >>>
export STRATA_PIPE_ADAPT_EVERY=${STRATA_PIPE_ADAPT_EVERY:-8}
export STRATA_BATCH_MTP=${STRATA_BATCH_MTP:-1}
export STRATA_PIPED_MTP=${STRATA_PIPED_MTP:-1}
export STRATA_BATCH_SELF_COMMIT=${STRATA_BATCH_SELF_COMMIT:-0}
# >>> AC922 adopted env (DECISIONS.md: admission short-first) >>>
# serve/server.py only (no engine flag): a NEW admission whose prompt is at most this many tokens goes
# before longer ones waiting for the control lines; a long prompt that has waited
# STRATA_ADMIT_MAX_WAIT_S (default 30) counts as short, so nothing starves.
# Measured 2026-11-20 (4-card prod shape, burst = 6 cold ~23K reads + 1 chat probe):
#   chat TTFT  median 62.4 s -> 14.5 s (-77%), worst 90.5 s -> 15.3 s (-83%)
#   long-request wall    +10..12%
# Rates (PP/TG tok/s) and every byte of the numeric path are untouched.  Raise the cap to move more
# traffic into the fast lane; 0 turns the policy off (upstream default).
export STRATA_ADMIT_SHORT_FIRST=${STRATA_ADMIT_SHORT_FIRST:-8192}
# >>> AC922 adopted env (production decode-window observation) >>>
# One stderr line per batched window: expert/host-reach/CPU ms split, drafts accepted N of M.
# Pure counters - no diagnostic kernels, no extra syncs (upstream anatomy doc, 2026-11-22).
# The 22nd anatomy run used this exact setting in production; steady-state overhead measured flat.
# Lane discipline: every production boot log is archived with its boot config (runbook 356 handback).
export STRATA_DECODE_TIMING=${STRATA_DECODE_TIMING:-1}
# <<< AC922 adopted env (production decode-window observation) <<<
# <<< AC922 adopted env <<<
# >>> AC922 adopted env (DECISIONS.md: prefill chunk ceiling) >>>
# `--prefill auto` sizes the chunk from what every expert cache can lend at boot, and with the cold
# tier's pages resident that has been settling one notch down per boot (32768 -> 16384 -> 8192), which
# costs cold prefill ~20% and lengthens the stall every other request pays behind a long read.
# Asking for the ceiling does not override the engine's safety: it still clamps to what fits and says so
# ("prompt chunk 32768 -> 16384 tokens so its buffers fit in every expert cache").
# Measured 2026-11-23 (DEVLOG H.31, 4 cards, ~220K cold prompt + a 64-token prompt 12 s behind it):
#   auto -> 8192: deep 80.5 s, small 65.9 s   |   ask 32768 -> 16384: deep 65.3 s, small 51.6 s
# Chunk-class gate: selfcheck.sh pf32k1 => ALL-EQ 3 (cold == cached at 8.5K/64K/131K).
export PREFILL_CHUNK=${PREFILL_CHUNK:-32768}
# <<< AC922 adopted env <<<
