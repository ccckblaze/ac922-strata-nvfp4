# Measurement methodology

Everything published in `PERFORMANCE.md` / `ACCURACY.md` obeys these rules. They exist because
each of them caught a real, expensive mistake.

## 1. Paired same-boot A/B

- One variable per arm; ≥30 windows per arm; ≥6 alternating rounds; decision = paired median +
  sign test (p < 0.05).
- **Effects smaller than 4 ms/window must never be judged across boots** (box drift is 5–10%;
  two of our early "wins" and two early "losses" died to this rule).
- Δ < 1.5% is recorded as neutral, not as a win.

## 2. Mechanism-combination audit before any death verdict

Three separate tickets were initially "dead" only because the correct combination was never
run together (the batched-window case: `--batch` without `--batch-groups ≥2 rows` is not
batching, it is pipelining overhead). Before adopting a negative verdict, we now ask: *which
switches would a working configuration need, and were all of them on at once?*

## 3. Quality gates for anything touching numeric paths

Non-bit-exact changes must pass all three before adoption:
1. **quant_quality ruler** — fixed-corpus token-level agreement vs the pinned reference;
2. **divergence bisection probe** — first-divergence position scan against an int8 reference
   decode, to localize numeric drift instead of trusting aggregate scores;
3. **determinism** — three identical runs, identical hashes; plus an acceptance-rate drift
   monitor (systematic acc drop = numeric-bias sentinel).

Bit-exact opt-ins skip 1–2 but still require 3. `det_same=true` on a pinned REF set is the
gate for "the lane is the lane" after any restart.

## 4. Lane acceptance before suites

`scripts/alive.py` — three real requests (arithmetic, one GSM8K item, one HumanEval exec-graded
item). A lane that fails any probe cannot produce valid suite data; this gate saved us from
hours of GPU burned on a lane that answered 200 OK while emitting exactly one token.

## 5. Budget audit on every scored run

- Any sample that consumed the full token budget is suspect by definition (truncation ≠ wrong
  answer). `eval/tailfix.py` re-runs exactly those samples at 2× budget and reports
  `flipped_to_hit`; both pre- and post-fix numbers are published side by side, never merged.
- `errors` (HTTP/parse failures) must be 0 for a reading to be citable.
- Multiple-choice suites run with thinking disabled and a fixed 1,200-token cap (a grading-
  caliber bug we found and fixed: reasoning text pushed the answer marker out of budget).

## 6. Grading shapes

- GSM8K/MATH: last `####`/`\boxed{}`/`Answer:` extraction, NFKC-normalized; we re-check
  `pred=None` cases under NFKC before accepting them as true misses (recovered=0 on GSM8K's 13).
- HumanEval/LiveCodeBench: execution-based (`check()` / hidden private tests) — immune to
  answer-shape artifacts, which is why it is our cross-endpoint yardstick.
- Rule tasks (ifeval/IFBench): official checker registries only; unresolved constraint ids are
  errors, never silent passes (this replaced three hand-written checkers that silently graded
  2 of 25 constraint classes).
- Grader parity: our port was diffed against official `eval_results` fixtures; residual 5/344
  instruction-level differences traced to upstream fixture drift, documented, gated.

## 7. Integrity of assets

- 17-file model manifest with sha256 verified before every baseline (`results/MODEL-SHA256SUMS.txt`).
- A hard power cut once left a sparse file with the right size and wrong bytes; size checks are
  banned as evidence — hashes only.

## 8. What we do NOT claim

- No PPL/logprob channel on this engine → quantization quality is measured by agreement,
  divergence probes, and task accuracy, not perplexity.
- Cross-stack accuracy comparison is currently single-stack (see `ACCURACY.md`); the planned
  anchor is official-model same-question replay (`eval/ref_run.py` + `eval/compare_results.py`).
