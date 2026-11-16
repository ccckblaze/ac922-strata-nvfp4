# Run — reference deployment and acceptance gates

## Layout used by the final baseline

```
AC922_ROOT=/data/work
SRC=/data/work/ac922-strata-nvfp4          # this repo, pinned: engine, serve, vision encoder and
                                           # data/expert-profile.bin all run from inside the checkout
MODEL_DIR=/data/work/Strata-data/models/nvfp4-gptq   # 17 files, see results/MODEL-SHA256SUMS.txt
LOGD=/data/champion
```

On the delivery host `/root/strata-nvfp4-run.sh` is a thin wrapper that exports exactly the
layout above (plus `RUN_JSON=/data/work/strata-nvfp4.run.json`,
`PIDF=/tmp/strata-nvfp4-run.pid`) and execs `ac922/scripts/strata-nvfp4-run.sh` from this
checkout. Nothing in the serving path reads the older `strata-nvfp4-src` tree: that tree
carries unreleased experiment bits (a default-off `STRATA_DEDUP` patch in `serve/` plus eight
uncommitted engine edits) and its `serve/` has drifted, so the pinned lane launches engine
**and** serve from this repo only.

Multimodal adds one optional asset under `MODEL_DIR` (`mmproj-F16.gguf`, ~904 MB, top level of
`unsloth/Qwen3.8-Flash-Next-GGUF`) and the CPU encoder at `$SRC/build-vision/bin/strata-vision`
(build: `BUILD.md`). Both are wired through the `"vision"` block in
`configs/strata-nvfp4-final.json`; `VISION=0 bash scripts/strata-nvfp4-run.sh start` removes the
block before launch for a text-only lane.

The repository launcher (`scripts/strata-nvfp4-run.sh`) defaults `SRC` to this checkout and
rewrites the reference config's paths from environment variables, so building here and running
here works without touching `configs/strata-nvfp4-final.json`.

## Bring-up sequence

1. Verify model assets before anything else:
   `cd $MODEL_DIR && sha256sum -c SHA256SUMS` → every line OK (a sparse "fake-complete" file from
   a hard power cut once passed a size check but failed hashes; sizes are not evidence).
2. Build the engine (`BUILD.md`) and, if this box has two NUMA islands like ours, the shim:
   `gcc -O2 -shared -fPIC ac922/scripts/numabind.c -o numabind.so -ldl`.
3. Start the lane:
   - 6-card: `GPUS=0,1,2,3,4,5 NB_CUT=0.5 bash ac922/scripts/strata-nvfp4-run.sh start`
   - 4-card: `GPUS=0,1,2,3 NB_CUT=0.69 bash ac922/scripts/strata-nvfp4-run.sh start`
   `NB_CUT` is the expert-arena NUMA split and must track the card:island ratio (0.5 for 3:3,
   0.69 for 3:1). The launcher calls `ple_park.sh 500` (splits the 48.8 GiB PLE page cache across
   both islands) and `LD_PRELOAD=numabind.so` (arena placement) — both are documented in
   `DECISIONS.md` and each is worth ~1% tg on its own.
4. Wait for health (`/health`, typically 3–7 min: the engine locks ~48 GiB of experts and fills
   the GPU expert cache; the box may be slow for 1–3 minutes, which is normal).

## Acceptance gates (run these, in this order, before trusting any number)

| Gate | Tool | Pass condition |
| --- | --- | --- |
| Multimodal | image chat probe (`image_url`, OpenAI content-list shape) | `/health` reports `images: true`; a reference image parses; probe with `max_tokens ≥ 256` (this model spends the budget on `reasoning_content` first — `content: null` at low budgets is truncation, not failure) |
| Lane evidence | `scripts/laneverify.sh http://127.0.0.1:8091 <engine-log>` | `max_context=262144`, geometry line `batch (pipelined, 3 groups of 2)` (6-card) or `2 groups of 2` (4-card), per-card VRAM 14.5–15.0 GiB, no stray containers |
| Lane alive | `scripts/alive.py` (three real probes: arithmetic / GSM8K item / HumanEval exec) | `ALIVE OK failed=0` — any `503`/`ctok=1` result means the lane is NOT usable, do not run suites |
| Determinism | `eval/bench_selftest.py`, `eval/selfcheck.py` | all OK, failures=0 |
| Quality reference | `eval/eval_runner.py` on a pinned subset, twice | identical hashes (`det_same=true`) |

The three-probe lane gate is not ceremony: two "reference stacks" in our campaign died for hours
of wasted GPU because a lane that *started* was assumed to *work*. It never produced more than
one token per request.

## Geometry switching and rollback

`stop` then `start` with the other `GPUS`/`NB_CUT`. The adopted state is fully described by:
launcher exports + `configs/strata-nvfp4-final.json` + engine pin. Keep dated backups of the
launcher before edits; adopt changes through the paired A/B protocol in `METHODOLOGY.md`, never
by vibes.
