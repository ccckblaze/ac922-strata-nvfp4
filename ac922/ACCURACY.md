# Accuracy — final lane (N1, NVFP4-GPTQ) on this box

Grading stack is torch-free and self-built (`eval/`), methodology in `METHODOLOGY.md` §5-6.
All full-suite readings below have **errors = 0** and passed the budget audit.

## Full suites (citable)

| Suite | n | Accuracy | Notes |
| --- | ---: | ---: | --- |
| GSM8K | 1,319 | **97.12%** | 13 `pred=None` re-checked under NFKC, recovered 0 → true misses |
| MATH500 | 500 | **89.40%** | — |
| HumanEval | 164 | **96.95%** (96.34% before tail fix) | 1 sample hit the 3,000-token budget and failed; re-run at 6,000 flipped it to pass (`flipped=1`). Both numbers published side by side, the 96.95 is the citable one |

## 0.1.61 re-test (2026-11-19, 4-card boot after the bump)

| Suite | n | Accuracy | Notes |
| --- | ---: | ---: | --- |
| GSM8K (500-item subset) | 500 | **97.00%** | 485/500, errors = 0; the full 1,319 suite has not been re-run on 0.1.61, 97.12% stays the citable full-suite number |
| HumanEval | 164 | **96.34%** | 158/164 at the **3,000**-token budget; the same tail sample that 0.1.59 flipped at 6,000 was not re-run at 6,000 on 0.1.61 — the two numbers are budget calibers, not a regression claim |
| MATH500 | — | not re-run | 89.40% remains the 0.1.59 reading |

`qualgate` on the bumped lane: 0 FAIL, `det_same: true`, `acc-drift-ok` — the numeric path is
unchanged at the gate level (`PERFORMANCE.md` §7c).

## Smoke tier (24 items — trajectory only, not citable as a score)

| Suite | Smoke acc |
| --- | ---: |
| GSM8K | 93.75% |
| MATH500 | 93.75% |
| AIME | 87.50% (full stopped at 10/60, 90.00% — not citable) |
| HumanEval / HumanEval+ | 100% |
| ifeval (official checkers) | 100% |
| LiveCodeBench (union selftest) | 83.33% |
| ceval (fixed MC caliber) | 95.83% |

## What is NOT here, and why

- **Cross-stack accuracy.** Both reference lanes were closed under our grading shapes:
  - **S1** (IQ3_XXS on strata 0.1.41, accuracy-lane recreation): nine arms across two campaigns;
    death modes covered verify timeout (#267), immediate-EOS (`ctok=1`), and a `decode -1`
    watchdog self-kill. Its *performance* lane (2026-11-12, port 8090, minimal flags) did work
    and is published in `PERFORMANCE.md` — the accuracy replay never did.
  - **M1** (llama.cpp Monster, IQ3_XXS): chat endpoint stops after 1 token under template
    wrapping; bare completion produces out-of-distribution indented `def` continuations that no
    stop-string or prompt-shape variant fixed (three falsified hypotheses). This is a pack/asset
    property, not a config bug.
- **Official-model denominator.** The meaningful next measurement is N1 vs the official model on
  *identical prompts* (`eval/ref_run.py` replay + `eval/compare_results.py`: agreement rate,
  both-correct, flips, regressions). Harness is built and gated; it needs one API key.
- **GPQA-Diamond / IFBench / LiveCodeBench-v6 / ToolEval**: graders, fixtures and self-tests are
  green (including grader parity against official fixtures, residual 5/344 traced to upstream
  fixture drift and documented); GPU runs are queued.
- **Vision suites** (RealWorldQA / MathVision / CharXiv): still queued, and the earlier reason is
  withdrawn — the tower exists (`mmproj-F16.gguf`, ~904 MB, in `unsloth/Qwen3.8-Flash-Next-GGUF`),
  so image input itself is shipped and on by default (CPU FP32 `strata-vision`; reference-image
  round trip 2-4 s). What has not been run is the graded *suites*, which are GPU-queue work, not a
  missing asset.

## Quality gates the lane passes today

- Determinism: three identical runs → identical hashes (`det_same=true` on pinned REF).
- Any non-bit-exact engine knob must clear quant-quality ruler + divergence-bisection probe +
  determinism before adoption (`DECISIONS.md` adopted exactly one such class, and it cleared).
