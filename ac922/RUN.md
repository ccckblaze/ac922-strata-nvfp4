# Run — reference deployment and acceptance gates

## Layout used by the final baseline

```
AC922_ROOT=/data/work
SRC=/data/work/bump-src                    # this repo at the 0.1.61 bump line: engine, serve, vision
                                           # encoder and data/expert-profile.bin all run from inside
                                           # the checkout.  The 0.1.59 tree (/data/work/ac922-strata-nvfp4)
                                           # is kept intact as the rollback, not as the serving path.
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
   - 4-card (the shipped shape): `bash ac922/scripts/lane4.sh start`
   - 6-card: `bash ac922/scripts/lane6.sh start`
   Both are two-line shortcuts over `lane.sh <4|6> <start|stop|restart|status>`, which is the thing
   that actually knows how to boot this box: it sources the machine layout (`LANE_ENV`, default
   `/data/work/lane.env`, template `scripts/lane.env.example`), refuses to start when the NUMA shim
   is missing, waits for `/health`, then proves the geometry from the engine's own lines — the
   `batch (pipelined, 2 groups of 2)` shape, the stage count, and zero `NUMA placement FAILED` /
   `remote reads` lines — and prints `GEOMETRY-PASS` or fails loudly.  Add `--reanchor` after a
   geometry change to preserve the old REF and write this shape's anchor in one go.
   Use the bare launcher (`strata-nvfp4-run.sh start`, `GPUS=... NB_CUT=... STRATA_BATCH=...`) only
   when you are deliberately overriding a knob: it takes the repo-relative layout by default, and a
   boot that misses `LD_PRELOAD=numabind.so` is silently not comparable (measured 2026-11-17).
   Card count is a launch-time geometry, not a hot switch: `lane.sh <cards> restart` stops, waits
   for per-card VRAM below the reserve, boots the other shape and re-verifies (2-7 min to health).
   The launcher derives the batch pair and the NUMA cut from the card count (4-card 4/2 with
   `NB_CUT=0.69`, 6-card 6/3 with `NB_CUT=0.5`); `STRATA_BATCH`/`STRATA_BATCH_GROUPS`/`NB_CUT`
   still override them for experiments.
   `NB_CUT` is the expert-arena NUMA split and must track the card:island ratio (0.5 for 3:3,
   0.69 for 3:1). The launcher calls `ple_park.sh 500` (splits the 48.8 GiB PLE page cache across
   both islands) and `LD_PRELOAD=numabind.so` (arena placement) — both are documented in
   `DECISIONS.md` and each is worth ~1% tg on its own.
   `start` refuses to launch across cards another lane already holds (the same occupancy guard the
   dev lane uses) — otherwise the 6-card default silently straddles the dev cards while a dev or cooperating
   thread is live there; override deliberately with `FORCE=1`. `stop`/`status` are pidfile-tree
   scoped for the same reason: a box-wide `pkill -f "build/strata --serve"` once killed the other
   lane's engine while its supervisor kept answering `/health` 200 with nothing behind it.
4. Wait for health (`/health`, typically 3–7 min: the engine locks ~48 GiB of experts and fills
   the GPU expert cache; the box may be slow for 1–3 minutes, which is normal).

## After a host reboot: what is still yours and what quietly changed

This box gets rebooted for real operations (DIMMs, disks), so treat "it came back" as a claim to
check, not an assumption.  Everything below was re-verified on 2026-11-19 after a cold reboot:

| Item | Where it comes from | How to verify it survived |
| --- | --- | --- |
| swap off | nothing re-enables it (no unit, no fstab entry) | `swapon --show` prints nothing |
| `vm.swappiness 10`, `vm.dirty_ratio 0`, `vm.min_free_kbytes 360448` | `ac922-memcfg.service` -> `/usr/local/sbin/ac922-memcfg --apply --tmpfs shm`, which derives the numbers from the **real CPU-attached DDR**, so a DIMM change is picked up on the next run | `sysctl vm.swappiness vm.dirty_ratio vm.min_free_kbytes` |
| `/dev/shm` 8 G | the same unit (`--tmpfs shm`) | `findmnt -no SIZE /dev/shm` |
| 57 GiB of locked memory | the shell limit is still `64` (KiB) and stays that way; root's `CAP_IPC_LOCK` overrides `RLIMIT_MEMLOCK`, which is why pinning works without editing limits.conf | the engine's own line: `mapped pinned cache complement ready: resident 57.07 GiB, pinned 57.07 GiB in 18 s` |
| NUMA shim | built file at `/data/work/numabind.so` | `lane4.sh start` refuses when it is missing, `status` prints `shim : LD_PRELOAD=...` |
| LAN entry `192.168.168.171:8091` | `ac922-strata-lan-proxy.service` (enabled, came back on its own) | `curl` `/health` on the LAN IP, not only on `127.0.0.1` |

Three operational facts worth remembering:

- **Public relay is not on this host.**  After this reboot the BMC reported
  `HostState.Running` while `ssh ac922-ip` (the 3322.org:2222 mapping) stalled at *banner exchange* -
  the mapping lives on the router, so it does not recover with the OS.  The lab jump (`ssh ac922-j`,
  via `jlab`) reaches the host directly and is the route to use until the relay comes back.
- **Reboot is also the fix for unattributed VRAM.**  Card 4 had held 14,190 MiB for ~14 h with
  `nvidia-smi -i 4 -q -d PIDS` = `Processes: None`, 0% utilization and identical readings across
  samples - a leaked driver allocation, not somebody's live job.  After the reboot it read 0 MiB.
  Check `PIDS` before blaming a co-resident thread for "occupying" a card.
- **A co-resident job does not come back by itself.**  The I2V/GR00T stream on card 5 was the only
  other live GPU tenant and died with the reboot; its owner has to restart it.

On the launch itself: `lane4.sh start` boots, waits for `/health`, proves the geometry and prints
`GEOMETRY-PASS`, but it does not reliably return to the caller afterwards - the launcher ends up
`sitting in wait` on the serve frontend (observed: PID reparented to init, frontend stdout already
redirected to the log, so the lane does not depend on the caller's channel).  Consequences that
actually matter: never pipe the start into `tail`/`head` (the pipe keeps the whole output buffered
and you conclude the boot hung when only your reader did), and read the verdict with
`bash ac922/scripts/lane4.sh status`, which re-runs the same geometry proof read-only.

## Development lane on the leftover cards (requires operator authorization)

Once the pinned lane holds the 4-card geometry (the shipped default `0,1,3,4` — two cards per
island, see step 3 above), the remaining cards `2,5` free up for an independent dev/iteration
instance.  The split is deliberate: one dev card per island, so the dev lane sees the *same*
cross-NUMA straddle as the ship lane (environment consistency for the NUMA tickets).

```
bash ac922/scripts/devlane.sh check    # who holds the dev cards, RAM available
bash ac922/scripts/lane4.sh start      # prod lane, 4 cards (default, geometry-gated)
bash ac922/scripts/devlane.sh start    # dev lane on 2,5 at port 8092 (straddle mirror)
```

- **Deep windows on two cards need a different shape than the ship lane.** The config the shipped
  lane runs (`--resident-budget-gib 64 --vram-reserve-mib 832 --batch <cards>`) dies on two cards at
  >=96K prompts: first `ggml-cuda (strata mmq): out of memory`, then the stall watchdog killing a slow
  engine. The verified dev shape is
  `DEV_BATCH=1 DEV_KV_RESIDENT=8192 DEV_MAX_CONTEXT=163840 DEV_RESIDENT_GIB=40 STRATA_WATCHDOG_S=600
   DEV_FLAGS="--vram-reserve-mib 3072" bash ac922/scripts/devlane.sh start`.
  It holds a 126K window, but its decode window costs ~200 ms and is host-CPU-expert dominated, so use
  it for build checks, deep-window availability and bit-exactness - not to price a decode kernel
  (`DEVLOG.md` H.1).

- **Occupancy guard**: `start` refuses if any process holds the dev cards (default `2,5`) — they may
  belong to a cooperating thread (e.g. I2V); preempt only deliberately with `FORCE=1`.
- **NUMA**: GPU3-5 are the node8 NVLink triangle (cpus 88-175), GPU0-2 the node0 triangle.
  When the dev cards live on one island, `devlane.sh` binds the engine to it (`numactl
  --cpunodebind --preferred`, auto-derived, override with `DEV_NUMA=off|<node>`).  When they
  **span** islands (the default `2,5`), it mirrors the ship lane instead: `LD_PRELOAD=numabind.so`
  with `NB_CUT` = the node0 card share, so arena placement behaves like production.  It does not
  re-run `ple_park` — weight page cache is shared read-only between lanes.
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

`bash ac922/scripts/lane4.sh restart` / `bash ac922/scripts/lane6.sh restart` — nothing else, the
batch pair, `NB_CUT` and the machine layout are `lane.sh`'s job, and the boot is not accepted until
the engine's own lines match the requested shape. The adopted state is fully described by:
launcher exports +
`configs/strata-nvfp4-final.json` + engine pin. Keep dated backups of the launcher before edits;
adopt changes through the paired A/B protocol in `METHODOLOGY.md`, never by vibes.

The quality reference is per geometry: `/data/work/qualgate/REF.json` belongs to the shape the
lane is running, and the previous shape stays beside it as `REF-<cards>-<date>.json`.  Switching
geometry means re-anchoring (`qualgate.sh baseline`) after a warm-up round, then verifying in the
same boot that the det hashes still equal the other geometry's — the det fixture is bitwise across
geometries (measured 2026-11-17: 4-card and 6-card both `bfd40ecd4692`), so a det change is a real
numeric regression, while relay-cell agreement against a cross-geometry REF has no verdict power.

## Switching back from production: audit the lane before trusting the next measurement

The lane serves real traffic while you are not looking.  A `/health` 200 and a green geometry check
say nothing about whether it kept its **rates** or its **mechanisms**, and every campaign in this
repository that started from "the lane was fine" later found a boot where it was not.  So the rule is:
whenever work switches back from the production lane, run the audit over the boot that just served,
before taking a baseline.

```bash
cd /data/work/ac922-strata-nvfp4
CARDS=4 python3.11 ac922/eval/laneaudit.py            # the current boot
CARDS=4 SINCE=delta python3.11 ac922/eval/laneaudit.py    # only the traffic since the last audit
CARDS=6 SINCE=200000 python3.11 ac922/eval/laneaudit.py   # or a trailing slice
```

`SINCE=delta` is the shape for a switch back: it audits the lines nobody has audited yet, then moves
the watermark (`/data/work/laneaudit.watermark`, override with `WM=`) to end-of-log, so the next
switch back starts exactly where this one stopped.  Seed it once from a boot audit:

```bash
wc -l < /data/champion/strata-nvfp4-engine.log > /data/work/laneaudit.watermark
CARDS=4 SINCE=delta python3.11 ac922/eval/laneaudit.py
```

Verdicts are `PASS` / `WARN` / `FAIL` / `THIN`.  `THIN` means the sweep was clean but the window held
fewer than five requests, which is not evidence about rates -- audit again after more traffic, or
fall back to `SINCE=boot`.  The same reasoning applies per band: a narrow scope needs 6 decode
samples (4 cold-prefill) in a band before that band gets a verdict, and prints **`SKIP`** with its
numbers below that.  A few requests sharing the lane with live concurrency is not a band rate -- the
first delta audit on the box flagged `decode b3 80-165K` at 0.79 on n=5, which was the sample floor
talking, not the lane.  A green lane must always mean "green over traffic", never "green over
nothing".  In a line-window scope `engine-respawn` counts the `engine started:` lines inside the
window: zero is expected, one means the lane restarted while you were away (WARN, check why), two
means it did it more than once (FAIL).

Read-only: it parses the engine's own per-request telemetry block (prompt/reused/read with pp and tg,
resident RAM and blob reads, expert and KV tier hit rates, the file-tier line, conversation-cache
park/restore with eviction counts, the batched ring line with rows per group-step and MTP proposals
kept).  No GPU, no request, no model text.  Exits non-zero on FAIL, so it can gate a switch.

What each family means, and the verdicts we have actually earned:

| Check | Question it answers | Adopted expectation |
| --- | --- | --- |
| `decode b1..b5` | did decode keep its band rates | p90 within 15% of the `PERFORMANCE.md` anchor; p90 not median, because concurrent traffic legitimately slows every stream |
| `short-vs-deep inversion` | is a short window slower than a deep one | must not be; that shape is a defect, never noise (it is how the 2026-11-16 "decode 20 / prefill 500" report was finally reconciled) |
| `cold pp b*` | did cold prefill keep its rates | median within 25% |
| `batch rows/group-step` | are windows carrying more than one row | >= 1.3; near 1.0 means each window serves one sequence, so the shared expert read that makes batching worth anything never happens |
| `expert blob reads` | did the RAM expert tier miss to storage | 0, hard FAIL |
| `storage reads` | how often experts were fetched through the OS anyway | WARN with MB and major faults; a warm page cache should hold this near zero |
| `KV VRAM hit` | how much of the window is waiting on the host | p10 >= 60; the residual is the host-wait surface still on the backlog |
| `eviction shape` | is residency saturated | WARN when every eviction lands on the deepest parked level; averaged eviction counts hid this once (98.5% on one level named the knob) |
| `prefill chunking` | how varied the deep-prefill chunk geometry is | informational WARN: chunk size depends on live slots, and that is the mechanism behind the deep-window hash instability in `DEVLOG.md` H.3 |
| `mtp acceptance`, `cc restore live/ckpt` | are the mechanisms we paid for still armed | acceptance >= 0.55 (adopted ~0.72-0.74); live restores should dominate checkpoint rebuilds |
| `engine-respawn`, `fatal-signatures` | did the engine die and get reborn under a green `/health` | one `engine started:` line per boot (zero restarts inside a delta window), zero death strings |
