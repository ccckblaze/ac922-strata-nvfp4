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
| `--conversation-cache-mib 131072 --conversation-cache-slots 16` | ×5.3 budget | parked-chain capacity: recompute→restore −97% (65 s → 2 s) | pp/tg/VRAM regression 0.0% — pure config buys time |

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
`--prefill auto`, `--expert-cache auto`, `--layer-split auto`, expert-profile = repo default.

## Rejected (with the price we measured — do not re-litigate without new evidence)

| Ticket | Verdict | Evidence |
| --- | --- | --- |
| TurboQuant KV (k8v4) | **dead**: RAM −27% bought with PP −7–10%; quality not locally measurable | paired wave; re-priced once more after cold-cache PP landed, still negative exchange rate |
| Batched lockstep (old shape) | dead — **but see above**: the original verdict was taken on groups-of-1 and is partially overturned by correct shape | §"batch-groups auto" audit |
| DP `replicas: 3` | shelved: conflicts with shared expert arena (`--resident-budget-gib` is ARENA_INCOMPATIBLE); buys streams, costs 262K full-window per replica | read-only audit of `serve/replicas.py` |
| QPN2 kernel port (ticket ⑧) | cancelled: three independent reasons incl. sister tree already has same-family source; M≥17 windows never materialize at our stage counts | audit, no GPU burned |
| `--topk` variants, `TOPK_COMPACT` port | micro-benchmark says ~0.8%/window; sister claims 3.5–4% end-to-end — unresolved, parked behind a responsibility-field sieve before any wave | measured micro |
| `STRATA_SPLIT_OWN=1`, `--prefill-head-alias`, `--peer-prefill-rows` | all buy PP with tg/VRAM — direction opposite to our objective | static + spot checks |
| Reference lane S1 (IQ3_XXS Strata 0.1.41, accuracy replay) | **closed for accuracy**: the 2026-11-12 performance lane (port 8090, minimal flags, `STRATA_FETCH_PARTNER=0`) did work and its curves are published in `PERFORMANCE.md`; the later 0.1.41 accuracy-lane recreation (port 8092, extended flag set) never produced usable output — nine arms across two campaigns, death modes: verify timeout (#267), immediate-EOS, `decode -1` watchdog | full ledger in DEVLOG |
| Reference lane M1 (llama.cpp Monster IQ3_XXS) | **closed for accuracy**: no usable endpoint under our grading shapes (chat stops after 1 token; bare completion is OOD); performance data remains valid and published | three repair attempts, all falsified |
