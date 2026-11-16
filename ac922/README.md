# AC922 final lane — Qwen3.8-Flash-Next NVFP4-GPTQ on 6× V100 (POWER9)

[English](README.md) · [简体中文](README.zh-CN.md)

This directory is the **pinned, measured deliverable** of our deployment of this engine on an
IBM AC922: dual POWER9 20C SMT4, 6× V100-16GB SXM2 (NVLink 2.0, two NUMA islands of three cards),
**256 GiB real DDR** (note: `/proc/meminfo` over-reports — the V100 HBM windows are mapped into
system physical address space and are not RAM), NVMe (Intel PM1725a). Everything here is what we
actually ran; nothing is aspirational.

## Headline numbers (final baseline, all measured on this box)

| Metric | 6-card lane (B6) | 4-card lane (B4) |
| --- | ---: | ---: |
| Max context, live | **262,144** (probe 261,494 passes) | **262,144** |
| Cold prefill @ 262K | **7,016 tok/s** | 5,120 tok/s |
| Cold prefill @ 6.8K / 54K | 2,246 / 5,154 | 1,488 / 3,970 |
| Single-stream decode @ 8K | 66.0 tok/s | 57.2 tok/s |
| Single-stream decode @ 262K | 72.5 tok/s | 57.1 tok/s |
| Full-window concurrency | **6 × 245K live, agg 166.4 tok/s** (23.4/stream) | 4 × 245K, agg 93.5 (21.6/stream) |
| Short-window concurrency @ 8K | agg 218.2 (35.7/stream) | agg 133.6 (33.3/stream) |
| VRAM resident | 87.7 GiB (≈14.5–15.0/card) | 58.6 GiB |
| Host RAM pinned | 47.7 GiB | 47.7 GiB |

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
- **Config + launcher**: `configs/strata-nvfp4-final.json`, `scripts/strata-nvfp4-run.sh`
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
