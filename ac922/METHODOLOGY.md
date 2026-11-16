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

Two corrections found while gating the parked-slot capacity change on 2026-11-16, both of which
change how the three gates above may be read:

- **A gate that cannot reach the lane must abort, not grade.**  Since the lane started requiring
  `STRATA_API_KEY` (2026-11-16), every fixture in `sbench.py` / `divprobe.py` / `cccheck.py` was
  issuing unauthenticated requests, so each gate collected zero rows and the wrapper turned that
  into `FAIL det-nondeterministic` / silent empty comparisons for ~24 h of adoptions.  The fixtures
  now send the key, exit 3 on 401/403, and `qualgate.sh` refuses to grade when rows are missing.
  Related hygiene: never resolve the engine log with `ls -t $LOGD/*.log | head -1` — that lands on
  the serve stdout log (or the other lane's), which is how pp/tg were being read back as `null`.
- **A hash cell is only meaningful at a controlled re-read depth.**  Greedy output on this lane depends
  on how much of a prompt was re-prefilled versus restored: for one identical 54K prompt, `reused=0`
  (cold), `reused=36864` (partial) and `reused=54028` (full relay) each give a different answer.  The
  first `divprobe` therefore could not even reproduce itself inside one boot -- its baseline and check
  passes ran at 54021 vs 54022 tokens re-read, which is a path change, not a numeric one.  Cells are now
  fixed-per-prompt, primed once so every measured cell sits on the same relay depth, and `qualgate`
  compares a cell only when REF's recorded `reused` matches exactly.
- **The cold path is bit-stable; the long-context relay path is not, and the gap is measured.**  The
  det fixture (8.5K prompt, 32 tokens, cold) reproduced the 2026-11-15 REF hash `bfd40ecd4692` exactly
  after a full day of config adoptions -- so determinism holds where it was ever claimed.  But replaying
  one identical 30K-token request four times at `temperature=0` in a single boot produced 3 distinct
  answers at 128 tokens and 4 at 512, first differing character at index 8 in half the pairs; and at
  *matched* depth, gate-to-gate runs still move 6 of 8 long relay cells (agreement ~2/8) while the cold
  det fixture never moves (`eval/repro_probe.py`).  The floor scales with context, not with horizon
  length alone.  So the gate is two-tier: **cold divergence = FAIL**, relay cells = **agreement rate
  against REF**, failing only at total collapse (0/8) or when no cell is comparable.  Demanding bit-equality of a single long relay cell
  would ask for something the lane cannot provide, and a gate nobody can pass is not a gate.
- **What remains a hard invariant**: `det_same` over three runs in one boot at the 32-token horizon,
  cold-path cell equality at recorded depth, `selfcheck.sh` cold == cached, and the aggregate accuracy
  anchors (`ACCURACY.md`) -- which are what actually carry a quality claim across a config change.
  Acceptance-rate drift is a *gross-collapse* sentinel only: it follows the suffix-draft model, which
  learns from everything the lane served since boot (observed 0.81 against a REF written minutes
  earlier in the same boot, 1.00 minutes later).

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

## 9. Resume-gap accounting (user-visible latency is not pp)

- Per-request `pp` in the engine self-report is a *phase rate*; a chat turn's perceived speed is
  `fixed cost + gap read + decode`, where `gap` is the tokens a continuation re-reads from its
  last checkpoint (`gap = prompt − reused`).  `eval/resume_probe.py` measures both against the
  same base prompt, and `eval/tg_short.py` runs alongside every geometry arm so a resume win that
  costs decode speed is never booked as a win.
- The re-read token is more expensive than a cold-computed token on this lane (measured slopes:
  resume ≈ 0.47 ms/token vs cold ≈ 0.17 ms/token), so *checkpoint grid depth*, not raw prefill
  throughput, sets interactive feel on long sessions.
- Which knob actually moves the grid is a measured question, not a doc question: `--prompt-cache-every`
  on its own moved nothing (bit-identical `reused`), and `--prompt-cache-tail` is `!multi_gpu`
  gated, i.e. unusable on a split.  Arms therefore always test chunk alone **and** chunk × every
  together, with the launched chunk read back from the engine's own startup banner — a config file
  is not evidence that a flag landed.
- Chain capacity is priced separately from geometry: `eval/cap_probe.py` runs N conversations cold,
  then sequentially, then concurrently, and reports the gap distribution *and* the serve RSS delta,
  because `--prompt-cache` is charged per session (~118 MB/entry here) — a capacity fix that is not
  budgeted is a pending OOM, not an optimization.
