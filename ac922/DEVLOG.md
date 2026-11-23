# Development log — how this lane came to be

Condensed from a ~7,000-line operational runbook kept during 2026-11-08 → 11-16. Phases in order;
each entry is a *result*, not a diary.

## Phase A — hardware truth (11-08 → 11-09)

- AC922 (8335-GTW), dual POWER9 20C, 6× V100-16G SXM2. First bring-up revealed the machine was
  not what its labels said: **real DDR was 31.5 GiB** — the remaining ~90 GiB reported as online
  memory were V100 HBM windows mapped into the System Physical Address space. Trusting
  `/proc/meminfo` caused a chain of freezes (PHB link kills under sustained read *and* write,
  an EEH recovery path hitting `kernel BUG`, a 35-second-after-boot kernel Oops later pinned to
  NVIDIA RM adapter init, reproducible from a vmcore).
- Storage was the first-class reliability risk (8+ incidents on the delivered SATA disk);
  resolution: migrate to an NVMe (Intel PM1725a) with a planned, verified layout — not a raw
  whole-disk clone (partition-size mismatch would have been fatal), and the "work out of a RAM
  tmpfs" era ended once real capacity existed.
- Memory was upgraded in stages to **256 GiB of real DDR**. The box still reports ~341 GiB
  "online" in `/proc/meminfo` — the excess is V100 HBM windows mapped into the System Physical
  Address space (the same trap as the 31.5 GiB finding above), and no capacity plan ever counts
  it as RAM. Multi-channel behavior was verified by DIMM presence table + bandwidth probe. OS
  was reinstalled clean (EL8 family, the distro whose stock 4.18 kernel carries the NPU/ATS
  behavior the accelerator stack expects); GPU acceptance run after every hardware action.
- Ops rules that stuck: one variable per change; never prewarm disk caches by hand; swap policy
  written explicitly; SHM/VM tunings derived from real memory, scripted, not guessed.

## Phase B — reference stacks (11-09 → 11-12)

- **llama.cpp "Monster" tree** (IQ3_XXS + MTP): traced the fork's upstream base, re-implemented
  its delta as our own fork (`ac922-llama.cpp`), first champion geometry run; hybrid PLE admission
  formula reverse-verified from the shipped `libllama.so` strings and confirmed on-machine both
  ways. Established the 4-card boundary: 262K context needs `-ngl 47` + tight per-card traffic
  shaping and leaves < 1.2 GiB free — "memory fits, speed can't be bought" (path ② verdict).
- **1Cat-vLLM stack** studied as the paper anchor; **Strata int8 custom** (`eelgaev/Strata-AC922`
  lineage) evaluated; TurboQuant integration assessed statically and parked (quality cost).
- **DFlash2 drafter**: weight→pack conversion completed end-to-end (CPU-only), kept as a
  candidate drafter upgrade for the NVFP4 lane.

## Phase C — the NVFP4 lane (11-12 → 11-15)

- Model assets: `Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata` (17 files) downloaded via
  mirror lanes with resume + hardlink dedup discipline; one hard power cut left a sparse
  fake-complete 47 GiB file (right size, wrong bytes) — after that, **sha256 is the only accepted
  evidence**, sizes are not.
- Path ladder P1→P5 (each adopted after the previous baseline was taken): KV host-tier +
  block-key-pool analysis → 262K+MTP exploration → `llama.cpp-v100` baseline comparison →
  NVFP4 cold-cache OMLX-style mechanism (recompute→restore −97% via conversation-cache budget
  ×5.3, zero pp/tg/VRAM regression) → final-slot elasticity (short contexts fast, long contexts
  correct).
