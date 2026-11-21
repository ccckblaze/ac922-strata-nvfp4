# Adopted-knob ledger

Every knob below was adopted through paired same-boot A/B (`METHODOLOGY.md`) with the stated
effect, or rejected with the stated cost. "Neutral" means measured inside ±1.5% and kept only
when bit-exactness or risk justified it.

## Adopted — engine opt-ins (bit-exact, zero quality risk)

| Knob | Value | Why | Measured |
| --- | --- | --- | --- |
| `STRATA_SM70_TABLE` | 1 | three bit-exact sm70 decode kernels (upstream `docs/NVIDIA_V100.md`) | positive, kept |
| `STRATA_FUSE_DECODE` | 1 | same doc's bit-exact fuse | kept |
| `STRATA_ARGMAX_ROWS_SM70` | 1 | bit-exact argmax rows | kept |
| `STRATA_PF_WMMA` | 1 | FP16 tensor-core prompt experts | **verified inert on NVFP4 packs**; kept for A/B only |
| `STRATA_SELECT_SIMT` / `..._ALT_MIN_TOKENS` | 2 / 65536 | SIMT select path for long windows | small positive, adopted with the batchenv wave |
| `STRATA_PREFILL_STREAM_AHEAD` | 0 | do not steal decode bandwidth for prefill lookahead | direction we chose deliberately |

## Adopted — batched pipelined ring (the concurrency revolution)

| Knob | Value | Why | Measured |
| --- | --- | --- | --- |
| `--batch 6 --batch-groups 3` | (6-card) | **three groups of two rows** — the shape that makes expert streaming shared per window. Earlier "batch is a loss" verdicts were config self-sabotage: `--batch-groups auto` silently degraded to groups-of-1 (one row per window = pure pipelining overhead, agg ≈ 0.5× single stream) | aggregate decode **+74%** at 8K×6; full-window 6×245K agg 166.4 tok/s |
| `STRATA_BATCH_MTP` / `STRATA_PIPED_MTP` | 1 / 1 | MTP drafting inside the pipelined window (default pipelined path skipped drafts entirely — "mechanism self-sabotage #4") | per-stream tg preserved under batch |
| `STRATA_PIPE_ADAPT_EVERY` | 8 | pipelined batch rings never adapt the VRAM expert tier otherwise | adopted with batchenv |
| `STRATA_BATCH_SELF_COMMIT` | 0 | measured negative in paired test | kept off |
| `--aux-cpus auto` | auto | offload helper work to spare cores | wall −12.9% (64K) / −13.3% (131K), thread-only, zero quality risk |
| `--conversation-cache-mib 98304 --conversation-cache-slots 32` | slots ×2, budget ×0.75 | parked-chain capacity is the ceiling that actually bites under concurrency: on the production lane 20 live conversations relayed **0/40** continuations at `slots 16` and **40/40** at `slots 32`; median turn prefill **6574 ms → 1306 ms** (5.0×) | control arm: a *fresh* engine at `slots 16` also relayed 0/40 (6662 ms), so the win is the slot count, not a clean cache.  tg8k 55.9 (B4 band), det ×3 identical.  Budget kept below the old value so worst-case RAM falls while capacity doubles |

## Adopted — multimodal (vision), ship default on

| Knob | Value | Why | Measured |
| --- | --- | --- | --- |
| config `"vision"` block + `--vision` | on (`VISION=0` opts out) | image input restored on the final lane; the dense GGUF already declares `image-text-to-text` and carries `image_token_id` — only the tower + encoder were missing | see RUN.md gates; ship default because the text-path cost measured zero |
| `vision.gpu` | false | a GPU encoder would eat 1.2–1.4 GB of the expert cache (upstream `docs/NVFP4.md` ruling for this pack family) | VRAM delta 0 — the encoder process blocks its CUDA context and skips warm-up |
| encoder precision | FP32 + floating-point attention | GPU BF16+FA drifted cosine by 11.2% on large images (0.1% on CPU FP32); x86 AVX-512 native builds have a "wrong and slow" precedent, so the VSX path is self-validated locally, not inherited | acceptance battery passed (RUN.md gate row): idle probes 2–4 s round trip, 6/6 probes clean inside a 6×245K + 6×8K + image mixed-shape load, text lane unchanged |

## Adopted — NUMA placement (POWER9 two-island box)

