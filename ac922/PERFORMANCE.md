# Performance — final baselines and cross-stack comparison

All rows are measured on this AC922. The final lane is **B6** (6-card) with the adopted state in
`DECISIONS.md`; **B4** is the same config restricted to GPU 0-3.

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
| NVFP4 6-card / 4-card | **261,494** | model hard cap 262,144; engine keeps ~24-30K per slot, net usable ≈ 232K for prompt+generation headroom |
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
