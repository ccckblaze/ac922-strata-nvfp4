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
   - 4-card: `GPUS=0,1,2,3 NB_CUT=0.69 STRATA_BATCH=4 STRATA_BATCH_GROUPS=2 bash ac922/scripts/strata-nvfp4-run.sh start`
   Card count is a launch-time geometry, not a hot switch: `stop`, wait for per-card VRAM to
   drop below the reserve, then `start` with the other env set (about 2-7 min to `loaded:true`).
   `STRATA_BATCH`/`STRATA_BATCH_GROUPS` rewrite the pinned config's batch pair in place — the
   6-card default (6/3) needs no override; pass 4/2 on a 4-card boot and check the geometry
   line reports `2 groups of 2`.
   `NB_CUT` is the expert-arena NUMA split and must track the card:island ratio (0.5 for 3:3,
   0.69 for 3:1). The launcher calls `ple_park.sh 500` (splits the 48.8 GiB PLE page cache across
   both islands) and `LD_PRELOAD=numabind.so` (arena placement) — both are documented in
   `DECISIONS.md` and each is worth ~1% tg on its own.
   `start` refuses to launch across cards another lane already holds (the same occupancy guard the
   dev lane uses) — otherwise the 6-card default silently straddles 4/5 while a dev or cooperating
   thread is live there; override deliberately with `FORCE=1`. `stop`/`status` are pidfile-tree
   scoped for the same reason: a box-wide `pkill -f "build/strata --serve"` once killed the other
   lane's engine while its supervisor kept answering `/health` 200 with nothing behind it.
4. Wait for health (`/health`, typically 3–7 min: the engine locks ~48 GiB of experts and fills
   the GPU expert cache; the box may be slow for 1–3 minutes, which is normal).

## Development lane on the leftover cards (requires operator authorization)

Once the pinned lane drops to the 4-card geometry (B4: `GPUS=0,1,2,3`, `NB_CUT=0.69`,
see step 3 above), cards 4/5 free up for an independent dev/iteration instance:

```
bash ac922/scripts/devlane.sh check    # who holds the dev cards, RAM available
GPUS=... STRATA_BATCH=4 STRATA_BATCH_GROUPS=2 bash ac922/scripts/strata-nvfp4-run.sh start   # prod -> B4 (authorized)
bash ac922/scripts/devlane.sh start    # dev lane on 4,5 at port 8092
```

- **Occupancy guard**: `start` refuses if any process holds the dev cards — cards 4/5 may
  belong to a cooperating thread (e.g. I2V); preempt only deliberately with `FORCE=1`.
- **NUMA**: GPU3-5 are the node8 NVLink triangle (cpus 88-175), GPU0-2 the node0 triangle.
  `devlane.sh` binds its engine to the island its cards live on (`numactl --cpunodebind
  --preferred`, auto-derived, override with `DEV_NUMA=off|<node>`), so dev staging never
  pulls node0 bandwidth away from the production arena. It deliberately does **not** use
  `numabind.so`/`NB_CUT` (that is the straddling lane's arena split) and does not re-run
  `ple_park` — weight page cache is shared read-only between lanes.
- **Sizing** (env-overridable): `DEV_MAX_CONTEXT=131072 DEV_KV_RESIDENT=16384
  DEV_CC_MIB=32768 DEV_CC_SLOTS=4 DEV_RESIDENT_GIB=16`, batch pair follows the card count
  (2/1). These keep the dev lane's new RAM footprint (host-tier KV) small enough that the
  production B4 lane keeps its pinned arena; `start` also aborts below `DEV_MIN_AVAIL_GIB=32`
  available. Vision attaches automatically if `build-vision/bin/strata-vision` exists
  (`DEV_VISION=0` to force text-only).
- **Isolation**: separate pidfile (`/tmp/strata-nvfp4-dev.pid`), config
  (`/data/work/strata-nvfp4-dev.json`), logs (`*-dev*`), port 8092; `stop` kills only its own
  process tree, never a broad pkill.
- **Parked-conversation capacity ships at `--conversation-cache-slots 32 --conversation-cache-mib
  98304`** (see `DECISIONS.md`): below the live conversation count it relays nothing at all, so size
  it to the concurrency you intend to serve, not to the RAM budget — the footprint reaches only
  17-42% of the budget in production, while the slot count is a cliff.  `CC_SLOTS`/`CC_MIB` override
  the shipped values at launch without editing the config.

Full 6-card acceptance (B6) is only restored by `stop`-ing the dev lane and restarting the
production launcher without the 4-card overrides.

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