| Item | What | Measured |
| --- | --- | --- |
| `numabind.so` (`LD_PRELOAD`) | mmap hook: expert arena `[0, NB_CUT)` → node0, rest → node8; PLE file mappings PREFERRED node0. `MPOL_BIND`, not PREFERRED (PREFERRED was measurably dropped on first fast-path miss) | tg +1.0–1.4% (2/2 sign tests), placement 100% on target, zero numeric-path change |
| `ple_park.sh 500` | split the 48.8 GiB PLE page cache across both islands (file pages follow the CPU that reads them; the engine's pinned main thread otherwise parks everything on node0) | fixes `NUMA placement FAILED ... remote reads`; prerequisite for the 6-card lane |

## Adopted — model/config baseline

`--spec 6 --spec-min-p 0.7` (MTP depth; acceptance 0.738–0.75 measured), `--kv int8
--kv-resident 32768`, `--max-context 262144`, `--ple-io ram --ple-row-cache 1048576`,
`--prompt-cache 6`, `--resident-budget-gib 64`, `--vram-reserve-mib 700`,
`--prefill auto`, `--expert-cache auto`, `--layer-split auto`, expert-profile = the pack's
learned file (adopted below), `--trim-stage-weights` (adopted below).

## Rejected (with the price we measured — do not re-litigate without new evidence)

| Ticket | Verdict | Evidence |
| --- | --- | --- |
| TurboQuant KV (k8v4) | **dead**: RAM −27% bought with PP −7–10%; quality not locally measurable | paired wave; re-priced once more after cold-cache PP landed, still negative exchange rate |
| Batched lockstep (old shape) | dead — **but see above**: the original verdict was taken on groups-of-1 and is partially overturned by correct shape | §"batch-groups auto" audit |
| DP `replicas: 3` | shelved: conflicts with shared expert arena (`--resident-budget-gib` is ARENA_INCOMPATIBLE); buys streams, costs 262K full-window per replica.  It is **not** a prefill accelerator either: cold PP is a throughput floor, so a second engine divides the same ~2500 tok/s | read-only audit of `serve/replicas.py` + paired cold bursts (11-20) |
| QPN2 kernel port (ticket ⑧) | cancelled: three independent reasons incl. sister tree already has same-family source; M≥17 windows never materialize at our stage counts | audit, no GPU burned |
| `--topk` variants, `TOPK_COMPACT` port | micro-benchmark says ~0.8%/window; sister claims 3.5–4% end-to-end — unresolved, parked behind a responsibility-field sieve before any wave | measured micro |
| `STRATA_SPLIT_OWN=1`, `--prefill-head-alias`, `--peer-prefill-rows` | all buy PP with tg/VRAM — direction opposite to our objective | static + spot checks |
| Reference lane S1 (IQ3_XXS Strata 0.1.41, accuracy replay) | **closed for accuracy**: the 2026-11-12 performance lane (port 8090, minimal flags, `STRATA_FETCH_PARTNER=0`) did work and its curves are published in `PERFORMANCE.md`; the later 0.1.41 accuracy-lane recreation (port 8092, extended flag set) never produced usable output — nine arms across two campaigns, death modes: verify timeout (#267), immediate-EOS, `decode -1` watchdog | full ledger in DEVLOG |
| Reference lane M1 (llama.cpp Monster IQ3_XXS) | **closed for accuracy**: no usable endpoint under our grading shapes (chat stops after 1 token; bare completion is OOD); performance data remains valid and published | three repair attempts, all falsified |
| `STRATA_ADAPT_FETCH` (author-claimed decode +10%) | **geometry-dead on a layer split**, and "tune it to 2" is a no-op - our fork's default is already 2 | boot self-report `STRATA_ADAPT_FETCH is off (a layer split)`; gate `generate.cpp:15075` (`multi_gpu \|\| !stages.empty()`), plus our second matching reason "the resident RAM mode" |
| `STRATA_EXCHANGE_ROTATE=1` | rejected: rotation and the NUMA cpu pool are **mutually exclusive** (slots must be fixed for the split), so it would trade away the pool that carries our deep-window host-CPU work | boot `NUMA pool off: ... moves experts between slots`; lane guard raises `GEOMETRY-FAIL`; shallow probe cpu 2.18 -> 2.59 (+19%), tg -2.6% |
| `STRATA_COALESCE_PREFILL=4` | true negative for **long** cold prompts: coalescing removes per-read overhead, and we have none - 4-way aggregate already equals one stream (2474 vs 2518 tok/s), `tail_ratio` 1.00 | mechanism proven firing (`coalesce: 3 waiting prompt(s) (149101 tokens)` after raising `STRATA_COALESCE_TOKENS`, whose 16384 default silently excludes long prompts); paired bursts, runbook §329 |

## Adopted — serving-layer parsing policy (outer parsing, no numeric path)

`"undeclared_tool_calls": "call"` in `configs/strata-nvfp4-final.json` (upstream #4622; the serve
layer's default is `"text"`). Deep-context requests sometimes emit a well-formed call of a tool the
request did not declare. Under the default that call stays in the stream as assistant *text*, so a
client that replays history pastes the leaked call markup back into the next prompt — the drift
feeds on itself, and the turn never reaches the tool channel that would correct it. `"call"`
returns it as a tool call instead, which lets the client answer with the names it really has.

- Scope: outer parsing only (`serve/server.py` reads the config; the engine never sees it), so the
  numeric path is untouched — verified by the two-tier gate (det x3 / cold-cell bitwise / relay
  agreement) rather than asserted.
- Only a span the parser reads as a whole call is returned; prose and quoted examples stay text.
- Both lanes inherit it: the production launcher and the dev lane derive from this one file.

## Shape sweep verdict (2026-11-17): the shipped window shape stays

The overnight family (11 arms, `PERFORMANCE.md` §4b) says the shipped `--batch 6 --batch-groups 3`
is the best point on this box at short and mid depth, and that aggregate decode here is limited by
**rows per group**, not by card count: 2 rows/group 228 -> 3 rows/group 140 -> one window of 6 rows
92. Slots above the card count cost expert-cache VRAM and lose. Nothing adopted.

Two candidates stay priced rather than adopted:

- **`--trim-stage-weights` + explicit `--layer_split 8,16,24,32,40`.** The only lever that lifted a
  shape (same 2-rows shape: 140 -> 202; shipped shape: 228 -> 234, and at 131K 135 -> 154).
  It was blocked by an unexplained symptom: on the trim arms the *solo* panel point returned a
  truncated generation (`ctok` 18 of 256 asked on A11/A2/A7). **That block is now withdrawn** - see
  the D1 reclassification below - so trim goes back on the adoption list, to be re-gated rather than
  re-litigated.
- **`--batch-max-context 65536`** (elastic slots): requests over ~64K leave the slots and run on the
  solo path, where each gets single-stream speed (~75 tok/s at 98K instead of 21-24). It is a
  latency option for deep workloads, not a throughput one - aggregate falls from 90.6 to 63 at
  131K because the deep streams then take turns. Keep as an opt-in mode, documented, unadopted.

Caliber note for anyone reading the earlier tables: the deep single-stream figures published before
this sweep (tg 72.5 at 261,494 ptok) were **64-token generations** - they measure the drafting
ramp, not steady decode. Steady deep decode at 131K with a long generation is ~72-85 solo and
21-24 per stream when six of them share the lane. Both calibers are now produced by
`ac922/eval/aggbench.py`, which refuses to score a stream that came back short.


## Quality gate: the 4-card REF did not survive the 6-card switch (2026-11-17)

`qualgate.sh check` on the 6-card ship config returned **FAIL** (`relay-cell-agreement 0/8`,
`acc-drift 0.743`). It was not a numeric regression, and both signals that produced it are known
non-numeric artifacts. Evidence, in order of strength:

- **`det` reproduced the 4-card REF hash exactly** (`bfd40ecd4692`, three runs, same hash). The cold
  path is bit-stable, which is the one anchor this lane can hold to a bit. A real numeric change -
  the `undeclared_tool_calls` edit, the 4->6 card geometry - would have moved it. It did not.
- **`reused` depth was unchanged** (110539 / 54034), so the relay cells were still comparable in
  depth; what moved is which of them replay bit-identically. 4-card and 6-card differ in stage count
  and cut positions, so resident/park boundaries - and therefore what a replay restores versus
  recomputes - differ. On a path whose own reproducibility floor is ~2/8 cells, asking a 6-stage lane
  to reproduce a 4-stage lane's relay hashes asks for something the lane cannot provide.
- **`acc-drift` was a cold-draft-table artifact.** The engine booted at 07:29:46 and the gate ran at
  07:30, so the suffix-draft model was still cold: 24/35 = 0.686. The same boot four minutes later,
  after the fixtures themselves had served the lane, measured 24/26 = 0.9231. The sentinel compares
  against a REF written on a *warmed* lane, which is exactly the history sensitivity documented in
  `eval/qualgate.sh`. That ratio is what tripped 0.75.

Action taken: the 4-card REF is preserved as `REF-4card-20261116.json` (nothing deleted), and a new
REF was written on the ship config (`ship6x3b-1117`). A self-anchored `check` on the same boot then
returned **QUALGATE-PASS** with `relay-cell-agreement 4/8 = 0.50` (comfortably above the ~2/8 floor)
and `acc-drift-ok 1.0`, while `det` stayed bit-identical to the 4-card value.

Standing rule for future geometry changes: **a REF is per-geometry, not per-engine.** Any change to
card count, `--layer_split`, or cut positions invalidates relay cells and requires re-anchoring, with
`det` bitwise equality - not the relay rate - as the regression test. Keep the old REF beside the new
one so the cold path can still be traced across geometries.

## D1 reclassified: "solo requests truncate" was a broken ruler, not a lane fault (2026-11-17)

While testing whether the lane could hold six full-window streams, `aggbench` reported
`dropped_streams: 6` at 234K with generations of 409-736 against 1024 asked - which reads exactly
like a lane that cannot survive deep concurrency. It could not. The probe `eval/fullwinprobe.py`
separates the two candidate causes by reading the engine's own `usage.completion_tokens` and
`finish_reason` instead of counting SSE deltas, and the answer was unambiguous:

    n=6 depth=234300 gen=1024  ctok_use [714, 322, 401, 1024, 1024, 698]
                               finish   [stop, stop, stop, length, length, stop]
                               tok_per_chunk [1.00 ... 1.01]  sum_tg 121.8  span 52.9s

`tok_per_chunk` is 1.0, so the streams really did deliver only 322-714 tokens - and they stopped
because **the model ended its own turn** (`finish_reason=stop`) on a degenerate repeated-text
fixture at temperature 0, not because the lane refused anything. All six streams decoded in one
overlapping window at 15.4-26.7 tok/s each.

Two consequences, both measured rather than argued:

- **D1 is closed as not-a-bug.** The `ctok 18 of 256` solo truncation was attributed to
  `--trim-stage-weights`, but re-reading the sweep with this in mind shows the same signature on arms
  that have no trim at all - A4 `ctok` 4, A5 `ctok` 22, A10 `ctok` 50, A9 `ctok` 154, A2 `ctok` 18 -
  so it never discriminated trim from anything else. Trim is the only lever that ever lifted a shape
  here (228 -> 234 at 8K, 135 -> 154 at 131K), and it goes back on the list; it still has to pass the
  normal gates, it just no longer carries a defect.
- **Deep-concurrency figures taken with the old guard undercount streams.** `A2_6x2@262000 n=4`
  (`ctok_min` 128) and every 131K row (`ctok_min` 192) had at least one natural stop below the
  `>= 0.9 * gen` bar, so those aggregates were scored on fewer than the nominal number of streams.
  Direction of error is downward, so the shape ranking (rows/group is the axis, slots > cards loses)
  stands, but the absolute deep numbers should be re-read under the fixed guard before being quoted.

The guard now keeps a stream when `finish_reason == "stop"` regardless of length, reports
`early_stop` separately from `dropped_streams`, and `dropped_streams` means only "the lane failed
this stream" (error, or no decode window at all). `calibrate()` gained a one-unit refinement pass,
because the coarse 5% band parked at 234,300 for a 260,000 target - i.e. it would have answered a
"can we do 6 x 262K" question about 6 x 234K.

## Adopted: `--conversation-cache-mib 49152` (was 98304) — deep aggregate +24% (2026-11-17)

The trigger was a user question: the published figure showed ~220 aggregate and the lane now
reports under 150. Three separate things were conflated, and each was measured rather than argued.

1. **The 220 was the 8K point, not a full-window point.** Re-measured warm on the same shape:
   8K x 6 = **223.1 / 224.2** (two reps, wall 206.2 / 205.3) against the published 218.2 - the short
   window never regressed.
2. **The deep window had genuinely fallen, and the cause was memory state, not code.** At 245K x 6
   (gen 512, the same generation length as the original record - checked, since a shorter gen could
   have explained it and did not: 256 -> 130.4, 512 -> 130.7):

   | state | CC | MemAvailable | wall_tg | sum_tg | per-stream med |
   | --- | --- | ---: | ---: | ---: | ---: |
   | 2026-11-16 original | 96 GiB | 58.3 GiB | 100.85 | - | 23.35 |
   | 2026-11-17 before | 96 GiB | **10.9 GiB** | 89.3 | 130.7 | 20.6 |
   | **2026-11-17 after** | **48 GiB** | 49.1 GiB | **115.1 / 106.1** | **143.5 / 144.9** | 22.6 / 23.8 |

   A day of benchmarking had filled the conversation cache to both of its caps (`parked=32` = the
   slot ceiling, `bytes=79.5 GiB`, `evictions=99`), and parked conversations are exactly the memory
   the expert page cache wants. Halving CC gave the deep window back ~31 GiB and it recovered past
   the original figure; the short window is unchanged within drift.
3. **Capacity rule holds**: 48 GiB / ~4.8 GiB per full-window conversation = ~10 conversations, so
   the "N cards <-> N full-window streams" rule keeps 6 live plus slack. If a deployment wants more
   parked sessions than that, raise CC and accept the deep-window cost - the trade is now priced.

Gate: `QUALGATE-PASS` on the new CC (`det` bit-identical to REF `bfd40ecd4692`, relay 2/8 which is
the lane's documented floor, `acc-drift-ok 0.993`). A CC size is a cache capacity, not a numeric
path, and the gate confirms that rather than assuming it.

## Gate defect fixed: the det fixture graded a cold first run

`qualgate check` on the fresh CC boot returned **FAIL det-nondeterministic** - the first time that
anchor ever moved. It was the fixture, not the lane:

    run 0: pp=2550 tok/s (cold prefill)  -> hash 3acf024dbb26
    run 1: pp=51                         -> hash bfd40ecd4692  == REF
    run 2: pp=53                         -> hash bfd40ecd4692  == REF

The first request of a boot must prefill the fixture, and cold prefill and cached replay are not the
same numeric path on this lane (the same asymmetry as §70.4's residency rotation). Grading run 0
against a REF written warm labels a deterministic lane nondeterministic. `eval/sbench.py det` now
issues one **ungraded warm request** before the three graded ones; the re-run passed. Anyone
re-baselining after a boot gets the same treatment automatically.

## Adopted: expert-profile = learned + `--trim-stage-weights` (C-phase, 2026-11-17)

The last two residency tickets, settled with boot-level paired A/B (`eval/cphase.py`, arms
CTL×5, learned-profile×4, trim×3, combo×2; `/data/work/note_c17`). Deep-window single-shot tg
carries ±15-25% boot-to-boot noise, so verdicts read the family medians against the CTL envelope,
not single runs:

| target | CTL med | learned | trim | combo (adopted) |
| --- | ---: | ---: | ---: | ---: |
| 8K     | 57.1 | 74.9 | 73.5 | 77.9 |
| 64K    | 64.8 | 69.2 | 75.4 | 76.5 |
| 131K   | 58.5 | 68.2 | 79.0 | 73.8 |
| 196K   | 61.1 | 65.8 | 81.0 | 73.5 |
| 249K   | 69.5 | 72.4 | 79.8 | 79.4 |
| 8K×6 per-stream | 53.9 | 55.0 | 57.6 | 58.8 |
| 8K×6 cold-sum   | 18.5 | 18.6 | 20.6 | 20.7 |

- Both tickets clear the CTL envelope everywhere except learned-profile at 196K/249K (noise).
  The combo wins short and concurrent; trim alone keeps the 131K/196K edge (+7-10% over the combo)
  - the combo is adopted because short-window and concurrency are the shipped objective, and the
  combo still beats CTL everywhere.
- `--adapt-every 0` (PT1) measured neutral against the learned profile alone: the profile's gain
  does not require freezing adaptation. Default `--adapt-every` stays.
- The learned profile was always a shipped-not-live artifact (`expert-profile-learned.bin`,
  sha `592b6064...`, produced 11-14): this adopts the file that was already trained for this pack.
- Gates: `det` × 3 bit-identical in every arm and on the adopted boot (REF held at
  `bfd40ecd4692`); `acc-drift-ok` 0.993-1.003; teacher-forced yardstick (`qc/Q-adopt17.tsv`,
  2,997 positions): top-1 99.07%, KL 0.0012 vs the Q2 base - the expected signature of a
  residency ticket, same family as HC_Q8's "answers differ slightly", not a quantization change.
- One gate false alarm worth the rule: the first trim arm's cross-boot `relay-agreement-collapse
  0.0` FAIL was boot noise - the same default config scores 0/8..2/8 against its own REF across
  boots (see `qualgate` history), while the adopted boot re-scored **8/8 = 1.00** against a
  same-config REF. Cross-boot relay cells carry no verdict; same-config reproducibility does.
- Full-window acceptance on the adopted config: 6 streams × exactly 261,632 prompt + 512 gen,
  6/6 `prompt_exact`, 5/6 finish `length` at the window edge (262,136), one stream EOS-early at
  473 tokens (user content, not a lane fault); `sum_tg 155.0`, span 26.3 s - up from 148.5 pre-C.

Config: `ac922/configs/strata-nvfp4-final.json` (`--expert-profile .../expert-profile-learned.bin`
rewritten to the repo copy, `--trim-stage-weights`); profile file committed under `data/`.

## Adopted: the ship geometry is 4-card, and the launcher derives shape from card count (2026-11-17)

Cards 4/5 are needed by the dev/iteration lane, so the published ship state is the 4-card geometry.
Switching also removed a real footgun: the reference config carried the 6-card batch pair, so a
4-card boot was correct only if the operator remembered to pass four variables.

- Config now ships `gpu [0,1,2,3]` with `--batch 4 --batch-groups 2`; the launcher derives the batch
  pair and `NB_CUT` from the card count (4 → 4/2 with `NB_CUT=0.69`, 6 → 6/3 with `NB_CUT=0.5`),
  so `bash ac922/scripts/strata-nvfp4-run.sh start` alone boots production and a geometry switch is
  one variable. Verified offline against both shapes without disturbing the live lane:
  `auto batch shape for 4 cards: 4/2`, `auto batch shape for 6 cards: 6/3`.
- Geometry evidence on the live boot: `layer split: layers 0-8 (CUDA0), 9-21 (CUDA1), 22-34
  (CUDA2), 35-47 (CUDA3)`, `batch (pipelined, 2 groups of 2)`, `STRATA_PIPED_MTP ... 1 MTP proposal
  per slot each window`, expert caches 4100/4097/3339 slots + CUDA0 dense stage, resident pinned
  57.65 GiB, VRAM 14.6/14.5/14.5/14.9 GiB, cards 4/5 at 225/13 MiB (free).
- Per-geometry REF rule from the 6-card switch section, now exercised in the other direction: warm
  up, preserve the old anchor (`REF-6card-adopted-20261117.json`), `qualgate.sh baseline`, then
  `check` in the same boot. Result: `det` x3 `bfd40ecd4692` — **bit-identical across the geometry
  switch**, `acc-drift-ok 1.003`, `QUALGATE-PASS`, relay 3/8 (inside the same-config replay noise
  band established today, which is why the rate is reported and never used as the verdict).
- Numbers (first boot, same harnesses as the 6-card adopted state): single stream tg 77.3@8K,
  71.8@54K, 73.0@65K, 69.1@110K, 72.0@165K, 70.0@210K; `aggbench` 8K×4 sum_tg 135.6 (per 33.7),
  245K×4 sum_tg 100.2 (per 24.4); full window 4×261,632+512 all exact at 262,136 with sum_tg 108.1.
  A second 4-card boot plus two 6-card boots later the same day moved the published cells to
  medians (8K tg 77.3 → 71.7, because the second boot measured 66.1); see the next two sections and
  `PERFORMANCE.md` §6d for the full table and caliber caveats.

## Closed: the adopted 6-card aggregate gap, and why medians are now the house rule (2026-11-17)

- The two cells left open above are now measured with the same ruler: `aggbench` 8K×6 **240.1**
  sum_tg (per-stream 39.7, wall 211.6), 245K×6 **135.2** (per 21.4, wall 93.6), true full window
  6×261,632+512 all exact at 262,136 with sum_tg **136.2** in the second boot (155.0 in the first),
  plus the missing 6-card 65K single-stream point (tg 80.3). All of it on the adopted config, and
  the 6-card `det` fixture still hashes `bfd40ecd4692` - adoption is quality-neutral at both
  geometries.
- Card-count scaling on the adopted config, same harness: 8K aggregate **+72%** (139.3 → 240.1),
  245K **+35%** (100.2 → 135.2), full window **+34%** (108.3 → 145.6), single stream only
  **+10-14%**. The "cards buy streams, not speed" rule now holds on both geometries of the ship
  state, not just the pre-adopt one.
- The 166.4 for 245K×6 was never a lost regression: it came from the live-lane harness
  (`out/waves/baseline_final.json`, `agg_steady`) while `aggbench` is a synthetic burst. Different
  ruler, different number; §6d states the pair that is comparable (100.2 vs 135.2).
- **House rule going forward: published numbers are the median of two boots.** A deep-window single
  run carries ±15-25% boot noise (4-card 8K tg 77.3 then 66.1; 6-card full window 155.0 then 136.2),
  so a one-boot cell is written as `n=1` in the table rather than quietly promoted to a headline.

## Adopted: `lane.sh` / `lane4.sh` / `lane6.sh` as the geometry entry point (2026-11-17)

- Why the raw launcher was not enough: the repo root carries no `numabind.so`, so calling
  `ac922/scripts/strata-nvfp4-run.sh start` directly boots a lane **without** `LD_PRELOAD`, which
  is healthy, answers requests, and is simply not comparable to the ship state. `lane.sh` pins the
  machine layout (`lane.env`), sets the preload, waits for `/health`, and then proves the geometry
  before returning; `lane4.sh` / `lane6.sh` are the two one-word entry points.
- Two verdicts we believed until the machine disproved both, recorded so nobody re-adds them:
  1. *Read the last `batch (pipelined, N groups of M)` line in the engine log.* The engine log is
     cumulative across boots, so after a 6-card → 4-card switch the last such line was still the
     6-card lane's own runtime statistics. The gate now reads this boot only: it records a byte
     offset at start (`/tmp/strata-nvfp4-lane.state`) and greps the startup marker
     `STRATA_PIPED_MTP: N pipelined groups of 2 slots` inside that slice.
  2. *Gate on `FileExpertSource: WARNING: NUMA placement FAILED: this pack has no per-role byte
     sizes ... (the split runs with remote reads)`.* Every boot prints it, including the anchored
     ship state - it is a pack-level property (the NVFP4 pack carries no per-role byte sizes), so
     gating on it fails a correct lane. It is printed as a note; the real placement gates are
     `LD_PRELOAD` + `CUDA_VISIBLE_DEVICES` read out of `/proc/<pid>/environ`, the split stage count,
     and one `cpu pool: NUMA pool on` line.
- Consequence left on the books, not on the critical path: the first-touch warning is real even if
  it is not a gate defect - expert pages are still read remotely the first time. Closing it needs a
  full-pack first-touch (or per-role sizes in the pack), which is the pending remote-read ticket,
  not a lane.sh problem.

## Fixed: the shipped lane was dying in production — `mtp: round capture: out of memory` (2026-11-17)

- **Symptom.** 27 minutes after a clean `lane4.sh start`, with `/health` answering normally, the
  engine took `mtp: round capture: out of memory` on an ordinary ~8.5K-token/32-token request,
  exited 1, and the serve supervisor started it again. Two things that looked fine were not: the
  in-flight request got `HTTP 400`, and `lane.sh status` afterwards printed `GEOMETRY-FAIL` because
  the boot slice (one recorded byte offset) then spanned **two engine processes** and counted the
  NUMA pool line twice. A respawn is invisible to every gate we had.
- **Margin evidence.** Before the fix, free VRAM per card was 442/572/565/**149 MiB** and the engine
  had already written its own diagnosis: `verify: no VRAM for a new window graph: freed the graphs
  of 1 older batch slot layouts, 2 kept (a larger --vram-reserve-mib keeps more)`. CUDA3 is the
  tight stage because it carries 13 layers **plus** the head **plus** the 837 MiB MTP draft layer.
- **First remedy (reserve 700 → 1024) bought stability and paid with speed.** No respawn under the
  same gate workload (`boots=1`), `det` still the anchor hash, but the expert/draft caches shrank:
  MTP acceptance in the gate fell 0.9231 → 0.747 (`FAIL acc-drift`), the deep ladder lost 6-11% tg,
  and full-window 4-way aggregate fell 108.5 → 99.4. Rejected as a ship shape: the acceptance
  sentinel exists precisely to catch this kind of "the numeric path is the same but the machine
  behaves differently" change.
- **Adopted remedy: rebalance the geometry, then buy only a little headroom.** `layer_split
  9,24,37` (CUDA3 goes from 13 layers to 11; CUDA1/CUDA2 absorb the two) with
  `--vram-reserve-mib 832`. Measured on the shipped lane over **two boots**:
  `det` ×3 = the anchor hash and `QUALGATE-PASS` with `acc-drift-ok 1.0` in *both*, and **no respawn**
  (`boots=1`) through ~40 min of mixed load including the very gate fixture that killed the old shape.
  Full-window 4-way came back **byte-exact with all four streams finishing `length` at
  prompt+gen = 262,136 in both boots** (span 23.6 / 23.0 s, `sum_tg` 105.8 / 108.0 → median 106.9
  against the superseded shape's 108.3), and the 3-way floor is 85.7 with 3/3 `finish=length`.
  Ladder tg medians 69.9/71.5/72.0/66.0/66.8 at 8K/54K/110K/165K/210K vs the old shape's
  71.7/71.6/70.1/73.3/71.2 — short and mid depths unchanged, the two deepest single-stream points
  6-10% lower; aggregate cells barely moved (8K×4 139.3 → 138.6, 245K×4 100.2 → 103.5).
  Warm 262K single-stream decode improved 55.9 → 59.2.
- **Why this is not a quality-for-speed trade** (the red line): no kernel, no quantization, no
  acceptance rule and no draft depth changed. Layers moved between cards, and the expert cache left
  a slightly bigger graph reserve. The greedy output hash is identical to the anchor, which is the
  strongest statement this machine can make about the numeric path.
- **Ship invariant, stated as a rule (user, 2026-11-17): 满窗是前提.** The shipped 4-card lane must
  serve concurrent streams at the byte-exact full window (262,136). If 4-way cannot be held, the
  ship shape drops to **3-way full window**; the window itself never drops. `eval/fullwindow.py` is
  the arbiter and reports `prompt_tokens + completion == 262,144` exactly — "98.7% of the window"
  is not a pass, and neither is a run where streams end early on their own.
- **Gate defect fixed with it:** `lane.sh` now anchors the boot slice on the newest per-process
  `engine started:` banner instead of trusting the recorded offset, and reports
  `WARN the engine has restarted N times inside this lane boot` with the logged cause. Without this,
  a respawned lane passes as healthy.

## Adopted: the build pins gcc-toolset-12 as nvcc's host compiler (2026-11-17)

- **Decision.** The documented build command names the toolset explicitly for C, C++ **and**
  `-DCMAKE_CUDA_HOST_COMPILER`. `ac922/BUILD.md` carries the exact command verified on this box
  (`BUILD-EXIT=0`, 0 errors).
- **Why.** The engine is C++20. nvcc's host compiler otherwise defaults to the box's system GCC 8.5,
  which dies on the engine's own sources (`namespace "std" has no member "is_same_v"`) and on
  libstdc++ 8's `<random>` inline variables (~100 `redefinition of ...`). This is not a warning-level
  difference: the build simply does not exist without the pin.
- **Trap worth knowing.** `CMAKE_CUDA_HOST_COMPILER` is only honoured when the CUDA compiler
  identification is (re)computed. A build directory that already saw GCC 8.5 keeps it silently, so the
  fix looks like it does nothing; use a fresh build directory (or `PATH=/opt/rh/gcc-toolset-12/root/usr/bin:$PATH`
  before the first configure).

## Archived under the quality red line: `STRATA_HC_Q8` (2026-11-17)

- **What it buys.** Reading the hyper-connection projections as Q8_0 instead of BF16 buys VRAM
  (~139 expert slots per card in a sister measurement) and ~1.5% decode.
- **Why it is out.** It is a coarser read of live weights, so it is a quality-for-speed trade by
  definition: the engine itself warns `answers can differ in near-tie tokens`, and the sister's own
  numbers put it at KL 0.0003-0.0010 over its noise floor. The standing red line (user) rules out
  buying speed with output quality, so no GPU hours were spent on an A/B; the flag stays unset.
- **Audit closure.** The earlier "canonical HC skip may already be active" question is closed: on
  NVIDIA layer splits it requires `STRATA_HC_Q8=1` explicitly (unset = off outside gfx12/gfx1151), so
  the shipping lane reads BF16 today.

## Not shipped: the bring-up tree's two experiment patches (2026-11-17)

- **Inventory.** `/data/work/strata-nvfp4-src` (the pre-delivery tree) carries two opt-in patches that
  the repo does not: `STRATA_NUMA_ROLES` (derive per-role byte sizes for `experts.bin` blobs so NUMA
  placement can split gate/up/down across nodes) and `STRATA_DEDUP` (same-moment prefix dedup in the
  server: a follower with a matching long-prompt digest waits for the leader's first token).
- **Decision: keep them out for now.** Neither is set in the shipping lane's environment, so shipping
  without them changes no behaviour, and the repo states only mechanisms this box has measured. When
  either mechanism earns its numbers (the NUMA one belongs with the remote-read remediation, the dedup
  one with the same-moment-burst work), it lands as its own commit with its own A/B - not as cargo.

## Kept but not enabled: `STRATA_TOPK_COMPACT` (ported 2026-11-17, measured the same evening)

- **What it is.** An opt-in CUDA narrowing of the QSA long-row top-k (one probe + one weighted
  histogram over a monotone 12-bit key picks the bucket holding the width-th cell; only that bucket is
  refined with the byte-radix rule, and the emit keeps the wide kernel's ascending-cell tie rule).
  Ported from `Strata-V100 53a36c9b`, upstream `1CatAI/1Cat-vLLM#1171`.
- **Quality.** Exact, and checkable: `qsa_topk_parity --selftest` passes with the flag off **and** on,
  with identical ids on continuous / 64-level / equal-score / signed-zero+infinity / NaN / spread-tie
  rows at 262,144, 300,001 and 524,288 tokens. A ctest arm runs the selftest with the flag set. Scores
  stay fp32, so nothing about the selection semantics moves.
- **Value on this box.** From the matched harness lines, not from a serving run: the widest case
  (524,288 tokens, 64 queries) costs 1.185 ms (reference) / 0.475 ms (this tree's default dispatch) /
  0.351 ms (compact). Per query 0.0013-0.0019 ms saved; a decode window carries ~2 queries per layer
  over 48 layers → **0.13-0.19 ms per window**, i.e. **+0.4-0.6%** of the ship lane's ~30 ms window at
  tg 66. Our own independent sieve had said +0.8%. Both sit under the 1.5% adoption bar.
- **Decision.** Keep the code and its test (it is exact, and it is the cheaper kernel at every shape),
  leave the flag unset in `ac922/scripts/adopted-env.sh`, and revisit only if the ship lane's own
  window decomposition ever names the selector. A sister fork's +3.5-4% does not transfer: their
  selector call shape is not this engine's, and this tree's default dispatch was already 2.3-2.7x the
  reference before the narrowing.
- **Not measured here.** A 2-card dev lane's window is ~200 ms and host-CPU dominated, so the flag's
  effect is far below its noise; see `DEVLOG.md` H.1 for the two-arm table.

## Recon, not adopted: `serve/replicas.py` (data-parallel replicas) on this box (2026-11-17)

- **What it offers.** `replicas: N` runs N independent engines behind one HTTP front, routing each
  request to the replica with the cheapest re-read. It buys real concurrency (N pipelines), which the
  single engine's slots cannot: `two replicas run two pipelines`, and its router already tracks
  64-token block hashes per replica plus an optional shared parking pool.
- **Why it is not the answer to concurrency here.** Three walls, in order of hardness:
  1. **RAM, not the arena.** Each engine keeps its own expert copy (~65-72 GiB); the shared-arena
     option exists (`replica_shared_arena` / memfd) but `--resident-budget-gib` is in
     `ARENA_INCOMPATIBLE`, and dropping that flag is exactly what un-locks the expert set from RAM into
     reclaimable page cache - the failure class this box was stabilised against.
  2. **The window invariant.** Our ship rule is one full window (262,136) per card group sized to hold
     it. A 2-card replica does not hold a full window's resident KV; and hosting `replicas x 2` full
     windows needs ~57.7 GiB of host KV per chain, so 3 replicas x 2 chains ≈ 346 GiB against a
     256 GiB box. The KV host tier, not the expert arena, is what makes replicas-of-full-windows
     unreachable.
  3. **Stage count cuts both ways.** Fewer cards per replica means fewer pipeline stages (the sister
     box's 2-card tg ≈ our 6-card tg), but our own 4→6-card aggregate moved only +7.5%, so there is no
     stage-count windfall waiting to pay for the lost window.
- **Where it could still matter.** A *mixed* fleet: the ship lane keeps the full-window invariant, and
  a separate short-window lane serves small requests on its own cards - which is exactly what
  `ac922/scripts/devlane.sh` already is, without pretending to be one server. Adopting `replicas`
  would only be worth its risk if one front door and one config were a hard requirement.

## Adopted: the shipping binary must be built from this repository (2026-11-17 evening)

- **Problem.** The live lane ran `build/strata`, but `build/CMakeCache.txt` records
  `CMAKE_HOME_DIRECTORY=/data/work/strata-nvfp4-src` - the binary predates the delivery repo, so the
  published sources had never produced the thing we measure and publish.
- **Decision.** Build the lane from this tree into a separate directory while the old one keeps
  serving, then switch by renaming (`build` -> `build.pre-repo`, `build-new` -> `build`), which keeps
  rollback a second `mv`. Same for `build-vision`.
- **Verification of the claim before switching.** `CFG-EXIT=0` / `BUILD-EXIT=0` / 0 errors;
  `strata 0.1.59`; the 10 cache keys that define the engine (`CMAKE_BUILD_TYPE`,
  `CMAKE_CUDA_ARCHITECTURES`, the seven `STRATA_*` build flags, `STRATA_BUILD_TESTS`) are identical to
  the production cache; kernel self-checks on a free card pass (`qsa_topk_parity` PASS, `gr_parity` 0
  failures, `dequant_wide_parity` 360 cases bitwise); `build-vision-new` is CUDA-free with the
  `Qwen4Exp` mtmd symbols present.
- **Why the flags are now spelled out in `BUILD.md`.** `STRATA_EXPERIMENTAL_SM60` is read as a plain
  variable, not declared with `option()`, so a "clean" configure that omits it produces a quieter
  binary that still builds and still prints 0.1.59 - the worst kind of silent divergence.
- **Not changed by this decision.** Nothing about geometry, exports, or model assets; the switch is
  binary provenance only, and it still has to clear the full gate set (`qualgate check`, acc-drift,
  det x3, 4x full window, GEOMETRY-PASS) before the numbers in `PERFORMANCE.md` stay valid.

## Adopted: the conversation-disk cold tier (RAM floor + on-disk parking), with a geometry caveat (2026-11-20)

- Shipped as pure launcher/env on top of upstream's `--conversation-disk-*` five flags
  (`runbook §327`): `DISK_MIB=524288`, `DISK_TTL=0`, `DISK_PERSIST=1`, `DISK_SYNC=30`,
  `CC_MIN_TOKENS=32768`, plus `--memory-limit-mib 262144` and a dynamic
  `--conversation-cache-min-free-mib` floor of `max(20% x REAL_RAM, 16 GiB)` = 52428 MiB.
  The mem-limit pin is the official antidote to the inflated `MemAvailable` on this box
  (the HBM-window mapping; see the 341 GiB caliber note).
- Measured: 16 x 148K mutually distinct chains, parked under a full CC, resurrect at median
  **6.9 s** wall with **0 cold recomputes**, split ~50/50 between RAM and disk hits, disk read-back
  ~2 GB/s.  No PP/TG/VRAM regression, so it is a pure time-for-capacity trade at zero numeric cost.
- **Caveat that changes how it must be tested** (engine's own words, `runbook §331`):
  `parked conversations only: the live one cannot be saved with a layer split, --peer-device or
  --batch`.  Our shape matches both, so a chain that is evicted **while still being served** has no
  snapshot at all - that, not a write-queue race, is why one §327 resurrection fell to cold (94 s).
  Consequences: shrinking `DISK_SYNC` will not fix it, and any harness measuring resurrection
  benefit must let the target chain become parked (wait one idle-save window) before evicting it.
- Operations: the on-disk pool is budget-bounded and was already **99.5% full** on day one
  (`246 conversations, 521577 MiB`), so new parks evict the coldest.  Lowering `DISK_MIB`, or
  clearing `/data/cache/conv-disk/*`, costs only resurrection speed, never correctness.

## Adopted: `STRATA_ADMIT_SHORT_FIRST=8192` - admission short-first (2026-11-20)

- Mechanism (`serve/server.py:409-417`, boot env, **no engine flag, no numeric path**): with
  `--batch`, new admissions take the control lines one prompt read at a time; a prompt of at most
  this many tokens goes before longer ones still waiting, and a long prompt that has waited
  `STRATA_ADMIT_MAX_WAIT_S` (default 30 s) counts as short, so nothing starves.
- Measured on the shipped 4-card shape (`ac922/scripts/ttft_ab.py`: burst = 6 cold ~23K reads plus
  one chat probe, arms in separate boots, fresh corpus slices per arm because the disk tier persists):

  | arm | chat TTFT samples | median | worst | long-request wall |
  | --- | --- | --- | --- | --- |
  | FIFO (upstream default) | 13.7 / 39.1 / 85.6 / 90.5 s | **62.4 s** | 90.5 s | 75.1-76.8 s |
  | short-first 8192 | 13.7 / 15.3 s (+15.7 s on the shipped boot) | **14.5 s** | 15.3 s | 84.4 s (**+10-12%**) |

- Why we take it: the complaint this answers is a small request stuck behind agent-sized cold reads
  (observed up to 90 s), and the price is ~10% wall on requests whose wall is already dominated by
  the same shared read queue.  PP/TG **rates** are untouched - only the order of the reads changes -
  and the probe's answer hash was identical in both arms.  Raise the cap to widen the fast lane;
  `0` reverts to FIFO.
- Gate: `qualgate check` on the adopted boot (det x3 + divergence probe + acc drift), plus the
  one-round end-to-end check that the shipped `adopted-env.sh` really yields ~15 s.

## Host config our numbers depend on: a per-node free reserve for the UVM/ATS path (2026-11-20)

- `/etc/sysctl.d/90-ac922-numa-reserve.conf`: `vm.min_free_kbytes=1048576`,
  `vm.watermark_scale_factor=150`.  Reason and evidence in the bring-up runbook (§330): node 8 ran to
  0.85 GiB free and UVM's ATS fault handler, which must allocate the migration target **on the GPU's
  own node** (`__GFP_THISNODE`), logged 782 order-0 allocation failures in ~15 minutes.
- Not a lane knob and not optional if you want stable tails: the box has 219.5 GiB of real DIMM
  across two nodes plus the HBM windows (`ZONE_DEVICE`, nodes 250-255 - the source of the old
  "341 GiB" mistake), and both lanes page-lock tens of GiB.  Verified cost on our lane: engine
  majflt unchanged, no new `allocstall_normal`.

## Rejected — CPU-pool placement knobs (Wave J, 2026-11-21, 4-card deep window)

| Knob | Idea | Measured | Verdict |
| --- | --- | --- | --- |
| `STRATA_POOL_CORES=4-84:4,88-128:4` | put 21 of 32 workers on node 0 to match the arena's 0.69 NUMA cut (compute follows data) | tg 74.3 -> 71.6 (-3.6%), negative in all three paired same-base rounds | **rejected**; task routing does not scale with slice bytes the way the rebalance assumed |
| `STRATA_POOL_SPIN_US=200000` | the default 20 ms park is shorter than a ~30 ms decode window, so keep workers hot across windows | msw -3.6% but tokens/window -8%, net tg -4.8%; output hash moved (not bit-exact) | **rejected**; the sleep/wake cost the idea targets is not where the time goes |
| `STRATA_NUMA_POOL=0` | audit: is the auto per-node pool split paying or costing? | verify +8%, host-CPU experts 2.1x worse | **confirms the default** - the split earns; leave it on |

Also from the same wave: the "10.5 ms/window host-CPU experts" figure we carried is the
*cold-adaptation* number; warmed it is 2-3 ms/window, so the remaining deep-window cost is the
sm70 expert product itself (verify minus reach ~22 ms), which no pool knob touches.

## Measured neutral / incident - the fork-tuned MTP layer and the Wave N mis-baseline (2026-11-21, 6 cards)

**Incident (read this before trusting any same-day draft numbers).** The .4 server's drafting
auto-detect (`strata_control/drafting.py:467` `fork_tuned_mtp_dir`: a complete
`<model data>/mtp/rt-tuned` is picked at every start, and `FORK_MTP_FLAGS` (`--spec 8
--mtp-head proxy --mtp-cum auto`) ride with it) silently switched every boot after the 11:20
staging of `mtp/rt-tuned` onto the tuned dense draft. So Wave N's "A default" boot (13:21) was
already running the tuned layer, arm D (explicit `MTP_DIR=.../rt-tuned`) was the same
configuration, and arm E's code99 table was shadowed (the search hits the plain
`mtp/rt-tuned` first) and was never actually loaded. The apparent "D/E -5~-14%" was
cross-boot noise plus the rt1-style cold-adapt outlier (msw 37 ms first window) - against the
discipline "effects below ~4 ms/window must not be judged across boots".

**Fair re-run (abTuned.sh, 15:03-15:25, alternating boots RT1/SH1/RT2/SH2, same corpus slices,
agent-shaped code+tool probes at 185-247K, temp 0, ctok 256, default 0.7 gate):**

| arm | tg by slice (212687 / 184711 / 246871) | mean |
| --- | --- | --- |
| RT tuned (RT1 excl. cold outlier, RT2) | 85.1 / 84.6 / 77.3+84.7 | ~84 |
| SH shipped (sh1, sh2) | 80.2+81.0 / 85.8+84.5 / 79.3+79.7 | ~81.7 |

Verdict: **neutral within the ±5% pair band, signs inconsistent across slices** - the tuned
dense draft's sister pricing (+5.7~11.8%) does not show on this box at agent depth. Both bands
also confirm the flags themselves are not the axis: SH boots carried the same `--spec 8
--mtp-head proxy` (the flags come with the server's auto path regardless of the dense dir).

| Ticket | Verdict | Reason |
| --- | --- | --- |
| run production on `mtp/rt-tuned` | **not adopted; directory held out** at `/data/work/tuned-holdout/` | zero measured upside; its presence silently re-wires the draft at every start - the exact failure mode of this incident |
| `MTP_DIR=.../rt-tuned-code99` | **never tested** - the auto search picks plain `rt-tuned` before any custom dir; the code99 table (arm F) stays parked |
| `spec_min_p` 0.5 / 0.35 same-boot ladder | stays **dead ticket** | it was measured on a tuned-layer boot, but since RT≈SH the conclusion carries; do not re-run |
| cold-adapt first-window msw outlier (37 ms on RT1) | recorded | first probe round after a boot is not a measurement; discard or pair it |

**Gate-baseline discipline (H.21, same day):** after any bump containing numeric-path fixes,
compare the COLD det against REF before believing a relay collapse - cold bit-identical plus
relay 0/8 means the REF is stale (the .2-era REF was exactly this case: two relay cells proved
bit-stable across independent .4 boots while the .4 upstream fixes moved the long-relay output
path).  Re-baseline only with the old REF preserved as a backup, and remember the relay floor:
cells agree only 2/8-8/8 gate-to-gate, so `rate>0` is a collapse detector, not a quality score.
**Standing guard adopted with this verdict:** keep `mtp/rt-tuned` out of `<model data>/mtp/`
unless an experiment explicitly puts it back, and re-verify `--mtp` in the live engine's
cmdline after ANY bump or data-dir sync - the auto-detect will re-arm silently.

## Adopted: the 4-card ship shape is `--batch 4 --batch-groups 4` (2026-11-21)

A borrowed analysis said the 4-card sweet spot was 2 rows/group (4/2) because 12
layers/card make multi-row QSA scoring expensive.  Never measured here, so we paired
sweep boots: arms 4/2 (5 boots), 4/4 (2 boots), 4/1 (1 boot); aggbench warm-prefix
decode, n=4, wall_tg medians:

| depth | 4/2 band med | 4/4 med | 4/1 (serial) |
| --- | ---: | ---: | ---: |
| 8K   | 139.5 | **151.0** | 98.3 |
| 50K  | 119.2 | **126.0** | 81.1 |
| 98K  | 93.9  | **99.8**  | 71.1 |
| 131K | 70.0  | 73.9 (neutral) | 53.8 |
| tg1 8K (single-stream) | 86.5-90.5 | 86.5-88.0 | 88.9 |

- 4/4 wins 8K/50K/98K with **both** samples above the entire 4/2 band (+4-8%); 131K
  neutral (one 58.3 wall sample was admission stagger, ttft 1.7-9.4 s; its sum_tg 95
  in band); single-stream unharmed.  **Adopted**: config `--batch-groups 4`, launcher
  auto-shape `(4,4) if ncard==4` (6-card stays 6/3).
- 4/1 re-confirmed dead (-21 to -33%).  The analysis's reason was wrong though:
  `--batch-groups 1` keeps a serial window WITH slot drafts (`batch_groups.hpp`);
  drafts die on the auto-pipelined path only when groups>=2 without `STRATA_PIPED_MTP`.
  The 4-card idle-window floor is ~27-30 ms (tg1 84-90), not the borrowed 17-18 ms.
- Noise floor (accidental 3-boot 4/2 replicate, after the auto-shape clamping incident
  where run.sh silently forced (4,2) unless `STRATA_BATCH_GROUPS` env is passed): wall_tg
  ±2.5% at 8K, worst -5.6% at 50K, ±1.2% at 98K, ±2.2% at 131K.  Rule: effects under
  ~5% require paired boots; sweeps pass the shape via EXTRA env, never config alone.
- Gate on the adopted boot: cold det bit-identical to REF (7/8 relay cells, acc ok -
  H.21 discipline), QUALGATE-PASS recorded in runbook.