- Wave campaigns (A→K, plus T/U/V/X/Z series): paired same-boot A/B for every ticket. Notable
  outcomes: `--aux-cpus auto` wall −13% (today's cheapest big win); five-knob decode waves mostly
  neutral; TurboQuant and k8v4 rejected on exchange rate; QPN2 port cancelled with three
  independent pieces of evidence.
- **Four "mechanism self-sabotage" discoveries** — cases where our config, not the engine, was
  the ceiling: (1) `--batch-groups auto` silently produced groups-of-1, invalidating every
  earlier "batching doesn't help" verdict; (2) the pipelined window never ran MTP drafts until
  `STRATA_PIPED_MTP` was set; (3) pipelined rings never adapt the VRAM expert tier until
  `STRATA_PIPE_ADAPT_EVERY`; (4) a retention-default eviction loop (same failure independently
  hit and fixed upstream in the 1Cat line) was poisoning cold/warm asymmetry readings.
- **Concurrency revolution**: correct ring shape (`--batch 6 --batch-groups 3`) turned FIFO
  (agg/single = 0.98-1.00) into aggregate +74%; capacity rule N cards ↔ N full-window streams
  verified, over-subscription queues without crashing.
- **NUMA**: `NUMA placement FAILED ... remote reads` root-caused (file pages follow the reading
  CPU; the engine's pinned main thread parked all 48.8 GiB of PLE on node0); fixed with the
  mmap-hook arena binder (`numabind.so`) + PLE split-parking (`ple_park.sh 500`).
- Final Wave B baselines (B6/B4) taken as two boots with geometry as the only variable
  (`PERFORMANCE.md` §1); adopted state frozen into launcher + config with dated backups and a
  documented rollback.

## Phase D — accuracy campaign and reference-lane closures (11-16)

- Built a torch-free grading stack (13 tasks, official checkers where they exist) with four
  standing gates: selfcheck / bench-selftest / grader-parity / dryrun. Caught and fixed two
  caliber bugs (MC suites graded with reasoning enabled; a hand-written ifeval path that graded
  2 of 25 constraint classes — all historical ifeval scores voided and re-based on the official
  registry) and one data defect (cmmlu answer keys lost to a CSV column-name case change;
  rebuilt by row alignment).
- Full suites completed on N1: GSM8K 97.12 / MATH500 89.40 / HumanEval 96.95 (`ACCURACY.md`).
- Reference lanes closed with evidence rather than vibes: M1 after three falsified repair
  hypotheses (stop-sets, structure scan, prompt-shape strip); S1 after nine arms across two
  campaigns — and a post-mortem statistic showing the 8092 accuracy lane had *never* produced
  more than one token per request, i.e. "it was never up", not "it regressed". This produced the
  standing rule: **no suite runs before the three-probe lane gate passes** (`scripts/alive.py`).
- Cross-path panorama chart v2 published (`results/charts/`); accuracy panel currently carries
  the single citable stack — backfill scheduled behind the official-denominator anchor.

## Phase E — the "decode 20 / prefill 500" reconciliation (11-16 evening)

- Closed the gap between published numbers (tg 53.75 on 4 cards, tg 66.0 on 6, pp 7k) and the
  operator's felt speed (~20 / ~500) without touching the running lane.  The felt number is a
  **mixture**, not a floor: classifying 8,300 self-report turns by how deep their prefix reuse went,
  57.7% relay (mean prefill 0.34 s), 13.8% land partially (15.8 s), **28.5% start from nothing
  (20.7 s)** — and that last class carries 80.6% of all re-read tokens (222 Mtok).  The split is
  bimodal per length band: in the 240-250K band, 48% of turns relayed *at 249,829 tokens* while 50%
  re-prefilled 244K tokens at 43 s each.
- Root cause reproduced with a single flag: parked-conversation **slots**, not RAM.  Eight
  concurrent conversations on the dev lane: `--conversation-cache-slots 8` relayed 16/16
  continuations (gap = the new turn); the shipped default of 4 re-prefilled 16/16 from zero, for
  ~0.1 GiB of RSS.  In production 3,529 of 3,583 conversation-cache eviction events happen at
  exactly `parked=16` (its slot ceiling) while its RAM budget is nowhere near binding
  (footprint p90 21.8 GiB, max 53.6 GiB of 128 GiB; 0.0% of events reach 97% of budget).
- Priced the checkpoint grid over 10 arms with 10/10 pre-registered predictions:
  `spacing = ceil(prompt-cache-every / chunk) x chunk`, and a continuation re-reads its own prefix
  length modulo that spacing.  This reverses both intuitive fixes: setting `--prompt-cache-every`
  to <= chunk halves the spacing for ~0% cold-prefill cost, while shrinking `--prefill` alone is
  strictly dominated (it pays up to -56% cold prefill and cannot push spacing below 16384).
- Request shape dominates geometry: the same ~57K content delivered as one user message resumed
  12,670 tokens deep (9.3 s); delivered as a 12-turn messages array it relayed the whole prefix
  (3.5 s) -- identical engine, identical config.
- Dead end recorded so nobody re-opens it: `--batch-mtp` would remove the 333 "no per-slot draft
  K/V, so the slot cannot park" events, but its own help bars it together with `--batch-groups`.
  It is the price of the adopted pipelined ring, not a missed switch.
- Tools: `eval/resume_probe.py`, `eval/tg_short.py`, `eval/cap_probe.py` (+ METHODOLOGY 9).

## Phase F — production verification of the capacity fix (11-16 late evening)

- Phase E's root cause was reproduced on the **live production lane**, not a dev box: 20 concurrent
  conversations, cold build then continuation, `--conversation-cache-slots 16` relayed **0 of 40**
  continuations (every one re-prefilled ~10.8K tokens, median turn prefill 6574 ms); `slots 32` +
  `mib 98304` relayed **40 of 40** (gap = the new turn, median 1306 ms, **5.0× faster**).
- The obvious confound was killed with a control rather than argued: a *fresh* engine at the old
  `slots 16 / mib 131072` also relayed **0/40** with median 6662 ms, i.e. bit-comparable to the
  4-hours-into-production arm.  Freshness was not the variable; the slot ceiling was.
- Overshoot recorded: we had predicted a proportional failure (~4 of 20 missing above a 16-slot
  ceiling).  It is all-or-nothing -- past the ceiling nothing relays -- so a capacity ceiling shows
  up as a cliff in the outcome distribution, never in the averages.
- Adopted into the shipped config (`configs/strata-nvfp4-final.json`), with the RAM budget *lowered*
  128 → 96 GiB so doubling parked conversations still shrinks the worst case.  tg8k 55.9 tok/s
  (inside the B4 band), det ×3 identical, no VRAM change.
- Two defects surfaced by trying to gate this, both larger than the ticket itself:
  the numeric gates had been **grading their own auth failures** for ~24 h (see METHODOLOGY 3), and
  the production `stop()` used a box-wide `pkill -f "build/strata --serve"`, which killed the dev
  lane's engine while its supervisor kept answering `/health` 200 -- a lane that looks alive with
  nothing behind it.  Both fixed: lane-scoped stop/status/check, a card-occupancy guard on `start`
  (the 6-card default had already silently straddled cards 4/5 once), fixtures authenticate and
  abort on 401, and `eval/repro_probe.py` prices the reproducibility floor instead of pretending
  cross-boot hash equality is an invariant.
- Pre-existing, not caused by this change, still open: the 64K `selfcheck` cell flips
  cold != cached in roughly half of runs (Nov-14 history shows the same 5-of-9 pattern at
  `slots 4/16`), and it is the first-relay pass only -- passes 2 and 3 match cold.  Ticket owner:
  whoever chases the nondeterminism found by `repro_probe.py`.
- Chasing that flake turned up the real shape of it, and it is bigger than the cell that kept failing:
  at *matched* re-read depth, two gate runs in one boot disagree on 6 of 8 long-context cells
  (agreement ~2/8), while the 8.5K cold fixture reproduced the 2026-11-15 REF hash bit-for-bit after a
  day of config adoptions.  Output on this lane is therefore deterministic only for short prompts on
  the cold path; long-context relay output has a genuine run-to-run floor.  The gate is rebuilt around
  that fact (`divprobe` primes each depth and records exact `reused`; `qualgate` FAILs on cold
  divergence and on total relay collapse, and treats a single drifted relay cell as WARN), because an
  unsatisfiable gate is the same liability as a broken one -- it was exactly that for 24 h.
  Open ticket, nobody has claimed it yet: find the source of the long-context relay nondeterminism
  (candidates in order: nondeterministic reductions in the host-tier KV restore, expert-streaming
  residency differing pass-to-pass, near-tie argmax with no stable tie-break).  Nothing in the adopted
  capacity change depends on the answer -- the binary is unchanged since 11-15 13:07 and the Nov-14
  `selfcheck` history already shows the intermittency.

## Phase G — residency tickets adopted, ship geometry moved back to 4 cards (11-17)

- The day's tool is `eval/cphase.py`: it boots an arm, waits for health, runs det + a deep ladder +
  an 8K×N concurrency burst, stops, and moves to the next arm, with arms chained so a whole family
  lands unattended. Two defects found while building it: a launcher whose stdout is captured through
  a pipe never reaches EOF because the daemon's grandchild keeps the pipe open (boot hangs), and
  deep-window single runs carry ±15-25% boot noise, so no single cell decides anything — verdicts are
  family medians plus a "winner's min > control's max" bar.
- Adopted pair: `expert-profile-learned.bin` (trained 11-14 for this pack and shipped *unused*) +
  `--trim-stage-weights`.  Single stream 8K 57.1 → 77.9 (6-card boot), full-window 6×262,136
  `sum_tg` 148.5 → 155.0. `--adapt-every 0` measured neutral next to the profile, so adaptation
  stays on.
- Gate discipline hardened the same day, twice: the det fixture had been grading the *cold first run*
  of a boot (run 0 pp 2550 → different hash; runs 1-2 → REF hash), and a cross-boot `relay-collapse
  0/8` FAIL was boot noise — the same default config scores 0-2/8 against its own REF across boots.
  Cross-boot relay cells carry no verdict; same-config reproducibility does (the adopted boot scored
  8/8).
- Ship geometry then moved to 4 cards so 4/5 could go back to the dev lane. The reference config had
  carried the 6-card batch pair, so a 4-card boot was only correct if the operator remembered four
  environment variables; the launcher now derives the batch pair and `NB_CUT` from the card count.
  Re-anchoring the quality reference per geometry, `det` came back bit-identical across the switch
  (`bfd40ecd4692`) — the cold path does not see residency, which is exactly why it is the anchor.
- 4-card adopted state (first boot): single stream 77.3@8K / 70.0@210K, `aggbench` 8K×4 135.6 and
  245K×4 100.2, 4×261,632+512 all exact at the true ceiling with `sum_tg` 108.1, VRAM 58.6 GiB, RAM
  pinned 57.65. The honest gap that came with it — adopted 6-card aggregates never re-taken — is now
  closed: second boots on both geometries gave 6-card 8K×6 **240.1**, 245K×6 **135.2**, full window
  **136.2** (first boot 155.0), plus a 6-card 65K point (80.3), all `det`-identical at `bfd40ecd4692`.
- **Publishing rule changed as a result of those four boots: headline numbers are medians over two
  boots, single-boot cells are marked `n=1`.** The reason is mechanical, not stylistic — the same
  adopted config measured 77.3 and 66.1 at 4-card 8K tg, and 155.0 and 136.2 at 6-card full window,
  i.e. ±15-25% boot noise on deep runs. Promoting one boot into a README row was how we shipped a
  number we could not reproduce.
- Geometry is no longer something the operator remembers: `scripts/lane.sh` + `lane4.sh` / `lane6.sh`
  pin the machine layout, set `LD_PRELOAD=numabind.so`, wait for health and print `GEOMETRY-PASS`
  only after checking *this* boot's startup marker, the split stage count and the live engine's
  `/proc/<pid>/environ`. Two of our own gates died on the machine on the way there (the cumulative
  engine log made a "last batch line" check read the previous lane's statistics; the pack's
  first-touch `NUMA placement FAILED` line appears on every boot including the anchored one, so it is
  a note, not a gate) — recorded in `DECISIONS.md` so they are not re-added.
- Short-window aggregate did not move with the residency tickets (4-card +4%, inside the boot band) —
  it is admission-bound, so those tickets show up in single-stream and deep-window cells instead.
- The day ended on a defect the READMEs had hidden: the shipped lane **respawned itself** at 15:38,
  27 minutes after a clean boot, on `mtp: round capture: out of memory` (CUDA3 down to 149 MiB free
  while carrying 13 layers + the head + the 837 Mi MTP draft).  Nothing in our gates could see it -
  `/health` answered 200 the whole time and `lane.sh status` actually *failed* afterwards, because
  its boot slice spanned two engine processes.  Fixed in three moves: `lane.sh` anchors the slice on
  the newest `engine started:` banner and reports respawns; the exit status of a successful
  `lane.sh` boot is now 0 (a `&& reanchor` tail made it return 1 and silently killed a chained
  measurement run); and the geometry was rebalanced to `layer_split 9,24,37` with
  `--vram-reserve-mib 832` instead of shrinking the caches.
- Buying headroom with reserve alone was measured and rejected: at 1024 the gate's acceptance ratio
  fell 0.9231 → 0.747 (`FAIL acc-drift`), deep tg lost 6-11%, and cold 8K prefill lost 26%.  The
  rebalanced split at 832 keeps the anchor hash, passes the gate twice with `acc-drift-ok 1.0`,
  respawns in neither boot, and is the first ship shape where **every** full-window stream in both
  boots finished `length` at prompt+gen = 262,136 (4-way agg 106.9, 3-way floor 85.7).
- Standing lesson added below: a lane that answers `/health` is not a lane that stayed up; the
  process start count inside one boot is now part of every geometry check.
- Ship state re-proved after all of the above landed, on the same 4-card boot (docs merges need no
  restart): `lane4.sh status` GEOMETRY-PASS with cards 4/5 free, `/health` 200 `images: true`, and
  `eval/qualgate.sh check` giving `det` x3 = the anchor hash with `acc-drift-ok 1.003` →
  `QUALGATE-PASS`.  One caution recorded here rather than buried: the gate's `relay-cell-agreement`
  printed 4/8 with five drift WARNs, and a bare `sbench det` invocation returned HTTP 400 once before
  the same command reproduced the anchor hash four times in a row.  Neither changes a verdict -
  cross-boot relay cells carry no verdict by design, and a fixture that aborts is not a measurement -
  but a lane is only "ship" when the *graded* gate says so, not when one probe looks odd.

## Phase H — the delivery repo has to build from itself (11-17 evening, cards 4/5 as a dev lane)

Production stayed on the 4-card lane (8091) the whole phase; everything below ran on cards 4/5.

- **`devlane.sh` had no geometry for a different card count.** The base config is written for the
  shipping lane, and `serve` reads the config's `layer_split` key while the engine gets
  `--layer-split`: both must carry `len(gpu)-1` numbers, so a 2-card dev boot died with
  `"layer_split" ... got 3 number(s) for 2 GPUs`. It now recomputes an even split from the native
  gguf's `block_count` when the shape does not fit (`DEV_LAYER_SPLIT` overrides, `off` drops the
  split), and reads that key's count instead of the card count for the fit test (`25165d2a3c`).
- **The first genuine from-repo build found three platform fixes that only existed in the
  bring-up tree.** `build/CMakeCache.txt` says `CMAKE_HOME_DIRECTORY=/data/work/strata-nvfp4-src`:
  the delivered repo's `build/` had been configured against the pre-delivery source tree, so the repo
  had never built itself. A clean tree + clean build dir on this box hit, in order:
  1. `Cannot find source file: ggml-cpu/arch/powerpc/quants.c` - the vendored ggml ships x86/Arm CPU
     kernels, but ggml-cpu lists the PowerPC file unconditionally on ppc64le. Committed as AC922's own
     `added` file (every non-generic `ggml_vec_dot_*` forwarded to its `*_generic` reference impl: the
     CPU lane here is the expert pool and the quantizers, the experts run through `native_experts`),
     plus `-U__POWER9_VECTOR__` on the PowerPC `ARCH_FLAGS`, `UPSTREAM.md` edit 4 and the two manifest
     rows (`a2f280b160`; `tools/check_vendored.py`: ok, 128 files).
  2. `fatal error: cuda_fp4.h: No such file or directory` - CUDA 12.4 has no FP4 header and
     `mmq_nvfp4_a44.cu` is Blackwell-only, so the include is now guarded behind
     `BLACKWELL_MMA_AVAILABLE` (`f6a4a86740`).
  3. `namespace "std" has no member "is_same_v"`, then ~100 `redefinition of ...` from
     `/usr/include/c++/8/bits/random.tcc` - nvcc's host compiler defaulted to the system GCC 8.5, which
     cannot compile the engine's C++20 sources. Pin it: `-DCMAKE_CUDA_HOST_COMPILER=<gcc-toolset-12>`,
     and in a **fresh** build directory, because the CUDA compiler identification is cached. Verified
     end to end: `BUILD-EXIT=0`, 0 errors; `BUILD.md`/`README.md` carry the command that did it.
- **Two experiments live in the bring-up tree and were deliberately NOT shipped**: `STRATA_NUMA_ROLES`
  (per-role NUMA placement of `experts.bin` blobs, `src/core/expert_source.cpp`) and `STRATA_DEDUP`
  (same-moment prefix dedup, `serve/server.py`). Neither appears in the shipping lane's environment
  (its engine has exactly 12 `STRATA_*` variables, listed in `RUN.md`), so their absence changes no
  behaviour; each has to earn its ticket on its own numbers before it enters the repo.
- **`STRATA_TOPK_COMPACT` ported** (`b47d275dc3`, build fix `b5900b519f`): `qsa_topk_parity --selftest`
  PASS with the narrowing off *and* on - identical ids on the continuous / 64-level / equal-score /
  signed-zero+infinity / NaN / spread-tie rows at 262,144, 300,001 and 524,288 contexts; the micro-bench
  dispatched kernel is 2.3-2.7x the reference without it and 3.4-3.8x with it. Whether that survives a
  serving window is the open question (a sister fork measured -10% end to end from the same kernel win).
- **A 2-card lane cannot serve deep windows on the prod-tuned shape.** With `--resident-budget-gib 16`
  and `--vram-reserve-mib 832` a 96K prompt died twice: first
  `ggml-cuda (strata mmq): out of memory: cudaMalloc`, then - after the reserve went to 3072 - the
  stall watchdog killed a *slow, not stuck* engine: `no progress for 60 s during a request (reading the
  prompt (batched): layer 12 of the prompt chunk from token 0)`. A dev lane at these depths needs
  resident experts >= 40 GiB, reserve >= 3072 MiB and `STRATA_WATCHDOG_S` raised (the IO allowance
  `STRATA_WATCHDOG_IO_S` does not cover it: the reads are page-cache hits, so the file-tier counter
  that grants it never moves).


### H.1 — the first tickets run on the dev lane

**The dev lane cannot price decode tickets, and here is the measurement that says so.** With the
shape a 2-card lane can hold (`--resident-budget-gib 40`, `--vram-reserve-mib 3072`, expert cache
2457 slots = 6.33 GiB/card), a 126K-window decode looks like this (own engine self-report,
`STRATA_DECODE_TIMING=1`, 8 requests/arm, medians):

| arm | ms/window | verify | GPU-reach wait | per-layer host | CPU experts × 48 | tg |
| --- | --- | --- | --- | --- | --- | --- |
| control | 208.07 | 196.33 | 12.23 | 64.40 | 72.0 | 7.55 |
| `STRATA_TOPK_COMPACT=1` | 228.02 | 215.38 | 12.50 | 76.27 | 65.8 | 7.40 |

The window here costs ~200 ms and ~35% of it is experts computed on the **host CPU** with
3.6-7.3 cache swaps per window: this is a cache-starved regime, not the ship regime (whose window is
~30 ms at tg 66). Anything under ~2 ms/window is invisible, and the arm order is decided by which
boot happened to cache more experts. Two conclusions, both recorded: the sister fork's window split
(expert product 61% / GPU-reach 28% / host 12%) **does not reproduce on this box** at thin cache
coverage, and decode tickets have to be measured on the ship lane.

**`STRATA_TOPK_COMPACT`: ported, exact, and worth less than the ticket claimed.** The parity harness
passes with the narrowing on *and* off (identical ids on continuous / 64-level / equal-score /
signed-zero+infinity / NaN / spread-tie rows at 262,144, 300,001 and 524,288 tokens), so it is
quality-neutral by construction. Its price comes from the matched harness lines, not from a serving
number: at the widest case (524,288 tokens, 64 queries) the reference call is 1.185 ms, the default
dispatch 0.475 ms and the compact dispatch 0.351 ms. Per query that is 0.0013-0.0019 ms saved; a
decode window carries ~2 queries per layer over 48 layers, so a window saves **0.13-0.19 ms** - about
**+0.4-0.6%** of the ship lane's ~30 ms window budget. That is the same order as the 责任田 sieve's
independent estimate (+0.8%) and below the 1.5% adoption bar, and it explains why a sister fork
measured +3.5-4%: their selector call shape is not this engine's. Decision in `DECISIONS.md`: keep
the code and its ctest arm (opt-in, default off), do not put it in the shipping exports.

### H.2 — the delivery tree compiles the shipping lane (11-17 21:15, cards 0-3 live throughout)

Configured `build-new/` from this tree at `b68aaf6aca` with the `BUILD.md` command plus the shipping
flag set: `CFG-EXIT=0`, `BUILD-EXIT=0`, zero errors, ~10 min at `-j 64` under `nice -n 19 ionice -c3`
while the 4-card lane kept serving.  `build-new/strata` reports `strata 0.1.59`, 135,598,888 B,
sha256 `de5c351c0ac5bd5d...`; the binary the live lane is running is `b1f12bb047b79002...`, and the
two differ for exactly the expected reason - the production cache still names the pre-delivery source
tree.  `build-new/` is deliberately separate: the live lane's `build/` is never written to while it is
serving, and it doubles as the rollback copy.

`build-vision-new/` (CPU encoder, same tree) is 8,572,224 B, sha256 `a90dddffd6d7a799...`, `ldd`
shows no CUDA library, `GGML_CUDA=OFF`, and it carries 41 `Qwen4Exp` mtmd symbols - the multimodal
path is present in what we ship.

**Two `BUILD.md` gaps, both found by running the documented command rather than reading it.**  The
vision section never pinned the toolset, so a first-time configure picks system GCC 8.5, every compile
step passes, and only the link dies on `std::filesystem` (which 8.5 keeps in `libstdc++fs`).  The main
section omitted the flag set, and one of those flags - `STRATA_EXPERIMENTAL_SM60` - is not an
`option()`, so omitting it silently drops a compile definition from `strata_core`.  Both are fixed in
`BUILD.md`; the documented flag set is now the production cache, checked key by key (10 keys, all
identical between `build/` and `build-new/`).

Kernel self-check for the new binary, on a free card (`CUDA_VISIBLE_DEVICES=5`, no impact on the
lane): `qsa_topk_parity --selftest` PASS (identical ids across all six row classes at 262,144 /
300,001 / 524,288), `gr_parity` 0 failures, `dequant_wide_parity` 360 cases bitwise OK.

**Switch executed at 22:52, lane ready 77 s later.**  `lane4.sh stop` released cards 0-3 to <=141
MiB; `mv build build.pre-repo && mv build-new build` and the same for `build-vision`; `lane4.sh
start`.  `build/CMakeCache.txt` now names `/data/work/ac922-strata-nvfp4` as its source - until this
boot the shipping binary had never been produced by the delivery tree.  Rollback is the inverse `mv`
plus `start`; both old build directories are intact as `*.pre-repo`.

**Every gate green on the repo-built binary.**  `qualgate.sh check repo-build` -> QUALGATE-PASS with
zero FAIL lines; `det` three runs all `bfd40ecd4692`, which is the factory anchor and `REF.json`;
`acc-drift-ok 1.0`; `relay-cell-agreement 2/8 = 0.25`, the documented healthy noise floor in
`qualgate.sh` and not a regression.  `lane4.sh status` -> GEOMETRY-PASS on 4 cards (0,1,2,3), 2
groups of 2, shim loaded.  `engine started:` went 418 -> 419: exactly one boot, no respawn hiding
behind a green `/health`.  VRAM after start 14831/14726/14733/15120 MiB on cards 0-3, cards 4/5 idle
at 65/20 MiB, `MemAvailable` 92.3 GiB.

**Full window held, and it got faster.**  `fullwindow.py run --n 4 --gen 512 --total 262144`:
`all_prompt_exact true`, ptok `261632` x4, ctok `504` x4, prompt+gen `262136` x4, `finish=length` x4,
span 23.1 s, `sum_tg 109.3` against the factory pair 105.8/108.0 (median 106.9) - +2.2%, so the
switch bought no regression.  tg per stream `[23.77, 26.81, 27.48, 31.28]`, ttft
`[1.7, 3.6, 4.75, 6.44]`.  The warm rows show the restore path working: 58.1/58.0 s ttft when the
window has to be recomputed vs 2.0/1.53 s when the conversation cache answers it.

**Multimodal works on what we now ship.**  Same prompt text-only `ptok=67`, with an attached image
`ptok=419` (delta 352, `vision_tokens_consumed=True`), both `finish=stop`, and the two completions
differ - so the image reaches the model rather than being tokenized away.  One trap worth repeating:
at `max_tokens=32` the `content` field comes back empty because the model spends the budget on
`reasoning_content` first; `RUN.md` says smoke probes need `max_tokens>=256`.

**A harness bug found on the way, and it was the gate that was wrong.**  `window_fully_consumed`
counts rows where `ctok == gen`, but a stream that consumes the last byte of the window is cut off
with *fewer* than `gen` tokens - so at the byte-exact edge that invariant can never report success.
All four rows sat on prompt+gen 262,136 with `finish=length` while the flag read `0`.  `fullwindow.py`
now also computes `held_edge`: finish=length, every stream parked on the same position, and that
position plus the tokens a stream still wanted reaching `total` (the window closed on it,
`max_tokens` did not), printing `FULLWINDOW-PASS n/n held the window edge at prompt+gen=<edge>`.  The
comparison is `>=` because the engine's own reservation at the top of the window is exactly the
difference: 262,136 + 8 = 262,144.
`window_fully_consumed` is kept for compatibility.  The real sentence for the record is **4/4 held
the window edge**, not 8/8 rows.  Verified on the live lane at 23:15 with `--n 1`: `FULLWINDOW-PASS
1/1 held the window edge at prompt+gen=262136`, `ctok=504`, old flag still `0`, tg 54.16 with warm
ttft 1.11 s (the restore path answering a 261,632-token prompt), and the boot count stayed at 419, so
the probe cost the lane nothing.

### H.3 — the panorama's last fillable cells, and one new caliber rule (11-17 23:2x-23:5x)

`results/charts/plot-samelen-v5-2026-11-17.py` -> `results/charts/ac922-samelen-v5-2026-11-17.png`.  Three
of v4's blanks were fillable without touching a card, one needed a live probe:

- **65K single-stream decode on 4 cards** (`depthtg.py`, shipping binary boot): `ptok=65376`, two
  independent runs of 5 rounds, `tg_med` **75.4** and **70.8** - a **6.1% run-to-run drift** on the same
  lane, same prompt, same flags.  Recorded as the 10-round median **72.1**, not as either shot.  The
  6-card cell (80.3) is a *single* reuse reading from `note_c17/6CARD2.p65k`, so the 11% gap between
  them is a median against a single shot and is labelled as such on the chart rather than claimed.
- **Monster 8K x 4 aggregate**: 122.0 (4 cards) and 83.1 (6 cards) - already in runbook section 74.5,
  never plotted.  Both stacks' 6-card-vs-4-card concurrency reversal is now visible in panel 3.
- **Monster 6-card resource bill**: Sigma 66.8 GiB with the node8-island range 11.6-13.6 GiB/card is in
  the archive; per-card same-moment sampling and host RAM are not, so the bar renders as a total with
  the missing evidence named instead of a grey "not sampled" placeholder.
- **Resource bill refreshed to the shipping boot**: `resident RAM 57.10 GiB` (84 identical self-reports
  in one boot), VRAM 14.48/14.38/14.38/14.77 GiB, free 528/633/626/237 MiB with card 3 tightest.

**Deep-window decode is not round-to-round reproducible on this lane, and the short window is.**  At
65K the five rounds of one run produced 2-3 distinct reply digests: rounds 2-5 agreed, round 1 did
not, and a second run produced three.  The 10K `det` fixture stayed at `bfd40ecd4692` across three
runs in the same boot.  The mechanism is visible in the engine's own line: a 65K replay is
`65371 reused + 5 read`, so what varies is not the cached prefix but the *tail* prefill, whose chunk
geometry depends on which slots are live when the request lands - a 332-token probe from another
client sat between my rounds.  This is the same family as the cold/cached asymmetry that ticket T1
(`--prefill-pipe` x `STRATA_SPLIT_PF_RES`) is meant to close.  Consequence for every table we publish:
deep-window numbers are **rate** evidence only; determinism claims stay anchored to the 10K fixture.

### H.4 — the lane audit becomes standing practice (11-18 daytime, operator on the box)

`eval/laneaudit.py`: whenever work switches back from the production lane, audit the boot that just
served before taking any baseline.  It is log analysis only - the engine already writes a telemetry
block per request - so it costs no GPU, sends no request and prints no model text; it exits non-zero
on FAIL so it can gate a switch.  `RUN.md` now documents what each check means.

First run over the boot that served the day (1,374 requests since the 22:53 boot): 24 checks,
**0 FAIL, 4 WARN**.  Rates held: decode p90 against the adopted anchors 1.06 / 1.04 / 0.93 / 0.92 /
0.88 across the five window bands, cold prefill 0.81-1.27, `short-vs-deep` inversion check clean
(64.5 vs 54.0 = 1.19), MTP acceptance 0.720, batch ring at **1.84 rows per group-step** (windows
really do carry more than one row, so the shared expert read is happening), expert hit rate median
96.2% / p10 90.1%, **expert blob reads 0**, resident RAM a single value 57.10 GiB over 1,374
reports, prefix reuse on 96% of requests, live-vs-checkpoint restores 302/40.

The four WARNs are all known attack surfaces, not regressions: KV VRAM hit p10 53.4% (median 67.0)
is the host-wait pressure still on the backlog; 126 requests touched storage through the OS with
major faults; deep prefill chunking varied over 24 sizes with the smallest at 2,304 and 93 requests
under 8,192 - that is the cold/cached asymmetry behind the deep-window hash instability; and one
boot line noting low VRAM on the tightest card.

### H.5 — the audit learns to read only what is new (11-18 09:3x)

A boot-scoped audit re-reads everything, so a second switch back on the same boot mostly re-judges
traffic it already judged, and the newest traffic - the part actually at risk - is diluted by history.
`SINCE=delta` now audits the lines since the last audit and moves a watermark to end-of-log
(`WM=` to relocate), and a line-window scope counts `engine started:` lines inside the window:
0 restarts PASS, 1 means the lane restarted while nobody was watching (WARN), 2+ FAIL.

The first real delta window (96 requests) then taught the second lesson: `decode b3 80-165K` came
back **WARN at 0.79 on n=5**, which was the sample floor talking rather than the lane - five requests
sharing the box with live concurrency cannot define a band p90.  A narrow scope now needs 6 decode
samples (4 cold-prefill) per band to judge, and prints the band's numbers as **SKIP** below that:
visible, never silently missing, never a verdict.  The boot scope keeps its old floors (3 / 2).

Third guard, against a specific kind of false comfort: a clean sweep over an empty window used to
print `LANEAUDIT-PASS`.  Under a delta scope, fewer than five requests now prints **`THIN`** - a clean
sweep is not evidence about rates.  A green lane has to mean green over traffic.

Re-audit of the same boot, same day, 1,417 requests (15 more than H.4): still **0 FAIL / 4 WARN**, the
same four names, and the mechanism numbers unmoved - ring 1.84 rows/group-step, MTP acceptance 0.720,
expert hit median 96.1% / p10 90.0%, blob reads 0, resident RAM a single 57.10 GiB, reuse share 0.96,
restores 339 live / 41 checkpoint.  Decode band p90 ratios 1.06 / 1.04 / 0.93 / 0.92 / 0.88 identical
to H.4, and the inversion check still clean at 1.19.  That stability is the point of running it on
every switch back: it is what lets a later WARN be read as a change rather than as noise.

### H.6 — cold reboot, and what it proved about the lane and about card 4 (11-19 10:0x)

Operator rebooted the box.  Restoring the 4-card lane was the occasion to check every ops assumption
in `RUN.md` ("After a host reboot") rather than trust them, and to answer the question that had been
hanging since yesterday: who was holding card 4.

Pre-flight, all confirmed after the reboot: swap still off; `vm.*` re-derived by `ac922-memcfg`
(swappiness 10, dirty ratios 0, min_free 360448 KiB); `/dev/shm` 8 G; `/data` on `xfs` 5.7 T with
3.1 T free; NUMA nodes 126,744 + 130,782 MB = the real 256 GiB of CPU-attached DDR; NUMA shim and
LAN proxy service back on their own.  One thing that did **not** come back: the public relay - the
BMC said `HostState.Running` while `ssh ac922-ip` stalled at banner exchange, because the mapping
lives on the router.  The lab jump (`ssh ac922-j`) works, so the lane is reachable either way.

Card 4 read 0 MiB after the reboot.  It had held 14,190 MiB for ~14 h with `PIDS: None`, 0% util and
byte-identical readings, which is now proven to be a leaked driver allocation and not a live tenant -
so neither our lane nor the co-resident thread was "occupying" it.  The answer to "did you take 4/5"
was always no on the evidence (`CUDA_VISIBLE_DEVICES=0,1,2,3`, engine fds only on nvidia0-3); the
honest residue of that question is the leak, and reboots clear leaks.

Boot 420, and the acceptance the reboot demanded: `GEOMETRY-PASS 4 cards (0,1,2,3), 2 groups of 2,
shim loaded`, split `0-8 / 9-23 / 24-36 / 37-47`; pinned `resident 57.07 GiB, pinned 57.07 GiB in
18 s`, 0 blob reads; after the acceptance traffic, free VRAM per card 568 / 693 / 686 / 290 MiB (the
anchored boot was 528 / 633 / 626 / 237 - the same shape, slightly roomier).  Quality gate
`QUALGATE-PASS` with `acc-drift-ok 1.0` and four `relay-cell-drift` WARNs at 64K/131K, which is the
calibrated reproducibility floor in `qualgate.sh` (cold cells clean, FAIL only on total collapse).
The factory invariant held on the fresh boot: `FULLWINDOW-PASS 1/1 held the window edge at
prompt+gen=262136`, `ptok 261632` exact, `tg 59.58`, warm ttft 1.22 s.

Two process lessons, both now in `RUN.md`.  Piping `lane4.sh start` through `tail` looks exactly like
a hung boot and is not one - the launcher legitimately lingers waiting on the frontend, and the
buffered reader swallows `GEOMETRY-PASS`; the verdict belongs to `lane4.sh status`.  And the
switch-back audit needs its watermark re-seeded per boot (`296004` now), otherwise `SINCE=delta`
audits across a reboot and reports a restart that nobody hid.

### H.7 — the dev lane wedged the driver and cost the operator an hour (11-19 12:0x-12:5x)

Symptom: production `/v1/responses` errors for the operator from ~12:38, cards empty at 12:45, no OOM in
`dmesg`.  First blame - "`devlane.sh restart` killed the prod tree" - was wrong: `stop()` walks only its own
pidfile tree (audited line by line), and the prod tree was already gone before it could matter.

Real chain, from `coredumpctl`, the supervisor logs and `/sys/fs/pstore`:

1. The paired A/B (`devarm.sh`) ran the OLD arm (0.1.59, prod build path) on cards 4/5 with
   `--resident-budget-gib 16 --batch 2`.  Its batched prompt read hung at layers 5/9/15 from token 0, the
   hang watchdog (issue #29) SIGABRT-ed the engine every ~2 minutes (12:22-12:31, five cores), and the
   supervisor dutifully respawned it every time.
2. On this box a SIGABRT mid-`rm_init_adapter` holds the global NVIDIA RM semaphore for minutes: pstore
   preserved a hung task in `os_acquire_semaphore -> rm_init_adapter -> nvidia_open`.  That wedge is
   global, not per-card - production requests stalled behind it.
3. The operator power-cycled the machine at 12:43:21 (journal ends with no shutdown sequence; proxy unit
   up at 12:43:39).  Production was restored by hand at 12:45:52 (`lane4.sh start`); `loaded:true` and a
   real graded 200 by 12:48, decode back in the normal 34-112 tok/s band.

Guardrails from today:

- Never run the dev lane unattended on an unproven binary x config combination; the hang-watchdog respawn
  loop converts one hang into a driver-wide wedge.  If a dev engine aborts 3x in 10 minutes, kill its
  supervisor - do not let it boot again.
- Production `/health` plus one graded probe, immediately before and after ANY dev-lane action, abort on
  failure.  And no dev-lane work inside the operator's window at all - the freeze stands until the box is
  explicitly handed over.
- `coredumpctl` + `/sys/fs/pstore` are the witnesses when journald dies mid-crash; check both before
  assigning blame.

### H.8 — the bump's first breath: vision on the vendored tree, and a cublas failure that was memory discipline (11-19 14:5x-15:2x)

Supervised smoke of the bumped engine (`0.2.15-nvfp4.2`, `strata 0.1.61`) on cards 4/5, production untouched on
0-3 the whole time.

- **vision builds from the repo itself now.** The ppc64le shim was replicated from `third_party/strata-ggml` into
  `third_party/strata-llama` (`arch/powerpc/quants.c` added, `-U__POWER9_VECTOR__` in the PowerPC ARCH_FLAGS,
  manifest rows, UPSTREAM.md note); `check_vendored` says ok (917 upstream / 1 edited / 1 added). The `LLAMA_DIR`
  override in BUILD.md is obsolete as of this commit. `devlane.sh` had a second self-trap: its `fix()` rewrote
  paths in `args` but not `vision.exe`, so a dev lane would have run the *other* tree's vision helper - patched.
- **cublasCreate status 1/3 at the end of startup is a budget collision, not an engine regression.** With the
  shipping-shape dev arm (resident 16 + CC 32 GiB, production already page-locking ~190 GiB), the engine fails its
  first cuBLAS handle right after the stepwise page-lock dance ("9.15 GiB really free for an 11.60 GiB
  complement"). Cut the budgets (resident 2-6, CC 4-8 GiB, ctx 32K) and it loads in <20 s, text answers (17*23),
  and the vision probe (solid red) passes end to end. Rule: when co-tenanting a dev lane with production, size the
  dev lane's pinned budget against `MemAvailable` *before* the page-lock phase, not after; a cryptic cuBLAS status
  at the end of startup is the last domino, not the cause.
- Not done (evening window): the full-geometry paired A/B vs 0.1.59 (§57.1 protocol) and the production cutover.
  For the cutover, stop the old engine *before* starting 0.1.61 - same lesson, its pinned phases need the headroom.

### H.9 - the repo line grew a 6-card lane, and the layout's real owner was `lane.env` (11-19 17:5x-19:0x)

- `lane.sh 6` could not boot the shipping config at all: serve wants one rising `layer_split`
  number per extra GPU and the base json carries the tuned 4-card `9,24,37`.  The repo launcher now
  falls back to `auto` whenever the card count is not 4 (exactly what the historical 6-card boots
  self-reported, "split auto"); `STRATA_LAYER_SPLIT=<shape>` pins an explicit geometry when one is
  tuned.  Caught live: the failure surfaced as a python traceback in the supervisor log with the
  frontend never answering - `lane.sh status`'s GEOMETRY-FAIL is what pointed at it.
- Layout ownership: `lane.sh` sources `/data/work/lane.env` (SRC/MODEL_DIR/LOGD/RUN_JSON/PIDF/
  NUMABIND_SO), and `/root/strata-nvfp4-run.sh` is a separate wrapper.  They had silently diverged
  (wrapper at bump-src, lane.env still the old tree) - a `lane.sh 6 start` would have booted the
  0.1.59 tree under a "cutover" label.  One layout file now says `SRC=/data/work/bump-src`; check
  `lane.sh start` prints `layout: SRC=...` before believing any boot's lineage.
- 6-card adoption on 0.1.61 (same protocol as the 4-card cutover): GEOMETRY-PASS (3 groups of 2,
  auto placement), qualgate 0, 6x262K warm 5/6 `held_edge` (one natural EOS, the shipping-boot
  signature) at agg **158.5** vs 145.6 (+8.9%); 6x262K cold agg 131.4; solo 8K **91.8** vs 82.1
  (+11.8%), solo 50K 80.8 vs ~80.3; 131K x6 138.4.  Watch-item: 8K x6 agg 182.1 came in under both
  our 50K x6 (208.0) and the morning A1_6x3 reference (227.9) - rerun when idle before trusting it.

- 8K x6 follow-up (same boot, three paired runs): **182.1 / 207.4 / 231.9** against 50K x6 205.8/208.0 -
  i.e. no regression, but the shallow cache-hit regime is **queue-timing dominated**: prefills are
  ~100 ms (7927/7932 reused), so the span moves with admission overlap and CC state, ~±12% around
  the median. Judge 8K x6 from medians of >=3 runs, never from a single panel row.

### H.10 - the bump gets its paper: repanel, chart, and a lane-restore near-miss (11-19 19:2x-19:5x)

- **Second 4-card boot on 0.1.61** (`repanel4c`, 19:21-19:42): 8K x6 **253.7** (vs 240.8),
  50K x6 **231.5** (vs 245.0), 131K x6 **181.6** (vs 178.0), solo 8K **77.2** (vs 85.0),
  solo 50K **74.6** (vs 70.7).  Boot-to-boot spread on the shallow cells is +-5-10% on 4 cards
  too - the H.9 median rule is not a 6-card-only caution.  The pair means now ships as
  `PERFORMANCE.md` §7 with the state column as the two-boot mean.
- **`lane.sh 6 start` does not stop a live 4-card lane.**  The occupancy check printed
  `cards clear` while GPU0 still held 14.7 GiB (it reads the lane's own pidfile, not the target
  cards' VRAM), the new engine died against the old one's port and page locks, and the geometry
  check then parsed the *dead boot's* log and printed GEOMETRY-FAIL over a healthy 4-card
  `/health`.  `lane.sh 6 restart` is the correct card-count switch (it is what the docs say, but
  the guard should make `start` impossible to misuse this way).  Ticket, not shipped: extend the
  occupancy guard to per-card VRAM on the target set.
- **Docs/chart round** (this commit): `PERFORMANCE.md` §7 (6-card same-protocol pairs, 4-card
  two-boot state, full-window pair), `ACCURACY.md` 0.1.61 re-test block (GSM8K-500 97.00,
  HumanEval-164 96.34 at the 3K budget - budget caliber, not a regression claim), the pin
  corrected everywhere from `b08a70078`/0.1.59 to `828a8082d`/0.1.61 with the honest
  "eleven engine-side files" diff, `RUN.md` layout now names `bump-src` as the serving tree and
  the old tree as the rollback, and the new chart
  `results/charts/ac922-bump-061-2026-11-19.png` (generator beside it).
- Production restored: `GEOMETRY-PASS 6 cards (0,1,2,3,4,5), 3 groups of 2, shim loaded`,
  `/health` loaded with images, cards 4/5 back in the lane.

### H.11 - the conversation-disk cold tier shipped: plan v2 executed end to end (11-20 11:40-12:35)

- Phase A (coexistence gate) passed on the forced shape (CC 2 GiB, park >=16384): disk-hit line
  `64745 tokens, 1172 MiB in 4 files, 592 ms (2076 MB/s)`, reused 99.97%, and two
  eviction-resurrection cycles return bit-identical answers.  Near-miss worth remembering: the
  determinism check compared **empty strings** until the response field was fixed - this lane
  answers in `reasoning_content`, `content` is empty; any future hash-the-answer harness must
  concatenate both fields (the phaseA2 rerun is the citable one).
- Phase B shipped the production block (CC 49152 untouched, disk 512 GiB, persist, sync 30,
  min-tokens 32768, `--memory-limit-mib 262144` + dynamic floor 52428 from REAL 262144).  The
  mem-limit pin is what makes the floor honest on a box whose MemAvailable is inflated by the
  HBM-window mapping - the 341 GiB trap, finally given its official antidote.
- Phase C verdict: 16 x 148K resurrections, median wall **6.9 s** (gate 11), COLD 0, 8 RAM + 8
  disk hits - the tier splits the load exactly as the budget math predicted.  One real finding:
  a cold-prefill storm can outrun the idle-sync write queue and evict a not-yet-persisted newest
  chain (that resurrection fell to cold, 94 s); with the snapshot on disk, the same injection
  under concurrency costs 1.3 s.  Scheduler-side ticket, no numbers path touched.
- Handback: test dirs purged, production boot at 12:26, GEOMETRY-PASS, QUALGATE-PASS.

### H.12 - decode timing is on by default in the 4-card lane, and our own window breakdown lands (11-20 13:5x-14:1x)

- `STRATA_DECODE_TIMING=1` now ships in the 4-card production env (lane.env line branching on
  `CARDS`; the 6-card lane untouched).  Cost audit first: the flag reads counters the engine
  already keeps and prints one stderr line per request - no diagnostic kernels, no added sync
  (the round-cost-ledger discipline applies to a different mechanism).
- The fix that made it stick: `lane.sh` sourced `lane.env` *before* parsing `$1`, so a
  card-count-conditional export silently saw an empty `CARDS`.  Source moved after the parse
  (nothing between the two lines consumes SRC/MODEL_DIR).  First boot after the flip printed
  `0` for the flag in `/proc/<pid>/environ`; second printed `1` and the timing lines flow.
- Our own numbers, first sight (previously everything was borrowed from the sister box):
  ~49 ms/window, verify ~45 of it, **GPU-reach wait 4.5 ms/window** - the host-wait 26% family
  confirmed on this machine, not just in sister accounting; per-layer host CPU ~22 ms/window,
  draft ~3.1, commit ~0.5.  2.07 tokens/window at T~2.8 reads ~42 tok/s for the probe's batched
  mix - decode-rate ceiling work should now be argued against these lines, not the sister's.

### H.13 - card-4 orphan context, the full-GPU reset, and the "reset broke CUDA enumeration" trap (11-20 18:0x-18:5x)

- Symptom first, because it reads like nothing: card 4 climbed **736 MiB -> 12658 -> 14822** with
  **no entry in `--query-compute-apps`**, lane healthy, engine log quiet.  A context the driver
  refuses to drop, not an allocation we made.  Card 5 trailed at 1594.  Operator called for a
  full reset (3/4/5 needed together to release it).
- Reset procedure that worked: `lane.sh 4 stop` -> stop `nvidia-persistenced` -> loop
  `nvidia-smi --gpu-reset -i 0..5` -> restart persistenced.  All six cards to 0 MiB, confirming the
  leak was driver-held.  Note the NVLink-group quirk: resetting one SXM2 reports it reset a *peer*
  GPU - normal, not a wrong index.
- The trap cost two boots: after the reset `nvidia-smi -L` listed all six cards, but the engine
  self-report degraded to `--layer-split ... (1..0; ...)` - **the CUDA driver API enumerated zero
  devices** while nvidia-smi looked perfectly healthy.  `dmesg` explained it: a kernel oops in
  `nv_close_device` (trace 2d9a421308504ab4) - the reset's own handle-close path corrupted module
  state.  Lesson recorded in runbook 328: *nvidia-smi health is not evidence CUDA works*; the only
  cheap check before burning a two-minute load is whether the split line reads
  `layers 0-8 (CUDA0) ...`.
- Recovery without a reboot (one shot): stop persistenced -> `rmmod nvidia_uvm nvidia_drm
  nvidia_modeset nvidia` -> `modprobe nvidia_uvm` (+`nvidia_drm`) -> start persistenced -> lane
  start.  18:49:54 boot came up `GEOMETRY-PASS 4 cards, 2 groups of 2`, health 200 after 90 s,
  cards 0-3 ~14.5 GiB each, cold-tier block and the 4-card `STRATA_DECODE_TIMING=1` intact,
  smoke answer hashed (8 tokens / 0.96 s).
- Post-reset sentinel (24 h, n=2697 request lines / 71353 windows): mswin median 22.21 ms,
  tg 47.30, verify 20.92, GPU-reach 3.13, host 0.07, cpu 1.44 - same book as before the reset, so
  the recovery introduced no regression.  Cards 4/5 afterwards belong to the other thread's
  H3/ComfyUI (pid 749782, :8188) at ~306 MiB/card: real tenant, not a leak, leave alone.
- Three standing disciplines adopted by the operator (verbatim in runbook 328): no long-arm
  launches from an interactive ssh session (`setsid`+`nohup`+pidfile, session death == orphan ==
  leak); no HMM oversubscription to rescue an OOM outside the certified geometry (it turns a 3 s
  failure into 18 min of thrash and can die inside the handler); after a card recovery, run one
  single-variable `--disable-mmap` arm - costs a copy and slower load, removes the ATS migration
  source.  That arm is also the cheapest probe for tonight's still-unattributed leak, and a
  cards-4/5 orphan-context watchdog (3 samples >1 GiB with an empty process table -> alert) is the
  obvious sentinel extension.

### H.14 - three ladder tickets, two killed by geometry gates before spending a boot (11-20 19:42-20:15)

- Method upgrade worth keeping: read the engine's own **refusal line** before spending a restart.
  Two of the three levers died on paper and saved ~6 boots.  New standing rule for any
  "env-Flag-boost" ticket: grep its off-gate, boot once only if the gate can pass.
- `STRATA_ADAPT_FETCH` - dead, not untuned.  Our fork's default is already **2**
  (`generate.cpp:2337`); the production boot prints `STRATA_ADAPT_FETCH is off (a layer split)`,
  gated at `generate.cpp:15075` (`multi_gpu || !stages.empty()`), and we additionally match the
  "resident RAM mode" reason.  The author's comment explains why: under a split, verifiers mutate
  `host_res` inside a window while pipelined groups upload, so admission-style swap-in is refused.
  Same family, same verdict: `adaptive overlap: off: not on a layer split`.
- `STRATA_EXCHANGE_ROTATE=1` - rejected, mutually exclusive with the NUMA pool.  Rotation itself
  qualifies on this box (`exchange buffer rotation enabled: 96 buffers ... no host commit memcpy`),
  but `pool.cpp:623` feeds the flag into `decide_numa_split`, and the boot then prints `NUMA pool
  off: ... moves experts between slots, the split needs fixed slots`.  Our lane guard caught it:
  `GEOMETRY-FAIL the NUMA cpu pool did not come up`.  Shallow-window probe (2 clean reps/arm):
  mswin 25.04 -> 25.03, reach 3.72 -> 3.67, **cpu 2.18 -> 2.59 (+19%)**, tg -2.6%.  The memcpy we
  save does not show; the pool we lose already does - and the pool serves our biggest open item
  (deep-window host-CPU experts, 10.5 ms/window).
- `STRATA_COALESCE_PREFILL=4` - true negative, mechanism proven firing.  Trap recorded: the
  default `STRATA_COALESCE_TOKENS` is **16384**, which silently excludes long prompts from the
  co-read, so a naive test measures the config, not the idea.  Raised to 200000, the boot logged
  `coalesce: 3 waiting prompt(s) (149101 tokens) read with this request's 49646`.  Paired cold
  bursts (non-overlapping slices, offsets changed per boot to dodge the persisted disk tier):

  | arm | conc | ptok each | wall | aggregate |
  | --- | --- | --- | --- | --- |
  | default, synth 50K | 1 | 49.8K | 19.6 s | **2518 tok/s** |
  | default, synth 50K | 4 | 49.8K | 79.9 s | 2474 tok/s |
  | coalesce, synth 50K | 4 | 49.7K | 79.0 s | 2499 tok/s |
  | coalesce, natural ~44K | 4 | 43.9K | 83.8 s | 2095 tok/s |
  | default, natural ~43K | 4 | 43.5K | 78.9 s | **2206 tok/s** |
  | default, natural | 1 | 36.5K | 17.6 s | 2075 tok/s |

- The read that matters: **4-way aggregate == one stream**, `tail_ratio` 1.00 everywhere.  Cold PP
  is a pipeline-throughput ceiling (~2100-2500 tok/s on 4 cards), not a per-read overhead, so
  merging the reads moves nothing; and by the same arithmetic `replicas` cannot raise PP aggregate
  either - it buys concurrency and TTFT isolation, not prefill throughput.  Corollary correction:
  the old "cold PP p10 -39% tail" is not queue contention (bursts show no tail at all) - source
  still open, likely the shared-CPU/NVLink tenant family (T10).
- Ladder after this wave: to move cold PP we must reduce compute per token (SpecPrefill, path 6)
  or raise per-card math (QPN2/tensorfold - still absent from our tree, sister-port work).  For TG
  the remaining untested handles are NUMA affinity itself (`STRATA_NUMA_POOL` split, `--aux-cpus`)
  and the resident ratio; ADAPT_FETCH / ROTATE / ADAPT_OVERLAP are geometry-locked.
- Next boot-costed ticket queued: `STRATA_ADMIT_SHORT_FIRST=<tokens>` (`server.py:409-417`, default
  0 FIFO, `ADMIT_MAX_WAIT_S=30` anti-starvation) - a TTFT/fairness ticket for exactly the mixed
  length traffic the operator complained about, not a throughput ticket.
- Handback: default production boot 20:08:58 (pid 799119), GEOMETRY-PASS, cold tier + floor +
  decode-timing all self-reported, zero experiment flags in `/proc/<pid>/environ`, probe env files
  deleted.

## Deferred to an evening window (operator uses the box by day)
### H.37 - the lane's deep-window slowness was VRAM starvation on CUDA-graph capture, and the concurrency residual turned out to be a FLOP budget, not a knob (11-23 15:3x-17:0x, 4-card, arms FIX1/FIX5/P1)

- **What triggered it.**  Arm F's first cell (H.36) reported `engine_tg 7.95` at 1,495.92 ms/window on a single
  131 K chain.  The same boot's 1-token probe was 19.34 ms/window, and another cell sat at 175.39 ms/window with
  T 2.43.  Read as a law, "ms/window is superlinear in tokens/window" would have sent me after `--spec` width.
  A read-only census of the log said otherwise.

- **The mechanism, in the engine's own words.**  `strings`/log: `strata verify: no VRAM for a new window graph:
  freed 0 batch layouts, 9 solo window graphs and 0 MiB of the expert cache (said once; a larger
  --vram-reserve-mib avoids it)`, which appeared **6,655 times** in that boot.  Adjacent lines show why it could
  free nothing: `capturing the 4-token window (44 MiB of VRAM free)`.  `--expert-cache auto` takes everything the
  dense weights leave (`12.57 GiB free, 832 MiB reserved ... -> 4470 slots`), and the window-graph library - one
  capture per (tokens-per-window, slot-layout) shape, and `--spec 8` makes that 1..9 shapes times the group
  layouts - had to live in the 832 MiB the reserve gave it.  When it did not fit, the engine evicted its own graph
  library and re-captured **inside a live decode window**: the slowest capture measured **1,047.3 ms**, and
  940.6/965.7 ms captures show up in the same boot.  That is the whole "superlinear" story: a window that happened
  to need a fresh graph paid a second, and the average over 1,237 windows came out at 175 ms.

- **One config line, and the pathology is gone.**  `ac922/configs/strata-nvfp4-final.json`: `--vram-reserve-mib`
  832 -> **1536**.  Same protocol, same boot shape, nothing else touched:

| measure | boot at 832 (arm F) | boot at 1536 (FIX1) |
| --- | ---: | ---: |
| `no VRAM for a new window graph` | 6,655 | **0** |
| slowest window-graph capture | 1,047.3 ms | **7.2 ms** |
| deep 131 K decode, T ~2.4-3.0 | 175.39 ms/window (tg 13.8) | **31.40 ms/window** (T 2.97, tg ~65-70) |
| expert cache | 4,470 slots, 98.1 % hit | 4,203 slots (-6 %), 97.3-97.5 % hit |

  It is a VRAM-for-wall-clock trade inside the machine's own accounting, not a quality trade: no numeric path
  changes, drafts are still verified by the target, and the price is ~6 % of the expert cache.  **Shipped.**

- **FIX1's concurrency curve, and what is left.**  Four independent ~131 K chains, words, budget 60 s, 600-token
  turns, rounds ascending then descending (median of two rounds):

| n | 1 | 2 | 3 | 4 |
| --- | ---: | ---: | ---: | ---: |
| FIX1 agg tok/s | 44.1 (66.1 / 22.1) | 81.8 (86.3 / 77.2) | 101.7 (105.5 / 97.8) | 70.1 (30.3 / 109.9) |

  Healthy cells now reach **105-110 agg at four deep chains** (H.36's best tier-off cell was 115.8, its n=4 median
  60.5), so the reserve fix plus `CC_PARK_GAIN` is worth roughly the difference at n=3/n=4.  What still collapses a
  cell is the request boundary: the same boot logged **five** full cold re-prefills -
  `prompt 130,2xx tokens = 0 reused + 130,2xx read in 36,360-67,308 ms` - and a 36-67 s cold prefill on a serial
  feed is exactly what pulls 105.5 down to 30.3 and inflates a lone stream's own windows from 20 to 112 ms.

- **Why a cold prefill happens at all with 48 GiB of cache free of the disk tier.**  The parked image of a 131 K
  chain is ~2.12 GiB (`snapshot_bytes=2115849904`), and four chains were holding **24 parked entries** = 50.9 GiB
  against the 49,152 MiB cap, with `evictions=` climbing to 80 by the end of the run.  The cache is not full of
  conversations, it is full of **generations of the same four conversations** (~6 each): every park appends a new
  entry and the superseded one is only dropped when the byte cap already binds.  Eviction is oldest-first, so the
  entry that leaves is whichever chain's oldest generation - including, often enough, the copy a chain needs next
  turn.  There is no per-conversation generation knob in the engine (`--conversation-cache-mib/-slots/
  -min-tokens/-min-free-mib/--conversation-cache-park-gain` is the whole surface), so the lever is the slot cap.

- **FIX5 (slots 32 -> 8) does not fix the collapses, and that is the useful part.**  Same protocol:

| arm | n=1 | n=2 | n=3 | n=4 | mean of 8 cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| FIX1 (slots 32, 50.9 GiB parked, evictions 80) | 44.1 | 81.8 | 101.7 | 70.1 | 74.4 |
| FIX5 (slots 8, 17.0 GiB parked, evictions 64) | 62.9 | 86.7 | 66.6 | 73.7 | 72.5 |

  Both arms are bimodal, two cells each, and the means are inside the box's +-27 % reproduction band: the slot
  cap is not the lever, so `--conversation-cache-slots 32` stays shipped (the 11-17 relay test needs it).
  What the two arms do agree on is the shape of a collapsed cell: `rows/group-step` unchanged at 1.68, ring step
  up from 14.4-19.0 ms to **51.7-70.8 ms**, rows/s down from 109-116 to 23.9-32.5, `duty` 1.00 and engine CPU
  *down* to 0.3-1.0 cores, and **zero** request summaries inside the cell.  Waiting, not working.

- **The one mechanism that explains the residual variance: a cold deep prefill is a ring-wide tax.**  Of 75 deep
  requests in the FIX5 boot, 6 came in cold (`= 0 reused`, 36.1-61.2 s each); four of those are the harness's own
  session builds, and the remaining two land exactly on the two collapse cells.  While one 131 K cold prefill is
  in flight, every group-step of every other stream costs 3.5-4.7x its healthy 15 ms, because the prefill's FLOPs
  are on the same four cards and the shape is a serial feed (`one hand-off per window`, `4 groups of 1`).  That is
  the same coupling the operator reports as "开并发反而变慢" and it is what H.33's ledger said when it found
  "two 53 K cold reads concurrently took 54.4 s against 38.2 s serially (-30 % aggregate PP), 42 % of ticks with
  exactly one card moving".  Slicing does not help: the work has to happen somewhere on those cards.  The lever is
  scheduling - defer or pace the prefill while decode streams hold the ring (PP yields to TG, per the stated
  priority) - and the candidate surface is admission ordering (`STRATA_ADMIT_SHORT_FIRST`, `ADMIT_MAX_WAIT_S`),
  `STRATA_COALESCE_PREFILL` with its `COALESCE_MIN_REQS/BURST_MS` brakes, and `--prefill` chunk pacing.
  **This is the named next attack; nothing in it is shipped today.**

- **P1 (`STRATA_PIPED_READ_PIECE=32768`, `STRATA_PIPED_DECODE_MAX_MS=400`): the mechanism fires and does not buy
  what we need - and it reframes the whole "cold-prefill tax".**  Upstream's opt-in (#1906) reads a prompt on the
  pipelined groups in pieces so the slots decode between them.  The engine took it, and the log proves it:

```
strata batch: piped read 32768 tokens in 14498 ms (to 32768 of 130209), then the slots decoded 20 rows in 454 ms
strata batch: piped read 32768 tokens in 14911 ms (to 65536 of 130209), then the slots decoded 18 rows in 511 ms
strata batch: piped read 32768 tokens in 14854 ms (to 98304 of 130209), then the slots decoded 22 rows in 452 ms
```

  Three things in those three lines.  (1) The decode gap is **452-511 ms per ~14.5 s piece** - that is the 400 ms
  cap, i.e. the streams got 3 % of the time, so the stall is only chopped up, not shortened.  (2) A piece costs
  14.5-18.4 s for 32,768 tokens = **2,260 tok/s against 3,490 tok/s uninterrupted**, because every piece ends by
  draining the pipeline: the cold read got 1.55x slower, and the first piece of one read took 34 s (upstream's own
  documented flake: one uninterrupted run before any slot decodes).  (3) The curve is unchanged within noise -
  P1 medians 68.3 / 79.8 / 104.0 / 66.8 against FIX1's 44.1 / 81.8 / 101.7 / 70.1 and FIX5's 62.9 / 86.7 / 66.6 /
  73.7.  **Rejected, off again**, consistent with what upstream itself writes: "a latency choice, not a throughput
  gain".

- **So the collapsed cells were never a scheduling fault.**  A collapsed cell reads 23.9 agg and **24 rows/s**
  against a healthy **115 rows/s**, with `rows/group-step` unchanged at 1.68 and engine CPU *down* to 0.3-1.0 cores.
  24/115 = 21 %, and the prompt in flight is 131 K tokens at ~2,260-3,490 tok/s: the ring was spending ~79 % of the
  GPU on the new request's read.  The decode tokens are not lost, they are displaced, and `agg_wall` - which counts
  decode only - cannot see the work.  That is why H.36/H.37's earlier wording ("boundary storms", "ring tax") was
  half right about the trigger and wrong about the nature: on four cards, one cold deep read and four deep decode
  streams are in plain FLOP competition.  The harness now carries the covariate that proves it
  (`pf_cold_reads/pf_cold_tok/pf_cold_ms/pf_warm_ms/pf_frac`, commit `cf607c88b`), and every future
  "concurrency is slower" verdict has to be read against it.  Two consequences worth writing down: raising the cap
  (`DECODE_MAX_MS`) buys fairness for the waiting streams at 1.55x the prompt's own wall - a real PP-for-TG trade if
  the operator's traffic is mostly continuations; and what actually reduces the tax is **not re-reading** (cache
  policy, fewer superseded generations), more prefill capacity (6 cards / replicas), or SpecPrefill reading less.
  One more number from the same covariate, whole-log lifetime: cold deep reads 2,083 / 79,274 s against warm deep
  continuations **22,314 / 83,819 s** - warm reads cost as much total time as cold ones, so the reuse path itself
  (scan + restore, median 772 ms, p90 36 s at the tail) is a ticket nobody has priced.

- **Not believed / queued.**  (a) The reserve value is priced on the 4-card shape; 6 cards leave less per card
  behind (`--trim-stage-weights` there keeps ~11 GiB), so re-check the expert-cache hit rate after the next 6-card
  boot before assuming 1536 is free there.  (b) `--mtp-head proxy --mtp-cum auto --spec 8` arrive through
  `strata_control/drafting.py:FORK_MTP_FLAGS`, whose own docstring restricts them to "one request at a time, one
  card's layers (**not a split**)"; they are live on our split.  Harmless once the reserve fits (thrash 0), but the
  factory `--spec 6` arm was never A/B'd against them on this lane.  (c) `STRATA_SSD_GBPS` still misprices our
  LVM `dm-2` as 0.45 GB/s SATA.  (d) `numa_slice_ranges` (`src/core/expert_source.cpp:184`) still splits every
  expert blob mid-blob across the two islands.  (e) node 0 free drifted to 2.2 GiB during FIX1 with `majflt=0/s` -
  not yet a cost, but the CC floor is measured against global `MemAvailable`, so it is node-blind by construction.

### H.36 - where the deep-concurrency wall actually is: request boundaries, a 48:1 cold-tier write treadmill, and one arm that died of its own cure (11-23 13:4x-16:0x, 4-card, verification boots C/D/E1/F)

- **What was asked.**  H.35 left two unpriced things: why the ring holds `rows/group-step ~1.7` at every
  concurrency, and why single-stream deep tg inside one boot walks from ~68 down to ~30 while nothing on the
  GPU changes.  Four arms, all same protocol (four independent ~131 K chains, words corpus, 3 rounds run
  ascending then descending, every cell bracketed by engine-log byte offset).

| arm | one variable | n=1 | n=2 | n=3 | n=4 | best cell | writes MB/s |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |
| C | shipped base + cold tier ON + `park-gain 8192` | 34.9 | 76.7 | 87.8 | 91.7 | 100.6 (r1 n=3) | 416-1,265 |
| D | cold tier **OFF** | 36.6 | 70.4 | **108.2** | 60.5 | **115.8** (r2 n=4) | ~0 |
| E1 | tier off + CC 48 -> **16 GiB** | 67.7 | 87.8 | **23.7** | 36.3 | died of the cure | ~0 |
| F | tier off, turns 600 -> 4,000 tokens | **void** | - | - | - | see H.37 | ~0 |

- **The tier is a treadmill, measured in production, not in the harness.**  Boot-by-boot census over the
  engine log (read-only, 40 boots with >=18 deep requests): saves 79-177 per boot writing **156-352 GiB**,
  against **2-9 disk read-backs** per boot - a **40:1 write:read ratio**, on the same `nvme0n1` that holds the
  expert pack.  Lifetime tier volume 8.53 TiB written / 6,640 s of pure write time / 22.5 % of saves only
  rewrote a copy that the next turn dropped as superseded.  The `DECISIONS.md` 11-20 adoption priced the tier
  at "16 x 148 K distinct chains resurrect in 6.9 s instead of 46 s"; that benefit is real but it is bought
  ~5 times per boot, and the harness regime (four deep chains re-continued) pays 1.2-1.4 GB/s for it and loses
  a third of its single-stream deep tg (C: 68.2 at wr 0 -> 29.9 at wr 1,265 MB/s).  Tier OFF is shipped on
  this lane; the resurrection path stays one env line away (`DISK_DIR`), and the honest open question is
  whether traffic with many distinct long sessions returning would want it back - it should then be priced
  against its own device or a writer rate limit, neither of which the engine has.
- **`--conversation-cache-park-gain 8192` is necessary but not sufficient, and the reason is instructive.**
  Its guard is "the copy I would supersede is still held" (`park_held == 0` => park anyway).  At four deep
  chains the cache runs at its byte cap (log: `parked=24 bytes=50.9 GB evictions=` climbing one per park),
  so the copy it would supersede has already been evicted and the gate never closes.  Parking is cheap only
  when the cache is not oversubscribed by near-duplicates - which is the same observation as 1Cat's
  retention-default incident (`c5efe9f0a0`) from the other side: a *periodic* retention/eviction policy
  evicts the state that the next request needs.
- **E1 is the arm that killed itself: CC 16 GiB is below the working set, not below the noise.**  Four
  ~131 K chains cost ~2.8 GiB of parked image each, so 16 GiB holds five - and with four streams plus
  supersede churn the cache turns over every boundary.  n=3 fell to **23.7 agg / 7.9 per stream / 70.7 ms
  group-step**, while node 8 sat with 50 GiB free.  So the 48 GiB cap is not "RAM that decode is missing":
  free RAM did not buy speed, and shrinking the cache converts a park into a re-read.  Recorded as **rejected
  with no ambiguity**, and it retires a plausible-looking inference (parked chains starving expert streaming)
  that I had almost shipped.
- **The wall is at boundaries, and it is two different losses.**  Same boot, same config (arm D): n=1 cells
  gave `engine_tg` 72.0 / 64.4 / 33.0 ms against `agg_wall` 68.8 / 36.6 / 24.3.  Where `engine_tg` stayed
  ~64 and the wall rate fell to 36.6, the loss is **between** requests: a 600-token turn at this depth ends,
  parks (median 439 ms, max **7.16 s** per slot-park this run), and the next turn queues - so ~40 % of wall
  is boundary.  Where `engine_tg` itself fell to 33 the windows really inflated (`mswin` 58 ms,
  `group-step` 55.7 ms in the worst n=4 cell) - the boundary storms overlap and land on the ring.  Both
  channels are host-side bookkeeping at the request boundary, neither is expert compute: `rows/group-step`
  never left **1.69-1.71** in any arm, which is H.35's observation restated with four independent chains and
  confirmed as a property of the `4 groups of 1` shape rather than of load.
- **Arm F is retracted, and the reason is a separate mechanism found after it (H.37).**  Its first cell read
  `engine_tg 7.95` at **1,495.92 ms/window** with T 4.21 and `node0 free 4.6 GiB`.  I was about to record that
  as "deep-window verify is superlinear in tokens per window"; it is not.  The same boot emitted
  `no VRAM for a new window graph` **6,655 times** and a window-graph capture costing **1,047 ms**, and after
  one config line (the VRAM reserve) the identical long-turn probe sits at 31.40 ms/window.  Nothing about
  4,000-token turns was ever measured, because the cell that looked like the cure was the disease.
- **Not believed / queued.**  (a) The CC has no per-conversation generation retention, so `--conversation-cache-slots`
  is a blunt proxy for it - re-priced in H.37, and if the traffic is >8 *distinct* deep sessions the 11-17 relay
  test (which needed slots 32) has to be re-run against whatever ships.  (b) `STRATA_SSD_GBPS`: the engine reads
  the device name and prices our LVM `dm-2` as 0.45 GB/s SATA, 5.5x off (engine `generate.cpp:1759`,
  `prefill_plan.hpp:97`).  (c) The mid-blob NUMA split (`src/core/expert_source.cpp:184`) still puts half of
  every expert blob on the far node by construction.  (d) Shape `4/2` on a tier-quiet lane was never re-run
  to >=3 rounds, so H.32/H.35's shape verdicts stay inside their +-27% noise.  (e) `STRATA_PARK_ASYNC=1`.

### H.35 - the real deep-window concurrency curve, and the mechanism H.32 got wrong (11-23 11:0x-11:5x, 4-card, quiet lane)

- **What this was.**  Two harnesses landed (`ac922/scripts/concscale.py` + `.sh`, `ac922/scripts/interf_attrib.py` + `.sh`, commit `f282d311f` plus three fixes), then a scale curve ran in the quiet window.  The curve answers a question H.32 answered with the wrong word: it called deep-window concurrency "capacity-bound".  It is not, and the consequence is a bigger prize than a knob.

- **The curve**  (4/4 ship shape, four **INDEPENDENT** ~131K sessions, words corpus, 600-token turns, 2 rounds run ascending and descending, every cell from the single boot after the 11:21 restart, so internally comparable):

  | streams | agg tok/s | per-stream | ratio to n=1 | mswin ms | **tpw** | reach ms | cpu ms | idle % |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | 1 | **53.1** | 53.1 | 1.00x | 28.2 | **1.86** | 4.07 | 0.77 | 88 |
  | 2 | **67.4** | 33.7 | 1.27x | 20.1 | **1.00** | 3.42 | 0.79 | 89 |
  | 3 | **74.5** | 24.8 | 1.40x | 19.8 | **1.00** | 3.38 | 0.57 | 90 |
  | 4 | **83.8** | 21.0 | 1.58x | 19.9 | **1.00** | 3.20 | 0.33 | 91 |

- **"Capacity" is the wrong diagnosis - nothing saturates.**  At n=4 `duty` is 0.999-1.000, host `idle` *rises* 88 to 91 %, `cpu` per window *falls* (0.77 -> 0.33 ms), `gpu_reach` *falls* (4.07 -> 3.20 ms), and `mswin` itself *falls* 28.2 -> 19.9 ms.  A saturated device does not get cheaper windows.  The single quantity that collapses is **`tpw` (tokens per window) 1.86 -> 1.00**.  Per-stream tg does not drop 2.5x because windows got expensive - it drops because each window now emits one token instead of ~1.9.  The MTP speculative multiplier is bought back by nothing at n>1: it is simply absent.
- **Why it is absent (config, not law).**  The boot says it out loud: `--batch-mtp: 4 slot drafters on CUDA3` and `STRATA_PIPED_MTP: 4 pipelined groups of 1 slot draft up to 1 MTP proposal`.  With `--batch-groups 4` every group carries one slot, so every window in the ring advances by a single proposal.  If the ring could hold `tpw` near the solo value, the same four streams project to **~150 agg** (1.8x the observed n=4).  That is the first concrete 2x-shaped target on this box, and it is neither bandwidth nor NUMA.
- **Corpus validity - words is a conservative floor, and cross-corpus cell comparison is void.**  Equal depth, single stream: tg words **68.5** (69.9/67.1) vs prose **79.3** (65.0/93.7).  Random words understate real tg by ~16 %, and the prose reps themselves spread +-18 % while words spread +-2 % - prose is the noisier fixture.  Window *profiles* differ by construction (words acc 0.79 / tpw 1.85 / mswin 26, prose acc 0.95 / tpw 7.0 / mswin 55), so `mswin`/`tpw`/`acc` may be compared within a corpus and never between two.
- **conv-disk write amplification, quantified for the first time.**  Sustained write into `nvme0n1` goes **151-190 MB/s at n=1 -> 0.78-1.39 GB/s at n=3/4**, on the same device that holds the model pack.  Reads are near zero (0-308 MB/s) because the page cache serves them (`MemTotal 341 GiB`, buff/cache 99 GiB, 1,170 `reused from the disk` lines this boot).  Note the honest counter-evidence: the n=3/4 cells are *faster* than n=2 despite the heavier write-out, so I am **not** claiming the writes currently hurt - only that the collision is now measurable.
- **Historical census (20,881 request lines across boots, read-only).**  Single-stream deep-window tg **57-64 tok/s and flat from 32K to 256K** (acc 0.72-0.74) - context depth costs nothing in decode, so "long context is slow" is a prefill/restore statement, never a tg one.  Deep-link **restore** (prompt >100K, n=7,405): p50 **1.07 s**, p90 3.8 s, max 31.5 s (130K p50 740 ms, 260K p50 2,744 ms).  Cold **PP** (n=492): p50 **3,101 tok/s**, p10 2,225.
- **A cost model that has been wrong since the pack moved onto LVM.**  The engine self-reports `drive 0.45 GB/s (dm-2 is a SATA-class SSD (assumed))`.  `src/program/generate.cpp:1759` classifies by device *name* (`dev.rfind("nvme",0)==0 -> 2.5`), and `/data` is an LVM volume - `dm-2`, VG named `nvmevg`, `queue/rotational=0`, backing a PM1725b - so every drive decision silently takes the SATA branch.  `include/strata/program/prefill_plan.hpp:97` then prices a cold plan as `read_bytes/(h.ssd_gbps*1e9)`, i.e. the I/O term is **5.5x too expensive**.  `STRATA_SSD_GBPS` is the documented override (`generate.cpp:1751`) and production has never set it.  Config-only, and it changes plans, so it gets the full gate: `selfcheck` first, then three gates.
- **The lane restarted underneath me and I only found it in the census.**  Engine pid 229099 -> **253968** at 11:21:36, auto-restarted by the serve wrapper, reason (`strata-nvfp4-2026-11-23T01:46:26Z.log:3358`): `Unrecoverable native verification failure: verify: timed out at layer 20; its GPU waits were released but the GPU did not finish within 5 s (#267)`.  It fell inside my own deep-pressure window.  Whether my load caused it or merely coincided with it is **unproven**; health was 200 across the whole run and the post-restart gate passed (`QUALGATE-PASS`, det hashes identical to REF, `acc-drift 0.962`, relay agreement 0.12 - which is what the pre-incident boot logged too, i.e. the known deep-context drift, not new damage).  Every cell above carries a boot stamp, so the curve is at least self-consistent.
- **Noise, and what it costs a shipped conclusion.**  Same config, same protocol, same depth, **n=2 gave 82.1 and 52.7 in the two rounds - +-27 %.**  H.32's `4/4` over `4/2` call rests on a 12 % gap (81.8 vs 72.7), which now sits *inside* my own reproduction spread.  The shape stays shipped (it also won the 11-21 sweep and needs no change), but it is recorded as not independently reproduced, and any future shape claim needs >=3 rounds.
- **Harness bugs found by running them.**  (1) drain-delta rows were attributed to the wrong request; (2) `/proc/stat` idle parsing read the wrong column on this kernel's 10-field line, producing impossible idle percentages; (3) corpus calibration ran per-cell and its own probe landed in the conversation cache, contaminating the first cell - calibration now happens once and the probe is excluded.  `interf_attrib` is **abandoned** after one CTL row: its three-arm design measures nothing the curve does not already measure, better, and without a lane to itself.
- **Not believed, and what would settle it.**  (a) The `tpw` -> ~150 projection is arithmetic, not measurement; the test is a same-boot A/B of ring shapes that keep proposals per window (>=2 rows/group) **on independent deep links**, >=3 rounds, judging `agg` with `tpw` in the cell - and H.32's rejection of 4/2 must be re-read because it was taken on *shared-prefix* aggbench.  (b) The 5.5x I/O mispricing is proven in code but its wall-clock effect is unmeasured.  (c) `no VRAM for a new window graph` fired **6,580 times** this boot - still the largest recurring event in the log and still unpriced.

**CORRECTION, same day - the "lost speculative multiplier" reading is void.**  `tpw` comes from the
`strata decode timing` line, which the engine emits on the **solo** path; during concurrent cells the harness
sampled a population that does not describe the ring, so 1.00 was an artifact and not a measurement.  The
engine's own batch census - `strata batch (pipelined, G groups of S): W group-steps, R rows in T ms = X rows/s
... (A of B MTP proposals kept)`, flushed when every slot goes idle (`src/program/generate.cpp:16881`) - says
the ring keeps its drafts: under this boot's deep-window load those lines read **rows/group-step 1.70-1.72**
with **MTP proposals kept 68-72 %**, and the window cost *falls* with concurrency.  Speculation is intact and
the ~150 aggregate projection is withdrawn.  What survives above is the wall-clock curve itself
(53.1/67.4/74.5/83.8) and the saturation evidence (duty 1.00, idle 88-91 %, reach and cpu falling).
`concscale.py` now brackets every cell by engine-log byte offset and reports the census directly (ms per
group-step, rows/step, rows/s, MTP kept); the bottleneck is being re-derived from those numbers, and H.32's
shape conclusion is treated as unmeasured until that run lands.

### H.34 - the operator's slow stretch, attributed: the pathological window is 87 % host CPU, not reach and not bandwidth (11-23 10:0x-10:4x, 4-card production, read-only)

- **What was asked.**  After H.33 the question was whether the wall is NUMA cross-socket transfer or memory
  bandwidth.  The operator then ran a stretch of real traffic and said it was still very slow, so this entry
  answers with the engine's own decomposition instead of a mechanism story.
- **The traffic** (this boot's request table, 26 requests; the operator's own are the deep ones):

| ptok | reused % | prefill ms | tok/s (PP) | gen ms | decode tok/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 137,102 | 13.1 | 72,749 | 1,639 | 567 | 1.8 |
| 125,384 | 15.3 | 47,652 | 2,230 | 39 | 25.7 |
| 151,638 | 0.0 | 94,375 | 1,606 | 28 | 35.3 |
| 197,483 | 9.1 | 86,459 | 2,070 | 22 | 46.0 |
| 125,633 / 137,530 / 137,739 | ~100 | 4,756 / 8,414 / 7,552 | - | 20-23 | ~50 |

  Steady-state from H.33's own census on the same boot family: a cold 131K prefill in 38.2 s = **3,435 tok/s**.
  His four cold reads ran at 1,606-2,230 - **40-53 % slower than the same machine's silent baseline** - and the
  three ~100 %-reused turns cost 4.8-8.4 s where the restore path is documented at ~2 s.  So this is a PP and
  restore-time problem under concurrency, and the short `decode tok/s` column is an artifact of 20-567 ms
  generations, not a decode measurement.
- **Where the time goes when it is slow** (`/data/work/decodetiming/rows-202611.jsonl`, last 2 h, `windows` = rows in the
  window): ordinary windows are `mswin 19.7 / verify 19.2 (97 %) / gpu_reach 3.24 (16 %, overlapping) / cpu 0.15`.
  The pathological ones (deep prefill running while a session decodes) are `mswin 315-567 ms` with
  **`cpu` 274-501 ms = 87-92 % of the window** and `gpu_reach` 6.9-10.6 ms = 2 %.  The hand-off/reach machinery is
  not what collapses; the host-CPU segment is.
- **So, the direct answer.**  Not memory bandwidth as the primary wall, and not NUMA reach: the reach segment caps
  the NUMA ticket's value at ~16 % of an ordinary decode window (and part of that is NVLink, which no placement can
  remove).  The weighted block-aligned slice stays a first-tier ticket but must be priced against *that* segment,
  plus whatever it saves on the prefill feeder - which is not yet measured here.  H.33's own conclusion stands:
  the feeder is the shared upstream, and what saturates it in this sample is the host-CPU side of concurrent deep
  prefill.
- **Two real co-suspects, both measurable and neither needing a kernel.**  (1) The boot's first request paid
  `OS read 127,954.9 MB from storage (5,390 major faults) for 44,629.7 MB of expert reads; page cache held
  264.7 MB ... not 66,267.8 MB` - a cold start pulls ~125 GiB from NVMe, and `lsblk` shows the pack and the 512 GiB
  conversation-disk tier on the *same* device (`nvme0n1`, `/data` 79 % used), so warm-up, restore and idle saves all
  contend on one drive.  (2) VRAM headroom is ~32 MiB, and this boot logged 285 `no VRAM for a new window graph:
  freed the graphs ... (a larger --vram-reserve-mib keeps more)` events (the 6.5 h morning boot logged 578) - each
  forces a re-capture of a batch window layout.
- **Not done, on purpose.**  No configuration was touched and no restart was issued while the operator was live
  (lesson 23).  The attribution experiment queued for a quiet window: N decode streams + one deep cold prefill,
  same-boot paired, arms = shipped `--aux-cpus auto` vs a reduced CPU share, judging the `cpu` segment per window
  before touching GPU-side knobs.

### H.33 - the per-card work census: what "one card pinned, three watching" is, and a rebalance that bought nothing (11-23 09:1x-09:5x, 4-card)

- **What this was.**  The operator watched `GPU1` at 91 % SM load with the other three at 5-10 % and asked whether the
  four cards' compute is pooled at all.  Two things had to be separated: a *structural* property of a layer split
  (nothing to fix) and a *load imbalance* (possibly fixable).  Both boots were measured with a 200 ms sampler over
  `utilization.gpu`, `power.draw` and `clocks.sm` (`/tmp/mix2.py` + `/tmp/mix2sum.py`, nonce `q4` = shipped geometry,
  `b1` = the rebalance arm), on a lane confirmed idle (load 0.02) before each phase.  During the experiment that
  harness was scratch (`/tmp/mix2.py`, `/tmp/mix2sum.py`, `/root/arm.sh`); it has since been productised as
  `ac922/scripts/workcensus.py` + `workcensus-sum.py` + `workcensus-arm.sh` (RUN.md, "Per-card work census"), so
  every number below is reproducible from this repo by one nonce per arm.
- **Structural, first, so it is on the record.**  A split is a pipeline, not a pool: the boot says
  `layers 0-8 (CUDA0), 9-23 (CUDA1), 24-36 (CUDA2), 37-47 (CUDA3), one hand-off per window`, each card holds only its own
  layers' dense weights (`--trim-stage-weights`), and the only cross-card expert tier the engine has (`--peer-device`) is
  explicitly incompatible with `--layer-split`.  In decode a token traverses the four stages in order, so window time is
  their *sum*: moving layers cannot shorten it.  4 -> 6 cards buying +7.5 % was the same fact.
- **`utilization.gpu` is residency, not work.**  In 4-way decode at 131K all four cards look busy - 78-85 % of ticks -
  while their mean util is 22-38 % and they draw 69-73 W against a 54 W idle floor on a 300 W card.  The whole box spends
  ~284 W to serve 79.2 tok/s.  Decode is bound by hand-offs and GPU-reach waits, not by FLOPs, which is why no card ever
  looks saturated *and* why saturating one would not help.
- **Cold prefill is where the asymmetry is real.**  Steady 131K prefill: busy 51 / 81 / 70 / 58 % for 9 / 15 / 13 / 11
  layers - a uniform 5.4-5.7 %/layer, so the only imbalance is that CUDA1 was handed the most layers - and the power
  census says CUDA0 is barely computing at all (71 W) while the other three sit at 195-210 W.
- **The arm.**  `STRATA_LAYER_SPLIT=13,25,37` (13/12/12/11), CUDA3 deliberately left at 11 layers + the head: it is the
  card that respawned the lane on `mtp: round capture: out of memory`.  Side benefit: CUDA1's expert coverage rises from
  3959/7680 = 51.5 % to 4194/6144 = 68.3 % (CUDA2 62.4 % -> 68.3 %), so the arm was not a coin flip.
- **The verdict: it evened out and achieved nothing.**

| arm | 131K cold prefill (steady) | busy 0/1/3/4 % | prefill energy kJ | decode4 agg (per-stream) | decode4 W | decode1 |
| --- | --- | --- | --- | --- | --- | --- |
| shipped `9,24,37` | 38.2 / 38.6 s | 51 / 81 / 70 / 58 | 3.4 5.1 4.6 3.9 (17.1) | 79.2 (20.2 20.4 20.2 20.0) | 73 69 69 73 (284) | 63.9 |
| rebalanced `13,25,37` | 45.3 / 39.3 s | 71 / 66 / 61 / 65 | 4.6 4.0 4.5 3.8 (17.0) | 81.6 (20.9 20.8 20.5 20.9) | 83 78 88 93 (342) | 65.2 |

  Decode "gains" (+3 % agg, +2 % single) sit inside the 5-10 % box drift and cost **+20 % power for the same 2.10 kJ of
  decode energy**; prefill got slower at depth.  The geometry stays `9,24,37`.
- **Why the energy ledger settles it.**  One 131K cold prefill costs 17.0-17.1 kJ in *both* geometries; the rebalance only
  moved joules between cards (5.15 -> 4.01 on CUDA1, 3.42 -> 4.68 on CUDA0).  With a fixed amount of work behind a serial
  feed, levelling the consumers cannot raise the rate - the wall is upstream of compute: the single prompt read (H.31) and
  expert streaming.  Corroborating it directly: **two 53K cold prefills concurrently took 54.4 s, the same two serially
  38.2 s** (-30 % aggregate PP), and 42 % of their ticks had exactly one card moving.  Prefill does not parallelise across
  cards; it contends.
- **The mechanism behind the contention, found in the same pass.**  `detail::numa_slice_ranges`
  (`src/core/expert_source.cpp:184`) cuts every complement slot at `role_bytes[r] / 2`: **half of every expert blob is
  placed on node 0 and the other half on node 8**, whichever card asks for it - a stream pays cross-socket reads by
  construction, not by accident.  Both boots still log `NUMA placement FAILED: slice 0 has 91.3-91.6 % of its pages on
  node 0 (< 95 %)`, and the same line shows the spill cause: node 0 had 2.19 GiB free against node 8's 68.67 GiB.  The fix
  is a code change on the dev lane: cut at expert-slot (block) boundaries with a per-node weight instead of mid-blob
  halves, so a card's expert range lives on its own node.  That is the standing weighted-slice ticket, now with a named
  function, a mechanism and an expected metric (per-card remote-read share, the placement gate passing).
- **Design sketch for that ticket, so the evening window starts coding instead of deciding.**  The split is
  between two *touchers*, but the reader is a specific card, so the unit that should be indivisible is the
  slot (one expert blob of one layer), and the key is the layer's owning GPU: `init()`
  (`NumaComplementPlacement`, `src/core/expert_source.cpp:629`) already walks slots in layer order, so a
  layer -> GPU -> node map replaces the `role_bytes[r] / 2` cut with `slice(slot) = node_of(the card that
  reads it)`.  The map is already on this host, measured 11-23 while drafting this: the six V100s'
  `/sys/bus/pci/devices/*/numa_node` reads **0** for `0004:04/05/06:00.0` (physical cards 0,1,2) and **8** for
  `0035:03/04/05:00.0` (cards 3,4,5) - the kernel resolves a card to the *populated* node of its island, so
  `cpu::card_node_pci_node` (`src/kernels/cpu/card_node_test.cpp:262`) is the whole lookup and no distance
  tie-break is needed.  (The memoryless per-GPU nodes 250-255 that `src/core/expert_source.cpp:549` warns about
  sit at distance 80 from *both* node 0 and node 8, so if a device ever reports one of those ids the tie must
  fall back to the plan's own node order rather than the distance row.)  On the ship lane this is clean
  arithmetic, not a heuristic: CUDA0+CUDA1 = layers 0-23 are read by node-0 cards and CUDA2+CUDA3 = layers
  24-47 by node-8 cards, so whole-slot assignment wants exactly half the complement per node, and the
  free-RAM gate at `sample_free`/`pin_step_fits` sets the weight - with node 0 down to 2.19 GiB free against
  node 8's 68.67 GiB, the spill is where the weight will actually land.  The bound is what makes it worth an
  evening: today every slot pays 50 % remote *always*, after the change a slot pays remote only if it spilled,
  so remote bytes strictly fall for any spill share under 1, and both halves of a blob are read by the same card
  in the same step, which is why a mid-blob cut is pure loss and why the placement gate can report 91 % and
  still be wrong.  Keep `numa_slice_ranges` byte-for-byte for the `1/2` weights the flag-off path uses
  (the default must stay identical), give it a weight argument, and extend `test_numa_slice_ranges`
  (`tests/core/file_expert_source_test.cpp:435`) with the page-coverage invariants for weights other than
  `1/2` and for whole-slot assignment.  The first metric is not throughput: it is the placement line passing
  `>= 95 %` and the per-card remote-read share falling, then PP/tg against a same-boot paired control.
- **Not forgotten (and not believed either).**  At 53K the rebalance arm looked *better*: 17.2 + 17.5 s serial against
  19.1 + 19.1 s, and the concurrency penalty fell 1.42x -> 1.06x.  The shipped arm's 53K pair ran while the lane was
  flushing 48 GiB of idle conversation saves to disk, so the two arms are not paired at that depth.  Anyone who wants the
  53K verdict must re-run both in one boot with the disk tier quiesced.
- **Handback.**  Default production boot restored (health 200, self-reported `layers 0-8 ... one hand-off per window`),
  `qualgate check lane4` + `lanecheck 4` re-run on the restored boot, no experiment flags in the process environment.

### H.32 - the lane's real deep-window concurrency curve, and a shape reversal I had to take back (11-23 07:4x-08:5x, 4-card)

- **What this was.**  The operator reported the 4-card lane "nearly unusable" and watched one card (`GPU1`)
  pinned while the other three idled.  This entry is the measurement of what the lane can actually carry, plus
  the correction of a wrong conclusion I drew halfway through.
- **The card that looked guilty is not guilty.**  `nvidia-smi` sampling showed physical GPU1 at 91 % SM with
  GPU0/3/4 at 5-10 %, owned by our own engine pid.  GPU1 is engine CUDA1 = layers 9-23 = 15 of 48, the heaviest
  stage; a pipelined line runs at its bottleneck stage's rate, so the picture is expected, not a fault.  The
  sub-chunking that made it visible is the engine's own (`STRATA_SPLIT_PF_CHUNKS` default 8 -> "chunks of 2560
  so the stages overlap").  The boot's earlier `verify 523/1209 ms/window` lines with every named sub-timer
  ~4 ms are the same effect: window wall time waiting on the line, not on reach/host/CPU.
- **Deep-window concurrency, ship shape (4/4, four INDEPENDENT sessions, 240 tokens each):**

  | depth | n=1 | n=4 | n=5 |
  | --- | ---: | ---: | ---: |
  | 8K | - | **145.6** (36.4/stream) | - |
  | 131K | - | **81.8** (20.4) | **63.8** (15.8, min 12.6) |
  | 170K | 63.1 | **77.6** (19.3) | - |

  Two facts to keep.  (1) Deep windows are capacity-bound: 4 streams at 170K give only **1.23x** the tokens/s of
  one stream, so a session's share falls to ~31 % of solo speed - that is what "unusable" feels like with several
  agents open, and no shape change creates capacity that is not there.  (2) **Over-subscription is negative**: the
  5th stream costs the lane 22 % of its aggregate (81.8 -> 63.8) because only 4 slots exist, so the practical fix
  is admission, not tuning: keep <= 4 in flight (`--max-queue` / config `max_queue`, `server.py:5242`, capacity =
  batch + max_queue), which turns the 5th request into a short wait or a clean 503 instead of a 5-way slowdown.
- **The reversal.**  Mid-entry I measured `4/4` at 53K x4 as **40.7** aggregate and `4/2` as 113.4, and patched
  the launcher to 4/2.  That 40.7 was taken while the operator's own sessions were on the lane.  Re-measured
  quietly with independent sessions: `4/4` beats `4/2` at both ends (8K 145.6 vs 121.0, 131K 81.8 vs 72.7) and
  single streams are identical (53K 66.1 vs 65.9) - the 2026-11-21 sweep stands, and the patch was reverted to
  `(4,4)` with the paired evidence recorded in its comment.
- **Standing lesson (23).**  A contention measurement taken on a lane that carries live traffic is not a
  measurement: same config, same protocol, 40.7 vs 145.6.  Either quiet the lane, or record the concurrent
  request census (`thinking:` / `done:` lines in the serve log) beside every number and discard contaminated
  samples instead of reasoning around them.

### H.31 - what "production is slow" actually was: one cold read holds the control lines (11-23 00:4x-01:3x, 4-card production)

- **What this was.**  The operator saw single requests at 0.8-1.3 tok/s on the 4-card lane and moved traffic
  away.  The lane itself was healthy: 46 real requests, 0 verify timeouts, 0 respawns, file tier 0.0 MB / 0
  faults (yesterday's thrash pathology is gone), `qualgate check lane4` PASS at acc-drift 1.0.  So the number
  was not compute, VRAM, or placement - it was queueing, and this entry pins which queue.
- **The mechanism, measured.**  A cold deep prompt takes the control lines (`serve/server.py`: one prompt read
  at a time) and holds them for the *whole* read.  A 64-token request sent 12 s behind a ~220K cold prompt
  waits 52-74 s for the lock, then decodes normally: the trace shows it landing in a batch slot
  (`send> BGEN 2 64 ... BADM 2 1`) at 47.1 tok/s, and its own read reporting "282 of 287 tokens, 72 s so
  far".  `serve`'s tok/s is tokens/total-elapsed, so the wait prints as slowness.
- **Ticket #656 (`BYIELD`) does not fire on this build.**  A long read is supposed to give way at a chunk
  boundary to a much shorter waiting one (`give_way`, YIELDS_MAX=2).  Across 6 boots and 8 paired probes the
  engine-side counter is **0** (`BYIELD|YIELDED` in the engine log) and no `YIELDED` line ever reached the
  front door.  Filed as an upstream defect for the next bump, with the repro above; it is the difference
  between "small chat waits 52 s" and "small chat waits one chunk".
- **Arms that were neutral.**  `STRATA_ADMIT_SHORT_FIRST=32768` - as expected from its own docstring (it
  orders *waiting* admissions; with one read in flight there is nothing to reorder): 78.3/64.7 s vs the
  80.5/65.9 s control, inside box drift.  Shape 4/2 vs 4/4: 67.3/53.6 vs 65.3/51.6 at equal chunk - and 4/4
  stays, because the 2026-11-21 sweep (runbook 337) measured agg wall +4~8 % for 4 groups of 1 on 4 cards;
  only the stale comment claiming "4-card 4/2" was corrected.
- **The lever that worked: the chunk the engine lends itself.**  `--prefill auto` sizes the chunk from what
  every expert cache can lend *at boot*, and with the cold tier's pages in the cache that has been settling
  one notch down per boot (asked 32768 -> got 16384 -> later boots auto picked 8192).  Asking for the ceiling
  explicitly keeps the engine's own feasibility clamp (`prompt chunk 32768 -> 16384 tokens so its buffers fit
  in every expert cache`) but drops the drift:

  | shape | chunk (effective) | deep prompt | deep wall | small wall | cold prefill tok/s |
  | --- | --- | --- | --- | --- | --- |
  | 4/4 | auto -> 8192 | 242,498 | 83.7 s | 69.9 s | 2,900 |
  | 4/2 | auto -> 8192 | 220,578 | 80.5 s | 65.9 s | 2,740 |
  | 4/2 + short-first | auto -> 8192 | 220,809 | 78.3 s | 64.7 s | 2,820 |
  | 4/4 | ask 32768 -> 16384 | 220,821 | **65.3 s** | **51.6 s** | **3,381** |
  | 4/2 | ask 32768 -> 16384 | 220,591 | 67.3 s | 53.6 s | 3,278 |

  Same protocol (`/tmp/contend3.py`, unique-prefixed prompts so nothing restores from the cold tier - the
  first un-prefixed run measured the disk hit, not the prefill, and was discarded).
- **Adopted.**  `export PREFILL_CHUNK=${PREFILL_CHUNK:-32768}` in `lane.env` / `lane.env.example` (launcher
  already mapped it to `--prefill`): ~+16-23 % cold prefill throughput and ~-22 % on the stall it causes.
  Chunk-class gate `bash ac922/eval/selfcheck.sh pf32k1` -> `SELFCHK pf32k1 ALL-EQ 3` (cold == cached at
  8.5K/64K/131K), then `qualgate check lane4`.
- **What this does NOT fix, stated plainly.**  Even at the best arm a small request behind a cold 220K prompt
  waits ~52 s.  Cold PP is a pipeline-throughput ceiling (~2,100-3,400 tok/s on 4 cards, H.29), so neither
  coalesce nor `replicas` raises it; replicas only gives each engine its own control lines.  In production the
  real relief is the disk tier, which is working: a 212,769-token session restored in 1,803 ms (5.2 s total vs
  84 s cold), 204 conversations / 511 GiB at budget, `waits for RAM 0 ms`.

### H.30 - the cache release that was always firing: sparse node ids, and what the two arms said (11-22 15:0x-23:0x, dev 2,5 + 4-card production)

- **What this was.** Closing the tail of the `#267` incident: does `needs_drop` (937b4ac50) really gate
  the release of the model files' cached pages, and what does not releasing them cost.
- **The verdict.** Right logic, dead on this box.  Both walkers over `/sys/devices/system/node/nodeN/meminfo`
  stopped at the first missing directory, and POWER numbers its memory nodes 0 and 8 (the six V100s sit on
  memoryless 250-255).  So the free list held one entry: `free_before[nodes[1]]` read past the end, every
  start looked tight, the release fired unconditionally - and `check_room` printed the fabricated
  "node 8 has 0.00 GiB free".  The same walk silently disabled #1250's emptiest-node guard
  (`numa_pin_free` returns the global number below two nodes).
- **What was done** (4283c8d84).  Free memory now comes from `card_node_free_bytes` keyed by node number
  (already tested for exactly this), nodes without CPUs are excluded, an unreadable node is said out loud
  and judged conservative, and `STRATA_NUMA_DROP=auto|never|always` makes the release testable without a
  rebuild.  Verified honest on both lanes: dev `free before: node 0 2.21 GiB, node 8 77.48 GiB`; production
  (36.37 GiB complement) `node 0 6.46 GiB, node 8 98.46 GiB`, after the release `68.96 / 98.46`.
- **What the host actually looks like at boot.**  ~33 of the ~36 GiB released on dev and ~62 of the
  ~63 GiB released in production sat on node 0: the pack's page cache is almost entirely on one island
  while the complement is split 50/50 by role.  That mismatch, not the release itself, is the root cause
  of the crash chain - release destroys the cache the mmap fallback reads from, deep concurrency then
  re-reads 52.7 GB for one request at ~12x, and the host wait rides GPU verify work past the 5 s watchdog.
- **Arm B (`never`, dev, same shape).**  The page-lock completed anyway - "resident 16.00 GiB, pinned
  16.00 GiB in 5 s", no early stop, no OOM - but placement collapsed: `slice 0 has 0.1 % of its pages on
  node 0` (31 of 33054 sampled; the rest came from node 8's 77 GiB).  So the release is not only the OOM
  guard #1250 documented, it is what makes the even slice split possible at all.  `auto` stays shipped.
- **Why the 95 % gate still fails after a release.**  In production node 0 had 68.96 GiB free right after
  the release and slice 0 still ended 93.7 % local (4723 sampled pages, ~1.2 GiB, on node 8; slice 1
  mirrored it at 93.0 %).  "The node was full at the fault" is therefore not the story.  Two candidates:
  the released ~62 GiB refilling from continued pack reads during the 11 s of stepped page-locking, and
  `kernel.numa_balancing=1` migrating arena pages toward whoever reads them next (the placement's own
  comment forbids the caller from re-touching slice pages for exactly that reason).  Turning balancing off
  is a host-wide change touching the other tenants: operator's call, not ours.
- **`MADV_NOHUGEPAGE` errno 22, priced.**  Isolated probe: the same advice fails on a `posix_memalign`
  region and succeeds on a raw anonymous `mmap` of identical size.  This host runs THP=`[madvise]` with
  AnonHugePages 0 globally and in the engine's own rollup, so the arena takes 4 KiB pages regardless and
  the line is noise here.  On a THP=always host the failed call would silently lose the guarantee.
- **Production state.**  The 23:0x boot runs the fixed binary (complement 36.35 GiB pinned, slices
  18.34/18.02), GEOMETRY-PASS, `qualgate check lane4` → **QUALGATE-PASS**, acc-drift 1.0, zero verify
  timeouts; the crashed-era boot archived first.  The gate's warn list grew `131000/8,512,128` next to the
  known `64000/8,512` relay-cell-drift - same T1 chunk-invariance account, deeper windows.

### H.29 - the NUMA patch placed, the rotation un-exclusive itself, and the POWER9 build passed the yardstick at KL 0 (11-22 10:5x-14:3x, dev 2,5 + dual engines)

- **What this was.** First execution pass on the H.28 tooling: prove the raw-pack NUMA patch on
  hardware, price the power9 codegen, and re-read two Wave-G tickets against what the engine actually
  does on a layer split.  (`c71ef528b` fixed the first execution attempt: the devlane straddle branch
  left `NA=STRADDLE` and `setsid` tried to execute the marker - dev 2,5 died executing the word
  STRADDLE.)
- **The patch places.** On the 2,5 dev boot, the old `NUMA placement FAILED: this pack has no
  per-role byte sizes` is replaced by real slices - `slice 0 -> node 0 (8.07 GiB, toucher CPU 4),
  slice 1 -> node 8 (7.93 GiB, toucher CPU 88)` - and when the co-running ship lane squeezed node 8
  dry, the sentinel said so honestly (`WARNING: NUMA placement at risk: node 8 has 0.00 GiB free`,
  then a partial-placement FAILED at 62.7 %).  The shipping 4-card lane still runs bump-src and still
  prints the old FAILED line: the pack fix is one of the two reasons the lane should move onto the
  repo binary (power9 being the other).
- **Rotation semantics, corrected by the engine's own words.** The hpp comment about rotate and
  "fixed slots" is about the **CPU pool** split, and the engine says exactly which thing it turns
  off: `strata cpu pool: NUMA pool off: STRATA_EXCHANGE_ROTATE=1 moves experts between slots, the
  split needs fixed slots` - while complement placement and the rotation coexist happily (rotation
  requires the complement pinned: `exchange buffer rotation enabled: 96 buffers`).  So placement x
  rotate is a three-arm design (placement+pool / rotate+no-pool / both-off), not the two-arm A/B the
  plan priced.  Rotate's win is the last host commit memcpy in exchange-heavy regimes; at the 73 K
  dev floor its absence was already `host 0.06-0.10 ms/window`, so no floor effect was expected or seen.
- **ADAPT_FETCH dies structurally, not by measurement.** Both lanes print `STRATA_ADAPT_FETCH is off
  (a layer split)` at boot: on any layer-split engine - all shipping shapes - the upstream author's
  `+10.2 %` fetch mode is not the thing running, and no env value will make it so.  The ticket moves
  from "wave candidate" to "wrong topology for our lane", priced for non-split boxes only.
- **Pool affinity was already shipped.** The production log shows `cpu pool: NUMA split on: slice 0 =
  node 0 (17 threads), slice 1 = node 8 (16 threads)` - the thread half of the old T10 question is
  default-on; what was missing was the arena half, which is the patch above.
- **The power9 gate.** Same tree, two builds (`build` with the module at auto, `build-p8ref` with
  `STRATA_PPC_CPU=none` - the module's off-switch proven by its own status line), scored through the
  quant-quality yardstick in CPU-experts mode (the very surface power9 tuning touches): 4995 teacher-forced
  positions, **top-1 agreement 100.00 %, mean KL 0.00000, p99 KL 0.0000, PPL 3.937 vs 3.937**.  The
  codegen is numerically indistinguishable, which is stronger than the "expected bitwise" assumption -
  and stronger than what divprobe could have said: the same p9 binary, two boots, gave different
  divprobe hashes at 110 K (L=8/128/512; one of them equal to the p8 arm's), because adaptive residency
  sets drift between boots and deep-window greedy cascades amplify a near-tie.  Lesson 21.
- **Dev tg readings are a floor, not a verdict.** Three arms' best windows sat within 2 ms/window
  (placement 21.97-23.08, rotate 21.81-25.11, p8ref 23.49) - inside the same-boot ruler, and the
  dev lane barely exercises host-resident experts, the surface power9 prices.  The real reading is
  the ship lane's own DECODE_TIMING cpu column, boot over boot: switch the lane onto the repo binary,
  re-anchor REF, and let the ledger say whether the 10.5-17.5 ms/window host-expert ledger shrank.

### H.28 - the lane moved islands, the NUMA shim found its pack bug, and the build was silently compiling at the POWER8 floor (11-22 09:xx-11:0x, 4 cards ship + rebuild)

- **What this was.** Three related changes in one pass, all aimed at the memory side of the deep-window
  ledger: the card-set reshape (0,1,3,4 - two cards per island - per the user's consistency call), the
  NUMA placement patch, and the POWER-generation build flags.  (`66614dba9`, `1f62fbc6d`, `4a3fbfd6b`,
  `f469a043c`)
- **Lane reshape.** `lane.sh`/run.sh default to 0,1,3,4 now and `NB_CUT` derives itself from the sysfs
  numa_node of the chosen cards (topo -m fallback; probe failure keeps the old literal).  The dev lane
  took the leftovers 2,5 - one card per island - and when it spans islands it *mirrors* the production
  straddle (numabind.so + NB_CUT fraction) instead of pinning to one node, so dev arms price the same
  remote-read behaviour the ship lane pays for.
- **The pack bug.** NUMA placement warned "this pack has no per-role byte sizes" - true for raw-blob
  packs: the reader never saw role sizes because there is no GGUF metadata on experts.bin.  The patch
  synthesizes an even three-way split for raw packs before `numa.init`, which is exactly what the
  complement wants (q/k/v roles are equal-sized by construction here) and leaves the role-pointer path
  untouched.  Dev-lane boot on 2,5 is the feasibility proof; the old WARN must become a
  `FileExpertSource: NUMA placement:` line.
- **The POWER8 floor, confirmed.** `grep` of both live build caches: every target in the tree compiles
  with no `-mcpu` at all, so RHEL gcc-toolset-12's baked default governs - and that default is
  `-mcpu=power8 -mtune=power8` (visible in every cc1plus cmdline).  ggml-cpu already lifts itself with
  its own power9 branch (`-mcpu=power9`, and the `-U__POWER9_VECTOR__` tail guards only AC922's
  deliberately-scalar `arch/powerpc/quants.c` shim, not the engine).  What never got power9 codegen was
  the engine host code itself: orchestration, exchange, quantizers - the CPU half of every decode
  window.  `cmake/strata_ppc_cpu.cmake` now auto-detects the generation (`cpu : POWER9` line, pvr
  004e/004d; POWER10 MMA is never reached), probes the compiler's default arch macro so a toolchain that
  already defaults correctly adds nothing, verifies each flag with check_cxx_compiler_flag, and applies
  the delta to CXX/C plus nvcc host passes (-Xcompiler).  x86/ARM builds are untouched;
  `STRATA_PPC_CPU=none` opts out.
- **Codegen is a numerics-adjacent change.** No -ffast-math, no reassociation, so bit-identity is
  *expected* - but expected is not verified: the power9 binary goes through the standing quality gate
  (det x3 + quant_quality + divprobe) against the shipping reference before anything adopts it.
- **The stale fingerprint.** After the hard-reset the repo build failed to link `q80p_*`: the
  `build/` configure was from Nov 17, the DFLASH2-era sources (q80_vhead.cu) postdated it, and gmake
  happily compiled what the old target lists knew about and linked what they did not.  A full reconfigure
  (with the new module active) is the fix; see standing lesson 20.  (The same configure later also showed
  the directory had been moved: the cache reported it was born in `build-new`, which is why reusing that
  directory for a fresh configure fails outright.)
- **Tooling.** `ac922/scripts/abreq.py`: the same-boot two-arm harness - strata_tune request keys
  (pcie_frac/spec_min_p/prefill_pipe/aux_cpus), paired alternating rounds, sign test p<0.05, and the
  sub-4 ms/window ruler encoded as a verdict, not a comment.  It is the precondition for T1/T2/T9.

### H.27 - the 6-card corroboration came back neutral, and its one surprise was an artifact of our own ordering (11-22 08:0x-09:2x, 6 cards, then handback)

- **What was asked.** Re-prove the adopted `CC_MIN_TOKENS=4096` on the 6-card lane (the flag lives in
  the shared `lane.env`, so the ship decision silently speaks for that geometry too), then migrate back
  and re-verify 4-card production.  Driver: `/root/ship6c.sh <floor> <tag> <full|short>` - one place
  that edits the floor, boots through `lane.sh`, proves the shape from `/proc/<pid>/cmdline`, and prices
  the bands; arms chained server-side with `setsid` waiters so a ten-minute WAN dropout (09:12-09:22,
  mid-control) cost nothing but patience.
- **Why 6 cards never paid the tax.** Six conversations fit six batch slots, so nothing is evicted
  between turns and the park gate is never consulted: 8K x 6 at the OLD 32768 floor read 227.2/237.4 at
  99.9 % reuse, indistinguishable from 4096 (163.1/232.0, the first rep still carrying a cold-wave
  prefill) and from the ungated 11-19 history (240-254).  The tax needed n = slots with fixture
  contention, which is the 4-card lane's daily life.  Adoption verdict for 6-card: **neutral, keep**
  (245K band unchanged, `PARKPRESSURE-PASS` with resurrect 2.11/2.21 s, COLD = 0).
- **The surprise, and the arm that defused it.** 6-card `fullwindow` at 4096 failed `held_edge` 4/6 -
  the two last-decoded streams emitted end-of-text 8 and 56 tokens short of the 262,136 edge at
  `temperature=0` with byte-identical payloads.  The instinct was to blame the new floor; the honest
  first control (32768, run *after* a battery of probes) passed 6/6, which would have "confirmed" it -
  and would have been the ordering talking, not the floor.  The order-matched control (32768 booted
  fresh, fullwindow as the FIRST requests) failed the same way: **5/6, same stream**.  So: cold-boot
  conversation-cache state moves the chunked-prefill split, and the split flips EOS at the extreme edge;
  warm repeats hold 6/6.  Not a capacity failure (prompt side exact 6/6 in every read, no OOM, no
  dropped stream), not ship-blocking (the invariant is graded on 4-card, which has never shown it in
  any read), but now a named piece of debt tied to the T1 split-invariance question (§10d, §10
  PP-ladder note).
- **Handback proof (4 cards, floor 4096).** `GEOMETRY-PASS 4 cards, 4 groups of 1`; `fullwindow 4/4
  held_edge`; 8K x 4 128.9 @ 99.9 % reuse; `turnprobe` follow 0.26-0.31 s at 98.5-99.9 %; `lanecheck`
  13 ok / 3 warn - one warn is the new 59-token probe correctly not parking below the floor, which is
  the gate working as designed and the short-band checker discriminating for real.
- **Standing lesson this re-teaches (18 again, from the other side).** A control that answers "is it
  X?" must match the *ordering context* of the failure, not just the flag under test; a green control
  run in a different position in the boot is decoration.  Tables: `PERFORMANCE.md` §10d; runbook §346.

### H.26 - a same-boot anchor meant to decorate a table found a 2x short-window tax, and the gate that let it hide for two days (11-22 07:0x-08:0x, 4 cards)

- **How it surfaced.** H.25 left one optional item: give the ship table a same-night 4-card aggregate
  anchor.  `aggbench --n 4 --depth 8000` came back 78.3 and 94.9 `sum_tg`, where the same lane's own
  history is 133.6-143.0 and the 6-card box's 8K x 6 is ~210.  The first instinct was boot luck; the
  second was the reuse column: **0-25 % at 8K against 100 % at 245K**.  A short window was slower than a
  long one, which is the anomaly the user has been pointing at since the "4 卡的 36.8 tg" complaint.
- **The mechanism is one flag, and the engine names it in its own log.** `--conversation-cache-min-tokens`
  is a park-admission test: `conversation cache: skip parking (6972 tokens, below the 32768 minimum;
  parked=11)`.  Below the floor nothing is snapshotted, so a turn that arrives after the conversation has
  lost its slot re-prefills the history — ~7 s at 8K, ~10 s at 20K, against ~0.4 s from a parked chain
  (`eval/turnprobe.py`).  The ship-boot sweep sharpened this: an *immediate* follow-up reuses fine even
  under the floor (98.5 % at 1,869 ptok, live slot), so the taxed turn is the post-eviction one — which
  is what `aggbench`'s round-robin manufactures and an idle probe never sees.  We shipped that floor on
  11-20 with the NVMe cold tier (§8, `runbook 327`) to stop small requests crowding the slots.
- **Ship-boot re-proof (pid 121620).**  `lanecheck 4` 9 ok / 3 warn (no eviction yet so no `disk hit:`
  line; standing NUMA first-touch; graded pre-request), `FULLWINDOW-PASS 4/4` `sum_tg` 123.9, `aggbench`
  8K x 4 one rep 131.0 at 99.9 % reuse (adoption trio was 151.7/159.2/171.4 — short-band single reads
  carry spread too), `turnprobe` 2K-64K follow 0.28-0.37 s at 98.5-100 %.  Numbers in `PERFORMANCE.md`
  §10c.
- **Adopting it was not the same as pricing it.** §8's own sentence is "No PP/TG/VRAM regression" — and
  every gate that ran afterwards (resurrection, full window, 131K/245K aggregates, `det`, acc-drift, the
  whole .4/.5 wave) measured the deep band.  Nobody looked at a second turn of a short conversation.
  Two days of green gates sat on top of a 2x short-window regression.
- **What closed it.** `eval/parkpressure.py` (new): 2 deep chains at 133,582 tokens park, a wave of 16
  short two-turn chains runs through the same cache, then the deep chains are resurrected.  Under the
  candidate floor it is `PARKPRESSURE-PASS` - reused 100 %, TTFT 2.09 s, COLD = 0 - because 32 slots plus
  48 GiB against a ~0.02 MB/token parked footprint means the short wave is a few GiB.  With that, the
  floor drops to 4096: 8K x 4 151.7 / 159.2 / 171.4 (+84 % median), 245K x 4 unchanged (114.1 / 97.1),
  `FULLWINDOW-PASS 4/4`, `QUALGATE-PASS` with the identical four relay-cell WARNs.  Ledger:
  `DECISIONS.md`, tables: `PERFORMANCE.md` §10.
- **Why not upstream's default 0.** The no-floor arm was measured as a side arm (it is what the botched
  `setmintok` quoting produced at 07:46, and the lane answered correctly: follow-up TTFT 0.38-0.50 s from
  2,061 to 20,061 tokens).  It behaves the same above ~2K, but `0` lets a one-shot 200-token request hold
  one of 32 slots, which is the eviction the flag exists to prevent.  Keep 4096; note that the accident
  became an arm (standing lesson 19).
- **Two harness truths learned the hard way this morning.** (a) `FILL` is 26.6 prompt tokens per copy on
  this tokenizer, not 48: a "148K" fixture was quietly 74K, and a depth sweep whose labels are half the
  claimed size cannot show a switch point.  Both new probes now measure tokens-per-unit from the engine
  before building payloads.  (b) A config-change arm needs its *counter*-test designed before its
  favourable test, or it will look green: the favourable half (short band recovers) was 4 minutes of work,
  the counter-half (does it starve the deep chains?) is the reason this is adopted rather than queued.
- **New standing piece: `scripts/lanecheck.sh`.** Read-only, anchors on the newest `engine started:`
  banner, and grades what this lane has to be doing: single boot, no OOM lines, the shape/layer-split/
  resident-RAM/cold-tier proof lines, NUMA placement, **short-band and deep-band turn reuse and tg from
  the engine's own completion lines**, draft acceptance, KV/file-tier hits, conversation-cache state,
  per-card VRAM including a warning when a non-ship card is occupied, and MemAvailable.  Exit
  0/1/2.  Run it on every hand-back - it is the check that would have caught this on 11-20.

### H.25 - the ship invariant re-proved three times in one boot, and it did not agree with itself (11-22 06:30-07:2x, 4 cards)

- **What this was.** After H.24's hand-back, the full-window cell on the *shipping* lane had still only
  ever been measured by one read per boot.  `fullwindow.py run --n 4 --gen 512 --total 262144` was run
  three times against the production boot (06:44, 06:55, 07:06): byte-exact 261,632-token prompts 4/4 in
  every read, `finish=length` 4/4, `prompt+gen = 262,136` on all four positions in all three - and
  `sum_tg` **126.4 / 106.3 / 107.4**.  Table and read in `PERFORMANCE.md` §9f.
- **The 126.4 did not survive replication, and that is the whole lesson.** Had we shipped the first read
  as "4-card full window 126.4 tok/s", the next person to price a ticket against it would have been
  chasing 18% of noise.  Spread inside *one boot* is 19% at the window edge, so H.16's "never compare a
  single shot to a median" is not a cross-boot caveat only - at the deep end it is a replicate caveat,
  same lane, same binary, minutes apart.  Published value: median **107.4**, with the family (105.8 /
  108.0 / 109.3 / 102.4) named next to it.
- **The invariant itself is now a 0.1.61 fact.** README/DECISIONS carried the 4-card full-window row from
  the 0.1.59 adoption day; the row, the `held_edge` caliber sentence and a caliber caveat that names the
  still-0.1.59 ladder cells are all corrected in both languages (README, README.zh-CN, DECISIONS).
- **Three harness debts found by looking, not by measuring.** (1) The server's
  `/data/work/note312/fullwindow.py` was the pre-fix copy, so all three reads printed the retired
  `window_fully_consumed: 0` instead of `held_edge` - the repo copy is installed there now, old file kept
  as `*.pre-held_edge`; the invariant is computable from the JSON we already have (edge + still-wanted =
  total).  (2) H.24 and §9 both claimed the `armf5.sh` `FW`-gate fix was in place; it was not - the script
  reads `FW` from its own environment while the queue passes it positionally.  Applied and syntax-checked
  (`export "$kv"` for each leftover arg, right after the `shift 2`).  (3) Two dead pieces of experiment
  furniture were still armed: `/root/queue4.sh`, parked in a `sleep 30` loop waiting for a `Q3-END` marker
  that will never be written, and a `sleep 8630` watchdog that would have touched `q3.stop` at 07:29.
  A queue that wakes after hand-back restarts the lane under the operator's feet, so both were killed;
  the check is now part of hand-back: `pgrep -af "queue[0-9]*\.sh|armf|diagladder"` must be empty, not
  just the GPU (plain `pgrep -af queue` matches the box's `nv_queue` kernel helpers and is useless here).
- **Hand-back audit (the standing discipline: read the log before declaring the lane healthy).** The
  boot's own rotated log (`strata-nvfp4-2026-11-21T22:29:51Z.log`) carries **0** `out of memory` lines -
  the three in the concatenated live log are from superseded boots, including a 6-card slot-4 MTP capture
  that cannot apply to a 4-slot lane.  Last 200 completions read 51.6 tok/s mean generation rate over a
  p50 prompt of 97K, which is what §5a predicts for that depth; cards 4/5 at 658/27 MiB.  `lane4.sh
  status` -> `GEOMETRY-PASS 4 cards (0,1,2,3), 4 groups of 1, shim loaded`, one engine pid (105568) with
  33 min uptime, no `restarted N times inside this lane boot` WARN, 78.5 GiB `avail` RAM.

### H.24 - the .5 wave: a second tree has to carry the vision helper, a killed arm leaves an orphan boot that holds the lock, and our own window anatomy re-ranks the next attack (11-22 05:2x-, 6 cards)

- **The `.5` boot died before it ever answered health, and the reason was not the engine.** The lane
  launcher resolves the multimodal helper inside the source tree it is booting
  (`$SRC/build-vision/bin/strata-vision`), and only the `.4` tree had ever been built there, so
  `serve/lifeline.py:292` raised `FileNotFoundError` and the front exited while the C++ engine was
  still loading.  Fixed with one symlink (`ln -s /data/work/bump-src/build-vision
  /data/work/bump5-src/build-vision`); the helper is a separate process and this wave sends no images,
  so reusing the `.4` build is measurement-neutral.  The rule for a new tree: run the boot once,
  read the *boot* log (`$TAG.out.boot`), and only then trust the arm - the arm runner's own
  `GEOMETRY-PASS` line is what tells you whether the engine came up at all.
- **A killed arm is not a clean kill.** `armf5.sh` takes its single-instance lock on fd 9 and then
  execs `lane.sh restart`, which inherits that fd; killing the arm leaves the lane restart alive and
  holding the lock.  The relaunched queue then got `ARM-ctl5 ALREADY-RUNNING` (rc=2) in under a
  second, moved on to its next arm, and started a **second boot against the same six cards** - which
  is how a queue silently produces two engines and zero trustworthy rows.  Two fixes: the runner now
  closes the lock fd for children (`bash "$LANE" 6 restart 9>&-`), and the cleanup order is
  queue -> arm -> `lane.sh` -> engine, verified by `pgrep` before any relaunch.
- **The 1Cat retention-default hazard does not exist in our engine** (read-only audit, closed). The
  upstream incident (`1Cat-vLLM c5efe9f0a0`, 4x V100 + Flash-Next + host-KV: a periodic retention
  checkpoint at 8192 evicted other sessions' tail state and forced full re-prefills, 28.9 -> 90.4
  tok/s) has no analog here: `strings` on our binary finds no retention knob, and our policy is
  `include/strata/program/conv_cache.hpp` - the chain's **root is pinned** and every other slot
  rotates by least-recent-use, with the pin switching off only under a two-slot budget
  (`eviction_victim`, `cap < 2`).  We run `--prompt-cache 6`, so the pin is on.  What *is* shared with
  that incident is the structural premise stated in the same header: the KV arena "holds one branch of
  history at a time", which is exactly why cross-session deep windows re-read - the cold/warm asymmetry
  and the DRIFT-UNIQ observations are this, not a mis-set default.
- **Our own window anatomy re-ranks the queue** (PERFORMANCE 5a, chart
  `results/charts/ac922-anatomy-2026-11-22.png`). Pooling 6 755 `STRATA_DECODE_TIMING` lines from this
  night's boots: the GPU-executed body is flat at ~20 ms from short to deep windows while the
  host-CPU expert term goes 0.52 -> 17.48 ms and swaps 0.2 -> 16.8/window; GPU-reach wait is a flat
  9-12%, not the sister box's 28%.  Consequences taken seriously by this wave: the deep-window money
  is in *delivery* (complement NUMA placement, fetch overlap, resident coverage, adapt period), all of
  it quality-neutral; the tensor-core expert kernel (ticket 8/QPN2) attacks a body that is 38% of a
  deep window, which is why the sister box measured it net-negative in serving and why we keep it
  parked; and the third anchor (`ctlB`, same env as `ctlA` and `ctl22`) widened the 131 K boot-to-boot
  spread to 13.1%, so single-boot deep deltas below ~15% are now reported as unresolvable instead of
  neutral.
- **The wave's verdict (PERFORMANCE 9e): `.5` is not adopted.** Three boots on the `.5` tree -
  `ctl5` as-is, `kfast0` with `STRATA_KV_RESIDENT_FAST=0`, `mmerge` with `STRATA_MTP_MERGE_FAST=0` -
  land at 136.0 / 137.6 / 132.4 `sum_tg` at 131 K (3.9 % apart from each other) while the three `.4`
  boots of this same night land at 146.9 / 160.5 / 166.1 (13.1 % apart).  Every `.5` boot is below
  every `.4` boot, the `.5` cluster is the tight one, and reversing each of the two new all-CUDA
  defaults in its own boot moves nothing - so the depth-dependent cost (-13 to -16 % at 131 K,
  ~-3 % at 98 K, neutral at 8 K/50 K) belongs to the `.5` engine rather than to a switch we can set.
  The whole-version policy says no cherry-picking, so `.5` goes back on the shelf for the next
  `-nvfp4.x` tag with this evidence attached.  What made it a verdict instead of a hunch was the
  replication: three boots of the *same* thing is a distribution, and one boot of a *reversal* is a
  mechanism claim - having both is what the 13 %-band rule asks for.
- **Hand-back on the shipping tree, gated.** `lane.sh 4 restart` -> `GEOMETRY-PASS 4 cards (0,1,2,3),
  4 groups of 1` (the adopted 4/4 ship shape), engine 0.1.61, `resident RAM mode: 38.23 GiB of experts
  in RAM (page-locked) ... (no file reads)`, cards 4/5 verified free, and `QUALGATE-PASS`
  (`det` x3 exact, `acc-drift-ok 0.953`, four `relay-cell-drift` WARNs at the known cells).  The
  `NUMA placement FAILED: this pack has no per-role byte sizes` line is still in every boot - that is
  the dev-lane project, and 5a says exactly how much it is worth.
### H.23 - reading the .5 bump before running it; the noise floor came free, and one arm was deleted before it cost a boot (11-22 03:55-04:3x, 6 cards)

- The bump is upstream `v0.2.15-nvfp4.5`, merged into our line as `b352a38148`; the launcher knobs
  of our line came over it as `8e1f0eb04` (branch `nvfp4.5`).  It does *not* move the engine version
  string: `project(strata VERSION 0.1.61)` in both trees, so `strata --version` answers `0.1.61` for
  .4 and .5 alike.  Provenance is the binary's knob inventory instead:

  ```sh
  strings build/strata | grep -E '^STRATA_[A-Z0-9_]+$' | sort -u | wc -l   # 870 on .4, 899 on .5
  ```

  The binary production is serving right now carries the .4-only `STRATA_PF_REFILL_BG_API` and
  `STRATA_SCORES_DX` and none of the .5-only `STRATA_KV_RESIDENT_FAST` / `STRATA_MTP_MERGE_FAST`,
  which is how we know the box is still on .4 while both trees claim 0.1.61.
- Of the 30 names .5 adds, three apply to every CUDA card rather than only to the RTX 50 table:
  `STRATA_KV_RESIDENT_FAST` (`src/kernels/cuda/kv_stream.cu:401`, `env_int("...", 1)`: on unless 0),
  `STRATA_MTP_MERGE_FAST` (`src/kernels/cuda/qsa_decode_attn.cu:1635`: on unless 0) and the opt-in
  drafter-attention pipeline `STRATA_MTP_ATTN_PIPE` (:1630).  `src/core/nv_defaults.cpp` has sections
  for 8.6, 6.0, 12.1 and 12.0 and *none* for 7.0, so `MTP_PDL`, `SCORES_DX`, `SELECT_LONG(_AT)` are
  not silently adopted on V100 by the bump; the DX kernels do build for any card once the name is
  set (`qsa_select.cu:3576-3600`: unset means 6.x only, `=1` means any card), so pricing DX with an
  env arm is honest on both trees.
- One planned arm was deleted before it ran.  `STRATA_SESSION_GROW` reads like a free 320 expert
  slots per card, but `src/program/generate.cpp:8122` gates session-elastic tables on
  `o.batch <= 1` (and on `--kv-resident > 0`, which we ship), so with the shipped `--batch 6` it is
  inert by construction: an arm would have measured the noise and called it the ticket.  Read the
  condition, not the documentation line - this is METHODOLOGY section 2 in practice.
- The anchor replicate came free, and it is the number that matters for everything else tonight.
  `ctlA` reruns `ctl22`'s env on the same binary 3 h 38 min later, same six cards, same shape
  (`pipelined, 3 groups of 2`), four depths:

  | depth | sum_tg | tg_med | rows/step ctl22 -> ctlA |
  |---|---|---|---|
  | 8 000 (cold) | 108.1 -> 107.3 (-0.7%) | 12.8 -> 12.9 (+0.8%) | 3.42 -> 3.43 |
  | 50 000 | 208.4 -> 211.3 (+1.4%) | 33.9 -> 35.4 (+4.4%) | 3.35 -> 3.36 |
  | 98 000 | 179.8 -> 172.8 (-3.9%) | 28.8 -> 28.2 (-2.1%) | 3.39 -> 3.35 |
  | 131 000 | 160.5 -> 146.9 (-8.5%) | 26.1 -> 23.5 (-10.0%) | 3.24 -> 3.27 |

  rows/step agrees to two decimals at every depth, so the shape is not moving: what moves is the
  speed *inside* a window, and it moves by 8-10% at the deep end between two boots of an identical
  lane.  Consequence, written before any arm is judged: one boot resolves a deep-window effect only
  above about 9%, so this wave carries a second anchor at its end (`ctlB`, 8K/131K) and arms are read
  against that band, not against the 1.5% we use within a boot.
- The shallow row is not a decode number.  At 8 000 the prompts are cold (reused 0%), so admission
  and prefill dominate: TTFT 5.82 -> 35.34 s across the six streams and per-stream tg 7.3 -> 43.5 in
  the same burst, `sum_tg` 107.3 against `wall_tg` 43.3.  The two calibers are defined at
  `note312/aggbench.py:11-13` (sum of per-stream rates vs tokens per wall span); the gap is prefill
  serialization, and quoting 8K's sum_tg as a decode ceiling would be wrong.
- Provenance hygiene found one real inconsistency, in our own tooling rather than in the service:
  `/data/work/bump-src` is checked out at `90ca0bdcc7` (docs-era, whose `src` tree hash differs from
  main's) while the `build/strata` inside it is the .4-era binary that was built before that reset -
  the .4 bump commits are ancestors of `10b5df1a76`, not of that checkout.  Nobody served the wrong
  engine, but nobody could have rebuilt that tree and got the same engine either.  Both trees used by
  this wave are pinned to a commit that matches what they run before any measurement is taken.
- Wave Q2 came in on the shipping .4 lane (table in PERFORMANCE section 9b): dropping
  `--resident-budget-gib` costs -16.2 % sum_tg at 98K and -23.3 % at 131K with rows/step unchanged
  (`noarena`) - which is also the price the `replicas` idea has to pay, because that flag is on
  `serve/replicas.py`'s `ARENA_INCOMPATIBLE` list; the DX pair is neutral-to-negative on V100 and inert
  below 24 576 cells by construction; and `TOPK_COMPACT` turns out not to be reachable here at all -
  the compact kernel needs `> TK_T * TK_PER = 33 792` blocks (about 270 K tokens) or the counted prompt
  band, while our window tops out at 32 768 blocks.  Two of the wave's five tickets were therefore
  decided by reading a gating condition instead of spending a boot on them.
- Open when this entry was written: the wave-end anchor `ctlB` and the Q3 (.5) arm tables.

### H.22 - 4-card batch-shape calibration: the analysis's sweet spot was wrong, 4/4 wins; the noise floor got an accidental 3-boot replicate (11-21 evening, 4 cards)

- An upstream-style analysis claimed the 4-card aggregate sweet spot is `--batch 4
  --batch-groups 2` (2 rows/group), that `groups=1` "loses drafts", that the 4-card
  idle-window floor is 17-18 ms, and that full-window 4x concurrency is capacity-feasible
  (~5.2 GiB host-tier/stream, CC/floor timing the real bottleneck).  Only the last claim
  survived contact with the box: the shape had never been A/B'd on 4 cards (the shape fix
  was 6-card evidence), so we measured.
- Paired sweep (`/data/work/note312`, shapesweep boots, aggbench warm-prefix decode n=4,
  wall_tg medians per depth): arms 4/2 (5 boots incl. an accidental 3-boot replicate),
  4/4 (s44r, s44c), 4/1 (s41r).  4/4 beats the 4/2 band at 8K (151.0 vs 139.5), 50K
  (126.0 vs 119.2) and 98K (99.8 vs 93.9) with **both** samples above the entire 4/2
  band; 131K neutral (73.9 vs 70.0; one s44 sample at 58.3 traced to admission stagger
  - ttft 1.7-9.4 s - while its sum_tg 95 sat in band); single-stream tg1 8K unharmed
  (86.5-88.0 vs 86.5-90.5).  4/1 serial window is dead: -21 to -33% aggregate.
- **Adopted 4/4 on the 4-card lane** (config + launcher auto-shape `(4,4) if ncard==4`;
  6-card keeps 6/3).  Mechanism reading: pipelined groups keep MTP slot drafts at
  groups>=2 either way, and 4 groups of 1 row removes the pipeline bubbles that cost
  most at shallow/mid depth; the analysis's "2 rows/group for expert sharing" paid less
  than predicted - coalesced streaming reads were not the binding constraint here.
- Claims judged: `groups=1 丢草稿` is **mechanically wrong** (a 1xN serial window keeps
  slot drafts per `batch_groups.hpp`; the auto-pipelined path is what skips them), but
  4/1 is dead anyway on measured aggregate.  The idle-floor claim was **right and my
  original rejection was wrong**: `STRATA_DECODE_TIMING` single-window lines measure
  17.1-18.8 ms/window on the 4-card lane (and 16.95 ms on 2 cards); the 27-30 ms figure
  belongs to *burst* windows (tg1 84-90), not the idle floor - I conflated the two.
  Capacity claim confirmed.
- Methodology artifact worth keeping: the round-1 boots silently clamped to the
  launcher's auto-shape (run.sh forces `(4,2)`/`(6,3)` whenever `STRATA_BATCH*` env are
  unset, overriding config edits) - the accidental 3-boot 4/2 replicate became our
  **noise floor**: wall_tg ±2.5% at 8K, worst -5.6% at 50K, ±1.2% at 98K, ±2.2% at 131K.
  Anything under ~5% needs paired boots, and sweeps must pass
  `STRATA_BATCH=4 STRATA_BATCH_GROUPS=G` via EXTRA, never edit the config alone.
### H.21 - QUALGATE-FAIL traced to a .2-era REF: .4 moved the long-relay numerics, cold det untouched; re-baselined and green (11-21 afternoon, 6 cards)

- 15:31 `qualgate check` on the shipped-dense boot failed `relay-agreement-collapse 0/8` while
  det x3 matched REF **bit-exactly** and acc sat at 1.133 (ok).  Comparison of the three probes
  on disk (REF 00:43 = .2 binary; 11:09 = first .4 boot; 15:30 = current) showed 7 of 8 relay
  cells had already left REF at 11:09 - the pass that morning rode the `rate>0` floor rule with
  1/8 - and two cells (131000/32, 131000/8) were **bit-identical between the two independent
  .4 boots**.  Verdict: the .4 upstream numeric fixes (NVFP4 down-kernel over-dispatch, FP16
  prefill expert-offset) legitimately moved the long-relay/partial output path; the cold path
  (det fixture) is unchanged.  Not a regression - a stale baseline.
- Actions taken: old REF kept as `REF-v0.2.13-era.json.bak`, new baseline written at 15:42 on
  the archive-config boot, `check` at 15:53 **QUALGATE-PASS** (relay 7/8, drift cell 64000/512
  inside the reproducibility floor, acc 1.0).
- Rule added to the gate discipline: after any bump containing numeric-path fixes, compare the
  COLD det against REF first.  Cold identical + relay all-red means REF is stale, not the lane;
  re-baseline with the old REF preserved.  Relay cells only ever agree 2/8-8/8 gate-to-gate.

### H.20 - Incident: the .4 auto-detect silently switched the draft layer; fair RT/SH A/B says the tuned layer is neutral (11-21 afternoon, 6 cards)

- Root cause: `strata_control/drafting.py` `fork_tuned_mtp_dir()` looks for a complete
  `<model data>/mtp/rt-tuned` **at every start** and swaps it in with `FORK_MTP_FLAGS`
  (`--spec 8 --mtp-head proxy --mtp-cum auto`). We staged `mtp/rt-tuned` at 11:20 for the
  draft A/B - every boot from 13:21 on (the acceptance6 ladder, Wave N's "A default",
  chainprobe, both arms) ran the tuned dense draft without saying so in the banner. The
  `--mtp` in `run.json` is NOT the truth for the draft layer; the live process cmdline is.
- Consequently Wave N's original "D/E rejected by -5~-14%" was mis-baselined: D was the same
  config as A, and E's code99 table was shadowed (plain `rt-tuned` is searched first) and
  never loaded. The 1.4-1.7 ms/window "msw penalty" was cross-boot noise + one cold-adapt
  outlier round (msw 37.08 on RT1's first window).
- Fair test: `/data/work/abTuned.sh` - alternating boots with the tuned dir present/absent
  (RT1/SH1/RT2/SH2), same corpus slices, agent-shaped probes at 185-247K.  Result ~84 vs
  ~81.7 mean tg, bands overlap, signs inconsistent across slices: **neutral**. The sister's
  draft-layer pricing does not show on this box at agent depth.
- Actions: tuned dirs held out to `/data/work/tuned-holdout/` (their presence changes the
  config silently even when nobody asks for it); production left on the shipped dense draft
  with the auto FORK_MTP_FLAGS (which ride regardless - confirmed in both arms' cmdlines);
  post-bump check added: grep `--mtp` from the LIVE engine cmdline, not from run.json.
- Also learned: the spec_min_p ladder (0.7/0.5/0.35, same-boot) stays a dead ticket - it ran
  on a tuned-layer boot, but RT≈SH carries the conclusion; do not re-run.  Chainprobe at
  240K (tpw 1.79-2.35) never reproduced the live-session `avg T 1.00`: the live pain is
  content-specific (tool-result JSON outside the draft vocab) and remains a measurement hole
  we cannot close with synthetic chains.

### H.19 - Upstream followed to v0.2.15-nvfp4.4: zero-conflict bump, one CUDA-12.4 portability gate, dev lane green (11-21 morning, cards 4/5)

- Sources swept: our fork line (sergqwer) published **.3 and .4** since our 11-18 pin; the sister
  V100 line is at 0.1.45.12 (+41); 1Cat-vLLM +161 (a haul of SM70 Qwen3.8 items - host-KV DCP,
  per-token-head INT8 KV, concurrent MTP4 verify - worth a scheduled read, wrong stack to merge).
- Sister's PR #118 was titled "tensorfold-v100" and is **not** one: 50 lines of opt-in direct
  reads for packed expert arenas, and their own doc keeps the sentence that the QPN2 kernel gains
  "must not be presented as a 10-19% serving speedup".  The 61% expert-product lever remains
  unheld by anyone; the queue judgement stands.
- Whole-version bump .2 -> **.4** (9 commits, zero conflicts, merge `7cea05840e`):
  - .3 makes KV streaming the default **only on one CUDA card without --batch** - our split+batch
    lanes never see the flip, and the 2-card dev lane is a split too, so dev stays comparable.
  - .4 is mostly RTX 50 kernels (not applicable) plus portable build guards that push cp.async /
    L2-policy loads / `gemm_f16c` behind cc>=8.0/9.0 - the same direction as our ppc64le fixes -
    and **Basalt's self-distilled MTP draft layer** for the NVFP4-GPTQ set (`mtp/rt-tuned`,
    pinned repo+revision+SHA256): a draft-upgrade candidate to A/B after this build ships
    (acceptance-only change, target verification untouched - same class as spec knobs).
- One real portability gap found by the compiler: upstream's draft-graph builder calls the
  **6-arg `cudaGraphAddNode` with `cudaGraphEdgeData`** which only exists from CUDA 12.7; our
  12.4 wants the 5-arg form (`5d27e85037`, one call site, same semantics - edgeData was nullptr).
  Server link note: the box lost github.com HTTPS resolution mid-bump; the one-commit relay went
  `format-patch -> scp -> git am --3way`, which is still the fallback when the server fetch dies.
- Dev-lane verification (cards 4/5, ~10:30): GEOMETRY-PASS, vision auto-adds its flag as shipped,
  det x3 identical, 70K cold read 92.4 s (the 2-card dev lane is host-bound and slow by design -
  it verifies correctness and startup, not regime numbers).  Lane stopped, cards handed back.
- Production deliberately **stayed on the .2 binary** until the operator's go.  Ship note (11-21
  10:57-11:12): after the confirmed `lane.sh 4 restart`, all three gates came back green on the
  .4 binary - GEOMETRY-PASS (4 cards, 2 groups of 2), `qualgate check` **PASS** (acc-drift 1.08;
  six relay-cell drift warnings are CC-cold re-materialisations after the overnight waves, not
  numeric drift), decode-timing 27.22 ms/window inside the observed band.  No launch parameter
  changed going .2 -> .4 (the .3 streaming default is gated to single-GPU/no-batch; the two env
  names that looked absent are the python-layer `ADMIT_SHORT_FIRST` and the source-side
  `PF_WMMA`, both verified present).

### H.18 - Wave J closed: the CPU pool's own shape is already tuned, and the deep-window `tg` finally measured (11-20 23:44 -> 11-21 00:45)

- Three boot-level pool tickets, one variable per boot, deep window (~165K prompt, ~290 tokens
  decoded, fresh corpus base per arm):  **J1** `STRATA_POOL_CORES=4-84:4,88-128:4` (rebalance the
  32 workers 21/11 to match the arena's NUMA split 0.69 - the compute side of the T10 idea),
  **J2** `STRATA_POOL_SPIN_US=200000` (the default 20 ms park is shorter than a 30 ms decode
  window, so workers were sleeping and waking every window), **J3** `STRATA_NUMA_POOL=0` as a
  pricing audit of the auto per-node slices.
- All three lose or confirm the default.  With the parse bug fixed the primary metric became
  `tg = 1000 * tokens-per-window / ms-per-window` read from the engine's own decode-timing line:
  control (default env, base 12.6M) tg 74.3 / msw 30.68 / verify 26.55 / CPU-experts 3.27;
  J1 tg 71.6 (**-3.6%**, negative in *all three* paired rounds 56.8/59.7, 71.6/74.3, 78.1/81.2);
  J2 tg 70.7 (-4.8% net - msw did improve ~4% but tokens/window fell ~8%, and the arm is not
  bit-exact); J3 (first pass, pre-fix scale) verify +8%, CPU-experts 2.1x worse - the auto NUMA
  split is *earning*, not costing.  Nothing adopted; production defaults stand.
- Same-base across boots is legitimate here and worth remembering why: live chains are **never**
  written to the disk tier (H.16), and a restart clears the RAM cache, so an identical corpus base
  produces an identical *cold* prompt after every boot - the three arms' R0/R1 prompt hashes
  matched exactly, which is what makes the per-round pairing meaningful.
- First clean view of what a deep window is actually made of, post-adaptation: msw ~30 ms =
  verify ~26.5 (GPU-reach 4.2 + CPU-experts 2-3 + the rest is sm70 expert math itself)
  + draft ~2.6 + adapt wait/join ~1.7 + commit ~0.5.  The "10.5 ms/window host-CPU experts"
  number we carried was the *cold-adaptation* figure; warmed it is 2-3 ms, so the cheap
  host-side handles are mostly gone - the remaining block is the expert product on tensor-core-less
  sm70, i.e. only QPN2/tensorfold touches it, and that is the sister-tree project with its own
  negative serving record.
- Cross-boot determinism caveat, recorded honestly: temp=0 and same prompt, yet R2's *output*
  hash differed in all three arms (R0/R1 matched).  Deep-window tails drift across boots because
  adaptation changes which experts are resident, which changes fp32 accumulation order.  In-boot
  det is untouched (gate below); "det x3" is a same-boot guarantee only.
- Quality gate after seven reboots in ~90 minutes: `qualgate baseline` rewritten and `check`
  **PASS** (det x3 `bfd40ecd4692` unchanged, the same cell as the pre-Wave-J REF).  Four
  relay-cell drift warnings + acc-drift 0.911 are consistent with the disk tier being 99.5%
  full (246/250 chains) - the sizing decision stays with the operator, now with a measured
  consequence attached.
- Harness landed in the repo: `ac922/scripts/deepprobe.py` (numbers and hashes only; one process
  at a time).  Its first-pass bug - `tokens/window` and `ms/window` carry their number *before*
  the pattern - silently produced `tg: null` for a whole wave and nearly mis-killed J1 on the
  noisier `cpu` column; the <4 ms/window rule from METHODOLOGY is what kept the verdict honest.

### H.17 - TTFT fixed properly: admission short-first adopted, and the ATS mystery closed as "not our cards" (11-20 22:01-22:37)

- Method note that paid for itself twice: before spending a boot on the `--disable-mmap` arm, checked
  whether the flag even exists here.  It does not - this tree only has `--mmap-experts` /
  `STRATA_ARENA_MMAP=1`, which go the *other* way (into mapped-file mode), and we run neither.  Then
  attributed the kernel events by card: **all** 1563 `UVM GPU5 BH` bottom-half events and 781 of 782
  allocation failures are on GPU5, a card belonging to the cooperating I2V/H3 lane.  Our GPU0-3
  generated none.  So the leak/ATS thread is theirs, and what we owe is only the node-8 reserve
  shipped in H.15.
- Wave J arm 1 (`aux_cpus` request-level) closed as neutral in the same sweep of reasons: tg 66.7 vs
  65.9 (-0.3%, sign test p=1.0) inside one boot.  The adopted -13% win was the *boot-level*
  `--aux-cpus auto`; do not confuse the two tickets again.
- **Adopted: `STRATA_ADMIT_SHORT_FIRST=8192`** (in `adopted-env.sh`, so both lanes get it).  Built the
  measurement properly this time: `ttft_ab.py` fires a burst of 6 cold ~23K reads plus one chat probe
  - a lone waiter cannot show anything, because one read is already in progress and the policy only
  reorders requests still *queued* for the control lines.

  | arm | chat TTFT | worst | long-request wall |
  | --- | --- | --- | --- |
  | FIFO (upstream default) | 13.7 / 39.1 / 85.6 / 90.5 s -> median 62.4 | 90.5 s | 75.1-76.8 s |
  | short-first 8192 | 13.7 / 15.3 s -> median 14.5 | 15.3 s | 84.4 s (+10-12%) |

  Same corpus length class, disjoint slices per arm (the disk tier persists across boots, so reusing
  text would have measured resurrection instead of queueing), one process per run.
- A measurement-integrity catch worth the noise: the first CTL run wrote **two interleaved SUMMARY
  lines** into one log - two harness processes with identical arguments, i.e. twelve queued reads
  instead of six.  Every CTL number from that file is inflated.  It was renamed to
  `ttft-ctl.log.duplicate-load` and the arm re-run clean; the lesson is to count the harness's own
  processes before believing a queue-depth result (`pgrep -c` self-matches through `bash -c`, so match
  the interpreter line instead).
- Verification of the shipped state, not just the experiment: the adopting boot reports
  `STRATA_ADMIT_SHORT_FIRST=8192` in the server's own environment, GEOMETRY-PASS, and a fresh
  one-round check lands TTFT **15.7 s** - on the arm's side, not FIFO's.  Probe answer hash
  `5e71fa063c` identical across every arm and round, so the policy changes order, not output.
  `qualgate check` running against REF.json on the adopted boot.
- Cost stated plainly: long prompts give up ~10-12% of their wall clock, bounded from below by the
  30 s anti-starvation rule (a long read that waited longer counts as short).  For this box that is
  the right direction: the queue was hurting the small interactive request by up to 90 s.

### H.16 - Wave J arm 1 closed as neutral, and the cold tier's live-chain rule found in a routine sweep (11-20 21:42-21:5x)

- `strata_tune.aux_cpus=0|1`, paired **inside one boot** on production (`abreq.py t9 5`, 8K context,
  512 new tokens, alternating): tg 66.7 vs 65.9 (**-0.3%**, sign test p=1.0), wall +0.1%, draft
  acceptance unchanged.  Neutral, closed, no boot owed.  The distinction that matters: the adopted
  H2 win was the **boot-level** `--aux-cpus auto` (-13% wall); the request-level toggle is a
  different ticket and it is empty here.  Harness note: `abreq.py` carried no `Authorization` header
  and 401'd once the lane required a key - any harness pointed at production must fetch the key from
  `/proc/<engine>/environ` (now patched in place).
- Routine log sweep (the standing "analyse on every handback" rule) turned up a rule that rewrites a
  §327 open edge: `idle save ... parked conversations only: **the live one cannot be saved with a
  layer split, --peer-device or --batch**`.  Production matches both, so a chain evicted while still
  being served has no on-disk snapshot - which is exactly why that one resurrection went cold (94 s).
  It was never a write-queue race, so lowering `DISK_SYNC` is the wrong lever; the right levers are
  admission-side (do not evict a live chain) and the shape trade is one we are not making.  Added to
  DECISIONS with the test rule: let the chain park before measuring resurrection.
- Same sweep: the disk pool hit **99.5% of its 512 GiB budget on day one** (246 conversations,
  521577 MiB, ~2.07 GiB each - my own burst corpus filled it), so new parks now evict the coldest.
  Not a correctness issue; a capacity decision to revisit when the NVMe is needed for images/models.
- Host after §330: allocation-failure count frozen at 782, `allocstall_normal` still 1, node 8 idle
  holding 4.31 GiB (above the new 4.2 GiB high watermark), engine majflt flat at 5376, zero engine
  restarts inside this boot.  Only recurring warning remains `NUMA placement FAILED ... no per-role
  byte sizes`, whose cure (weighted placement, needs pack metadata) is queued behind Wave J.

### H.15 - the WAN outage was not a host outage: node 8 ran dry and UVM's ATS migration could not get a single page (11-20 21:33-21:5x)

- 20:2x-21:33 every path to the box was dead (WAN, internal, jump, BMC - 100% packet loss).  When
  ssh came back the machine had **not** rebooted (uptime 1d8h50m, same 20:08:58 boot, health fine),
  so the lane never dropped - but the routine log sweep caught what the outage window really was.
- Kernel fact, not speculation: **782** `UVM GPU5 BH: page allocation failure: order:0,
  mode:GFP_HIGHUSER_MOVABLE|__GFP_THISNODE` inside ~15 minutes, stack
  `warn_alloc <- migrate_vma_alloc_page <- uvm_migrate_pageable <- service_ats_requests <-
  uvm_gpu_service_non_replayable_fault_buffer`.  On POWER9+NVLink the GPUs reach system RAM through
  ATS/HMM, and when UVM services a non-replayable fault it must allocate the migration target **on
  that GPU's own node** - no fallback to the other socket.  At that moment node 8 had 0.85 GiB free
  while `min_free_kbytes=352 MiB` and `watermark_scale_factor=10` (0.1%) left kswapd waking far too
  late; direct reclaim in the fault path (`pgscan_direct` 1.1 M pages cumulative) still lost, on
  **order-0**.
- Sizing, which also settles the old 341 GiB mistake: node 0 = 91.8 GiB, node 8 = 127.7 GiB, so
  ~**219.5 GiB of real DIMM**; nodes 250-255 are `ZONE_DEVICE`, i.e. the six V100 HBM windows read as
  memory.  Per-process: our engine ~113.6 GiB (58.4 / 55.2 - first touch splits by *thread*, not by
  node headroom), the H3 tenant ~13.5 GiB.  The failures landed on node 8 - H3's GPU5 - but the
  pressure is joint: either lane can starve the other.  Node 8 has 36 GiB *more* capacity and still
  went dry first.
- Fix shipped to `/etc/sysctl.d/90-ac922-numa-reserve.conf`: `min_free_kbytes` 352 MiB -> **1 GiB**,
  `watermark_scale_factor` 0.1% -> **1.5%**.  Node 8 Normal watermarks went from low=150 MiB to
  **low 2.32 GiB / high 4.2 GiB** (page size here is **64 KiB** - read `/proc/zoneinfo` accordingly).
- Canaries, all clean (this is the part that made it safe to keep): allocation-failure count frozen
  at 782 (no new ones), `allocstall_normal` flat at 1, **engine majflt unchanged at 5376**, global
  pgmajfault +279 over 6 min (H3's own activity), decode-timing 24 h medians unmoved.  What got
  reclaimed is clean file cache (node 8 had 21.3 GiB inactive_file); our working set is the 37.67
  GiB page-locked expert complement plus `--ple-io ram`, which sits in `unevictable` (23.9 GiB on
  node 8) and cannot be touched.  Verified at *low* traffic - recheck under 4-way full-window.
- Discipline link: this is the live evidence behind the operator's rules 2 and 3.  It promotes the
  `--disable-mmap` single-variable arm from optional to **the discriminating experiment**: if ATS
  migration failures and the remote-read penalty both disappear with mmap off, attribution locks on
  the mmap/ATS path rather than our swap-in logic.  Queued into Wave J below.
- New tickets from this event: (a) a shared-node-8 etiquette note for the cooperating thread
  (check node 8 MemFree before a large allocation); (b) the real cure is **placement weighted by node
  headroom**, which needs per-role byte sizes in the pack - the same missing metadata behind
  `NUMA placement FAILED ... the split runs with remote reads`; (c) never reflexively `drop_caches`
  here: the 48.8 GiB PLE park file and the expert mmap live in page cache, so clearing it degrades
  us to ~2 GB/s NVMe re-reads.


- **Wave J - the CPU-side family (new, 11-20, after H.14 closed the three GPU-side gates).**  With
  `ADAPT_FETCH`, `ADAPT_OVERLAP` and `EXCHANGE_ROTATE` all locked by the layer-split geometry, the
  only untested handles on the two biggest decode-window lines (deep-window host-CPU experts
  ~10.5 ms/window, GPU-reach ~3.1 ms/window) and on the still-unexplained cold-PP p10 tail (-39%,
  which H.14 showed is *not* admission queueing) are the pool itself.  Arms, cheapest first, all
  read out of `src/kernels/cpu/pool.cpp` / `generate.cpp` so no arm is guessed at:
  0. ~~`--disable-mmap` single-variable arm~~ **reclassified by H.17 - not applicable to this lane**:
     the tree has no such flag (only `--mmap-experts` and `STRATA_ARENA_MMAP=1`, both of which *enter*
     mmap mode, and we run neither), and every one of the 1563 UVM bottom-half events / 781 allocation
     failures was on **GPU5**, a co-tenant card - our GPU0-3 produced zero.  The ATS/mmap question is
     now the H3 lane's to answer; ours stays "keep node 8's reserve healthy".
  0b. (was arm 0) `--disable-mmap` single-variable arm - **promoted by H.15 to the discriminating experiment**:
     if the ATS migration failures (runbook 330) and the remote-read penalty both disappear, the
     leak/tail attribution locks on the mmap/ATS path instead of our swap-in logic.  Cost: one extra
     copy and a slower load; benefit: the ATS migration source is off.  One boot.
  1. `--aux-cpus` **request-level** (`generate.cpp:1506`: a request's `strata_tune` `aux_cpus=0|1`
     beats the boot flag) - the only arm in this family that can be paired **within one boot**,
     which the <4 ms/window cross-boot rule demands.  Alternate with `abreq.py`-style rounds.
  2. `--pool-cores LIST` (`pool.cpp:753`, experimental, off by default; the plan comes from the real
     topology and only the pinning/count comes from the list).  Candidate lists: exclude the cores the
     co-resident I2V/H3 tenant burns, and a no-SMT-sibling list.  This is the direct test of the
     "the tail is a co-tenant CPU fight" hypothesis.  One boot per list.
  3. `STRATA_POOL_SPIN_US` (`pool.cpp:738`, the spin-before-sleep threshold).  Shorter spin = lower
     wake latency for the pool's host-expert work; longer spin = fewer sleep transitions but more CPU
     stolen from the tenant.  Pairs naturally with arm 2.  One boot per value.
  4. `STRATA_NUMA_POOL=0` as a **pricing audit**, not a gain ticket: it should hurt the `cpu` line in
     the decode-timing breakdown, which is how we put a number on the split we adopted.  Piggyback on
     another boot's corpus, no extra traffic.
  Audit-only note: `STRATA_NUMA_SPLIT=test` is a real mode in this tree (`pool.cpp:750`) - useful to
  check placement decisions without changing the production shape.


- **Caliber certification for our own ladder (new, 11-18)**: every same-length number in the panorama
  comes from `sbench.py`/`depthtg.py`, which build depth out of one repeated sentence.  That is exactly
  the mechanism that made the S1 archive points incomparable (repeated corpus feeds the suffix/ngram
  draft path, so decode looks faster than on real prose), and the chart says so while keeping them as
  hollow markers.  Our own lane has never been measured against a natural long corpus at the same
  depths.  Cheap check: one ladder at 4K / 65K / 131K, single stream, FILL vs natural, same boot.  The
  lane audit says the exposure is bounded - p90 against the adopted anchors under *real* traffic came
  out 0.88-1.06, so any inflation is of that order and concurrency pushes the other way - but a
  published panorama should state the corpus, and if the delta is over ~5% the fix is to re-mark the
  ladder on natural text, not to annotate it.  ~20 min, 4 cards.

- Monster 6-card per-card VRAM + same-moment host RAM: one 6-card boot, ~20 min, sampling only.
- S1 int8 deep-window tg on a natural long corpus: 4 cards, ~30 min; would replace the hollow
  archive points in chart panel 2 with comparable-caliber numbers.
- Monster 4/6-card deeper concurrency cells (32K x 4 and above): same window, free alongside the above.

## Upstream backlog (for the next whole-version bump; policy: bump, never cherry-pick)

- Engine main has **167 commits** after our pin (`b08a70078`). Must-verify on bump: MMQ row-id
  table OOB fixes ×3 (our `STRATA_DENSE_MMQ` path), CC parked-conversation RAM return, Qwen3.6
  per-token session memory formula, NVFP4 down-kernel overdispatch, FP16 prefill expert-offset,
  plus re-adjudication of H2 (`--aux-cpus`) and the batch tickets under new defaults.
- Sister tree (`Strata-V100`, 2× V100-PCIE): `HC_Q8` opt-in (needs explicit env, not default),
  `TOPK_COMPACT` +3.5-4% end-to-end claim vs our 0.8% micro estimate — reprice via
  responsibility-field sieve before porting; `qpn2_nvfp4.cu` now exists in-family (ticket ⑧ cost
  dropped from cross-stack port to same-family move).
- 1Cat-vLLM line: host-tier eviction design (age+reuse, three watermarks, grace window) as the
  complete version of our decay-count idea; `draft_vocab.py` production shape (extra_ids merge +
  sha256 gate) for our T3; retention-default fix as the cautionary tale matching our own
  cold/warm asymmetry finding.

## Standing lessons (violated = wasted GPU-hours, each at least once)

1. Restart-isolation beats inference: effects < 4 ms/window never judged across boots.
2. Before any "dead" verdict, audit the mechanism combination; three of our deaths were
   config-layer self-harm, not engine limits.
3. A lane answering HTTP 200 is not a working lane; probe with real graded requests.
4. Hashes, not sizes; backups, not edits-in-place; bracketed pgrep patterns, not `pkill -f x.sh`
   (it kills your own session).
5. Card count buys concurrency; stage count and expert streaming buy speed — do not spend on the
   wrong axis.
6. A crash loop inside `nvidia_open` is a whole-box event, not a lane event: the RM semaphore is global.
   Unattended respawn after a watchdog abort can take production with it (H.7).
6. Read a flag's own help before modeling it: "--prompt-cache N = N conversation checkpoints"
   (global, not per conversation) and "--batch-mtp ... not with --batch-groups" each killed a
   designed experiment before it cost GPU-hours.
7. Capacity ceilings show up as *outcome distributions*: 98.5% of evictions landing on
   `parked=16` named the knob; every average we had published hid it.
8. Cross-check the served prompt length against the payload you built.  Twice this session a probe
   was silently measuring something else (a messages array flattened to a string: ptok=52 for a
   20K transcript; four filler strings indexed by conversation: NC=8 died after four rows).
9. A green-looking gate is not a gate.  Assert the fixture reached the lane (rows > 0, HTTP 200 with
   auth) before interpreting its verdict, and check liveness with a real request -- a supervisor can
   answer `/health` 200 with its engine dead, and a 401 can be graded as `FAIL det-nondeterministic`.
10. Uptime is a measurement, not an assumption: count `engine started:` lines inside one lane boot.
    A supervisor that respawns a dead engine restores `/health` in 90 s and leaves every gate green,
    every hash reproducible and every number in the table taken across a crash we never saw.
11. When a knob's fix is "give the tightest resource more room", move the workload before shrinking
    the cache: two layers off CUDA3 bought the same headroom that reserve 1024 bought by costing
    26% of short-window prefill and a quarter of the draft acceptance.
12. A delivered repository has to build from a clean checkout of **itself**.  Our shipping `build/`
    directory had been configured against the pre-delivery source tree, so three platform fixes
    (ppc64le ggml quants, the CUDA 12.4 FP4 include, the C++20 host compiler) lived only in that tree
    and nothing noticed.  One clean `cmake` on the target architecture found all three.
13. A dev lane reproduces a *topology*, not a *regime*.  Two cards hold the full window but not the
    ship lane's expert-cache coverage, so their decode window is ~7x longer and dominated by host-CPU
    experts with swaps every window.  Sorting a decode ticket there ranks boot luck.  It is the right
    place to prove a build compiles, a deep window serves and a kernel is bit-exact; it is the wrong
    place to price a kernel.
14. A documented build command drifts from the configuration that produced the shipping binary, and
    the drift is invisible when the flags default ON.  Diff the build cache of the live build directory
    against a fresh configure key by key, and keep one flag spelled out even when it is the default if
    it is a plain variable rather than an `option()`.
15. A gate can be unreachable by construction.  `window_fully_consumed` required `ctok == gen` at
    the byte-exact window edge, where a genuinely full window guarantees `ctok < gen`; the invariant
    was fine and the assertion was decoration.  Assert the thing you mean (the window closed on the
    stream), not the first observable that looked like it.
16. Never compare a single shot to a median.  The 6-card 65K decode reading (80.3, one shot) and the
    4-card 65K reading (72.1, median of 10 rounds over two runs) differ by 11%, but the same 4-card
    protocol drifted 6.1% between its own two runs - so half of that "gap" is confidence, not speed.
    State the round count next to every deep-window number, or the chart silently ranks noise.
17. Replicate noise is not only a cross-boot thing.  Three full-window reads minutes apart **inside one
    boot, one binary** came back 126.4 / 106.3 / 107.4 - 19% spread at the window edge, which is where
    every ship claim lives.  At depth the unit of evidence is a repeat set, not a run, no matter how
    clean the boot is.
18. A sentence saying something was fixed is not a fix.  Two docs claimed the `armf5.sh` `FW`-gate bug
    was closed while the script still read `FW` from its own environment; and the server's copy of
    `fullwindow.py` predated the `held_edge` fix while the DEVLOG described the fixed behaviour.  Before
    citing a correction, grep the artifact that carries it (and diff the deployed copy against the repo).

19. A misquoted shell variable is an unplanned arm, and a good lane records it as one.  A `printf` that
    expanded `$MIN` locally wrote `CC_MIN_TOKENS=` (empty) into `lane.env`, booting the no-floor
    configuration minutes before the planned 4096 arm.  The lane answered correctly (park-all TTFT
    0.38-0.50 s, short band fixed), so the accident became the side evidence that killed upstream's
    default of `0` for a different reason - slot squatting by 200-token one-shots.  Never quietly
    re-roll a boot to erase the accident: harvest it, label it, and say which quoting rule made it.
20. A hard-reset workspace does not invalidate a build directory's *knowledge*.  After `git reset
    --hard`, an old configure fingerprint still links: gmake builds what the stale target lists contain
    and the new sources simply are not in them - the failure surfaces late, as undefined references at
    link time, after an hour of plausible-looking compiles.  Reconfigure before trusting any incremental
    build that crosses a reset, and diff the flags of the fresh configure against the live cache (a
    compiler-default flag that never appears in either, like `-mcpu`, is invisible to both).
21. A probe that drifts across boots cannot adjudicate a one-time codegen change.  The same p9
    binary, booted twice, disagreed with itself at 3 of 4 divprobe points at 110 K depth - adaptive
    residency sets differ per boot and greedy cascades amplify one near-tie - and one of those boots
    agreed with the *other arm*.  Before any cross-boot A/B, re-run the probe on the *unchanged* thing
    across two boots; for suspected-numerics changes, score against a yardstick that pins residency
    (quant_quality: expert-cache 1, adaptation off) instead of a traffic-shaped probe.
22. A build that exits 0 is not a build that contains the change.  Reconfiguring with a variable that
    expanded empty set `CMAKE_CXX_COMPILER=/bin/c++` (gcc 8.5), after which the `strata_core`/`strata`
    targets had vanished from the tree: the incremental build and the full build both reported success
    (nothing left to do, 100 %) while `build/strata` kept its old mtime and its old code.  Gate the
    artifact, never the exit code: `strings build/strata | grep -c <a literal only the new code emits>`,
    and compare the binary's mtime against the source you changed.  A degraded configure is repaired by a
    fresh configure into a new directory, then swapping the directories (lesson 20's practice) - not by
    reconfiguring the poisoned one.
23. A contention measurement taken on a lane that carries live traffic is not a measurement.  Same config,
    same protocol: 40.7 vs 145.6 tok/s aggregate, because the operator's own sessions were reading during the
    first one.  Quiet the lane, or log the concurrent request census beside every number and throw the
    contaminated samples away rather than explaining them.
24. Rebalancing the load is not removing the bottleneck.  Making the four stages' duty cycles equal
    (81/70/58/51 % -> 66/61/65/71 %) changed the wall time by nothing and the energy by +20 %, because
    the work of one request was the same 17.1 kJ either way.  Before spending GPU-hours on a distribution
    fix, prove which quantity you are moving: integrate the invariant (joules per prefill, bytes per
    window) across arms.  If it is flat, the bottleneck is upstream of the thing you are spreading.
25. Two toolchain traps that each cost a dead arm.  (a) `base64 -i f | ssh host 'base64 -d > f && run &'`:
    the `&&` list is backgrounded *inside* the remote shell, so the decode writes a zero-byte file while
    the launcher happily starts against it; `python3 -c 'ast.parse(open(f).read())'` on an empty file
    passes, and the arm fails with no error.  Upload and launch in two separate ssh invocations.
    (b) The remote syntax gate must be `python3.11 -m py_compile`, not `ast.parse` - the latter accepts
    `flush=True()` (calling a bool), which py_compile warns on and the interpreter dies on.
