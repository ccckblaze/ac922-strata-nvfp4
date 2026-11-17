# AC922 final lane — Qwen3.8-Flash-Next NVFP4-GPTQ on 6× V100 (POWER9)

[English](README.md) · [简体中文](README.zh-CN.md)

This directory is the **pinned, measured deliverable** of our deployment of this engine on an
IBM AC922: dual POWER9 20C SMT4, 6× V100-16GB SXM2 (NVLink 2.0, two NUMA islands of three cards),
**256 GiB real DDR** (note: `/proc/meminfo` over-reports — the V100 HBM windows are mapped into
system physical address space and are not RAM), NVMe (Intel PM1725a). Everything here is what we
actually ran; nothing is aspirational.

## Headline numbers (final baseline, all measured on this box)

| Metric | **4-card lane (shipped)** | 6-card lane (reference) |
| --- | ---: | ---: |
| Max context, live | **262,136 reachable**: 4 × 261,632 + 512, every stream exact | 6 × 261,632 + 512, every stream exact |
| Cold prefill @ 262K, single stream | 4,964 tok/s (TTFT 52.7 s) | 7,016 tok/s (261,494 ladder) |
| Cold prefill @ 6.8K / 54K | 2,057 / 4,156 | 2,499 / 6,104 |
| Single-stream decode @ 8K | 71.7 tok/s | 82.1 tok/s |
| Single-stream decode @ 210K | 71.2 tok/s | 81.1 tok/s |
| Full-window concurrency | 4 × 262,136 live, agg 108.3 tok/s (23.7–30.7/stream) | 6 × 262,136 live, agg 145.6 (20.7–32.4/stream) |
| Short-window concurrency @ 8K | agg 139.3 (34.8/stream) | agg 240.1 (39.7/stream) |
| VRAM resident | 58.6 GiB (14.5–14.9/card) | 85.3 GiB (14.0–14.8/card) |
| Host RAM pinned | 57.65 GiB | 54.42 GiB |

Both columns are the adopted state measured 2026-11-17 (`eval/cphase.py` arms, `aggbench`,
`fullwindow.py`) as the **median of two independent boots per geometry** — a single deep-window run
carries ±15–25% boot noise, so no number here comes from one boot alone unless §6d marks it `n=1`.
Full table with calibers: `PERFORMANCE.md` §6d.

Accuracy (full suites, errors = 0, budget audit clean): **GSM8K 97.12%** (1,319),
**MATH500 89.40%** (500), **HumanEval 96.95%** (164). See `ACCURACY.md`.

Multimodal is on by default: image input through the OpenAI `image_url` shape, encoded by a
CPU-only FP32 tower (`strata-vision` + `mmproj-F16.gguf`, reference-image round trip 2–4 s).
Measured cost to the text lane: **VRAM delta 0**, single-stream and full-window numbers inside
box drift (262K probe pp 6,949 vs 7,016; 6×245K steady 156–169 agg with and without image
probes interleaved); image requests under 18-row over-subscription queue (TTFT 28–44 s), never
crash. `VISION=0` at launch opts out.

Capacity rule that held across every experiment: **N cards ↔ N full-window streams**
(over-subscription queues, never crashes — verified at 8 × 245K on 6 cards).

## What is pinned here

- **Engine**: exactly upstream commit `b08a70078` (strata 0.1.59, tag `v0.2.13-nvfp4.1`) — the
  deliverable commit that adds this `ac922/` directory touches no engine code, so building this
  branch compiles the measured engine. Built on ppc64le with CUDA 12.4 for `sm_70`. See `BUILD.md`.
- **Model assets**: Hugging Face [`Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata`](https://huggingface.co/Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata)
  — 17 files, checksums in `results/MODEL-SHA256SUMS.txt` (all verified before the final baseline).
  Multimodal additionally uses `mmproj-F16.gguf` from `unsloth/Qwen3.8-Flash-Next-GGUF` (~904 MB;
  the dense GGUF already carries the `image-text-to-text` task declaration and `image_token_id`).
- **Config + launcher**: `configs/strata-nvfp4-final.json`; `scripts/lane4.sh` / `scripts/lane6.sh`
  (geometry shortcuts over `scripts/lane.sh`, which pins the machine layout, waits for health and
  proves the shape from the engine's own lines), low-level `scripts/strata-nvfp4-run.sh`
  (parameterized; production layout documented in `RUN.md`).
- **Adopted-knob ledger** with measured deltas and rejected tickets: `DECISIONS.md`.
- **Measurement discipline** (paired same-boot A/B, quality gates, budget audits): `METHODOLOGY.md`.
- **Full performance tables and cross-stack charts**: `PERFORMANCE.md`, `results/charts/`.
- **Development history**, incidents and lessons: `DEVLOG.md`.
- **Evaluation harness** (torch-free graders, lane probes, self-checks): `eval/`.

## Known gaps (deliberately not papered over)

- Cross-stack **accuracy** comparison is single-column: the two reference lanes (int8/IQ3_XXS
  Strata "S1", llama.cpp Monster "M1") were closed without usable endpoints **under our grading
  shapes** on this box — their *performance* curves remain measured and published
  (`DEVLOG.md`, `ACCURACY.md`, `PERFORMANCE.md`). The honest next step is re-anchoring against the official model's
  same-question answers via `eval/ref_run.py` (needs an API key).
- AIME/ceval are sample-level reads only (full suites were stopped); GPQA / IFBench /
  LiveCodeBench-v6 graders are built and gated but their GPU runs are queued.
- Chart panel ⑦ (accuracy) currently has one stack's bars; data backfill is scheduled.
- Upstream has 167 commits after our pin (next `-nvfp4.x` tag). Our policy: **whole-version bumps
  only, no cherry-picks**; the bump backlog is listed in `DEVLOG.md`.
