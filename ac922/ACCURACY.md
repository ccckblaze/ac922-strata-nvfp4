# Accuracy — final lane (N1, NVFP4-GPTQ) on this box

Grading stack is torch-free and self-built (`eval/`), methodology in `METHODOLOGY.md` §5-6.
All full-suite readings below have **errors = 0** and passed the budget audit.

## Full suites (citable)

| Suite | n | Accuracy | Notes |
| --- | ---: | ---: | --- |
| GSM8K | 1,319 | **97.12%** | 13 `pred=None` re-checked under NFKC, recovered 0 → true misses |
| MATH500 | 500 | **89.40%** | — |
| HumanEval | 164 | **96.95%** (96.34% before tail fix) | 1 sample hit the 3,000-token budget and failed; re-run at 6,000 flipped it to pass (`flipped=1`). Both numbers published side by side, the 96.95 is the citable one |

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
- **Vision suites** (RealWorldQA / MathVision / CharXiv): blocked by the model export — the
  NVFP4 repo has no vision tower (`mmproj*` absent), not a code gap.

## Quality gates the lane passes today

- Determinism: three identical runs → identical hashes (`det_same=true` on pinned REF).
- Any non-bit-exact engine knob must clear quant-quality ruler + divergence-bisection probe +
  determinism before adoption (`DECISIONS.md` adopted exactly one such class, and it cleared).
