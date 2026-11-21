# Performance — final baselines and cross-stack comparison

All rows are measured on this AC922. The shipped lane is **4-card** (section 6d); **B6**/**B4**
below are the same geometries measured *before* the residency tickets were adopted, kept as the
delta reference.

## 1. Final baseline (Wave B, two boots, geometry as the only variable)

### B6 — 6-card, `--batch 6 --batch-groups 3`

| Item | Value |
| --- | --- |
| Cold prefill ladder (ptok → pp, tg) | 6,764 → 2,246 (77.2) · 54,000 → 5,154 (75.4) · 65,802 → 5,363 (67.0) · **261,494 → 7,016 (72.5)** |
| Full-window single stream | ptok 261,494, pp 7,016 tok/s, tg 72.5 tok/s, MTP acc 0.742 |
| Single-stream decode @ 8K (6-round median) | 66.0 tok/s (acc 0.7461) |
| Full-window concurrency 245K × 6 | fail 0 · wall 30.5 s · **agg steady 166.4** (total 100.9) · per-stream median 23.4 · TTFT 6.3 s · peak live 6.0 |
| Short-window concurrency 8K × 6 | fail 0 · wall 15.3 s · **agg steady 218.2** (total 200.6) · per-stream 35.7 · TTFT 0.6 s |
| Resources | VRAM 87.7 GiB total (14.7/14.5/14.5/14.5/14.5/15.0 per card) · RAM pinned 47.7 GiB |
| Shape evidence | `batch (pipelined, 3 groups of 2)`, adaptive tier lines on, MTP alive inside the pipelined window |

### B4 — 4-card, `--batch 4 --batch-groups 2`

| Item | Value |
| --- | --- |
| Cold prefill ladder | 6,764 → 1,488 (57.1) · 54,000 → 3,970 (64.8) · 65,802 → 4,162 (62.4) · **261,494 → 5,120 (57.1)** |
| Single-stream decode @ 8K | 57.2 tok/s (acc 0.7504) |
| Full-window concurrency 245K × 4 | fail 0 · wall 30.1 s · agg steady 93.5 · per-stream 21.6 · TTFT 4.9 s |
| Short-window concurrency 8K × 4 | fail 0 · wall 16.4 s · agg steady 133.6 · per-stream 33.3 |
| Resources | VRAM 58.6 GiB · RAM pinned 47.7 GiB |

Scaling 4→6 cards: full-window aggregate **+78%**, short-window aggregate **+63%**, single-stream
tg +7.5% — card count buys concurrency, not per-stream speed (see `DEVLOG.md` §"stage count is
the axis").

## 2. Context reach per stack (single request, cold)

| Stack | Max live ptok | Note |
| --- | ---: | --- |
| NVFP4 6-card / 4-card | **262,134** | model cap 262,144; the engine's real reserve is **8 tokens**, not tens of thousands - prompt 262,134 is accepted and 262,136 is refused, and six concurrent streams were held at position 262,136 (§6a). The older "net usable ≈ 232K / engine keeps 24-30K per slot" note was wrong and is withdrawn; 261,494 had already been measured live. |
| Strata int8 (IQ3_XXS, 0.1.41, 4-card) | 210,038 stable | `-c 262144` builds; 210K is the measured stable depth |
| Monster (llama.cpp fork, 6-card, MTP) | 260,541 | barely; per-card free space < 1.2 GiB |
| Monster (4-card, MTP, `-ngl 47`) | **89,043** | the only stack that cannot stand in the full window |

Over-subscription behavior (8 × 245K on 6 cards): fail 0, no OOM, peak live pinned to slot count
— the scheduler queues; capacity rule **N cards ↔ N full-window streams**.

## 3. Cross-stack same-length comparison (measured, this box)

| Metric | N1 NVFP4 6c | N1 NVFP4 4c | S1 int8 4c | M1 Monster 4c | M1 Monster 6c |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold pp @ ~7K | 2,498.8 | 2,075.6 | 2,955 | 1,897 | 1,790 |
| Cold pp @ ~54K | 6,104.3 | 3,977.3 | 5,785 | 1,905 | 2,159 |
| Cold pp @ full window | **7,016** | **5,120** | — (reach 210K: 5,041) | — (reach 89K) | 2,121 @ 260K |
| tg @ 8K | 82.1 | 69.9 | 63.3 | 89.3–102.2 (bench3 caliber +14%) | 85.9 |
| tg @ ~65K | 80.3 (one shot) | **72.1** (median of 10 rounds; runs 75.4 / 70.8) | — (caliber, see below) | 75.5 | 79.0 |
| tg @ full window | 72.5 | 57.1 (warm, single stream) | — | — | 39.2 |
| Agg decode (best measured) | 240.1 (8K×6) | 138.6 (8K×4) | 235.0 (50K×4) | 122.0 (8K×4) | 115.3 (1.5K×4) |
| Agg @ byte-exact full window | **145.6** (6 × 262,136) | **109.3** (4 × 262,136, shipping-binary boot) | — | — | — |
| VRAM | 85.3 GiB | 58.0 GiB | 13.4–14.9/card | 50.5 | 66.8 (per-card not sampled) |
| RAM | 54.42 pinned | 57.10 pinned | ~40 experts | ~61 used | not sampled same-moment |

Caliber warnings (also printed on the chart): S1 deep-window tg (110–112) is inflated by the
synthetic repeated-corpus ladder + ngram drafting and is excluded from same-length slices;
Monster 8K tg differs ~14% between bench3 and ladder calibers — read same-boot ladders only;
the 65K pair is a single shot (6c) against a 10-round median (4c) and the same 4-card protocol
drifted 6.1% run to run, so the 11% gap is partly confidence, not speed.  Deep-window rounds are not
reproducible round to round at 65K (2-3 distinct digests per five rounds, tail-prefill chunk geometry
dependent); determinism is claimed only against the 10K `det` fixture.

## 4. Charts

- `results/charts/ac922-samelen-v2-2026-11-16.png` — 7-panel panorama: prefill / decode /
  aggregate / reach / same-length slices / resource bill / accuracy.
- `results/charts/ac922-paths-final-2026-11-15.png` — four-path final (V1 1Cat-vLLM appears only
  as a paper anchor, never ranked).
- `results/charts/ac922-samelen-v3-2026-11-17.png` — the same panorama re-taken on the shipped
  4-card state, with both NVFP4 ladders measured in one protocol on 2026-11-17 (generator:
  `results/charts/plot-samelen-v3-2026-11-17.py`).
- `results/charts/ac922-samelen-v5-2026-11-17.png` — v4 plus the last fillable cells: 65K on both
  hero rows, Monster 8K×4 aggregates on both card counts, Monster 6-card bill as a named-total
  instead of a placeholder, resource bill refreshed to the shipping-binary boot, and the full-window
  invariant relabelled `held_edge` (generator: `results/charts/plot-samelen-v5-2026-11-17.py`).
- `results/charts/ac922-samelen-v4-2026-11-17.png` — final panorama on the rebalanced shipping
  geometry (`layer_split 9,24,37`, `--vram-reserve-mib 832`): 4-card column re-measured over two
  boots, full-window 8/8 `finish=length` evidence, and the self-restart defect closed (generator:
  `results/charts/plot-samelen-v4-2026-11-17.py`).
- `results/charts/ac922-bump-061-2026-11-19.png` — the 0.1.59 → 0.1.61 adoption re-test: 6-card
  same-protocol pairs, 4-card two-boot state, and the byte-exact full-window pair (generator:
  `results/charts/plot-bump-061-2026-11-19.py`).
- `results/charts/ac922-anatomy-2026-11-22.png` — the lane's own decode-window anatomy (5a): the flat
  ~20 ms GPU body against the 0.5 → 17.5 ms host-CPU expert term, the 131 K read-threshold set by three
  same-env anchors against the wave's arm deltas, and the hit-ratio/absolute-miss pair that shows the
  cache is not thrashing (generator: `results/charts/plot-anatomy-2026-11-22.py`).

## 4b. Window-shape family, 6 cards (2026-11-17 overnight sweep, `note312/shapes.jsonl`)

Aggregate decode, published caliber (`sum_tg` = sum of the streams' own rates), at six concurrent
streams; `solo` is one stream at the same depth. Cold prefills are outside the measurement (each
payload is warmed first), so this table reads decode only.

| arm | slots/groups x rows | 8K | 50K | 98K | 131K | solo 8K | solo 50K |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **A1 (shipped)** | 6, 3g x 2r | **228** | **201** | **179** | 135 | 84.5 | 72.8 |
| A2 | 6, 2g x 3r | 140 | 158 | 139 | 128 | trunc | trunc |
| A3 | 6, 1g x 6r | 92 | 96 | 86 | 75 | 78.8 | 72.3 |
| A4 | 8, 2g x 4r | 134 | 158 | 139 | 124 | - | 49.2 |
| A5 | 8, 4g x 2r | 143 | 181 | 161 | 136 | - | 50.5 |
| A6 | 6, 2g x 3r + `--batch-max-context 65536` | 152 | 160 | 63* | 62* | 85.5 | 72.2 |
| A7 | 6, 2g x 3r + `--trim-stage-weights` | 202 | 173 | 147 | 140 | - | 77.7 |
| A8 | 6, 6g x 1r (the engine's own `auto`) | 157 | 186 | 166 | 143 | 78.1 | 77.6 |
| A9 | 8, 8g x 1r | 148 | 183 | 162 | 141 | - | 48.9 |
| A10 | A5 + `STRATA_COALESCE_PREFILL=4` | 147 | 176 | 156 | 113 | 71.1 | - |
| A11 | 6, 3g x 2r + `--trim-stage-weights` | 234 | 210 | - | **154** | trunc | trunc |

`*` A6's deep points left the slots and ran on the solo path: each stream decoded at ~75 tok/s
(`sum_tg` 432/454) but they did **not** overlap, so the honest aggregate is the `wall_tg` column -
63 tok/s, against 90.6 for A1 at the same depth. `trunc` = the solo request came back short (see
the defect note in `DECISIONS.md`); those cells are not speed measurements.

What the family says, on this box:

- **Two rows per group is the ceiling of useful sharing.** 2 rows -> 228, 3 rows -> 140, 6 rows in
  one window -> 92. Multi-row windows cost more than the shared expert reads buy: per-row QSA
  scoring on Volta is compute-bound and grows superlinearly, and the streamed-weight saving that
  motivates batching upstream is small here because experts already stream from RAM over NVLink2.
- **More slots than cards loses.** 8 slots shrinks the per-card expert cache (2924 -> 2749 slots,
  7.53 -> 7.08 GiB; the last stage 2236 -> 2022) and every 8-slot arm measured below the 6-slot
  arm at its same rows/group. The capacity rule stands: slots = cards.
- **`--trim-stage-weights` is the only lever that lifted a shape** (A2 140 -> A7 202; A1 228 -> A11
  234, and at 131K 135 -> 154). It needs an explicit `--layer_split`, so it is a config change, not
  an env flip - and it arrived with a truncation symptom that is not yet explained.
- **`STRATA_COALESCE_PREFILL` does not move decode** (A5 143 vs A10 147) - it is a prefill-side
  switch, as documented.

## 5. Decode window anatomy (why tg behaves the way it does)

First numbers here were the **sister box's** split (expert product ≈61% of window time, GPU-reach
wait ≈28%, host CPU ≈12%). 5a re-measures it on our lane and the shape is different, so read 5a before
re-using the 61/28/12 numbers. Consequences we verified on our own lane:
- per-stream tg is a **stage-count** axis, not a card-count axis (2–3 card tg ≈ 6 card tg);
- concurrency gains come from sharing expert streaming reads inside one window
  (`--batch-groups` with ≥2 rows), which is exactly what the adopted ring shape does;
- host-wait reduction levers adopted: NUMA bind + PLE park; the remaining big lever (FP4
  tensor-core expert kernels, ticket ⑧/QPN2) is parked upstream-side, not ported.

### 5a. Re-measured on this lane (2026-11-22, 6 cards, shipped env) — the deep window is host-side, not GPU-side

The 61 / 28 / 12 split above was borrowed from the sister box. Our own engine log carries 6 755
`STRATA_DECODE_TIMING` lines in its 68 MB tail, which is this night's ladder boots, and they do not
replicate it. Batched windows (the `>=4 windows` lines, where MTP is landing 1.9-3.5 tokens/window):

| window class | n | ms/win | tok/win | GPU-reach | host-CPU experts | adapt wait | draft | GPU body (residual) | VRAM hits/layer-win | misses/layer-win | swaps/win |
|---|---|---|---|---|---|---|---|---|---|---|---|
| <=30 ms (short) | 201 | 27.05 | 1.90 | 3.32 = 12% | 0.52 = 2% | 0.03 | 2.10 | **20.24 = 75%** | 17.0 | 0.04 | 0.2 |
| 30-45 ms (mid) | 369 | 36.33 | 2.57 | 4.29 = 12% | 6.24 = 17% | 1.19 | 3.20 | **18.97 = 52%** | 28.4 | 0.73 | 7.4 |
| >45 ms (deep) | 174 | 51.82 | 3.48 | 4.88 = 9% | 17.48 = 34% | 2.87 | 4.33 | **19.88 = 38%** | 38.8 | 1.68 | 16.8 |

Single-window lines (no draft accepted, 1.00 token/window) say the same thing at a lower rate: 18.95 ms
with 0.00 host-CPU at short depth, 32.38 ms with 8.91 ms host-CPU (28%) at 30-40 ms, 55.61 ms with
22.62 ms (41%) beyond that. Reading of the table:

- the **GPU-executed body is flat at ~19-20 ms** in every class while the window doubles (27 -> 52 ms);
  the whole deep-window penalty is **delivery**: host-CPU experts 0.52 -> 17.48 ms, adapt wait
  0.03 -> 2.87 ms, swaps 0.2 -> 16.8 per window;
- the expert-cache hit *ratio* stays high (95.8% at the deep end vs 99.8% short), but the absolute miss
  count per layer-window grows 0.04 -> 1.68, i.e. ~2 -> ~80 experts/window computed off-VRAM across the
  48 layers - that is the `FileExpertSource: RAM budget ... read from the files` complement, not a
  thrashing cache;
- GPU-reach wait is a flat 3-5 ms handshake (9-12%), so the sister box's 28% figure does not transfer;
- priority consequence: the deep-window levers are the **quality-neutral delivery ones** (complement
  NUMA placement, fetch overlap, resident coverage, `--adapt-every` / learned profile), and the
  tensor-core expert kernel (⑧/QPN2) attacks the flat ~20 ms body - 75% of a short window but only
  38% of a deep one, which is consistent with the sister measurement of QPN2 net-negative in serving.

## 6. Full-window concurrency ceiling (2026-11-17, measured on the ship config)

Question: does the "one full-window stream per card" rule actually hold at 6 cards?
Answer: **yes, and the wall is VRAM per card, not RAM.**

### 6a. What "full window" means here, to the token

`--max-context 262144` is the *configured* window; **the addressable ceiling is 262,136**, eight
slots short, because the engine keeps the MTP draft window (`--spec 6` plus slack) out of reach. Two
independent measurements bite together (`ac922/eval/maxwin.py`):

- a request at prompt 262,136 with `max_tokens 1` is refused - the 400 body quotes both numbers
  (`262136`, `262144`), and prompt 262,134 is the largest prompt accepted;
- six concurrent streams at prompt 261,707 asked for `gen 437` were cut at `ctok 429`, five of six
  of them landing on position 261,707 + 429 = **262,136** with `finish_reason=length`.

Corollaries worth pinning: the engine enforces `prompt_tokens + max_tokens <= 262144` (so exactness
is a property of the **sum**, not of hitting a round prompt), and it will not write past 262,136 -
so a client computing `max_tokens = 262144 - prompt` gets a 400 on the last eight tokens. Use
262,136 as the window end. The filler lesson behind all this: `" 1"` is not one token - it measures
**+2.05 tokens per copy** (+11 for 5, +41 for 20), which is why `maxwin.py` reads the engine's own
`usage.prompt_tokens` as truth and bisects a monotone knob instead of counting tokens itself.

**Measured at the true ceiling, 6 concurrent independent streams**

| arm | prompt (verified per stream) | gen to ceiling | reached 262,136 | sum_tg | per-stream tg |
| --- | ---: | ---: | ---: | ---: | --- |
| ARM-A | 261,707 | (437 asked, reserve overshoot) | 5/6 cut at 262,136 | 118.9 | 12.8-28.8 |
| **ARM-A2** | **261,275** | **861** | **2/6 at 262,136, 4 by model EOS** | **148.5** | **18.1-31.7 (med 23.3)** |

Four of six ending before the ceiling is the model choosing `finish_reason=stop` at temperature 0,
not the lane falling short - ARM-A shows five streams being held at exactly 262,136 at the same time.

Ship geometry (`--batch 6 --batch-groups 3`, `--kv int8`, `--kv-resident 32768`,
`--max-context 262144`, warm prefix), 6 independent streams (start-of-prompt marker makes every
prefix distinct, so no cross-stream sharing), temperature 0:

| Depth / stream | reused | sum_tg | wall_tg | per-stream tg | TTFT | streams scored |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| 234,300 (gen 1024) | 100% | 121.8 | — | 15.4-26.7 | 1.5-14.6 s | 6/6 |
| **258,780 = 98.7% of 262,144** | 100% | **131.2** | 84.3 | 15.5-31.9 (med 19.7) | 1.2-15.5 s | 6/6 |

Both runs decode inside one overlapping window (`decode_live_s` 29.9 s of a 31.1 s span,
rows/step 2.96 against a shape of 3 groups of 2), no refusals, no errors, `dropped_streams: 0`.
Five of the six streams ended early on `finish_reason=stop` - the model closing its own turn at
temperature 0 - which is why the depth row is quoted with 258,780 rather than the round 260,000
target, and why `early_stop` is now reported apart from `dropped_streams` (§see DECISIONS.md D1).

**Where the budget actually goes at 6 x ~259K**

- **VRAM is the binding constraint.** All six cards settle at 15.2-15.35 GiB of 16.38 GiB -
  ~1 GiB per card left. Six 32K resident windows plus the expert cache is exactly what fills a
  V100-16 under this geometry. A 7th stream, or pushing these to the literal 261,632-token maximum,
  has nowhere to grow.
- **RAM is bounded by design and degrades gracefully.** The conversation cache parked ~4.8 GiB per
  258,780-token conversation (18.4 KB/token in parked form - this is a GDN-hybrid model, so the
  parked state is nowhere near the 0.22 MB/token a dense-FA model would need), i.e. ~29 GiB for the
  six live conversations. `--conversation-cache-mib 98304` is the ceiling, and after a day of
  benchmarking it is reached: `parked=24 bytes=95.6 GiB evictions=9`. `MemAvailable` then sits at a
  steady 3.7 GiB with swap off - **not** a leak, not thrash: the engine evicts parked conversations
  instead of failing, vmstat shows no swap activity, and `/health` stays 200 throughout.

### 6b. That open cost was real, and is now closed: CC 96 -> 48 GiB (adopted)

Measured, not assumed: at 245K x 6 with `gen 512` (same generation length as the original record),
the shipped 96 GiB CC sitting at both caps (`parked=32`, `bytes=79.5 GiB`, `evictions=99`,
`MemAvailable 10.9 GiB`) gave **wall_tg 89.3 / sum_tg 130.7**; halving CC to 48 GiB
(`MemAvailable 49.1 GiB`) gave **wall_tg 115.1, 106.1 / sum_tg 143.5, 144.9** - **+24% deep
aggregate**, past the 2026-11-16 figure of 100.85. The short window did not move: 8K x 6 warm
**223.1, 224.2** (published 218.2). Gate `QUALGATE-PASS`, `det` bit-identical. Full ledger and the
two confounders that had to be excluded first (gen length; cold-boot warm-up) in `DECISIONS.md`.

### 6c. C-phase residency tickets: learned expert-profile + `--trim-stage-weights` (adopted 2026-11-17)

Boot-paired arms (CTL x5, learned x4, trim x3, combo x2, `eval/cphase.py`, `/data/work/note_c17`);
deep-window single runs carry +/-15-25% boot noise, so the table reads family medians:

| regime (fresh boot) | CTL | learned | trim | **combo (adopted)** |
| --- | ---: | ---: | ---: | ---: |
| tg 8K | 57.1 | 74.9 | 73.5 | **77.9** |
| tg 64K | 64.8 | 69.2 | 75.4 | **76.5** |
| tg 131K | 58.5 | 68.2 | 79.0 | **73.8** |
| tg 196K | 61.1 | 65.8 | 81.0 | **73.5** |
| tg 249K | 69.5 | 72.4 | 79.8 | **79.4** |
| 8K x 6 per-stream | 53.9 | 55.0 | 57.6 | **58.8** |
| 8K x 6 cold-sum agg | 18.5 | 18.6 | 20.6 | **20.7** |

- Full-window acceptance on the adopted boot: 6 x exactly 261,632 + 512 gen, 6/6 exact, 5/6
  reached the window edge (262,136), one stream EOS-early at 473 tokens; **sum_tg 155.0**
  (pre-C 148.5), span 26.3 s.
- Quality: `det` x3 bit-identical everywhere (REF `bfd40ecd4692` held); gate on the adopted boot
  **relay 8/8 = 1.00, acc-drift 1.0, QUALGATE-PASS**; teacher-forced yardstick over 2,997
  positions: top-1 99.07%, KL 0.0012, PPL 4.419 vs 4.414 - the residency-ticket signature, not a
  quantization change. Ledger and the one gate false alarm it produced: `DECISIONS.md`.
- Trade kept honest: trim alone is the better 131K/196K decoder (+7-10% over the combo); the
  combo was adopted because the shipped objective is short-window and concurrent first, and the
  combo still beats the old baseline at every depth.
- Warm single-pass on the live lane (after the gate traffic, one run): 8K **84.8**, 64K 76.0,
  131K 77.8, 196K 82.2, 249K **82.7** pp 5,431 - direction-consistent, not a new anchor.

The CC 96 -> 48 prediction from the previous draft is closed in 6b; no open items remain in this
section.

### 6d. The shipped geometry is 4-card (2026-11-17, adopted config, two boots per geometry) — superseded by 6e

*Kept as the delta reference.  6d is the `layer_split auto` + `--vram-reserve-mib 700` shape, which
measured fast and then proved unstable (it respawned under load); 6e is the ship shape.*

Cards 4/5 go back to the dev/iteration lane, so the ship state is the 4-card shape: `gpu [0,1,2,3]`,
`--batch 4 --batch-groups 2` (the launcher now derives that pair and `NB_CUT=0.69` from the card
count, so `bash ac922/scripts/lane4.sh start` is the production command).  Both geometries were then
measured with the same harnesses on the adopted config — 4-card boots at 13:51 and 15:11
(`note_c17/4CARD.*`, `4CARD2.*`), 6-card boots at 13:00 and 14:41 (`ADOPT.*`, `6CARD2.*`) — and the
published cells are **medians of the two boots**, because a deep-window single run carries ±15-25%
boot noise (4-card 8K tg measured 77.3 then 66.1; 6-card full window 155.0 then 136.2).  Cells with
one boot only are marked `n=1`.

| Item | 4-card adopted | 6-card adopted | 4-card before (B4) |
| --- | ---: | ---: | ---: |
| tg 8K single stream | **71.7** (n=2) | 82.1 (n=2) | 57.1 |
| tg 54K / 110K / 165K / 210K | 71.6 / 70.1 / 73.3 / 71.2 (n=2) | 76.4 / 76.1 / 77.9 / 81.1 (n=2) | 64.8 / — / — / — |
| tg 65K single stream | 73.0 (n=1) | 80.3 (n=1) | 62.4 |
| cold pp 54K / 110K / 210K | 4,156 / 4,175 / 3,707 | 6,104 / 6,047 / 5,441 | 3,970 / 4,162 / — |
| 8K × N aggregate (`aggbench`) | **139.3** sum_tg, per-stream 34.8 (n=2) | 240.1 sum_tg, per-stream 39.7 (n=1) | 133.6 |
| 245K × N aggregate (`aggbench`) | **100.2** sum_tg, wall_tg 77.8, per 24.4 (n=1) | 135.2 sum_tg, wall_tg 93.6, per 21.4 (n=1) | 93.5 |
| full window N × 261,632 + 512 | **all exact** at 262,136, sum_tg **108.3** (108.1 / 108.5), span 22.6-23.0 s | all exact at 262,136, sum_tg 145.6 (155.0 / 136.2), span 26.3-32.9 s | not measured at the true ceiling |
| cold 262K prefill, single stream | ttft 52.7 s (pp ~4,964) (n=1) | — | — |
| warm 262K decode, single stream | 55.9 tok/s (EOS after 456) (n=1) | — | — |
| VRAM / RAM | 14.6/14.5/14.5/14.9 GiB, Σ 58.6 · resident pinned 57.65 GiB | 14.5/14.0×4/14.8 GiB, Σ 85.3 · resident pinned 54.42 GiB | 58.6 GiB Σ · 47.7 pinned |

- Scaling, same harness, same day: 8K aggregate **+72%** for two more cards (139.3 → 240.1),
  245K **+35%** (100.2 → 135.2), true full window **+34%** (108.3 → 145.6).  Single stream moves
  ~10-14% — the card-count rule buys *streams*, not *speed*, and that now holds on both geometries
  of the adopted config rather than only on the pre-adopt one.
- What adoption bought per geometry: 6-card 8K×6 aggregate 218.2 → 240.1 (**+10%**), 4-card 8K×4
  133.6 → 139.3 (**+4%, inside the boot band**).  Short-window aggregate is admission-bound, so the
  residency tickets show up in single-stream and deep-window cells, not here.
- The 6-card deep aggregate needs one honest note: `aggbench` 245K×6 measures 135.2 sum_tg while the
  pre-adopt archive cell says 166.4 — but that cell came from the live-lane harness
  (`out/waves/baseline_final.json`, `agg_steady`/`agg_total` on production traffic), not from this
  synthetic burst, so the two are not the same ruler.  The same-boot, same-ruler pair is
  4-card 100.2 vs 6-card 135.2, and the full-window row above is measured identically on both.

- Shape evidence: `batch (pipelined, 2 groups of 2)`, `rows_per_step 3.35`, `STRATA_PIPED_MTP`
  drafting one MTP proposal per slot per window - the 4-card geometry runs the same batched ring,
  not a degraded one.
- Quality: `det` x3 reproduced `bfd40ecd4692`, **bit-identical to the 6-card REF across the geometry
  switch** (the det fixture does not see residency), gate `QUALGATE-PASS` with acc-drift 1.003
  against a freshly written 4-card `REF.json`; the 6-card anchor is preserved beside it as
  `REF-6card-adopted-20261117.json`.
- What the tickets bought at 4 cards (two-boot medians): short-window single stream 57.1 → 71.7
  (**+26%**), deep single stream +8-11% over B4, 245K × 4 aggregate +7%, 8K × 4 aggregate +4% -
  concurrency at short windows is admission-bound, not residency-bound, which is why the single-stream
  gain does not appear in the aggregate column.
- Geometry is now machine-checked rather than remembered: every boot above ended with
  `lane.sh` printing `GEOMETRY-PASS` (pipelined group shape from the startup marker, stage count
  from the split summary, `LD_PRELOAD=numabind.so` and `CUDA_VISIBLE_DEVICES` read out of the live
  engine's `/proc` environ, one `cpu pool: NUMA pool on` line).
- Remaining thin cells (`n=1`): the 65K single-stream points on both geometries, both `aggbench`
  245K cells, the 6-card 8K×6 aggregate, and the cold/warm 262K single-stream pair (4-card only).
  They are marked in the table; closing them is a repeat of the same harness, not new methodology.
- Ship-state re-proof after the lane scripts landed (15:45, same 4-card boot, nothing restarted for
  the docs merge): `lane4.sh status` → `GEOMETRY-PASS 4 cards (0,1,2,3), 2 groups of 2, shim
  loaded` with cards 4/5 at 225/13 MiB, `/health` 200 with `images: true`, and
  `eval/qualgate.sh check` → `det` x3 `bfd40ecd4692` equal to `REF.json`, `acc-drift-ok 1.003`,
  `QUALGATE-PASS`.  The gate printed five `relay-cell-drift` WARNs (4/8 cells agreed): cross-boot
  relay cells carry no verdict — the same config scores 0-6/8 against its own anchor across boots —
  so they are reported and never used as a FAIL (`DECISIONS.md`, gate-discipline section).

### 6e. The ship shape is rebalanced: `layer_split 9,24,37` + `--vram-reserve-mib 832` (2026-11-17)

The 6d shape was not stable.  27 minutes after a clean boot the shipped lane took
`mtp: round capture: out of memory`, exited 1 and was respawned by the serve supervisor - CUDA3 was
down to 149 MiB free while carrying 13 layers + the head + the 837 MiB MTP draft layer.  Four
shapes were measured on the same harnesses to fix it without paying for it in speed:

| Shape | det vs anchor | gate | respawn under the gate fixture | tg 8K/54K/110K/165K/210K | full-window 4-way |
| --- | --- | --- | --- | --- | --- |
| `auto` + 700 (was ship, 6d) | = | PASS | **yes - the defect** | 71.7/71.6/70.1/73.3/71.2 (2 boots) | agg 108.3, one boot EOS-early 3/4 |
| `auto` + 1024 | = | **FAIL** acc-drift 0.747 | no | 65.3/67.5/62.1/67.3/65.5 (one boot) | agg 99.4, 3/4 EOS-early |
| **`9,24,37` + 832 (ship)** | **=** | **PASS** acc-drift-ok 1.0 | **no** | **69.9/71.5/72.0/66.0/66.8** (2 boots) | **agg 106.9, 8/8 streams `length` at 262,136** |
| `9,24,37` + 1024 | not run | not run | not run | 65.6/61.0/66.6/66.0/66.1 (one boot) | not run |

- Ship numbers (two boots, `note_c17`-style files `/data/work/rb-*.txt`, `/data/work/c2-*.txt`):
  cold pp medians 2,075.6 / 3,977.3 / 3,964.3 / 3,817.2 / 3,562.8 tok/s at ptok
  6,786 / 54,022 / 110,526 / 165,350 / 210,038; decode medians 69.9 / 71.5 / 72.0 / 66.0 / 66.8.
- **Full window is the headline, and it is byte-exact twice**: `fullwindow.py run --n 4 --gen 512`
  gave `all_prompt_exact: true`, `prompt_plus_gen = 262,136` on all four streams and
  `finish = ["length","length","length","length"]` in *both* boots (span 23.6 / 23.0 s,
  `sum_tg` 105.8 / 108.0).  The rebalanced shape is the first one where every concurrent stream is
  proved to have consumed the window to its last slot, which is the ship invariant.
- Price of the fix, stated honestly: short and mid windows are unchanged (8K -2.5%, 54K -0.2%,
  110K +2.7%), the two deepest single-stream points read 6-10% lower than the 6d medians, and cold
  pp at depth is ~4-5% lower.  Aggregate at the true ceiling moved 108.3 → 106.9 (-1.3%, inside the
  boot band).  That is what buying back CUDA3 headroom costs; the alternative was a lane that
  restarts itself under load.
- Free VRAM at rest after a full gate run: 526/626/630/239 MiB (was 442/572/565/149).  Card 3 is
  still the tight stage, so the respawn detector in `lane.sh` stays mandatory, and the 6-card
  reference lane (223 MiB free on CUDA5 before this change) carries the same risk un-mitigated -
  its rebalance is an open ticket, not a shipped fact.
- One more reason not to buy headroom with reserve alone: at 1024 the **cold 8K prefill** falls
  2,076 → 1,539 tok/s (`auto`) and → 1,529 (`9,24,37`), i.e. -26%, because the short-window prefill
  buffer is squeezed by the same carve.  832 leaves it intact (2,069 / 2,082).

#### 6e.1 The ship table (two boots, `/data/work/rb-*`, `/data/work/c2-*`)

| Item | 4-card ship (`9,24,37` + 832) | 4-card 6d shape (superseded) |
| --- | ---: | ---: |
| cold pp 6.8K / 54K / 110K / 165K / 210K | 2,075.6 / 3,977.3 / 3,964.3 / 3,817.2 / 3,562.8 (n=2) | 2,056.6 / 4,156.1 / 4,174.8 / 4,031.5 / 3,706.8 |
| tg 8K / 54K / 110K / 165K / 210K | 69.9 / 71.5 / 72.0 / 66.0 / 66.8 (n=2) | 71.7 / 71.6 / 70.1 / 73.3 / 71.2 |
| 8K × 4 aggregate (`aggbench`) | 138.6 sum_tg, wall 131.8 (n=1) | 139.3 |
| 245K × 4 aggregate (`aggbench`) | 103.5 sum_tg, wall 79.6 (n=1) | 100.2 |
| full window 4 × 261,632 + 512 | **106.9** (105.8 / 108.0), span 23.0-23.6 s, **8/8 streams `finish=length` at prompt+gen 262,136** | 108.3, one boot with 3/4 streams ending early |
| full window 3 × 261,632 + 512 | 85.7 sum_tg, 3/3 `finish=length` (n=1) | not measured |
| warm 262K decode, single stream | 59.2 tok/s (59.1 / 59.3) | 55.9 |
| cold 262K prefill, single stream | not re-taken (the payload was already cached by the warm probe) | 4,964 tok/s, TTFT 52.7 s |
| VRAM / RAM | Σ 58.1 GiB used (14.5/14.4/14.4/14.8), free 504/587/597/211 MiB · pinned 57.20 GiB | Σ 58.6 GiB · 57.65 pinned |
| gate | `QUALGATE-PASS` both boots, `acc-drift-ok 1.0`, `det` ×3 = anchor, **`boots=1`** through ~40 min of mixed load | PASS, but the lane respawned at 15:38 |

- The concurrency cells are the point: aggregate did not pay for the fix at all (8K×4 139.3 → 138.6,
  245K×4 100.2 → **103.5**, full window 108.3 → 106.9), while the deepest *single-stream* points
  read 6-10% lower and cold pp at depth ~4-5% lower.  A lane that survives its own gate fixture and
  proves 8/8 streams at the window edge is worth 6% at 165K single stream.
- Card 3 still runs down to ~210 MiB free under sustained gate + full-window load.  It survived
  (no respawn), which is the difference between a thin margin and no margin, but it is why the
  respawn detector in `lane.sh` is mandatory and why the 6-card rebalance stays an open ticket.

## 7. Engine bump 0.1.59 → 0.1.61 — adoption re-test (2026-11-19)

The bump is whole-version, never cherry-picks (standing policy): upstream `828a8082d`
(`v0.2.15-nvfp4.2`, strata **0.1.61**), 1169 commits / 280 engine files over the old pin
`b08a70078`; our 72 commits carried eleven engine-side files across the merge (bump commit note,
`DEVLOG.md` H.7-H.8).  Re-measure ran on the shipping lane with the `aggbench` protocol (warm
burst, published `sum_tg` caliber): 4-card boots `cutover061` (15:38) and `repanel4c` (19:21),
6-card boot `6c061` (18:12) plus three paired 8K×6 runs.

### 7a. 6 cards, same protocol on both sides (0.1.59 arm = `A1_6x3`, the shipped shape)

| Metric | 0.1.59 | 0.1.61 | Δ |
| --- | ---: | ---: | ---: |
| solo 8K | 84.5 | **91.8** | **+8.6%** |
| solo 50K | 72.8 | **80.8** | **+11.0%** |
| 8K × 6 | 227.9 | 207.4 (median of 182.1 / 207.4 / 231.9) | −9.0% — queue-timing dominated, read the band, not the point (`DEVLOG.md` H.9) |
| 50K × 6 | 200.9 | 206.9 (208.0 / 205.8) | +3.0% |
| 131K × 6 | 135.0 | 138.4 | +2.5% |
| full window 6 × 262,136 warm | 145.6 | **158.5** | **+8.9%** (5/6 `held_edge`, 1 natural EOS — the shipping-boot signature) |
| full window 6 × 262,136 cold | not taken same-caliber | 131.4 | — |

### 7b. 4 cards, two boots (0.1.59 has no same-caliber 6-stream history — the shipped 4-card shape is 4-way, §6e.1)

| Metric | boot 1 | boot 2 | state |
| --- | ---: | ---: | ---: |
| solo 8K | 85.0 | 77.2 | 81.1 |
| solo 50K | 70.7 | 74.6 | 72.7 |
| 8K × 6 | 240.8 | 253.7 | 247.2 |
| 50K × 6 | 245.0 | 231.5 | 238.2 |
| 131K × 6 | 178.0 | 181.6 | 179.8 |
| full window 4 × 262,136 warm | 102.4 (4/4 `held_edge`) | — | **−4.2%** vs 0.1.59 106.9 (105.8 / 108.0), inside the boot band; the shipping-binary boot read 109.3 |
| full window 4 × 262,136 cold | 90.7 | — | not taken on 0.1.59 same-caliber |

- The 4-card lane keeps its full-window invariant on 0.1.61: every stream byte-exact to 262,136
  with `finish=length`.
- The short-window aggregates (247 / 238 at six streams) now read **above** the 6-card lane's —
  the stage-count axis again (§5, `DEVLOG.md` H.9): six shallow streams share more per window on
  four fat stages than on six thin ones.  Card count remains the full-window concurrency axis,
  not the decode-rate axis.

### 7c. Gates and accuracy on the bumped lane

- `qualgate` 0 FAIL on both card counts, `det_same: true` against the anchor.
- GSM8K-500 subset **97.00%** (485/500); HumanEval-164 **96.34%** (158/164) at the 3,000-token
  budget — the 0.1.59 tail-fix sample fails again at 3,000 and was not re-run at 6,000 on 0.1.61;
  MATH500 not re-run.  Caliber notes in `ACCURACY.md`.
- Chart: `results/charts/ac922-bump-061-2026-11-19.png`.
- Left open by this round: full-suite GSM8K / MATH500 / HumanEval on 0.1.61, the `sbench long`
  ladder on the new engine (today's numbers are `aggbench` caliber; §6e.1's ladder is 0.1.59),
  and the 8K×6 median band (±12%) still needs a third 6-card boot to tighten.

## 8. Conversation-disk cold tier — adopted (2026-11-20)

What: evicted parked conversations go to NVMe and are read back on the next turn instead of being
re-prefilled.  Engine flags (`--conversation-disk-*`, `--conversation-cache-min-free-mib`,
`--memory-limit-mib`) are upstream since the 0.2.15 line; this lane wires them through env only
(`scripts/strata-nvfp4-run.sh` passthrough, `lane.env` production block) — zero engine-code change,
rollback = delete the block.

Production shape: CC 49152 unchanged · disk budget 512 GiB (`/data/cache/conv-disk`, ttl 0 = budget
eviction only, persist on, idle sync 30 s) · park only ≥32768 tokens · RAM floor 52428 MiB computed
from REAL 262144 · `--memory-limit-mib 262144` pins the engine's RAM view (MemAvailable on this box
is over-reported by the HBM-window mapping; without the pin the floor is weaker than it looks).

Measured (4-card boots, `phaseA/phaseC` harnesses, `AC922-bringup-runbook.md` §327):
- parked footprint ≈1.2-1.6 GiB per 64K conversation (~0.02 MB/token — tail state, not the full KV);
- resurrection wall at scale: 16 × 148K conversations, CC exactly saturated → **median 6.9 s**
  (gate ≤11 s), **COLD = 0**, 8 RAM hits + 8 disk hits, NVMe read-back ~2.1 GB/s;
- cold prefill of the same depth is 46 s — the disk tier buys ~6.6× on the evicted half;
- gates: `QUALGATE-PASS` (det ×3 + acc-drift) on both the test boot and the shipped boot;
  follow-up answers are bit-identical across two eviction-resurrection cycles.
- known edge (open ticket, scheduler-side): a cold-prefill storm can race the write queue — the
  most recent chain may not be on disk yet when it is evicted, and that one resurrection falls to
  cold (94 s).  With the snapshot on disk, resurrection under concurrency costs 1.3 s.

## 9. The nvfp4.5 read-through, the real cross-boot band, and three arms that could not fire (2026-11-22, 6 cards)

### 9a. Anchors: how much a boot-to-boot difference is worth

Same lane, same binary, same env (`--batch 6 --batch-groups 3`, CC 48 GiB, resident complement 64 GiB,
learned expert profile), three boots - `ctl22` 00:45-01:00, `ctlA` 04:07-04:24, `ctlB` 05:13-05:23:

| depth | sum_tg (ctl22 / ctlA / ctlB) | tg_med | rows/step | spread (max-min) |
|---|---|---|---|---|
| 8 000 (cold) | 108.1 / 107.3 / 105.5 | 12.8 / 12.9 / 12.6 | 3.42 / 3.43 / 3.35 | 2.4 % |
| 50 000 | 208.4 / 211.3 / - | 33.9 / 35.4 / - | 3.35 / 3.36 | 1.4 % (2 boots) |
| 98 000 | 179.8 / 172.8 / - | 28.8 / 28.2 / - | 3.39 / 3.35 | 4.0 % (2 boots) |
| 131 000 | **160.5 / 146.9 / 166.1** | 26.1 / 23.5 / 27.2 | 3.24 / 3.27 / 3.30 | **13.1 %** (sd ~6 %) |

rows/step agrees to two decimals in every boot at every depth, so the ring shape is stable and what
moves is the speed inside the window - and at the deep end the movement is larger than any single
ticket we have measured all week.  The rule this wave therefore runs on:

- shallow/mid deltas (<=98 K): read against ~2-4 %, a single boot is admissible;
- deep (131 K): a single boot resolves nothing below ~15 %; anything smaller is reported as
  "not resolvable at this statistical power", never as neutral and never as a win;
- `wall_tg` is noisier than `sum_tg` at depth (112.1 / 102.0 / 129.8), because it folds the admission
  and prefill tail in; the decode columns are the ones quoted.

### 9b. Wave Q2 - env arms on the shipping .4 lane (all 6 cards, deltas against ctlA)

| arm | what it changes | 8K | 50K | 98K | 131K | verdict |
|---|---|---|---|---|---|---|
| `noarena` | drop `--resident-budget-gib`: experts stream through the file-cache arena (`MAP_HUGETLB unavailable`, NUMA placement not applied) | +3.3 % | -2.7 % | **-16.2 %** | **-23.3 %** (tg_med -27.2 %) | **the page-locked complement is worth 16-27 % at deep context** - and it is the price `serve/replicas.py:106` makes a replica layout pay, since `--resident-budget-gib` is on its `ARENA_INCOMPATIBLE` list |
| `dx` | `SELECT_LONG=1 SCORES_DX=1 SELECT_LONG_AT=24576` (the RTX 50 long-context block scorer) | -2.0 % | -2.5 % | -1.2 % | -5.2 % | no gain on V100.  8K/50K are inert by construction (the path needs a window graph over 24 576 cells, `src/core/verify.cpp:191-214`), so only 98K/131K are informative, and they are neutral-to-negative at or under the band |
| `topk` | `TOPK_COMPACT=1` (sister claim: +3.5-4 % decode) | -3.2 % | -0.0 % | +1.3 % | -1.8 % | **cannot fire in this lane**: the compact path dispatches past the register kernel's reach, `TK_T * TK_PER = 1024 * 33 = 33 792` blocks, i.e. above ~270 K tokens, or in the counted prompt band (`> 22 528` blocks with `active_blocks > 0`) - our decode windows top out at 32 768 blocks (`src/kernels/cuda/qsa_select.cu:907,3470-3476`).  The four deltas are the band, not a ticket |

With `ctlB` as the second anchor (9a), the 131 K column of this table is read as advisory: `noarena` is
the only arm whose delta exceeds the 13 % boot-to-boot spread, and it exceeds it in the same direction
as its 98 K delta (-16.2 %), so the mechanism and the magnitude agree.  `dx` and `topk` sit inside the
spread and are retired on their **mechanism** (9c), not on these numbers.

### 9c. The mechanism audit is earning its keep

Three of this wave's tickets turn out to be structurally inert here (session-elastic tables gated on
`o.batch <= 1`; `TOPK_COMPACT` gated on a block count our window cannot reach; `SELECT_LONG` gated on
a cell count the two shallow depths never reach).  Booking a boot for each of them would have produced
four "neutral" rows and one wrong adoption - which is exactly what METHODOLOGY section 2 exists to stop.
The rule that came out of it: before an arm is scheduled, name the condition that makes the code path
run and check the lane can satisfy it.

### 9d. What the bump itself changes for a V100 box

`v0.2.15-nvfp4.4` -> `v0.2.15-nvfp4.5` keeps `project(strata VERSION 0.1.61)`, so the version string is
not evidence; the knob inventory is (870 `STRATA_*` names in the .4 binary, 899 in .5).  Three of the
30 new names act on **every** CUDA card rather than only the RTX 50 tables:
`STRATA_KV_RESIDENT_FAST` (on unless `=0`, `src/kernels/cuda/kv_stream.cu:401`),
`STRATA_MTP_MERGE_FAST` (on unless `=0`, `src/kernels/cuda/qsa_decode_attn.cu:1635`) and the opt-in
`STRATA_MTP_ATTN_PIPE` (:1630).  `src/core/nv_defaults.cpp` carries sections for 8.6, 6.0, 12.1 and
12.0 and none for 7.0, so nothing else is silently adopted on V100.  Their arms (`kfast0`, `mmerge`,
`dxr`, `ilp`) follow in 9e.

### 9e. Wave Q3 - the `.5` tree measured on our cards (three boots, deltas against the three `.4` anchors)

Same protocol, same pack, same `expert-profile-learned.bin` (sha256 `592b...` in both trees), launch
cmdlines byte-identical apart from the tree path:

| arm | what it changes | 8K | 50K | 98K | 131K |
|---|---|---|---|---|---|
| `ctl5` (boot 05:35) | `.5` as-is | 106.4 (-0.5%) | 207.7 (-1.0%) | 172.1 (-2.4%) | **136.0 (-13.8%)** |
| `kfast0` (boot 05:51) | `ctl5` + `STRATA_KV_RESIDENT_FAST=0` | 104.7 (-2.1%) | 208.0 (-0.9%) | 170.0 (-3.6%) | **137.6 (-12.8%)** |
| `mmerge` (boot 06:07) | `ctl5` + `STRATA_MTP_MERGE_FAST=0` | 106.3 (-0.6%) | 208.2 (-0.8%) | 170.5 (-3.3%) | **132.4 (-16.1%)** |
| `.4` anchors | `ctl22` / `ctlA` / `ctlB` | 108.1 / 107.3 / 105.5 | 208.4 / 211.3 | 179.8 / 172.8 | 160.5 / 146.9 / 166.1 |

Read:

- the three `.5` boots cluster at **132-138** at 131K (3.9% apart) while the three `.4` boots sit at
  **147-166** (13.1% apart): every `.5` boot is below every `.4` boot, and the spread inside `.5` is
  *tighter*, so this is not the band moving;
- the two candidate explanations we could reverse from our side (`KV_RESIDENT_FAST`, `MTP_MERGE_FAST`)
  were each reversed in a dedicated boot and neither moved 131K by more than the replicate noise -
  the regression is not one of the two new all-CUDA defaults;
- the same shape repeats at 98K (170.0/170.5/172.1 vs 172.8/179.8, ~-3%) and disappears at 8K/50K
  (all within +-2%), i.e. it is a **depth-dependent** cost, consistent with `.5`'s "decode round 5"
  work being aimed at RTX 50 long-dialog paths;
- `dxr` (the DX/long-scorer bundle) was not run - the queue was closed once the verdict was clear.

**Decision: not adopted.** Production stays on `v0.2.15-nvfp4.4` (`/data/work/bump-src`). Per the
whole-version policy we do not cherry-pick, so `.5` is re-read at the next `-nvfp4.x` tag; the evidence
we hand upstream is the table above (3 boots x 4 depths, byte-identical launch, V100 SXM2, sm70 tables
on, `STRATA_DECODE_TIMING=1`).

**Operational defect found and fixed while closing the queue:** `armf5.sh` reads `FW`/`GEN` from its own
environment, but the queue passes them as positional args (`FW=1`), so the full-window arm silently
skipped its `fullwindow.py` step - the fix exports the named args before use.  Also recorded: killing an
arm is not enough, the `strata-nvfp4-run.sh start` supervisor and its `serve.server` front respawn the
engine; the kill order is queue -> arm -> supervisor -> front -> vision helper -> engine, verified by
pid stability over 10 s.
