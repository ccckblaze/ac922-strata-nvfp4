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
| Cold pp @ ~7K | 2,246 | 1,488 | 2,955 | 1,897 | 1,790 |
| Cold pp @ ~54K | 5,154 | 3,970 | 5,785 | 1,905 | 2,159 |
| Cold pp @ full window | **7,016** | **5,120** | — (reach 210K: 5,041) | — (reach 89K) | 2,121 @ 260K |
| tg @ 8K | 66.0 | 57.2 | 63.3 | 89.3–102.2 (bench3 caliber +14%) | 85.9 |
| tg @ full window | 72.5 | 57.1 | — | — | 39.2 |
| Agg decode (best measured) | 218.2 (8K×6) | 133.6 (8K×4) | 235.0 (50K×4) | 119.7 (1.5K×4) | 115.3 (1.5K×4) |
| VRAM | 87.7 GiB | 58.6 GiB | 13.4–14.9/card | 53.0 | 66.8 |
| RAM | 47.7 pinned | 47.7 pinned | ~40 experts | ~62 used | — |

Caliber warnings (also printed on the chart): S1 deep-window tg (110–112) is inflated by the
synthetic repeated-corpus ladder + ngram drafting and is excluded from same-length slices;
Monster 8K tg differs ~14% between bench3 and ladder calibers — read same-boot ladders only.

## 4. Charts

- `results/charts/ac922-samelen-v2-2026-11-16.png` — 7-panel panorama: prefill / decode /
  aggregate / reach / same-length slices / resource bill / accuracy.
- `results/charts/ac922-paths-final-2026-11-15.png` — four-path final (V1 1Cat-vLLM appears only
  as a paper anchor, never ranked).
- `results/charts/ac922-samelen-v3-2026-11-17.png` — the same panorama re-taken on the shipped
  4-card state, with both NVFP4 ladders measured in one protocol on 2026-11-17 (generator:
  `results/charts/plot-samelen-v3-2026-11-17.py`).

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

From the engine's own `STRATA_DECODE_TIMING` on this box: the dominant term is the **expert
product** (≈61% of window time), GPU-reach wait ≈28%, host CPU ≈12%. Consequences we verified:
- per-stream tg is a **stage-count** axis, not a card-count axis (2–3 card tg ≈ 6 card tg);
- concurrency gains come from sharing expert streaming reads inside one window
  (`--batch-groups` with ≥2 rows), which is exactly what the adopted ring shape does;
- host-wait reduction levers adopted: NUMA bind + PLE park; the remaining big lever (FP4
  tensor-core expert kernels, ticket ⑧/QPN2) is parked upstream-side, not ported.

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

### 6d. The shipped geometry is 4-card (2026-11-17, adopted config, one boot)

Cards 4/5 go back to the dev/iteration lane, so the ship state is the 4-card shape: `gpu [0,1,2,3]`,
`--batch 4 --batch-groups 2` (the launcher now derives that pair and `NB_CUT=0.69` from the card
count, so `start` with no env is the production command). Everything below is one boot of the
adopted config, measured with the same harnesses as 6c so the two geometries are directly
comparable.

| Item | 4-card adopted | 6-card adopted (6c) | 4-card before (B4) |
| --- | ---: | ---: | ---: |
| tg 8K single stream | **77.3** | 84.8 | 57.1 |
| tg 54K / 110K / 165K / 210K | 71.8 / 69.1 / 72.0 / 70.0 | 76.0 / 77.8 / 82.2 / 82.7 | 64.8 / — / — / — |
| tg 65K single stream | 73.0 | not measured | 62.4 |
| cold pp 54K / 110K / 210K | 4,154 / 4,158 / 3,704 | 6,069 / 6,004 / 5,431 | 3,970 / 4,162 / — |
| 8K x 4 aggregate (`aggbench`) | **135.6** sum_tg, wall_tg 110.3, per-stream 33.7 | (6 x 8K 218.2, pre-adopt) | 133.6 |
| 245K x 4 aggregate (`aggbench`) | **100.2** sum_tg, wall_tg 77.8, per-stream 24.4 | (6 x 245K 166.4, pre-adopt) | 93.5 |
| full window 4 x 261,632 + 512 | **all exact**, prompt+gen 262,136, span 22.6 s, sum_tg **108.1**, per-stream 23.7-30.7 | 6 x exact, sum_tg 155.0 | not measured at the true ceiling |
| cold 262K prefill, single stream | ttft 52.7 s (pp ~4,964) | — | — |
| warm 262K decode, single stream | 55.9 tok/s (EOS after 456) | — | — |
| VRAM / RAM | 14.6/14.5/14.5/14.9 GiB, Σ 58.6 · resident pinned 57.65 GiB | 87.7 GiB Σ · 47.7 pinned | 58.6 GiB Σ · 47.7 pinned |

- Shape evidence: `batch (pipelined, 2 groups of 2)`, `rows_per_step 3.35`, `STRATA_PIPED_MTP`
  drafting one MTP proposal per slot per window - the 4-card geometry runs the same batched ring,
  not a degraded one.
- Quality: `det` x3 reproduced `bfd40ecd4692`, **bit-identical to the 6-card REF across the geometry
  switch** (the det fixture does not see residency), gate `QUALGATE-PASS` with acc-drift 1.003
  against a freshly written 4-card `REF.json`; the 6-card anchor is preserved beside it as
  `REF-6card-adopted-20261117.json`.
- What the tickets bought at 4 cards: short-window single stream +35% (57.1 → 77.3), deep single
  stream +8-11% over B4, 245K x 4 aggregate +7%, 8K x 4 aggregate flat (+1.5%) - concurrency at
  short windows is admission-bound, not residency-bound, which is why the same +35% does not appear
  in the aggregate column.
- Honest gap: the adopted 6-card *aggregate* was never re-measured with `aggbench`, so the 218.2 /
  166.4 cells above are pre-adopt. Reading 4-card adopted against them understates the 6-card ship
  state; the comparison that is fully same-day and same-harness is the single-stream ladder and the
  true-ceiling full-window row.
